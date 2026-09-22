"""Track 3 (Benchmark Scoring Modernization & Harness Wiring): the
two-pass evaluation CLI, wiring `prism.engine.PrismEngine.retrieve_two_pass`
(Track 2) into a real ground-truth task evaluation loop, scored with the
two-pass-aware metrics Track 3 introduced to fix the real metric gaps
`reports/spike_noise_reduction_debrief.md`'s Closing Note diagnosed:
`score_debug_causal` (causal-sequence-containment scoring - fixes the
granularity trap, `django_t02_005`) and `cpi_turn1_selection`/
`cpi_end_to_end` (recall against Turn 1's own request vs. the model's
actual Turn 2 answer, instead of `cpi_strict`'s single "the rendered
package's own node set" view, which under-credits a real two-pass
success - `django_t02_009`).

**Not `benchmarks/run_benchmark.py`, deliberately**: that module is a
different, older harness entirely - `ContextKnapsackPacker`'s
structural-coverage benchmark (ground-truth subgraph reachability,
code-block validity, raw-vs-packed compression), with no LLM call, no
task/TSR concept, and no ground-truth `pipeline_symbols` at all. There
is nothing in it to "add --mode two_pass to" - the real TSR/CPI
task-based evaluation loop this needs to plug into is `benchmarks.
runner`'s `run_evaluation` (`python -m benchmarks.runner`). This module
is a focused sibling of that CLI rather than a change to
`run_evaluation` itself: that loop's own per-cell shape (one `retrieve()`
call, then exactly one scored LLM call) doesn't fit a two-turn protocol
without restructuring every task type's own path through it, and the
hop=3+scope candidate index (Track 2) and its manifest format were
validated exclusively against debug (T02)-shaped tasks - this module
is scoped to exactly that, not a silent generalization to task types
never validated against this protocol.

Usage:
    python -m benchmarks.run_two_pass_benchmark --repo django --budgets 2000 4000 \\
        --tasks django_t02_005_model_save_signals django_t02_009_queryset_filter_clone \\
        --seeds 42 --output reports/two_pass/

    python -m benchmarks.run_two_pass_benchmark --repo django --dry-run
        # real retrieval (both turns' own candidate-index/hydration work),
        # zero LLM calls - checks the wiring for free.

**Stated honestly, same discipline `benchmarks.runner`'s own module
docstring already establishes**: a real (non---dry-run) run makes real,
paid LLM calls via `OpenAICompatibleClient` - an operator's own action,
their own credentials, their own cost. This module never makes one
silently, and never silently skips one either.

Checkpointed separately from the protected pilot checkpoint
(`reports/pilot/checkpoint.json`, whose hash is pinned throughout this
project's own verification gate) - this writes to
`reports/pilot/checkpoint_two_pass.json` instead, so a two-pass run can
never touch the file that gate depends on.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

from prism.engine import PrismEngine
from prism.surface.renderer import RenderOptions, render

from benchmarks.corpora.resolver import CORPORA, CorpusResolutionError, resolve
from benchmarks.engines.base import selected_symbols
from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.ground_truth.schema import EvaluationTask
from benchmarks.metrics.cpi import cpi_end_to_end, cpi_turn1_selection
from benchmarks.metrics.fpr import fpr
from benchmarks.openai_client import OpenAIClientError
from benchmarks.runner import (
    DEBUG_TASK_RESPONSE_CONTRACT,
    DEFAULT_TASKS_DIR_TEMPLATE,
    SYSTEM_PROMPT,
    _ground_truth_universe,
    load_checkpoint,
    resolve_seeds,
    save_checkpoint,
)
from benchmarks.tsr.client import DEFAULT_SEEDS, OpenAICompatibleClient, run_tsr_prompt
from benchmarks.tsr.scorer_debug import ParseError, extract_flat_symbols, score_debug_causal

DEFAULT_BUDGETS = (2000, 4000)
DEFAULT_CHECKPOINT_PATH = "reports/pilot/checkpoint_two_pass.json"

#: Mirrors `benchmarks.experiments.hydration_loop`'s own Turn-1 prompt
#: (`experiment/noise-filtering-spike`, commit 7c352a5's v3) - the exact
#: instruction the spike's own live grid validated, unchanged, since
#: reproducing a genuinely different prompt here would make this run
#: uncomparable to the spike's own results rather than a real
#: production graduation of them.
TURN1_SYSTEM_PROMPT = (
    "You are a senior software engineer investigating a codebase. You will be given a compact "
    "<candidate_index> - every symbol reachable from a seed function, one per line as "
    "qualified_name|role|kind|signature|calls=[...] (role is one of seed/callee/caller/transitive; "
    "signature is the symbol's own raw declaration line; calls lists the names it directly invokes "
    "in its own body, deterministically extracted, never a docstring or comment) - followed by a "
    "real task. Examine the symbol signatures and their direct call targets to trace the complete "
    "causal execution path from the seed to termination. Request all necessary intermediate and "
    "helper symbols required to form an unbroken execution chain. Only name symbols that appear in "
    "the index - never invent one."
)


class TwoPassBenchmarkError(Exception):
    """A user-facing failure (bad repo, no matching debug tasks, a
    client-construction failure without --dry-run) - the CLI prints
    `str(exc)` and exits non-zero rather than showing a traceback."""


@dataclasses.dataclass
class TwoPassCellResult:
    task_id: str
    budget: int
    seed: int | None
    tsr: float | None
    cpi_turn1_selection: float
    cpi_end_to_end: float | None
    fpr_gt: float
    candidate_count: int
    requested_count: int
    skipped_hallucinated: list[str]
    turn1_parsed_ok: bool
    turn1_prompt_tokens: int | None = None
    turn2_prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    #: The resolved model tag that actually priced this cell (`CallResult.
    #: model` - after the client's own env-var/default resolution, never
    #: the raw `--model` argument, which can be `None`) - carried through
    #: so a downstream summary/cost report can say exactly which pricing
    #: table entry (`benchmarks.tsr.client._known_model_pricing_override`)
    #: applied, instead of assuming. Empty string for a --dry-run cell
    #: (no LLM call happened at all).
    model: str = ""
    turn1_response: str = ""
    turn2_response: str = ""


def _turn1_user_prompt(manifest_text: str, task_prompt: str) -> str:
    return (
        f"{manifest_text}\n\nTask:\n{task_prompt}\n\n"
        'Respond with a JSON object: {"thought_process": "1-2 sentences on why", '
        '"requested_symbols": ["qualified.name", ...]} - requested_symbols ordered seed first, '
        "then the causal stages in execution order, using each symbol's own full qualified_name "
        "exactly as given in the index (never a bare name from a calls=[...] list). Respond with "
        "this JSON object and nothing else."
    )


def _parse_requested_symbols(response_text: str) -> tuple[list[str], bool]:
    """`(requested_symbols, parsed_ok)` - mirrors `hydration_loop.py`'s
    own contract: a Turn 1 parse failure degrades Turn 2 to "only the
    seed" (an empty `requested_symbols` list) rather than aborting the
    cell."""
    try:
        obj = json.loads(response_text.strip())
    except json.JSONDecodeError:
        return [], False
    if not isinstance(obj, dict):
        return [], False
    symbols = obj.get("requested_symbols")
    if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
        return [], False
    return symbols, True


def run_two_pass_cell(
    engine: PrismEngine,
    client: OpenAICompatibleClient | None,
    task: EvaluationTask,
    budget: int,
    seed: int | None,
    model: str | None,
) -> TwoPassCellResult:
    """One (task, budget, seed) two-pass cell: Turn 1 manifest + LLM
    request, Turn 2 hydration + scored LLM answer. `client=None` (and
    `seed=None`) is the `--dry-run` path: both turns of real retrieval
    run for real (candidate-index build, hydration, render), no LLM
    call happens at all, and `tsr`/`cpi_end_to_end`/token/cost fields
    are `None` - there is no model answer to score.
    """
    manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)

    if client is None:
        pkg, skipped = engine.retrieve_requested(task.seed_symbol, budget, [], candidate_universe, task_type=task.task_type)
        candidates = selected_symbols(pkg)
        return TwoPassCellResult(
            task_id=task.task_id, budget=budget, seed=seed, tsr=None,
            cpi_turn1_selection=cpi_turn1_selection([], task.adjudicated.pipeline_symbols),
            cpi_end_to_end=None,
            fpr_gt=fpr(candidates, _ground_truth_universe(task)),
            candidate_count=len(candidate_universe), requested_count=0, skipped_hallucinated=skipped,
            turn1_parsed_ok=True,
        )

    turn1_user = _turn1_user_prompt(manifest_text, task.prompt)
    turn1_call = client.complete(
        model, TURN1_SYSTEM_PROMPT, turn1_user, seed=seed, task_id=task.task_id, engine="prism_two_pass_turn1",
    )
    requested_symbols, parsed_ok = _parse_requested_symbols(turn1_call.content)

    pkg, skipped = engine.retrieve_requested(
        task.seed_symbol, budget, requested_symbols, candidate_universe, task_type=task.task_type,
    )
    candidates = selected_symbols(pkg)

    rendered_xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
    task_prompt = task.prompt + DEBUG_TASK_RESPONSE_CONTRACT
    turn2_results = run_tsr_prompt(
        client, SYSTEM_PROMPT, rendered_xml, task_prompt, model=model, seeds=(seed,),
        task_id=task.task_id, engine="prism_two_pass_turn2",
    )
    turn2_call = turn2_results[0].call

    tsr = score_debug_causal(turn2_call.content, task.adjudicated.pipeline_symbols, candidates)
    try:
        answer_symbols = extract_flat_symbols(turn2_call.content)
    except ParseError:
        answer_symbols = []

    return TwoPassCellResult(
        task_id=task.task_id, budget=budget, seed=seed, tsr=tsr,
        cpi_turn1_selection=cpi_turn1_selection(requested_symbols, task.adjudicated.pipeline_symbols),
        cpi_end_to_end=cpi_end_to_end(answer_symbols, task.adjudicated.pipeline_symbols),
        fpr_gt=fpr(candidates, _ground_truth_universe(task)),
        candidate_count=len(candidate_universe), requested_count=len(requested_symbols), skipped_hallucinated=skipped,
        turn1_parsed_ok=parsed_ok,
        turn1_prompt_tokens=turn1_call.prompt_tokens, turn2_prompt_tokens=turn2_call.prompt_tokens,
        completion_tokens=turn1_call.completion_tokens + turn2_call.completion_tokens,
        cost_usd=(turn1_call.cost_usd or 0.0) + (turn2_call.cost_usd or 0.0),
        model=turn2_call.model,
        turn1_response=turn1_call.content, turn2_response=turn2_call.content,
    )


def run_two_pass_evaluation(
    repo: str,
    budgets: list[int],
    task_ids: list[str] | None,
    tasks_dir: str | None,
    seeds: tuple[int, ...],
    model: str | None,
    dry_run: bool,
    checkpoint_path: str,
    resume: bool,
) -> list[TwoPassCellResult]:
    try:
        repo_path = str(resolve(repo))
    except CorpusResolutionError as exc:
        raise TwoPassBenchmarkError(str(exc)) from exc

    resolved_tasks_dir = tasks_dir or DEFAULT_TASKS_DIR_TEMPLATE.format(repo=repo)
    loaded = load_tasks_from_dir(Path(resolved_tasks_dir))
    if loaded.rejected:
        print(f"warning: {len(loaded.rejected)} ground-truth task(s) failed to load: {loaded.rejected}", file=sys.stderr)
    debug_tasks = [t for t in loaded.accepted if t.task_type == "debug"]
    if task_ids:
        wanted = set(task_ids)
        debug_tasks = [t for t in debug_tasks if t.task_id in wanted]
        missing = wanted - {t.task_id for t in debug_tasks}
        if missing:
            raise TwoPassBenchmarkError(f"requested task(s) not found among debug-type tasks in {resolved_tasks_dir}: {sorted(missing)}")
    if not debug_tasks:
        raise TwoPassBenchmarkError(
            f"no debug-type ground-truth tasks found in {resolved_tasks_dir} - the hop=3+scope candidate index "
            "(Track 2) was only ever validated against this task type"
        )

    client: OpenAICompatibleClient | None = None
    if not dry_run:
        try:
            client = OpenAICompatibleClient()
        except OpenAIClientError as exc:
            raise TwoPassBenchmarkError(f"could not construct an LLM client: {exc}") from exc

    print(f"[two_pass] indexing {repo_path}...", file=sys.stderr)
    t0 = time.monotonic()
    engine = PrismEngine.from_repo(repo_path)
    print(f"[two_pass] indexed in {time.monotonic() - t0:.1f}s", file=sys.stderr)

    checkpoint = load_checkpoint(checkpoint_path) if resume else {"cells": {}}
    results: list[TwoPassCellResult] = []
    effective_seeds: tuple[int | None, ...] = seeds if not dry_run else (None,)

    for task in debug_tasks:
        for budget in budgets:
            for seed in effective_seeds:
                key = f"{task.task_id}|{budget}|{seed}"
                cached = checkpoint["cells"].get(key)
                if cached is not None:
                    results.append(TwoPassCellResult(**cached))
                    continue
                result = run_two_pass_cell(engine, client, task, budget, seed, model)
                results.append(result)
                checkpoint["cells"][key] = dataclasses.asdict(result)
                if not dry_run:
                    save_checkpoint(checkpoint_path, checkpoint)

    if not dry_run:
        save_checkpoint(checkpoint_path, checkpoint)
    return results


def render_summary_table(results: list[TwoPassCellResult]) -> str:
    lines = [f"{'task_id':<45} {'budget':>7} {'seed':>6} {'tsr':>6} {'cpi_t1':>7} {'cpi_e2e':>8} {'fpr_gt':>7}"]
    for r in results:
        tsr_s = f"{r.tsr:.3f}" if r.tsr is not None else "  n/a"
        e2e_s = f"{r.cpi_end_to_end:.3f}" if r.cpi_end_to_end is not None else "   n/a"
        lines.append(
            f"{r.task_id:<45} {r.budget:>7} {str(r.seed):>6} {tsr_s:>6} "
            f"{r.cpi_turn1_selection:>7.3f} {e2e_s:>8} {r.fpr_gt:>7.3f}"
        )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.run_two_pass_benchmark",
        description="Two-pass (manifest + hydration) evaluation against real ground-truth debug tasks.",
    )
    parser.add_argument("--repo", choices=sorted(CORPORA), default="django")
    parser.add_argument("--budgets", type=int, nargs="+", default=list(DEFAULT_BUDGETS))
    parser.add_argument("--tasks", nargs="+", default=None, help="Restrict to these task_ids (default: every debug-type task in --tasks-dir).")
    parser.add_argument("--tasks-dir", default=None, help=f"Default: {DEFAULT_TASKS_DIR_TEMPLATE}")
    parser.add_argument(
        "--seeds", default=None,
        help=f"Comma-separated seeds. Default: {','.join(str(s) for s in DEFAULT_SEEDS)}.",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Real retrieval, zero LLM calls.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT_PATH)
    parser.add_argument("--output", default=None, help="Write raw per-cell results as JSON to this path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        seeds = resolve_seeds(args.seeds)
        results = run_two_pass_evaluation(
            repo=args.repo, budgets=args.budgets, task_ids=args.tasks, tasks_dir=args.tasks_dir,
            seeds=seeds, model=args.model, dry_run=args.dry_run, checkpoint_path=args.checkpoint,
            resume=args.resume,
        )
    except (TwoPassBenchmarkError, ValueError, CorpusResolutionError, OpenAIClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(render_summary_table(results))
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps([dataclasses.asdict(r) for r in results], indent=2))
        print(f"\nWrote raw results to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
