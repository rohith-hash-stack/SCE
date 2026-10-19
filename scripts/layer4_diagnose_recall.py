"""Forward recall funnel for `prism_full` on the Layer 4 tournament: where
each task's gold callers are lost between the static graph and the
model's answer.

    PYTHONPATH=.:src python scripts/layer4_diagnose_recall.py \\
        --cells reports/layer4/tournament/cells.jsonl --out reports/layer4/diagnostics

Stages, per forward task (gold = the task's traced callers within its
`max_depth`):

1. **in index** - gold names in the corpus symbol table;
2. **in static graph** - reached by the upstream walk over CALLS /
   INSTANTIATES in-edges within `max_depth` (`_upstream_walk`, tests
   excluded): the graph's own upper bound;
3. **in V_cand** - in the Turn 1 candidate manifest exactly as `prism_full`
   builds it: for a `blast` task `direction="both"` under the cell budget
   (the transitive caller walk); before that routing, the default
   `direction="downstream"` admitted upstream only the top
   `UPSTREAM_FRONTIER_CAP` direct callers. A missing gold caller gets its
   reason;
4. **in packed context** - in the cell's recorded `selected_symbols` (after
   Turn 1, Turn 2's render budget, filtering and the token ceiling), per
   seed;
5. **emitted** - in the model's answer (`affected_symbols`, resolved as
   the scorer resolves them), per seed; split into gold emitted from the
   context, gold emitted without being in it, and packed gold not emitted.

Turn 1's reply is not stored in the cells, so the Turn 1 / budget split
(3 -> 4) is measured by replay: the same `build_context` path with a
stand-in Turn 1 that requests what the cell packed plus every gold caller
in V_cand. A gold caller that then survives packing was lost to Turn 1
not requesting it; one that does not was lost to the budget. Turn 2
itself has no knapsack (`pack_symbol_context_requested` admits every
requested candidate); the budget acts in `_enforce_render_budget` and the
token ceiling.

The manifest rows carry the 4-axis feature annotation (`|axes=...`); the
report lists it for every gold candidate next to the rate at which Turn 1
picked gold vs non-gold candidates, caller vs downstream rows.

Counterfactual: V_cand under `direction="both"` (Design C: the transitive
caller walk, interleaved with downstream candidates under the 8,000-token
budget, and with no budget).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace as NS

from harness.experiments.layer4 import context as X
from harness.experiments.layer4 import runner as R
from harness.experiments.layer4 import tasks as T

ARM = "prism_full"
COMPARE = ("normal_oracle", "prism_ask", "arm1_hybrid_rag", "repomap_pagerank", "arm0_no_retrieval")


class Turn1:
    """A stand-in client: Turn 1 requests `symbols`; no other call is made."""

    def __init__(self, symbols):
        self.symbols = list(symbols)

    def complete(self, *a, **k):
        return NS(content=json.dumps({"requested_symbols": self.symbols}), completion_tokens=10, model="replay",
                  response_model="replay", system_fingerprint=None, prompt_tokens=0, latency_seconds=0.0, cost_usd=0.0)


def emitted(reply: str, known: set[str]) -> set[str]:
    parsed = T.parse_forward(reply)
    names = [str(n) for n in (parsed or {}).get("affected_symbols") or [] if isinstance(n, (str, int))]
    return {q for q in (T.resolve_name(n, known) for n in names[:T.MAX_ANSWER_SYMBOLS]) if q}


def replay(corpus, task, request, cfg) -> set[str]:
    """`selected_symbols` of prism_full's context when Turn 1 requests `request`."""
    from benchmarks.final_sweep.runner import CellRecord, _CallLog
    rec = CellRecord(repo=task["corpus"], task_id=task["task_id"], engine_id=ARM, seed=0, budget=cfg.budget,
                     token_ceiling=cfg.token_ceiling, model_requested="replay", temperature=0.0)
    return set(X.build_context(corpus, ARM, task, 0, cfg, Turn1(request), _CallLog(), rec)["selected_symbols"])


