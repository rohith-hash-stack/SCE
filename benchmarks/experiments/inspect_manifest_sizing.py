"""Offline sizing analysis for Approach A's Turn 1 manifest - no LLM calls,
no real-API spend. Approach A v2 (`hydration_loop.py`, commit `a3039b4`)
recovered `cpi_strict` to within 0.056 of baseline while keeping `fpr_gt`
well under the `<0.50` target, but at ~6x baseline's token cost (Turn 1
alone averaged 44,194 tokens, up to 73,380 for the most-connected seed).
This script measures, without spending anything, how much of that cost two
independent levers can remove before the next live grid:

1. **Format-level slimming**: the current pipe-delimited manifest line
   (`qualified_name|role|kind|signature|calls=[...]`) vs. a condensed
   YAML-flow format that drops `kind` (it mostly duplicates `role` for
   this spike's own 3-task/1-corpus scope) and drops the pipe-delimited
   XML-adjacent punctuation.
2. **Candidate-universe pruning**: hop-depth capping (2/3 hops instead of
   production's own default 6) and a same-top-level-module scope filter
   (the same `_module_prefix3` rule `submodular_knapsack.py`'s own
   `SCOPE_GATE_MIN_DIST` gate already uses elsewhere in this codebase,
   reused here, not reinvented) - keeping a cross-module candidate only
   when it's a direct (1-hop, real `CALLS`/`INSTANTIATES` edge) successor
   of the seed itself.

**Token counts are calibrated, not raw-local, numbers.** This sandbox has
no real BPE tokenizer available (`active_backend()` reports
`"fallback-regex (tiktoken unavailable: ProxyError)"` - confirmed while
validating Approach A v2, see the debrief) - a local `count_tokens()`
estimate on dotted-identifier-heavy text like this manifest can be off by
multiples of the real, API-billed number. Instead: for each task, this
script reconstructs the *exact* current-format, unbounded-reach Turn 1
prompt (same deterministic graph - `use_cache=False`, `PrismEngine`,
commit `c1026d0` - the real API call in commit `1e7f6fb` was built from),
measures its real byte length, and divides that into the REAL, API-billed
token count from that same commit's grid to get a real
tokens-per-byte ratio. Every other variant's projected token count is that
task's own ratio applied to the variant's own (locally reliable) byte
count - a calibrated projection, not a guess, and clearly labeled as a
projection, not a measurement, in the output.

Recall is checked against `task.adjudicated.pipeline_symbols` (what
`cpi_strict` itself measures) at every candidate-set variant, specifically
flagging any pipeline symbol a pruning level would drop - the real risk
"pruning" always carries, checked directly rather than assumed safe.

Usage:
    python -m benchmarks.experiments.inspect_manifest_sizing
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.experiments.hydration_loop import _declaration_line, _outgoing_call_names, _turn1_user_prompt
from benchmarks.ground_truth.loader import load_tasks_from_dir
from prism.packer.blast_radius import compute_upstream_callers
from prism.packer.submodular_knapsack import DEFAULT_MAX_HOPS, DEFAULT_UPSTREAM_MAX_HOPS, _classify_role, _module_prefix3
from prism.traversal.continuous_dijkstra import build_causal_graph, compute_topological_distances

TASKS_DIR = Path(__file__).resolve().parent.parent / "ground_truth" / "tasks" / "django"
TARGET_TASKS = (
    "django_t02_005_model_save_signals",
    "django_t02_009_queryset_filter_clone",
    "django_t02_017_redirect_url_safety_check",
)
HOP_RADII = (2.0, 3.0, DEFAULT_MAX_HOPS)  # DEFAULT_MAX_HOPS (6.0) = "Unbounded (Current)"

#: Real, API-billed Turn 1 `prompt_tokens` for the CURRENT (v2, pipe-
#: delimited, unbounded-reach) format, from the real 36-cell grid in
#: commit `1e7f6fb` (deterministic graph, use_cache=False, commit
#: `c1026d0`) - the one trustworthy source of real token counts in this
#: sandbox (OpenAI counts server-side; this sandbox's own local
#: tokenizer does not have a real BPE backend available - see this
#: module's own docstring). Used to calibrate every other projection
#: below against real, not locally-estimated, numbers.
REAL_TURN1_TOKENS = {
    "django_t02_005_model_save_signals": 23465,
    "django_t02_009_queryset_filter_clone": 35736,
    "django_t02_017_redirect_url_safety_check": 73380,
}


def _candidate_set_at_hops(builder, seed_id: str, max_hops: float, upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS):
    dist_w_map = compute_topological_distances(builder, seed_id, d_max=max_hops)
    upstream_callers = compute_upstream_callers(builder, seed_id)
    dist_w_upstream_map = {s: c.dist_w_upstream for s, c in upstream_callers.items()}
    candidates = {seed_id} | {n for n in dist_w_map if dist_w_map[n] <= max_hops} | {n for n in dist_w_upstream_map if dist_w_upstream_map[n] <= upstream_max_hops}
    candidates = {n for n in candidates if builder.symbol_table.get(n) is not None}
    return candidates, dist_w_map, dist_w_upstream_map


def _scope_filtered(builder, seed_id: str, candidates: set[str]) -> set[str]:
    """Drop a candidate outside the seed's own top-level-3 module prefix
    unless it's a direct (1-hop, real `CALLS`/`INSTANTIATES` edge, not a
    causal-graph synthetic-coupling edge) successor of the seed itself -
    see this module's own docstring for why `_module_prefix3` is reused,
    not reinvented.
    """
    seed_info = builder.symbol_table.get(seed_id)
    seed_prefix = _module_prefix3(seed_info.module) if seed_info is not None else ""
    direct_real_successors: set[str] = set()
    if seed_id in builder.graph:
        for succ in builder.graph.successors(seed_id):
            edge = builder.graph.get_edge_data(seed_id, succ) or {}
            if edge.get("relation") in ("CALLS", "INSTANTIATES"):
                direct_real_successors.add(succ)
    kept = set()
    for qname in candidates:
        if qname == seed_id or qname in direct_real_successors:
            kept.add(qname)
            continue
        info = builder.symbol_table.get(qname)
        module = info.module if info is not None else ""
        if _module_prefix3(module) == seed_prefix:
            kept.add(qname)
    return kept


def _render_current_format(builder, seed_id: str, candidates: set[str], dist_w_map, dist_w_upstream_map, direct_successors) -> str:
    lines = []
    for qname in sorted(candidates):
        info = builder.symbol_table.get(qname)
        role = _classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map)
        raw_sig = _declaration_line(builder, qname) or ""
        signature = " ".join(raw_sig.split())
        calls = _outgoing_call_names(builder, qname)
        lines.append(f"{qname}|{role}|{info.kind}|{signature}|calls=[{','.join(calls)}]")
    return "<candidate_index>\n" + "\n".join(lines) + "\n</candidate_index>"


def _render_condensed_format(builder, seed_id: str, candidates: set[str], dist_w_map, dist_w_upstream_map, direct_successors) -> str:
    """Drops `kind` entirely (see this module's own docstring) and the
    pipe-delimited punctuation in favor of a compact YAML-flow list.
    """
    lines = []
    for qname in sorted(candidates):
        role = _classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map)
        raw_sig = _declaration_line(builder, qname) or ""
        signature = " ".join(raw_sig.split())
        calls = _outgoing_call_names(builder, qname)
        calls_str = ", ".join(calls)
        lines.append(f"- qname: {qname}\n  role: {role}\n  sig: {signature}\n  calls: [{calls_str}]")
    return "\n".join(lines)


def _pipeline_recall(candidates: set[str], pipeline_symbols) -> tuple[float, list[str]]:
    pipeline_set = set(pipeline_symbols)
    if not pipeline_set:
        return 1.0, []
    missing = sorted(pipeline_set - candidates)
    recall = (len(pipeline_set) - len(missing)) / len(pipeline_set)
    return recall, missing


def main() -> int:
    load_result = load_tasks_from_dir(TASKS_DIR)
    tasks_by_id = {t.task_id: t for t in load_result.accepted}
    missing_tasks = [t for t in TARGET_TASKS if t not in tasks_by_id]
    if missing_tasks:
        print(f"error: target tasks not found in {TASKS_DIR}: {missing_tasks}", file=sys.stderr)
        return 1

    repo_path = str(resolve("django"))
    engine = PrismEngine()
    print(f"[sizing] indexing {repo_path} (use_cache=False, deterministic) ...", file=sys.stderr)
    engine.index(repo_path)
    builder = engine._builder

    all_rows = []
    for task_id in TARGET_TASKS:
        task = tasks_by_id[task_id]
        seed_id = task.seed_symbol
        graph = build_causal_graph(builder)
        direct_successors = set(graph.successors(seed_id)) if seed_id in graph else set()
        pipeline_symbols = task.adjudicated.pipeline_symbols

        # --- Calibration: current format, unbounded reach - the exact
        # shape the real 1e7f6fb grid sent. ---
        cand_unbounded, dist_w_map_u, dist_w_upstream_u = _candidate_set_at_hops(builder, seed_id, DEFAULT_MAX_HOPS)
        manifest_current_unbounded = _render_current_format(builder, seed_id, cand_unbounded, dist_w_map_u, dist_w_upstream_u, direct_successors)
        full_current_unbounded = _turn1_user_prompt(manifest_current_unbounded, task.prompt)
        calibration_bytes = len(full_current_unbounded)
        real_tokens = REAL_TURN1_TOKENS.get(task_id)
        calibration_ratio = (real_tokens / calibration_bytes) if real_tokens else None

        print(f"\n{'=' * 70}\n{task_id}  (seed={seed_id})\n{'=' * 70}")
        print(f"calibration: real Turn1 tokens={real_tokens}  bytes={calibration_bytes}  ratio={calibration_ratio:.5f} tok/byte" if calibration_ratio else "calibration: no real token count on file for this task")

        variants = [("unbounded_current", cand_unbounded, dist_w_map_u, dist_w_upstream_u, _render_current_format)]
        for hop in (2.0, 3.0):
            cand, dw, dwu = _candidate_set_at_hops(builder, seed_id, hop)
            variants.append((f"hop={hop:.0f}_current", cand, dw, dwu, _render_current_format))
        cand_scope = _scope_filtered(builder, seed_id, cand_unbounded)
        variants.append(("scope_filtered_current", cand_scope, dist_w_map_u, dist_w_upstream_u, _render_current_format))
        variants.append(("unbounded_condensed", cand_unbounded, dist_w_map_u, dist_w_upstream_u, _render_condensed_format))
        cand_hop3, dw3, dwu3 = _candidate_set_at_hops(builder, seed_id, 3.0)
        variants.append(("hop=3_condensed", cand_hop3, dw3, dwu3, _render_condensed_format))
        variants.append(("scope_filtered_condensed", cand_scope, dist_w_map_u, dist_w_upstream_u, _render_condensed_format))

        print(f"{'variant':<26}{'candidates':>11}{'recall':>9}{'missing_pipeline':>28}{'bytes':>9}{'proj_tokens':>13}")
        for name, cand, dw, dwu, render_fn in variants:
            manifest = render_fn(builder, seed_id, cand, dw, dwu, direct_successors)
            full_prompt = _turn1_user_prompt(manifest, task.prompt)
            n_bytes = len(full_prompt)
            recall, missing = _pipeline_recall(cand, pipeline_symbols)
            proj_tokens = round(n_bytes * calibration_ratio) if calibration_ratio else None
            missing_str = ",".join(s.rsplit(".", 1)[-1] for s in missing) if missing else "-"
            print(f"{name:<26}{len(cand):>11}{recall:>9.3f}{missing_str:>28}{n_bytes:>9}{(proj_tokens if proj_tokens is not None else '-'):>13}")
            all_rows.append({
                "task_id": task_id, "variant": name, "candidates": len(cand),
                "pipeline_recall": recall, "missing_pipeline_symbols": missing,
                "bytes": n_bytes, "projected_tokens": proj_tokens,
            })

    out_path = Path(__file__).resolve().parent / "results" / "manifest_sizing.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_rows, indent=2))
    print(f"\nWrote {len(all_rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
