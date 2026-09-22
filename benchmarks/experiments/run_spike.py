"""Phase B spike: Approach B ("Frontier Index + Spine Hydration") and
Approach A ("Two-Pass Hydration Protocol") vs. the existing single-zone
baseline, on the 3 target Django tasks (django_t02_005, django_t02_009,
django_t02_017) at budgets 2000/4000/8000, seeds 42/43, against a real
OpenAI-compatible endpoint.

Isolated under benchmarks/experiments/ by design - reuses real scoring
machinery (score_tsr_response, cpi_strict, fpr, _ground_truth_universe)
so numbers are directly comparable to prior resweeps, but touches no
production selection/scoring code and writes results only under
benchmarks/experiments/results/, never reports/pilot/ or
reports/pilot-resweep-*/ - the protected suite and checkpoint.json are
untouched by construction, not by discipline alone.

Approach C ("Forked Spine Pruning in Knapsack") was tried and dropped -
see benchmarks/experiments/knapsack_spine_variant.py's own docstring and
commit 1f66771 for the full negative-result diagnosis (fpr_gt barely
moved, cpi_strict regressed). That module is kept as a documented
artifact but is no longer in the default APPROACHES rotation. Approach
A's own two-pass driver lives in benchmarks/experiments/hydration_loop.py
- like Approach C, it reuses production's own already-tested helpers by
import and never modifies submodular_knapsack.py/build.py.

Usage:
    python -m benchmarks.experiments.run_spike --dry-run   # plumbing only, no LLM calls
    LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen2.5-coder:14b-instruct-q8_0 \\
        LLM_API_KEY_ENV=OLLAMA_API_KEY python -m benchmarks.experiments.run_spike
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.experiments.hydration_loop import build_context_package_requested, run_hydration_cell, _build_candidate_index
from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.metrics.cpi import cpi_strict
from benchmarks.metrics.fpr import fpr
from benchmarks.runner import DEBUG_TASK_RESPONSE_CONTRACT, SYSTEM_PROMPT, _ground_truth_universe, score_tsr_response
from benchmarks.tsr.client import OpenAICompatibleClient, run_tsr_prompt
from prism.surface.renderer import RenderOptions, render

APPROACHES = ("baseline", "B_two_zone", "A_hydration")

TARGET_TASKS = (
    "django_t02_005_model_save_signals",
    "django_t02_009_queryset_filter_clone",
    "django_t02_017_redirect_url_safety_check",
)
BUDGETS = (2000, 4000, 8000)
SEEDS = (42, 43)
TASKS_DIR = Path(__file__).resolve().parent.parent / "ground_truth" / "tasks" / "django"
RESULTS_PATH = Path(__file__).resolve().parent / "results" / "spike_results.json"

#: A selected node whose combined graph degree exceeds this is skipped
#: as a frontier-expansion source (see `_frontier_index_for`'s own
#: docstring - found empirically necessary, not a guessed constant).
_HUB_DEGREE_CAP = 25
#: Hard cap on total frontier entries regardless of hub filtering -
#: guarantees "lightweight index" stays true even if several selected
#: nodes each contribute a moderate, non-hub-sized neighbor set.
_MAX_FRONTIER_ENTRIES = 40


def _frontier_index_for(builder, pkg) -> list[dict[str, str]]:
    """Cheap, spike-only frontier discovery - deliberately NOT threaded
    through production `SubmodularPackResult`/`ContextPackage` (see
    `RenderOptions.two_zone`'s own docstring in prism.surface.renderer
    for why). One-hop `graph.successors`/`predecessors` of every already-
    selected node, minus the selected set itself: a real, if approximate,
    stand-in for "what the packer explored but didn't admit" - the exact
    candidate frontier `select_submodular_context` itself discards on
    return is not exposed anywhere today, and widening that production
    return type for a spike that may not ship is exactly the risk this
    function's own placement (here, not in submodular_knapsack.py) is
    avoiding.
    """
    selected = {n.id for n in pkg.nodes}
    frontier_ids: set[str] = set()
    for node_id in selected:
        if node_id not in builder.graph:
            continue
        # Hub filtering - found necessary empirically, not assumed: an
        # unfiltered version of this function returned 365 frontier
        # entries for django_t02_005 at budget=2000 (14 spine nodes),
        # inflating the rendered package 3.3x (18.9KB -> 63.1KB) and
        # directly defeating the "ultra-lightweight index" premise this
        # whole approach is testing. The real cause: a selected node like
        # `django.db.models.base.Model` has hundreds of real
        # predecessors/successors in the actual Django graph - exactly
        # the hub explosion the Subgraph Processor design conversation
        # already flagged as needing down-weighting, now confirmed with
        # a real number rather than a hypothesis. A node whose own
        # combined in/out-degree exceeds `_HUB_DEGREE_CAP` is skipped as
        # an *expansion source* (its own neighbors aren't added to the
        # frontier) - it stays in the spine if selected, this only stops
        # it from flooding the frontier with everything it touches.
        if builder.graph.in_degree(node_id) + builder.graph.out_degree(node_id) > _HUB_DEGREE_CAP:
            continue
        frontier_ids |= set(builder.graph.successors(node_id))
        frontier_ids |= set(builder.graph.predecessors(node_id))
    frontier_ids -= selected
    entries = []
    for fid in sorted(frontier_ids)[:_MAX_FRONTIER_ENTRIES]:
        info = builder.symbol_table.get(fid)
        if info is None:  # external/unresolved - no real symbol-table entry to describe
            continue
        entries.append({"id": fid, "role": "frontier", "kind": info.kind})
    return entries


def _package_for(engine: PrismEngine, task, budget: int, approach: str):
    """Single-package approaches only (baseline, B_two_zone) -
    A_hydration needs two LLM turns to even decide what to render, so it
    is never routed through this helper; see `run_cell`/the dry-run loop
    below for how it's handled instead."""
    return engine.retrieve(task.seed_symbol, budget, task_type=task.task_type)