def diagnose_task(corpus, task, cells: list[dict], all_cells: list[dict]) -> dict:
    from benchmarks.final_sweep.context_ops import annotate_manifest_axes
    from benchmarks.final_sweep.runner import SweepConfig
    from prism.packer.blast_radius import compute_upstream_callers
    from prism.packer.candidate_index import _upstream_walk, build_candidate_manifest
    from prism.packer.submodular_knapsack import DEFAULT_UPSTREAM_MAX_HOPS, UPSTREAM_FRONTIER_CAP

    b = corpus.state.builder
    seed = task["arm_view"]["seed_symbol"]
    gold = list(task["gold"]["affected_symbols"])
    depth = task["gold"].get("max_depth")
    cfg = SweepConfig()  # Stage 2 defaults: budget 8000, token ceiling 8800

    # 1-2: index and static graph
    in_index = [g for g in gold if g in b.symbol_table]
    walk = _upstream_walk(b, seed, max_hops=depth or 99)
    walk_any = _upstream_walk(b, seed, max_hops=99, include_tests=True)
    in_graph = [g for g in gold if g in walk]

    # 3: V_cand as prism_full builds it, and why a gold caller is not in it
    blast = task["arm_view"]["task_type"] == "blast"  # as benchmarks.final_sweep.runner._two_pass_context routes it
    with corpus.lock:
        manifest, universe = corpus.state.prism.build_candidate_manifest(
            seed, **({"direction": "both", "budget_tokens": cfg.budget} if blast else {}))
    axes = {}
    for line in annotate_manifest_axes(manifest, corpus.state.feature_masks, "all").splitlines():
        if "|" in line:
            q, rest = line.split("|", 1)
            axes[q] = {"role": rest.split("|", 1)[0], "axes": next((f for f in rest.split("|") if f.startswith("axes=")), "")}
    upstream = compute_upstream_callers(b, seed)
    ranked = [c.symbol for c in sorted(upstream.values(), key=lambda c: -c.weight)]
    reasons = {}
    for g in gold:
        if g in universe:
            continue
        if g not in b.symbol_table:
            reasons[g] = "not in the index"
        elif g not in walk_any:
            reasons[g] = "no static caller path to the focal (call edge missing)"
        elif g not in walk:
            reasons[g] = f"static caller path longer than max_depth {depth} (hop {walk_any[g]})"
        elif walk[g] > 1:
            reasons[g] = f"transitive caller (hop {walk[g]}): the downstream-mode manifest admits direct callers only"
        elif g in upstream and ranked.index(g) >= UPSTREAM_FRONTIER_CAP:
            reasons[g] = (f"direct caller ranked {ranked.index(g) + 1} of {len(ranked)} by W_upstream: past "
                          f"UPSTREAM_FRONTIER_CAP={UPSTREAM_FRONTIER_CAP}")
        elif g in upstream and upstream[g].dist_w_upstream > DEFAULT_UPSTREAM_MAX_HOPS:
            reasons[g] = (f"direct caller with dist_w_upstream {upstream[g].dist_w_upstream:.2f} > "
                          f"DEFAULT_UPSTREAM_MAX_HOPS={DEFAULT_UPSTREAM_MAX_HOPS}")
        else:
            reasons[g] = "direct caller in the walk but not among compute_upstream_callers"
    in_cand = [g for g in gold if g in universe]

    # counterfactual: Design C manifests
    both = {}
    for label, budget in (("both_budget_8000", cfg.budget), ("both_unbudgeted", None)):
        with corpus.lock:
            _m, u = build_candidate_manifest(b, seed, direction="both", budget_tokens=budget)
        both[label] = {"n_candidates": len(u), "gold_in": sorted(set(gold) & u)}

    # 4-5: per seed, from the cells
    direct = set(X_direct_callees(b, seed)) & universe
    per_seed, turn1_rates = [], defaultdict(Counter)
    for c in sorted(cells, key=lambda c: c["seed"]):
        packed = set(c["selected_symbols"])
        out = emitted(c["reply"], corpus.known)
        g_packed = set(gold) & packed
        g_out = set(gold) & out
        # Turn 1's picks (proxy): packed candidates other than the anchor and its auto-included direct callees
        picked = (packed & universe) - {seed} - direct
        for q in universe - {seed} - direct:
            kind = "caller" if axes.get(q, {}).get("role") == "caller" else "downstream"
            turn1_rates[(kind, q in gold)]["offered"] += 1
            turn1_rates[(kind, q in gold)]["picked"] += q in picked
        # replay: would Turn 2's budget have kept the gold candidates next to what this cell packed?
        missing = [g for g in in_cand if g not in packed]
        survived = replay(corpus, task, sorted((packed & universe) - {seed}) + missing, cfg) if missing else set()
        per_seed.append({
            "seed": c["seed"], "packed_n": len(packed), "context_tokens": c["tokens"]["context"],
            "n_ceiling_dropped": c["detail"].get("n_ceiling_dropped"),
            "gold_packed": sorted(g_packed), "gold_emitted": sorted(g_out),
            "emitted_from_context": sorted(g_out & packed), "emitted_without_context": sorted(g_out - packed),
            "packed_not_emitted": sorted(g_packed - out),
            "lost_turn1": sorted(g for g in missing if g in survived),
            "lost_budget": sorted(g for g in missing if g not in survived),
            "n_emitted": len(out), "recall": c["score"]["recall"], "precision": c["score"]["precision"],
        })

    # whole-manifest budget pressure: everything in V_cand requested
    everything = replay(corpus, task, sorted(universe - {seed}), cfg)

    compare = {}
    for arm in COMPARE:
        cs = [c for c in all_cells if c["task_id"] == task["task_id"] and c["arm"] == arm and c["status"] == "ok"]
        if cs:
            compare[arm] = {
                "packed": round(sum(len(set(gold) & set(c["selected_symbols"])) for c in cs) / len(cs), 2),
                "emitted": round(sum(len(set(gold) & emitted(c["reply"], corpus.known)) for c in cs) / len(cs), 2),
                "emitted_from_context": round(sum(len(set(gold) & emitted(c["reply"], corpus.known) & set(c["selected_symbols"]))
                                                  for c in cs) / len(cs), 2)}
    return {
        "task_id": task["task_id"], "seed_symbol": seed, "max_depth": depth, "gold": gold, "gold_n": len(gold),
        "gold_hops": {g: walk_any.get(g) for g in gold},
        "in_index": in_index, "in_static_graph": in_graph, "in_cand": in_cand, "not_in_cand": reasons,
        "cand_n": len(universe), "cand_callers": sorted(q for q in universe if axes.get(q, {}).get("role") == "caller"),
        "direct_callers_n": len(upstream), "upstream_rank": {g: (ranked.index(g) + 1 if g in ranked else None) for g in gold},
        "gold_axes": {g: axes.get(g) for g in in_cand},
        "turn1_pick_rate": {f"{k}|{'gold' if g else 'other'}": dict(v) for (k, g), v in sorted(turn1_rates.items())},
        "budget_all_requested": {"packed_n": len(everything), "gold_kept": sorted(set(in_cand) & everything),
                                 "gold_dropped": sorted(set(in_cand) - everything)},
        "design_c": both, "seeds": per_seed, "compare": compare,
    }


