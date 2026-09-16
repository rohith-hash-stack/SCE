"""v1.1+ Empirical Benchmarking Harness - CLI entrypoint.

    python -m benchmarks.runner --repo=django --budget=4000 --seeds=42,43,44,45,46 --output=reports/
    python -m benchmarks.runner --mode=ablation --output=reports/

Every engine's output is serialized through the one canonical renderer
(`prism.surface.renderer.render(pkg, RenderOptions(include_timestamp=False,
include_run_id=False))`) before it ever reaches an LLM prompt, so TSR
differences measure retrieval quality, not prompt formatting.

**Stated honestly**: running a real TSR sweep calls a real, paid LLM API
(`benchmarks.tsr.client.OpenAICompatibleClient`, generic - DeepSeek by
default, or Ollama/any OpenAI-compatible endpoint via
LLM_BASE_URL/LLM_MODEL/LLM_API_KEY_ENV) - this module never does so
silently, and it never silently *skips* doing so either. With
`--dry-run` passed explicitly, it runs the full retrieval +
diagnostic-metrics pipeline for real and skips only the LLM call
itself, leaving `tsr_scores` empty and saying so. Without `--dry-run`,
a working API key for whichever endpoint is configured (a reachable
`api.deepseek.com` for the DeepSeek default, or a reachable local
Ollama instance when LLM_BASE_URL points at one) is required - a
client-construction failure is a fatal error (exit 1, a clear message,
no report written), never a silent fallback to dry-run-shaped output
with exit code 0.

**Checkpointing** (Phase 1.4): every completed (task, engine, budget,
seed) LLM call is recorded in a JSON checkpoint file
(`reports/pilot/checkpoint.json` by default) after every 100 fresh
calls, plus once more at the end of the run. `--resume` skips a cell
already present in that file (re-using its recorded score) instead of
re-calling the LLM for it. This is CLI infrastructure only - whether a
given run is allowed to use `--resume` at all is a policy question
answered by `docs/pilot/stop_condition.md` Section 7, not by this
module.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from prism.cli import build_pipeline
from prism.surface.renderer import RenderOptions, render

from benchmarks.corpora.resolver import CORPORA, CorpusResolutionError, resolve
from benchmarks.engines.base import AbstractRetrievalEngine, selected_symbols
from benchmarks.openai_client import OpenAIClientError
from benchmarks.engines.baseline_bfs import BaselineBFSEngine
from benchmarks.engines.baseline_rag import BaselineRAGEngine
from benchmarks.engines.oracle_engine import ENGINE_NAME as ORACLE_ENGINE_NAME
from benchmarks.engines.oracle_engine import OracleEngine, PragmaticOracle
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.engines.prism_engine_cache import PrismEngineCache
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
from benchmarks.tsr.client import DEFAULT_MODEL, DEFAULT_SEEDS, OpenAICompatibleClient, run_tsr_prompt
from benchmarks.tsr.scorer_architecture import score_architecture
from benchmarks.tsr.scorer_blast import score_blast
from benchmarks.tsr.scorer_chain import score_chain
from benchmarks.tsr.scorer_debug import ParseError, extract_flat_symbols, score_debug
from benchmarks.tsr.scorer_redundancy import score_redundancy

DEFAULT_BUDGETS = (2000, 4000, 8000)
DEFAULT_TASKS_DIR_TEMPLATE = "benchmarks/ground_truth/tasks/{repo}"

#: This repo's own root (not the target corpus checkout `repo_path`
#: resolves to, e.g. the Django clone) - the `git -C` target for
#: `_push_checkpoint`, since reports/pilot/ lives here, not in the
#: corpus being evaluated.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Phase 1.4: checkpoint saved after every this-many fresh (task, engine,
#: budget, seed) LLM calls, plus once more at the end of the run.
CHECKPOINT_INTERVAL = 100
DEFAULT_CHECKPOINT_PATH = "reports/pilot/checkpoint.json"

#: Names the env var read for Fix 2's incremental checkpoint push - no
#: default, deliberately: pushing to the wrong branch by accident (or
#: to `pilot-execution`/`main` because some fallback branch name felt
#: reasonable) is worse than not pushing at all. Unset means "don't
#: push" - the checkpoint file on disk is still safe either way; this
#: is a best-effort durability improvement on top of it, not something
#: a run depends on to be correct.
PILOT_RESULTS_BRANCH_ENV_VAR = "PILOT_RESULTS_BRANCH"


def _push_checkpoint(fresh_calls_completed: int) -> None:
    """Commits and pushes `reports/pilot/` to `$PILOT_RESULTS_BRANCH`
    after a checkpoint save, so a Kaggle session dying mid-run loses at
    most `CHECKPOINT_INTERVAL` cells' worth of progress instead of
    everything since the notebook's own end-of-run push. A no-op (no
    subprocess call at all) when `PILOT_RESULTS_BRANCH` isn't set.

    Every git subprocess call is wrapped in one `try`/`except`: a
    network failure, a rejected push, git not being configured, or
    nothing new to commit must never crash the pilot run itself -
    `save_checkpoint`'s own write to disk already happened by the time
    this is called, so a failed push here only costs this one
    incremental durability improvement, not the run.
    """
    branch = os.environ.get(PILOT_RESULTS_BRANCH_ENV_VAR)
    if not branch:
        return
    try:
        subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "add", "-f", "reports/pilot/"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "commit", "-m", f"checkpoint {fresh_calls_completed} cells"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "push", "origin", f"HEAD:{branch}"],
            check=True, capture_output=True, text=True,
        )
        print(f"[runner] checkpoint pushed to {branch!r} ({fresh_calls_completed} cells)", flush=True)
    except Exception as exc:
        print(f"[runner] checkpoint push to {branch!r} failed (continuing): {exc}", file=sys.stderr, flush=True)


def _cell_key(task_id: str, engine_name: str, budget: int, seed: int) -> str:
    """One (task, engine, budget, seed) cell's checkpoint key - a plain
    string (not a tuple) since it round-trips through JSON, which has no
    tuple type and would otherwise silently turn into a JSON array key
    error (JSON object keys must be strings)."""
    return f"{task_id}|{engine_name}|{budget}|{seed}"


def load_checkpoint(path: str) -> dict:
    """`{"cells": {cell_key: {"score": float, "raw_response": str,
    "prompt_tokens": int, "completion_tokens": int, "cost_usd": float |
    None}}}` - an absent, unreadable, or corrupt file is treated as "no
    completed cells yet" (never raises), the same "checkpointing is a
    resumability convenience, not a correctness dependency" contract
    this codebase's other caches already establish. `raw_response` is
    read back with `.get("raw_response", "")` at the one call site that
    resumes a cell, so a checkpoint file written before
    fix-llm-response-persistence (no `raw_response` key at all) still
    loads - it just resumes with an empty response string for those
    older cells, never a `KeyError`."""
    p = Path(path)
    if not p.exists():
        return {"cells": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"cells": {}}
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict):
        return {"cells": {}}
    return data


def save_checkpoint(path: str, checkpoint: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(checkpoint, indent=2, sort_keys=True))


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

#: Appended to a T02 debug task's own prompt text (only - never to chain/
#: blast/architecture/redundancy tasks, each of which has its own
#: distinct expected response shape, e.g. scorer_blast.py's free-prose
#: substring convention) before the real LLM call. A flat contract -
#: {"reasoning": ..., "symbols": [...]} - not the earlier nested
#: {"reasoning": ..., "pipeline": [{"symbol": ..., "evidence": ...}]}
#: shape: Qwen 2.5 7B Instruct Q8_0 (the Ollama SLM used for local
#: format-compliance testing, docs/pilot/stop_condition.md Section 5)
#: passes 10/10 on the flat shape but fails at Q4 on the nested one -
#: a pre-registered harness change, not a DeepSeek-specific tweak.
#: `evidence` was never consumed by anything downstream (verified
#: directly: only `entry["symbol"]` was ever read out of the old
#: `pipeline` array) - dropping it costs nothing. `benchmarks.tsr.
#: scorer_debug.extract_flat_symbols` parses exactly this new shape.
DEBUG_TASK_RESPONSE_CONTRACT = (
    "\n\nOutput a JSON object with:\n"
    '  "reasoning": "2-3 sentences describing how the stages connect"\n'
    '  "symbols": ["fully.qualified.name", "fully.qualified.name", ...]\n\n'
    "The symbols array is ordered: first symbol is the seed, subsequent "
    "symbols are the causal stages in execution order. Respond with "
    "this JSON object, and nothing else, inside one fenced code block "
    "(```json ... ```)."
)


# --------------------------------------------------------------------- #
# Core evaluation
# --------------------------------------------------------------------- #
def _build_engines(
    task: EvaluationTask, oracle_packages_path: str | None = None, use_pragmatic_oracle: bool = False
) -> list[AbstractRetrievalEngine]:
    """Oracle (when configured) is returned *first* - `run_evaluation`
    needs its per-budget selected-symbol set computed before any other
    engine's turn, so every engine's `fpr_oracle` (divergence from the
    Oracle package at that same budget) can be computed in a single pass
    with no engine retrieved twice.

    At most one Oracle variant is ever active: `oracle_packages_path`
    (hand-curated, `OracleEngine`) takes precedence if both are somehow
    set - `use_pragmatic_oracle` (Gap 2 Blocker 2 Option B,
    `PragmaticOracle`) is the real, zero-annotation-cost substitute for
    when no hand-curated file exists, which for this repo's own real
    tasks is always (see `oracle_engine.py`'s own docstring)."""
    engines: list[AbstractRetrievalEngine] = []
    if oracle_packages_path is not None:
        engines.append(OracleEngine(oracle_packages_path, task.task_id))
    elif use_pragmatic_oracle:
        engines.append(PragmaticOracle(task))
    engines.extend(
        [
            PrismEngineCache(),  # same name="prism_v11" as PrismEngine - see Gap 5 (prism_engine_cache.py)
            BaselineRAGEngine(),
            BaselineBFSEngine(mode="forward"),
            BaselineBFSEngine(mode="bidirectional"),
        ]
    )
    return engines


def _ground_truth_universe(task: EvaluationTask) -> set[str]:
    """`fpr_gt`'s own ground-truth set - for a T02 debug task (Gap 8),
    this is exactly the union of all three annotated sets
    (`pipeline_symbols`, `required_context`, `boundary_symbols`); the
    other task types' own fields are unioned in too (empty by
    construction for a task that isn't their type, so harmless)."""
    adjudicated = task.adjudicated
    return (
        set(adjudicated.pipeline_symbols)
        | adjudicated.critical_callers
        | adjudicated.orthogonal_neighbors
        | adjudicated.reference_symbols
        | adjudicated.required_context
        | adjudicated.boundary_symbols
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


def resolve_seeds(seeds: str | None, *, default: tuple[int, ...] = DEFAULT_SEEDS) -> tuple[int, ...]:
    """Resolves the CLI's `--seeds` flag (a comma-separated list, e.g.
    `"42,43"`) into the effective seed tuple - each seed becomes one
    separate real LLM call per (task, engine, budget) cell. `None` or an
    empty string falls back to `default` (the spec's own 5-seed list).
    Raises `ValueError` for a malformed entry (never silently drops or
    truncates it).
    """
    if not seeds:
        return default
    tokens = [t.strip() for t in seeds.split(",")]
    if any(not t for t in tokens):
        raise ValueError(f"--seeds contains an empty entry: {seeds!r}")
    try:
        return tuple(int(t) for t in tokens)
    except ValueError as exc:
        raise ValueError(f"--seeds must be a comma-separated list of integers, got {seeds!r}") from exc


def run_evaluation(
    repo: str,
    budgets: list[int],
    tasks_dir: str,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    #: None, not DEFAULT_MODEL - see benchmarks/tsr/client.py's
    #: run_tsr_prompt docstring. A hardcoded default here would flow
    #: through as an explicit, non-None model= on every call, always
    #: overriding OpenAICompatibleClient's own env-var-resolved self.model
    #: (LLM_MODEL) - the fix-client-env-vars root cause.
    model: str | None = None,
    dry_run: bool = False,
    oracle_packages_path: str | None = None,
    use_pragmatic_oracle: bool = False,
    force_reclone: bool = False,
    resume: bool = False,
    checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
    output_dir: str | None = None,
) -> EvaluationRun:
    """The real end-to-end sweep: resolve the pinned corpus, load its
    ground-truth tasks, run every engine at every budget, and (unless
    `dry_run`, or no API key is configured at all) run the real TSR
    protocol - one LLM call per seed in `seeds` - against a real LLM.

    `resume`: when set, a (task, engine, budget, seed) cell already
    recorded in `checkpoint_path` is skipped (its recorded score is
    reused) instead of making a fresh LLM call for it. Retrieval and
    diagnostics are always recomputed fresh regardless - they're free
    and deterministic, so there's nothing to gain by caching them; only
    the LLM calls are checkpointed.

    `output_dir`: when set, `write_reports(run, output_dir)` is called
    at every interval checkpoint save (every `CHECKPOINT_INTERVAL`
    fresh calls), not just once at the very end via the CLI's own
    post-return call - so a report reflecting every fully-completed
    (task, engine, budget) record exists on disk throughout a long run,
    not only if it reaches the end. `None` (the default) preserves the
    original behavior for callers - tests included - that don't pass
    it: no incremental report writes, no output directory touched.
    """
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

    #: Engine count is computed by actually building the engine list for
    #: the first task (rather than a hardcoded constant) so this stays
    #: correct if _build_engines' own engine set ever changes - the same
    #: real function every (task, budget) pair below calls, just called
    #: once here purely to measure len(engines).
    num_engines = len(_build_engines(tasks[0], oracle_packages_path, use_pragmatic_oracle)) if tasks else 0
    total_cells = len(tasks) * num_engines * len(budgets) * len(seeds)
    print(
        f"[runner] starting: {total_cells} cells planned "
        f"({len(tasks)} tasks x {num_engines} engines x "
        f"{len(budgets)} budgets x {len(seeds)} seeds)",
        flush=True,
    )

    builder, _tag_matrix = build_pipeline(repo_path)
    feature_stats = compute_corpus_feature_stats(builder)

    client = None
    if not dry_run:
        # `dry_run` is False here precisely because the operator did NOT
        # pass `--dry-run` - they expect real LLM calls. A client-
        # construction failure (missing API key for whichever endpoint
        # is configured, the `openai` package not installed, etc.) must
        # be fatal in that case: this used to catch the exception and
        # silently fall back to `dry_run = True`, which produced a full
        # report - exit code 0, every diagnostic computed normally -
        # with every `tsr_scores` silently empty and no real signal in
        # the output that no LLM call was ever made (the "no LLM calls
        # were made" pilot-run bug). An operator who genuinely wants a
        # dry run passes `--dry-run` explicitly - that path never
        # reaches here at all (see the `if not dry_run:` guard above),
        # so it is unaffected.
        client = OpenAICompatibleClient()
        if not hasattr(client, "base_url") or not client.base_url:
            raise RuntimeError("client.base_url not set — check benchmarks/tsr/client.py __init__")
        print(f"[runner] using base_url={client.base_url} model={client.model}", file=sys.stderr, flush=True)

    checkpoint = load_checkpoint(checkpoint_path) if resume else {"cells": {}}
    fresh_calls_completed = 0
    #: Aggregate token/cost totals across every cell scored this run -
    #: both fresh calls and cells reused via --resume, since the
    #: end-of-run summary is meant to describe the pilot's real
    #: aggregate usage, not just this one process's fresh-call subset.
    total_calls_scored = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost_usd = 0.0

    run = EvaluationRun()

    for task in tasks:
        # Oracle first (see `_build_engines`'s own docstring): its
        # per-budget selected-symbol set must exist before any other
        # engine's turn so `fpr_oracle` can be computed for everyone in
        # one pass, with the Oracle itself retrieved exactly once.
        engines = _build_engines(task, oracle_packages_path, use_pragmatic_oracle)
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
                raw_responses: list[str] = []
                if not dry_run and client is not None:
                    rendered_xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))

                    cell_scores: dict[int, float] = {}
                    cell_responses: dict[int, str] = {}
                    pending_seeds = []
                    for seed in seeds:
                        key = _cell_key(task.task_id, engine.name, budget, seed)
                        cached_cell = checkpoint["cells"].get(key)
                        if cached_cell is not None:
                            cell_scores[seed] = cached_cell["score"]
                            # `raw_response` didn't exist in checkpoints written
                            # before fix-llm-response-persistence - "" (not a
                            # KeyError) for a resumed cell from an older file.
                            cell_responses[seed] = cached_cell.get("raw_response", "")
                            total_calls_scored += 1
                            total_prompt_tokens += cached_cell.get("prompt_tokens", 0) or 0
                            total_completion_tokens += cached_cell.get("completion_tokens", 0) or 0
                            total_cost_usd += cached_cell.get("cost_usd", 0.0) or 0.0
                        else:
                            pending_seeds.append(seed)

                    if pending_seeds:
                        task_prompt = task.prompt
                        if task.task_type == "debug":
                            task_prompt = task.prompt + DEBUG_TASK_RESPONSE_CONTRACT
                        tsr_results = run_tsr_prompt(
                            client, SYSTEM_PROMPT, rendered_xml, task_prompt, model=model, seeds=tuple(pending_seeds)
                        )
                        for r in tsr_results:
                            score = score_tsr_response(task, r.call.content, candidate_symbols)
                            response_is_unparseable = False
                            if task.task_type == "debug":
                                try:
                                    extract_flat_symbols(r.call.content)
                                except ParseError:
                                    response_is_unparseable = True
                            if response_is_unparseable:
                                print(
                                    f"[llm] parse-failure task={task.task_id} engine={engine.name} "
                                    f"seed={r.seed} raw_response={r.call.content[:500]!r}",
                                    file=sys.stderr,
                                )
                            cell_scores[r.seed] = score
                            cell_responses[r.seed] = r.call.content
                            key = _cell_key(task.task_id, engine.name, budget, r.seed)
                            #: CPI is only defined for chain/debug tasks
                            #: (the same gate compute_diagnostics uses,
                            #: below) - pipeline_symbols defaults to []
                            #: for blast/architecture/redundancy tasks,
                            #: and cpi_strict/cpi_fractional's own
                            #: vacuous-truth convention would silently
                            #: store a misleading 1.0 ("perfect") for
                            #: those rather than "not applicable" - None
                            #: instead, matching fpr_oracle's own
                            #: absent-comparison-point-is-None convention.
                            if task.task_type in ("chain", "debug"):
                                pipeline_syms = task.adjudicated.pipeline_symbols
                                cell_cpi_strict = cpi_strict(candidate_symbols, pipeline_syms)
                                cell_cpi_fractional = cpi_fractional(candidate_symbols, pipeline_syms)
                            else:
                                cell_cpi_strict = None
                                cell_cpi_fractional = None
                            checkpoint["cells"][key] = {
                                "score": score,
                                "raw_response": r.call.content,
                                "prompt_tokens": r.call.prompt_tokens,
                                "completion_tokens": r.call.completion_tokens,
                                "cost_usd": r.call.cost_usd,
                                "selected_symbols": sorted(candidate_symbols),
                                "cpi_strict": cell_cpi_strict,
                                "cpi_fractional": cell_cpi_fractional,
                            }
                            total_calls_scored += 1
                            total_prompt_tokens += r.call.prompt_tokens
                            total_completion_tokens += r.call.completion_tokens
                            total_cost_usd += r.call.cost_usd or 0.0
                            fresh_calls_completed += 1
                            if fresh_calls_completed % CHECKPOINT_INTERVAL == 0:
                                save_checkpoint(checkpoint_path, checkpoint)
                                _push_checkpoint(fresh_calls_completed)
                                print(f"[pilot] {fresh_calls_completed} cells completed - checkpoint saved to {checkpoint_path}")
                                print(
                                    f"[runner] checkpoint: {len(checkpoint['cells'])}/{total_cells} cells",
                                    flush=True,
                                )
                                if output_dir is not None:
                                    #: A partial report at cell 100 is more
                                    #: useful than no report at all if the
                                    #: session dies before reaching the end -
                                    #: reflects every fully-completed
                                    #: (task, engine, budget) record in `run`
                                    #: so far (the in-progress cell group that
                                    #: triggered this exact save isn't in
                                    #: `run.records` yet - it's only appended
                                    #: once its own seed loop finishes below).
                                    write_reports(run, output_dir)
                                    print(f"[pilot] {fresh_calls_completed} cells completed - reports written to {output_dir}")

                    tsr_scores = [cell_scores[seed] for seed in seeds]
                    raw_responses = [cell_responses[seed] for seed in seeds]

                run.records.append(
                    TaskRunRecord(
                        task_id=task.task_id,
                        task_type=task.task_type,
                        repo=task.repo,
                        engine_name=engine.name,
                        budget_tokens=budget,
                        raw_responses=raw_responses,
                        tsr_scores=tsr_scores,
                        diagnostics=compute_diagnostics(
                            pkg, task, feature_stats, oracle_selected=oracle_selected_by_budget.get(budget)
                        ),
                        selected_symbols=sorted(candidate_symbols),
                        ground_truth_symbols=sorted(_ground_truth_universe(task)),
                    )
                )

    if fresh_calls_completed % CHECKPOINT_INTERVAL != 0:
        # A final, sub-interval batch of fresh calls (the common case: the
        # total call count rarely lands on an exact multiple of 100) -
        # saved once more here so no completed cell is lost to a crash
        # after the loop's own last `% CHECKPOINT_INTERVAL == 0` save.
        save_checkpoint(checkpoint_path, checkpoint)
        _push_checkpoint(fresh_calls_completed)

    print(f"[runner] complete: {len(checkpoint['cells'])}/{total_cells} cells", flush=True)

    if total_calls_scored:
        avg_prompt = total_prompt_tokens / total_calls_scored
        avg_completion = total_completion_tokens / total_calls_scored
        print(
            f"[llm] summary: {total_calls_scored} calls, avg_prompt={avg_prompt:.1f} "
            f"avg_completion={avg_completion:.1f} total_cost=${total_cost_usd:.4f}",
            file=sys.stderr,
        )
        if avg_completion > 2000:
            print(
                f"[llm] WARNING: avg_completion={avg_completion:.1f} exceeds 2000 - "
                "the structured contract's own bound is not being respected by the model's "
                "responses; investigate before trusting the pilot's cost projections.",
                file=sys.stderr,
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
    """Runs `PrismEngine` for every pipeline-shaped task (`task_type`
    `"chain"` or `"debug"` - both score CPI against an ordered
    `pipeline_symbols`, the same as `compute_diagnostics`'s own dispatch
    already treats them) at `budget` with `causal_weights.LAMBDA_
    DATA_FLOW`/`LAMBDA_GUARD` temporarily overridden, returning
    `(mean_cpi_strict, mean_cpi_fractional)`. Real module-constant
    monkey-patching (restored in `finally`) - these two lambdas are read
    directly as module globals by `causal_edge_weight`, not threaded
    through as function parameters, so this is the only way to sweep
    them without forking the causal engine itself for the ablation run.
    """
    import prism.traversal.causal_weights as causal_weights

    pipeline_tasks = [t for t in tasks if t.task_type in ("chain", "debug")]
    if not pipeline_tasks:
        return 0.0, 0.0

    original_lambda1 = causal_weights.LAMBDA_DATA_FLOW
    original_lambda2 = causal_weights.LAMBDA_GUARD
    causal_weights.LAMBDA_DATA_FLOW = lambda1
    causal_weights.LAMBDA_GUARD = lambda2
    try:
        engine = PrismEngine.from_builder(builder, repo_root=getattr(builder, "repo_root", "."))
        strict_scores, fractional_scores = [], []
        for task in pipeline_tasks:
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
        help="'pilot' is 'eval' under another name (a pinned-corpus run) - pass --dry-run explicitly to skip real "
        "LLM calls (e.g. to check the retrieval/diagnostics pipeline for free); without it, a real DeepSeek call "
        "is made for every seed and a missing/broken DEEPSEEK_API_KEY is now a fatal error, not a silent fallback. "
        "'smoke' runs a network-free synthetic-fixture pipeline check, ignoring --repo/--tasks-dir/--seeds/--model",
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
    parser.add_argument(
        "--seeds",
        default=None,
        help="Comma-separated seeds for the TSR protocol, e.g. '42,43' - one real LLM call per seed per "
        f"(task, engine, budget) cell. Default: {','.join(str(s) for s in DEFAULT_SEEDS)} (the spec's own 5-seed list).",
    )
    parser.add_argument(
        "--model", default=None,
        help=f"Explicit model override. Default: None - falls back to OpenAICompatibleClient's own "
        f"env-var-resolved model (LLM_MODEL, else {DEFAULT_MODEL}). Passing a hardcoded default "
        "here instead of None would always override LLM_MODEL, regardless of its value.",
    )
    parser.add_argument("--tasks-dir", default=None, help=f"Default: {DEFAULT_TASKS_DIR_TEMPLATE}")
    parser.add_argument("--output", default="reports/")
    parser.add_argument("--dry-run", action="store_true", help="Skip real LLM calls even if an API key is configured")
    parser.add_argument("--oracle-packages", default=None, help="Path to a hand-curated oracle-packages YAML/JSON file")
    parser.add_argument(
        "--pragmatic-oracle",
        action="store_true",
        help="Use PragmaticOracle (Gap 2 Blocker 2 Option B: pipeline_symbols/required_context/boundary_symbols, "
        "truncated by real dist_W) when --oracle-packages isn't set, instead of running with no Oracle at all",
    )
    parser.add_argument("--force-reclone", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip a (task, engine, budget, seed) cell already recorded in --checkpoint, reusing its saved score "
        "instead of making a fresh LLM call for it.",
    )
    parser.add_argument(
        "--checkpoint",
        default=DEFAULT_CHECKPOINT_PATH,
        help=f"Checkpoint file path (read with --resume, written to after every {CHECKPOINT_INTERVAL} fresh calls "
        f"and once more at the end of the run). Default: {DEFAULT_CHECKPOINT_PATH}",
    )
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
            seeds = resolve_seeds(args.seeds)
        except ValueError as exc:
            parser.error(str(exc))
        run = run_evaluation(
            repo=args.repo,
            budgets=budgets,
            tasks_dir=tasks_dir,
            seeds=seeds,
            model=args.model,
            dry_run=args.dry_run,
            oracle_packages_path=args.oracle_packages,
            use_pragmatic_oracle=args.pragmatic_oracle,
            force_reclone=args.force_reclone,
            resume=args.resume,
            checkpoint_path=args.checkpoint,
            output_dir=args.output,
        )
        write_reports(run, args.output)
        print(f"Reports written to {args.output}")
        return 0
    except (CorpusResolutionError, ValueError, OpenAIClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
