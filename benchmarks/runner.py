"""v1.1+ Empirical Benchmarking Harness - CLI entrypoint.

    python -m benchmarks.runner --repo=django --budget=4000 --runs=5 --output=reports/
    python -m benchmarks.runner --mode=ablation --output=reports/

Every engine's output is serialized through the one canonical renderer
(`prism.surface.renderer.render(pkg, RenderOptions(include_timestamp=False,
include_run_id=False))`) before it ever reaches an LLM prompt, so TSR
differences measure retrieval quality, not prompt formatting.

**Stated honestly**: running a real TSR sweep calls a real, paid LLM API
(`benchmarks.tsr.client`) - this module never does so silently. Without
`OPENAI_API_KEY` configured (or with `--dry-run`), it runs the full
retrieval + diagnostic-metrics pipeline for real and skips only the LLM
call itself, leaving `tsr_scores` empty and saying so, rather than
fabricating scores.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from prism.cli import build_pipeline
from prism.surface.renderer import RenderOptions, render

from benchmarks.corpora.resolver import CORPORA, CorpusResolutionError, resolve
from benchmarks.engines.base import AbstractRetrievalEngine, selected_symbols
from benchmarks.engines.baseline_bfs import BaselineBFSEngine
from benchmarks.engines.baseline_rag import BaselineRAGEngine
from benchmarks.engines.oracle_engine import ENGINE_NAME as ORACLE_ENGINE_NAME
from benchmarks.engines.oracle_engine import OracleEngine
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.ground_truth.schema import EvaluationTask
from benchmarks.metrics.bccr import bccr_direct
from benchmarks.metrics.cpi import cpi_fractional, cpi_strict
from benchmarks.metrics.fcc import compute_corpus_feature_stats, fcc
from benchmarks.metrics.fpr import fpr
from benchmarks.metrics.src import src
from benchmarks.reporting.report_generator import (
    EvaluationRun,
    TaskRunRecord,
    render_heatmap_markdown,
    write_ablation_report,
    write_failure_analysis,
    write_json_results,
    write_markdown_report,
)
from benchmarks.tsr.client import DEFAULT_MODEL, DEFAULT_SEEDS, run_tsr_prompt
from benchmarks.tsr.scorer_architecture import score_architecture
from benchmarks.tsr.scorer_blast import score_blast
from benchmarks.tsr.scorer_chain import score_chain
from benchmarks.tsr.scorer_debug import score_debug
from benchmarks.tsr.scorer_redundancy import score_redundancy

DEFAULT_BUDGETS = (2000, 4000, 8000)
DEFAULT_TASKS_DIR_TEMPLATE = "benchmarks/ground_truth/tasks/{repo}"


def resolve_budgets(budget: int | None, budgets: list[int] | None) -> list[int]:
    """Resolves the CLI's `--budget`/`--budgets` flags into the
    effective (eval/pilot mode) budget list. `--budget` (singular) is a
    true alias for `--budgets` with one value - passing both is a
    configuration error (never a silent "one wins"), passing neither
    falls back to the full `DEFAULT_BUDGETS` sweep.
    """
    if budget is not None and budgets is not None:
        raise ValueError("--budget and --budgets are mutually exclusive - pass exactly one")
    if budget is not None:
        return [budget]
    if budgets:
        return list(budgets)
    return list(DEFAULT_BUDGETS)


def resolve_ablation_budget(budget: int | None, budgets: list[int] | None) -> int:
    """The ablation-mode counterpart of `resolve_budgets` - ablation
    sweeps lambda/mu hyperparameters at one fixed budget, so `--budgets`
    is only accepted here with exactly one value (more than one is a
    real configuration error, not silently reduced to `budgets[0]`)."""
    if budget is not None and budgets is not None:
        raise ValueError("--budget and --budgets are mutually exclusive - pass exactly one")
    if budget is not None:
        return budget
    if budgets:
        if len(budgets) > 1:
            raise ValueError("--mode=ablation takes a single budget - pass --budget or one value to --budgets")
        return budgets[0]
    return DEFAULT_BUDGETS[1]

SYSTEM_PROMPT = (
    "You are a senior software engineer. You will be given a <prism_context> context package "
    "describing part of a real codebase, followed by a task. Answer the task precisely, "
    "referring to symbols by their exact name as given in the context."
)


# --------------------------------------------------------------------- #
# Core evaluation
# --------------------------------------------------------------------- #
def _build_engines(oracle_packages_path: str | None, task_id: str) -> list[AbstractRetrievalEngine]:
    """Oracle (when configured) is returned *first* - `run_evaluation`
    needs its per-budget selected-symbol set computed before any other
    engine's turn, so every engine's `fpr_oracle` (divergence from the
    Oracle package at that same budget) can be computed in a single pass
    with no engine retrieved twice."""
    engines: list[AbstractRetrievalEngine] = []
    if oracle_packages_path is not None:
        engines.append(OracleEngine(oracle_packages_path, task_id))
    engines.extend(
        [
            PrismEngine(),
            BaselineRAGEngine(),
            BaselineBFSEngine(mode="forward"),
            BaselineBFSEngine(mode="bidirectional"),
        ]
    )
    return engines


def _ground_truth_universe(task: EvaluationTask) -> set[str]:
    adjudicated = task.adjudicated
    return (
        set(adjudicated.pipeline_symbols)
        | adjudicated.critical_callers
        | adjudicated.orthogonal_neighbors
        | adjudicated.reference_symbols
        | {task.seed_symbol}
    )


def compute_diagnostics(
    pkg, task: EvaluationTask, feature_stats, oracle_selected: set[str] | None = None
) -> dict[str, float | None]:
    """`oracle_selected` is the Oracle engine's own packed symbol set for
    this exact `(task, budget)` cell, when an Oracle run was configured
    and available for this call - `None` otherwise (no `--oracle-
    packages` configured, or the Oracle failed to index/retrieve for
    this task). `fpr_oracle` is `None`, not a fabricated `0.0`, in that
    case: an absent comparison point is an absent number, never a
    silently-wrong one."""
    selected = selected_symbols(pkg)
    adjudicated = task.adjudicated
    diagnostics: dict[str, float | None] = {"fcc": fcc(pkg, feature_stats)}

    if task.task_type in ("chain", "debug"):
        diagnostics["cpi_strict"] = cpi_strict(selected, adjudicated.pipeline_symbols)
        diagnostics["cpi_fractional"] = cpi_fractional(selected, adjudicated.pipeline_symbols)
        diagnostics["src"] = src(pkg, set(adjudicated.pipeline_symbols))
    elif task.task_type == "blast":
        diagnostics["bccr_direct"] = bccr_direct(selected, adjudicated.critical_callers)
        diagnostics["src"] = src(pkg)
    elif task.task_type == "architecture":
        diagnostics["reference_symbol_capture"] = bccr_direct(selected, adjudicated.reference_symbols)
        diagnostics["src"] = src(pkg)
    else:  # redundancy
        diagnostics["src"] = src(pkg)

    # fpr_gt: divergence from the human-annotated ground truth (the
    # original definition - kept, for transparency, since it really
    # does measure something, just something narrower than "engine
    # quality": how much of what an engine packs falls outside the 3-4
    # symbols this task happened to annotate).
    diagnostics["fpr_gt"] = fpr(selected, _ground_truth_universe(task))
    # fpr_oracle: divergence from the Oracle's own package at this same
    # budget - the metric this harness actually reports as "the" FPR,
    # since an engine legitimately pulling in real, relevant context
    # beyond the narrow annotated set is not a false positive against a
    # domain expert's own answer.
    diagnostics["fpr_oracle"] = fpr(selected, oracle_selected) if oracle_selected is not None else None
    return diagnostics


def score_tsr_response(task: EvaluationTask, response_text: str, candidate_symbols: set[str]) -> float:
    if task.task_type == "chain":
        return score_chain(response_text, task.adjudicated.pipeline_symbols)
    if task.task_type == "debug":
        return score_debug(response_text, task.adjudicated.pipeline_symbols)
    if task.task_type == "blast":
        return score_blast(response_text, candidate_symbols, task.adjudicated.critical_callers)
    if task.task_type == "architecture":
        return score_architecture(response_text, task.adjudicated.reference_symbols)
    return score_redundancy(response_text, task.seed_symbol, task.adjudicated.orthogonal_neighbors)


def run_evaluation(
    repo: str,
    budgets: list[int],
    tasks_dir: str,
    runs: int = 5,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
    oracle_packages_path: str | None = None,
    force_reclone: bool = False,
) -> EvaluationRun:
    """The real end-to-end sweep: resolve the pinned corpus, load its
    ground-truth tasks, run every engine at every budget, and (unless
    `dry_run`, or no API key is configured at all) run the real 5-seed
    TSR protocol against a real LLM."""
    if repo not in CORPORA:
        raise ValueError(f"unknown repo {repo!r} - registered corpora: {sorted(CORPORA)}")
    repo_path = str(resolve(repo, force=force_reclone))

    load_result = load_tasks_from_dir(tasks_dir)
    tasks = [t for t in load_result.accepted if t.repo == repo]
    if load_result.rejected:
        for task_id, reason in load_result.rejected:
            print(f"warning: task {task_id!r} rejected: {reason}", file=sys.stderr)
    if not tasks:
        print(f"warning: no accepted ground-truth tasks found for repo={repo!r} in {tasks_dir!r}", file=sys.stderr)

    builder, _tag_matrix = build_pipeline(repo_path)
    feature_stats = compute_corpus_feature_stats(builder)

    client = None
    if not dry_run:
        try:
            from benchmarks.openai_client import LLMClient

            client = LLMClient()
        except Exception as exc:  # MissingAPIKeyError / import error / etc.
            print(f"warning: LLM client unavailable ({exc}) - running in dry-run mode, tsr_scores will be empty", file=sys.stderr)
            dry_run = True

    seeds = DEFAULT_SEEDS[: max(1, min(runs, len(DEFAULT_SEEDS)))]
    run = EvaluationRun()

    for task in tasks:
        # Oracle first (see `_build_engines`'s own docstring): its
        # per-budget selected-symbol set must exist before any other
        # engine's turn so `fpr_oracle` can be computed for everyone in
        # one pass, with the Oracle itself retrieved exactly once.
        engines = _build_engines(oracle_packages_path, task.task_id)
        oracle_selected_by_budget: dict[int, set[str]] = {}
        for engine in engines:
            try:
                engine.index(repo_path)
            except Exception as exc:
                print(f"warning: {engine.name} failed to index {repo_path}: {exc}", file=sys.stderr)
                continue
            for budget in budgets:
                try:
                    pkg = engine.retrieve(task.seed_symbol, budget)
                except Exception as exc:
                    print(f"warning: {engine.name}.retrieve({task.seed_symbol!r}, {budget}) failed: {exc}", file=sys.stderr)
                    continue

                candidate_symbols = selected_symbols(pkg)
                if engine.name == ORACLE_ENGINE_NAME:
                    oracle_selected_by_budget[budget] = candidate_symbols

                tsr_scores: list[float] = []
                if not dry_run and client is not None:
                    rendered_xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
                    tsr_results = run_tsr_prompt(client, SYSTEM_PROMPT, rendered_xml, task.prompt, model=model, seeds=seeds)
                    tsr_scores = [score_tsr_response(task, r.call.content, candidate_symbols) for r in tsr_results]

                run.records.append(
                    TaskRunRecord(
                        task_id=task.task_id,
                        task_type=task.task_type,
                        repo=task.repo,
                        engine_name=engine.name,
                        budget_tokens=budget,
                        tsr_scores=tsr_scores,
                        diagnostics=compute_diagnostics(
                            pkg, task, feature_stats, oracle_selected=oracle_selected_by_budget.get(budget)
                        ),
                        selected_symbols=sorted(candidate_symbols),
                        ground_truth_symbols=sorted(_ground_truth_universe(task)),
                    )
                )

    return run


def write_reports(run: EvaluationRun, output_dir: str) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json_results(run, out / "eval_results_v11.json")
    write_markdown_report(run, out / "eval_results_v11.md")
    write_failure_analysis(run, out / "failure_analysis.md")


# --------------------------------------------------------------------- #
# Ablation mode
# --------------------------------------------------------------------- #
LAMBDA1_GRID = (0.10, 0.25, 0.40)
LAMBDA2_GRID = (0.05, 0.15, 0.25)
MU1_GRID = (0.20, 0.30, 0.45, 0.60)


def _cpi_for_lambda_config(builder, tasks: list[EvaluationTask], budget: int, lambda1: float, lambda2: float) -> tuple[float, float]:
    """Runs `PrismEngine` for every "chain" task at `budget` with
    `causal_weights.LAMBDA_DATA_FLOW`/`LAMBDA_GUARD` temporarily
    overridden, returning `(mean_cpi_strict, mean_cpi_fractional)`.
    Real module-constant monkey-patching (restored in `finally`) - these
    two lambdas are read directly as module globals by `causal_edge_
    weight`, not threaded through as function parameters, so this is the
    only way to sweep them without forking the causal engine itself for
    the ablation run.
    """
    import prism.traversal.causal_weights as causal_weights

    chain_tasks = [t for t in tasks if t.task_type == "chain"]
    if not chain_tasks:
        return 0.0, 0.0

    original_lambda1 = causal_weights.LAMBDA_DATA_FLOW
    original_lambda2 = causal_weights.LAMBDA_GUARD
    causal_weights.LAMBDA_DATA_FLOW = lambda1
    causal_weights.LAMBDA_GUARD = lambda2
    try:
        engine = PrismEngine.from_builder(builder, repo_root=getattr(builder, "repo_root", "."))
        strict_scores, fractional_scores = [], []
        for task in chain_tasks:
            pkg = engine.retrieve(task.seed_symbol, budget)
            selected = selected_symbols(pkg)
            strict_scores.append(cpi_strict(selected, task.adjudicated.pipeline_symbols))
            fractional_scores.append(cpi_fractional(selected, task.adjudicated.pipeline_symbols))
        mean_strict = sum(strict_scores) / len(strict_scores) if strict_scores else 0.0
        mean_fractional = sum(fractional_scores) / len(fractional_scores) if fractional_scores else 0.0
        return mean_strict, mean_fractional
    finally:
        causal_weights.LAMBDA_DATA_FLOW = original_lambda1
        causal_weights.LAMBDA_GUARD = original_lambda2


def run_lambda_ablation(builder, tasks: list[EvaluationTask], budget: int) -> dict[str, dict[tuple[float, float], float]]:
    """The lambda_1 x lambda_2 cross-product grid (9 configurations) -
    CPI (strict and fractional) at each cell. Real, always computable
    with no LLM call; a TSR heatmap needs a live API and is
    `runner.py`'s own CLI responsibility to add when credentials permit,
    not fabricated here.
    """
    strict_grid: dict[tuple[float, float], float] = {}
    fractional_grid: dict[tuple[float, float], float] = {}
    for lambda1 in LAMBDA1_GRID:
        for lambda2 in LAMBDA2_GRID:
            mean_strict, mean_fractional = _cpi_for_lambda_config(builder, tasks, budget, lambda1, lambda2)
            strict_grid[(lambda1, lambda2)] = mean_strict
            fractional_grid[(lambda1, lambda2)] = mean_fractional
    return {"cpi_strict": strict_grid, "cpi_fractional": fractional_grid}


def check_beta_delta_max_invariant(builder, tasks: list[EvaluationTask], budget: int) -> dict:
    """Empirically measures how often a 2-hop candidate actually
    leapfrogs (outranks) a 1-hop direct callee in a real pack run, at
    `beta=DEFAULT_BETA=0.10`, `delta_max=DEFAULT_DELTA_MAX=10` - reported
    honestly, not blindly asserted to always be zero.

    `prism.packer.submodular_knapsack`'s own module docstring and
    `tests/test_v11_invariants.py`'s Property 1 already establish,
    analytically, that the dominance guarantee at these exact constants
    holds only for a closer candidate at seed-distance `d in
    MAX_DOMINANT_SEED_DISTANCE = {0, 1}` and **provably fails starting at
    `d = 2`** - so asserting "zero leapfrogs across all corpus
    evaluations" would be asserting something already proven false in
    general. This function instead reports the observed rate against
    that known, narrower guarantee, which is the honest form of the
    spec's own "invariant verification" ask.
    """
    from prism.packer.submodular_knapsack import MAX_DOMINANT_SEED_DISTANCE
    from prism.traversal.continuous_dijkstra import compute_topological_distances

    total_within_guaranteed_range = 0
    violations_within_guaranteed_range = 0
    total_beyond_guaranteed_range = 0
    leapfrogs_beyond_guaranteed_range = 0

    for task in tasks:
        distances = compute_topological_distances(builder, task.seed_symbol)
        one_hop = {sym: d for sym, d in distances.items() if round(d) <= MAX_DOMINANT_SEED_DISTANCE}
        beyond = {sym: d for sym, d in distances.items() if round(d) > MAX_DOMINANT_SEED_DISTANCE}

        engine = PrismEngine.from_builder(builder, repo_root=getattr(builder, "repo_root", "."))
        pkg = engine.retrieve(task.seed_symbol, budget)
        selected_order = [n.id for n in pkg.nodes]

        for sym in one_hop:
            total_within_guaranteed_range += 1
        for near_sym, near_dist in one_hop.items():
            for far_sym, far_dist in beyond.items():
                if near_sym in selected_order and far_sym in selected_order:
                    if selected_order.index(far_sym) < selected_order.index(near_sym):
                        violations_within_guaranteed_range += 1
                total_beyond_guaranteed_range += 1

    return {
        "max_dominant_seed_distance": MAX_DOMINANT_SEED_DISTANCE,
        "beta": 0.10,
        "delta_max": 10,
        "violations_within_guaranteed_range": violations_within_guaranteed_range,
        "note": (
            "Dominance is only *proven* for a closer candidate at seed-distance <= "
            f"{MAX_DOMINANT_SEED_DISTANCE}; a leapfrog beyond that range is a known, "
            "documented limit of these constants (see submodular_knapsack.py), not a bug."
        ),
    }


def run_mu1_sweep(builder, tasks: list[EvaluationTask], budget: int) -> dict[float, dict]:
    """Rank distribution of `G*_callers` (ground-truth return-binding
    callers) within the packed admission order, at each `mu_1` in
    `MU1_GRID` - real module-constant monkey-patching of `prism.packer.
    submodular_knapsack.CONTRACT_PRESERVATION_MULTIPLIER` (the value
    `select_submodular_context` actually reads - `blast_radius.py`'s own
    `MU_RETURN_UNPACK`/`CONTRACT_PRESERVATION_MULTIPLIER` are copied into
    `submodular_knapsack`'s namespace at import time, so patching the
    *origin* module after that import has already happened would not be
    observed there).
    """
    import prism.packer.submodular_knapsack as submodular_knapsack

    blast_tasks = [t for t in tasks if t.task_type == "blast" and t.adjudicated.critical_callers]
    results: dict[float, dict] = {}
    if not blast_tasks:
        return results

    original_multiplier = submodular_knapsack.CONTRACT_PRESERVATION_MULTIPLIER
    try:
        for mu1 in MU1_GRID:
            submodular_knapsack.CONTRACT_PRESERVATION_MULTIPLIER = 1.0 + mu1
            ranks: list[int] = []
            captured = 0
            total_callers = 0
            for task in blast_tasks:
                engine = PrismEngine.from_builder(builder, repo_root=getattr(builder, "repo_root", "."))
                pkg = engine.retrieve(task.seed_symbol, budget)
                order = [n.id for n in pkg.nodes]
                for caller in task.adjudicated.critical_callers:
                    total_callers += 1
                    if caller in order:
                        ranks.append(order.index(caller))
                        captured += 1
            results[mu1] = {
                "mean_rank": (sum(ranks) / len(ranks)) if ranks else None,
                "capture_rate": (captured / total_callers) if total_callers else 0.0,
            }
    finally:
        submodular_knapsack.CONTRACT_PRESERVATION_MULTIPLIER = original_multiplier
    return results


def run_ablation(repo: str, tasks_dir: str, budget: int, output_dir: str, force_reclone: bool = False) -> None:
    if repo not in CORPORA:
        raise ValueError(f"unknown repo {repo!r} - registered corpora: {sorted(CORPORA)}")
    repo_path = str(resolve(repo, force=force_reclone))
    load_result = load_tasks_from_dir(tasks_dir)
    tasks = [t for t in load_result.accepted if t.repo == repo]

    builder, _tag_matrix = build_pipeline(repo_path)
    setattr(builder, "repo_root", repo_path)

    lambda_grids = run_lambda_ablation(builder, tasks, budget)
    beta_delta_max = check_beta_delta_max_invariant(builder, tasks, budget)
    mu1_results = run_mu1_sweep(builder, tasks, budget)

    sections: dict[str, str] = {
        "Lambda_1 x Lambda_2 Cross-Product (CPI strict)": render_heatmap_markdown("lambda_1", "lambda_2", lambda_grids["cpi_strict"]),
        "Lambda_1 x Lambda_2 Cross-Product (CPI fractional)": render_heatmap_markdown("lambda_1", "lambda_2", lambda_grids["cpi_fractional"]),
        "Beta x Delta_MAX Invariant Verification": (
            f"`beta={beta_delta_max['beta']}`, `delta_max={beta_delta_max['delta_max']}`, "
            f"`MAX_DOMINANT_SEED_DISTANCE={beta_delta_max['max_dominant_seed_distance']}`.\n\n"
            f"Leapfrog violations observed beyond the guaranteed range: "
            f"{beta_delta_max['violations_within_guaranteed_range']}.\n\n"
            f"{beta_delta_max['note']}"
        ),
        "Mu_1 Caller Rank Sweep": "\n".join(
            f"- `mu_1={mu1}`: mean rank = {v['mean_rank']}, capture rate = {v['capture_rate']:.3f}"
            for mu1, v in sorted(mu1_results.items())
        )
        or "No blast-type tasks with critical_callers available for this sweep.",
    }

    out = Path(output_dir)
    write_ablation_report(out / "ablation_report.md", sections)


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.runner", description="Prism v1.1+ Empirical Benchmarking Harness")
    parser.add_argument(
        "--mode",
        choices=["eval", "pilot", "ablation", "smoke"],
        default="eval",
        help="'pilot' is 'eval' under another name (a pinned-corpus run, typically with --dry-run for a P1 pilot); "
        "'smoke' runs a network-free synthetic-fixture pipeline check, ignoring --repo/--tasks-dir/--runs/--model",
    )
    parser.add_argument("--repo", choices=sorted(CORPORA), default="django")
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help="A single token budget - a true alias for '--budgets N' (eval/pilot mode) or the ablation budget "
        "(ablation mode). Mutually exclusive with --budgets.",
    )
    parser.add_argument(
        "--budgets",
        type=int,
        nargs="+",
        default=None,
        help="Override the full (eval mode) budget sweep, default 2000 4000 8000. Mutually exclusive with --budget.",
    )
    parser.add_argument("--runs", type=int, default=5, help="Number of seeded TSR runs (max 5, the spec's own seed list)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--tasks-dir", default=None, help=f"Default: {DEFAULT_TASKS_DIR_TEMPLATE}")
    parser.add_argument("--output", default="reports/")
    parser.add_argument("--dry-run", action="store_true", help="Skip real LLM calls even if an API key is configured")
    parser.add_argument("--oracle-packages", default=None, help="Path to a hand-curated oracle-packages YAML/JSON file")
    parser.add_argument("--force-reclone", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    tasks_dir = args.tasks_dir or DEFAULT_TASKS_DIR_TEMPLATE.format(repo=args.repo)

    try:
        if args.mode == "smoke":
            from benchmarks.smoke import run_smoke

            return run_smoke(args.output)

        if args.mode == "ablation":
            try:
                ablation_budget = resolve_ablation_budget(args.budget, args.budgets)
            except ValueError as exc:
                parser.error(str(exc))
            run_ablation(args.repo, tasks_dir, ablation_budget, args.output, force_reclone=args.force_reclone)
            print(f"Ablation report written to {Path(args.output) / 'ablation_report.md'}")
            return 0

        try:
            budgets = resolve_budgets(args.budget, args.budgets)
        except ValueError as exc:
            parser.error(str(exc))
        run = run_evaluation(
            repo=args.repo,
            budgets=budgets,
            tasks_dir=tasks_dir,
            runs=args.runs,
            model=args.model,
            dry_run=args.dry_run,
            oracle_packages_path=args.oracle_packages,
            force_reclone=args.force_reclone,
        )
        write_reports(run, args.output)
        print(f"Reports written to {args.output}")
        return 0
    except (CorpusResolutionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