def X_direct_callees(builder, seed):
    from prism.packer.candidate_index import _outgoing_call_names
    return _outgoing_call_names(builder, seed)


def mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def render(rows: list[dict]) -> str:
    L = ["# prism_full forward recall funnel (Layer 4 tournament)", "",
         "Per task: gold callers (traced, within max_depth) surviving each stage; stages 4-5 are means over the 10 "
         "seeds. `lost: Turn 1` / `lost: budget` split the V_cand -> packed loss by replay (module docstring).", "",
         "| task | gold | in index | in static graph | in V_cand | packed | emitted | from context | without context | "
         "packed, not emitted | lost: Turn 1 | lost: budget | recall |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = Counter()
    for r in rows:
        s = r["seeds"]
        vals = {"gold": r["gold_n"], "idx": len(r["in_index"]), "graph": len(r["in_static_graph"]), "cand": len(r["in_cand"]),
                "packed": mean(len(x["gold_packed"]) for x in s), "out": mean(len(x["gold_emitted"]) for x in s),
                "ctx": mean(len(x["emitted_from_context"]) for x in s), "noctx": mean(len(x["emitted_without_context"]) for x in s),
                "pne": mean(len(x["packed_not_emitted"]) for x in s), "t1": mean(len(x["lost_turn1"]) for x in s),
                "bud": mean(len(x["lost_budget"]) for x in s), "rec": mean(x["recall"] for x in s)}
        for k, v in vals.items():
            tot[k] += v
        L.append(f"| {r['task_id']} | " + " | ".join(str(vals[k]) for k in
                                                       ("gold", "idx", "graph", "cand", "packed", "out", "ctx", "noctx", "pne", "t1", "bud", "rec")) + " |")
    n = len(rows)
    L.append("| **total** | " + " | ".join(str(round(tot[k], 2)) for k in ("gold", "idx", "graph", "cand", "packed", "out", "ctx",
                                                                              "noctx", "pne", "t1", "bud")) + f" | {round(tot['rec'] / n, 3)} |")
    g = tot["gold"]
    L += ["", "Funnel over all 8 tasks (share of gold): "
          f"gold {g} -> in static graph {tot['graph'] / g:.1%} -> in V_cand {tot['cand'] / g:.1%} -> "
          f"packed {tot['packed'] / g:.1%} -> emitted {tot['out'] / g:.1%} "
          f"(of which from context {tot['ctx'] / g:.1%}, without it {tot['noctx'] / g:.1%}).", "",
          "## Why gold callers are not in V_cand", "", "| task | gold caller | hop | reason |", "|---|---|---|---|"]
    why = Counter()
    for r in rows:
        for gname, reason in sorted(r["not_in_cand"].items()):
            L.append(f"| {r['task_id']} | `{gname}` | {r['gold_hops'].get(gname)} | {reason} |")
            why[reason.split(" (")[0].split(":")[0]] += 1
    L += ["", "Reasons, counted: " + "; ".join(f"{k}: {v}" for k, v in why.most_common()), "",
          "## Turn 1 selection (proxy: packed candidates other than the anchor and its auto-included direct callees)",
          "", "| task | caller rows: gold picked / offered | caller rows: other | downstream rows: gold | downstream rows: other |",
          "|---|---|---|---|---|"]
    for r in rows:
        t = r["turn1_pick_rate"]

        def rate(k):
            v = t.get(k)
            return f"{v['picked']}/{v['offered']}" if v else "-"
        L.append(f"| {r['task_id']} | {rate('caller|gold')} | {rate('caller|other')} | {rate('downstream|gold')} | "
                 f"{rate('downstream|other')} |")
    L += ["", "Axis annotations Turn 1 saw on the gold candidates:", ""]
    for r in rows:
        for gname, a in sorted(r["gold_axes"].items()):
            L.append(f"- {r['task_id']} `{gname}`: role `{(a or {}).get('role')}`, `{(a or {}).get('axes') or 'no axes field'}`")
    L += ["", "## Budget", "", "With Turn 1 requesting **every** V_cand candidate (maximum budget pressure):", "",
          "| task | V_cand | packed | gold in V_cand kept | gold dropped |", "|---|---|---|---|---|"]
    for r in rows:
        bud = r["budget_all_requested"]
        L.append(f"| {r['task_id']} | {r['cand_n']} | {bud['packed_n']} | {len(bud['gold_kept'])}/{len(r['in_cand'])} | "
                 f"{', '.join('`' + x + '`' for x in bud['gold_dropped']) or '-'} |")
    L += ["", "## Counterfactual: Design C manifest (`direction=\"both\"`)", "",
          "| task | gold | in V_cand now | both, 8000-token budget | both, no budget |", "|---|---|---|---|---|"]
    for r in rows:
        d = r["design_c"]
        L.append(f"| {r['task_id']} | {r['gold_n']} | {len(r['in_cand'])} | {len(d['both_budget_8000']['gold_in'])} "
                 f"({d['both_budget_8000']['n_candidates']} cand.) | {len(d['both_unbudgeted']['gold_in'])} "
                 f"({d['both_unbudgeted']['n_candidates']} cand.) |")
    L += ["", "## Other arms (mean gold per cell: packed / emitted / emitted from context)", "",
          "| task | " + " | ".join(COMPARE) + " |", "|---|" + "---|" * len(COMPARE)]
    for r in rows:
        L.append(f"| {r['task_id']} | " + " | ".join(
            (f"{c['packed']} / {c['emitted']} / {c['emitted_from_context']}" if (c := r["compare"].get(a)) else "-")
            for a in COMPARE) + " |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cells", type=Path, default=Path("reports/layer4/tournament/cells.jsonl"))
    p.add_argument("--out", type=Path, default=Path("reports/layer4/diagnostics"))
    p.add_argument("--tasks", default="all")
    args = p.parse_args(argv)
    tasks = {k: v for k, v in R.load_tasks(R.TASKS_DIR).items() if v["family"] == "forward_blast"
             and (args.tasks == "all" or k in args.tasks.split(","))}
    cells = list(R.load_cells(args.cells).values())
    rows = []
    for corpus_name in sorted({t["corpus"] for t in tasks.values()}):
        corpus = X.Corpus.load(corpus_name)
        for tid in sorted(k for k, t in tasks.items() if t["corpus"] == corpus_name):
            mine = [c for c in cells if c["task_id"] == tid and c["arm"] == ARM and c["status"] == "ok"]
            rows.append(diagnose_task(corpus, tasks[tid], mine, cells))
            print(f"[diagnose] {tid}: gold {rows[-1]['gold_n']}, in V_cand {len(rows[-1]['in_cand'])}", flush=True)
        del corpus
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "recall_funnel.json").write_text(json.dumps(rows, indent=1, default=str) + "\n")
    md = render(rows)
    (args.out / "RECALL_FUNNEL.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