def run_cell(client: OpenAICompatibleClient, engine: PrismEngine, task, budget: int, seed: int, approach: str) -> dict:
    assert approach in APPROACHES, approach
    if approach == "A_hydration":
        return run_hydration_cell(client, engine, task, budget, seed)
    pkg = _package_for(engine, task, budget, approach)
    candidate_symbols = {n.id for n in pkg.nodes}

    if approach == "B_two_zone":
        frontier = _frontier_index_for(engine._builder, pkg)
        options = RenderOptions(include_timestamp=False, include_run_id=False, two_zone=True, frontier_index=frontier)
    else:
        frontier = []
        options = RenderOptions(include_timestamp=False, include_run_id=False)
    rendered_xml = render(pkg, options)

    task_prompt = task.prompt
    if task.task_type == "debug":
        task_prompt = task.prompt + DEBUG_TASK_RESPONSE_CONTRACT

    engine_label = f"prism_v11_{approach}"
    t0 = time.monotonic()
    results = run_tsr_prompt(
        client, SYSTEM_PROMPT, rendered_xml, task_prompt,
        seeds=(seed,), task_id=task.task_id, engine=engine_label,
    )
    llm_latency_s = time.monotonic() - t0
    call = results[0].call

    score = score_tsr_response(task, call.content, candidate_symbols)
    ground_truth = _ground_truth_universe(task)
    return {
        "task_id": task.task_id,
        "approach": "baseline_single_zone" if approach == "baseline" else approach,
        "budget": budget,
        "seed": seed,
        "tsr": score,
        "cpi_strict": cpi_strict(candidate_symbols, task.adjudicated.pipeline_symbols),
        "fpr_gt": fpr(candidate_symbols, ground_truth),
        "prompt_tokens": call.prompt_tokens,
        "completion_tokens": call.completion_tokens,
        "llm_latency_s": round(llm_latency_s, 3),
        "frontier_size": len(frontier),
        "spine_size": len(candidate_symbols),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Verify retrieval/rendering plumbing only - no LLM calls")
    parser.add_argument(
        "--tasks", default=None,
        help=f"Comma-separated subset of task IDs to run (default: all {len(TARGET_TASKS)} target tasks). "
        "For a small, cost-controlled first real-API sanity check before the full grid.",
    )
    parser.add_argument("--budgets", default=None, help=f"Comma-separated subset of budgets (default: {BUDGETS})")
    parser.add_argument("--seeds", default=None, help=f"Comma-separated subset of seeds (default: {SEEDS})")
    parser.add_argument(
        "--approaches", default=None,
        help=f"Comma-separated subset of approaches to run (default: all {APPROACHES}).",
    )
    args = parser.parse_args()

    target_tasks = tuple(args.tasks.split(",")) if args.tasks else TARGET_TASKS
    budgets = tuple(int(b) for b in args.budgets.split(",")) if args.budgets else BUDGETS
    seeds = tuple(int(s) for s in args.seeds.split(",")) if args.seeds else SEEDS
    approaches = tuple(args.approaches.split(",")) if args.approaches else APPROACHES
    unknown_approaches = [a for a in approaches if a not in APPROACHES]
    if unknown_approaches:
        print(f"error: unknown approaches {unknown_approaches} - valid: {APPROACHES}", file=sys.stderr)
        return 1

    repo_path = str(resolve("django"))
    load_result = load_tasks_from_dir(TASKS_DIR)
    tasks_by_id = {t.task_id: t for t in load_result.accepted}
    missing = [t for t in target_tasks if t not in tasks_by_id]
    if missing:
        print(f"error: target tasks not found in {TASKS_DIR}: {missing}", file=sys.stderr)
        return 1

    engine = PrismEngine()
    print(f"[spike] indexing {repo_path} ...", file=sys.stderr)
    engine.index(repo_path)

    if args.dry_run:
        for task_id in target_tasks:
            task = tasks_by_id[task_id]
            for budget in budgets:
                for approach in approaches:
                    if approach == "A_hydration":
                        manifest, candidate_universe = _build_candidate_index(engine._builder, task.seed_symbol)
                        # No real Turn 1 call in a dry run - validate Turn 2's
                        # own plumbing with the seed-only "request" a
                        # completely-failed/empty Turn 1 parse would produce,
                        # the one input build_context_package_requested must
                        # handle gracefully no matter what Turn 1 returns.
                        pkg, skipped = build_context_package_requested(
                            engine._builder, task.seed_symbol, engine._repo_root, budget, [], candidate_universe,
                            contracts=engine._contracts, task_type=task.task_type,
                        )
                        xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
                        print(
                            f"[dry-run] {task_id} budget={budget} approach={approach:<15} "
                            f"candidate_universe={len(candidate_universe)} manifest_bytes={len(manifest)} "
                            f"spine_nodes={len(pkg.nodes)} xml_bytes={len(xml)}"
                        )
                        continue
                    pkg = _package_for(engine, task, budget, approach)
                    frontier = _frontier_index_for(engine._builder, pkg) if approach == "B_two_zone" else []
                    if approach == "B_two_zone":
                        options = RenderOptions(include_timestamp=False, include_run_id=False, two_zone=True, frontier_index=frontier)
                    else:
                        options = RenderOptions(include_timestamp=False, include_run_id=False)
                    xml = render(pkg, options)
                    print(
                        f"[dry-run] {task_id} budget={budget} approach={approach:<15} "
                        f"spine_nodes={len(pkg.nodes)} frontier_nodes={len(frontier)} xml_bytes={len(xml)}"
                    )
        print("\n[dry-run] plumbing OK - no LLM calls made.")
        return 0

    client = OpenAICompatibleClient()
    rows = []
    for task_id in target_tasks:
        task = tasks_by_id[task_id]
        for budget in budgets:
            for seed in seeds:
                for approach in approaches:
                    row = run_cell(client, engine, task, budget, seed, approach)
                    print(
                        f"[spike] {row['task_id']} approach={row['approach']} budget={row['budget']} seed={row['seed']} "
                        f"tsr={row['tsr']} cpi_strict={row['cpi_strict']} fpr_gt={row['fpr_gt']:.3f} "
                        f"prompt_tokens={row['prompt_tokens']} latency_s={row['llm_latency_s']:.2f}",
                        file=sys.stderr,
                    )
                    rows.append(row)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(rows, indent=2))
    print(f"\nWrote {len(rows)} rows to {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
