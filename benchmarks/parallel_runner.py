"""Blocker 1 Decision 4: multiprocessing parallelization for the pilot/
eval sweep. `multiprocessing`, never threading - retrieval and
diagnostic scoring are CPU-bound (real tree-sitter parsing, real graph
traversal, real bitwise scoring), so threading would buy nothing under
the GIL.

**Explicit fork context, not the platform default** (Decision 4 detail
1): `multiprocessing`'s default start method is platform-dependent -
`fork` on Linux (copy-on-write, shares memory - what this module
depends on), `spawn` on macOS (3.8+) and Windows (pickles everything,
shares nothing). Using the bare default would silently degrade to
`spawn` on those platforms, defeating the entire point of this design.
`get_context("fork")` is requested explicitly; `_require_fork_context`
raises `RuntimeError` (not a silent fallback to `spawn`) if the current
platform doesn't offer it.

**Sharing, not rebuilding**: the real `ConcreteGraphBuilder`/corpus
feature stats are built exactly **once**, in the *main* process,
*before* the fork `Pool` is ever constructed (`_prepare_shared_state`).
Every worker is a forked copy of the already-warm main process, so
each one inherits this state via copy-on-write memory pages for free.
An earlier version of this module used `Pool(initializer=...)` to
rebuild the graph *inside* each worker after fork - confirmed via
direct `ps` process inspection to cause 4 workers to race to
independently re-parse the whole corpus simultaneously, each
ballooning to 1.8-4.4GB RSS with low CPU%, a thundering-herd pattern,
not real parallel work. Fixed here.

**Copy-on-write is only as good as what workers never write**
(Decision 4 detail 2): `_prepare_shared_state` doesn't just build the
graph - it also *exercises* the lazy caches that would otherwise be
populated (i.e. written to) lazily inside a worker on first use:
`ConcreteGraphBuilder.calls_graph` (a `@property`, lazily computed and
cached onto `self._calls_graph_cache` on first access - see that
property's own docstring), and one full `PrismEngineCache.retrieve()`
call, which populates every *repo-keyed* session cache Steps 2-4 added
(`continuous_dijkstra._GRAPH_CACHE`, `causal_weights._CAUSAL_EDGES_
CACHE`/`_DATA_FLOW_EDGES_CACHE`/`_GUARD_INDICATOR_EDGES_CACHE`) before
any fork happens. The one exception, by necessity: `continuous_
dijkstra._DISTANCE_CACHE` is *seed*-keyed (Step 3's own binding
constraint - see that module's docstring for why it must never be
keyed without the seed), so it cannot be exhaustively pre-warmed for
every seed a worker might see; each worker's own first Dijkstra run
for a *new* seed still writes its own entry there. This is a small,
bounded, expected divergence (a handful of `{node: float}` dicts, not
the ~50MB+ parsed corpus) - not a rebuild of the shared graph itself.

One `Cell` = one `(task, budget)` pair. A worker runs *every engine*
for that cell in one call, preserving `_build_engines`'s own Oracle-
first-within-a-cell ordering (`fpr_oracle` needs the Oracle's selected
set before any other engine's turn *for that same task/budget* - see
that function's own docstring) - only the cells themselves are spread
across workers, never split mid-cell.

**`DEFAULT_WORKERS = 2`, not 8 or 4 - a real, measured finding, not a
guess** (Decision 4, post-fix investigation): on this 4-core
environment, a 4-worker pool - despite forking correctly (workers
forked within 0.7ms of each other, confirmed via a per-worker
fork-timestamp probe) - hung indefinitely processing 4 real tasks: zero
cells completed within a 10-minute hard cap, no exception, no CPU
pegging (workers blocked, not spinning). The SQLite-connection-
inheritance hypothesis was tested directly and ruled out (`/proc/<pid>/
fd` on the parent immediately before `Pool()` construction shows zero
open `.db`/`.prism` file descriptors - both `prism.runtime.index_cache`
and `prism.cache.sqlite_cache` close every connection in a `finally`
block, confirmed by reading both modules). The root cause was not
identified via `py-spy` profiling within the 45-minute investigation
time-box - profiling the hang was skipped once the cheaper 2-worker
path (below) succeeded, per that time-box's own explicit sequencing.

2 workers, by contrast, completed the identical 4-task/5-engine/
1-budget sweep in 107-110s across two independent runs (no hang),
producing results byte-identical to sequential execution (verified
field-by-field: `selected_symbols`, `diagnostics`, `ground_truth_
symbols` for all 20 records), for a measured 1.68x speedup over
sequential (180.94s) on the same 4 tasks. This container has exactly 4
CPU cores (`nproc`) - a 4-worker pool leaves the parent process (still
alive, blocked in `imap_unordered`) with no spare core, which is a
plausible but **unconfirmed** contributing factor, not a verified root
cause. The 4-worker (and untested 8-worker) hang is a known, open
issue, deferred to v1.2 - do not raise `DEFAULT_WORKERS` without first
getting a real `py-spy dump` stack from a hung worker; guessing at a
fix here already failed twice (see the two reverted designs this
module's own git history holds).
"""
from __future__ import annotations

import multiprocessing as mp
import os
import resource
import time
from dataclasses import dataclass

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.base import selected_symbols
from benchmarks.engines.prism_engine_cache import PrismEngineCache
from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.ground_truth.schema import EvaluationTask
from benchmarks.metrics.fcc import compute_corpus_feature_stats
from benchmarks.reporting.report_generator import EvaluationRun, TaskRunRecord
from benchmarks.runner import ORACLE_ENGINE_NAME, _build_engines, _ground_truth_universe, compute_diagnostics

# DEFAULT_WORKERS = 2
#
# 4-worker hangs on this container (4 cores). Fork itself is healthy
# (0.7ms spread across workers), SQLite FD inheritance is ruled out
# (no open .db FDs at fork time, both cache modules close in finally),
# and the hang is post-fork. Root cause unidentified as of commit 277896c.
#
# Do NOT raise this to 4+ without first capturing a py-spy dump from a
# live hung worker. See reports/pilot/methodology.md for the 45-minute
# investigation summary.
#
# 2 workers verified: 1.68x speedup, byte-identical output, on 4-task
# sweep against Django. 107s vs 180s sequential.
DEFAULT_WORKERS = 2


def _require_fork_context() -> "mp.context.ForkContext":
    """Never silently falls back to `spawn` - see this module's own
    docstring, Decision 4 detail 1."""
    if "fork" not in mp.get_all_start_methods():
        raise RuntimeError(
            "benchmarks.parallel_runner requires the 'fork' multiprocessing start method "
            f"(copy-on-write sharing of the pre-built graph) - this platform only offers "
            f"{mp.get_all_start_methods()!r}. Refusing to silently fall back to 'spawn' "
            "(which would pickle-and-rebuild per worker, defeating this module's entire design). "
            "Stop and report - do not force a workaround."
        )
    return mp.get_context("fork")


#: Module-level globals populated ONCE by `_prepare_shared_state`, in
#: the main process, before the fork `Pool` is ever constructed - every
#: forked worker inherits these via copy-on-write, never rebuilds them,
#: never receives them as a pickled function argument.
_shared_repo_path: str | None = None
_shared_builder: ConcreteGraphBuilder | None = None
_shared_feature_stats = None


def _prepare_shared_state(repo_path: str, warm_up_seed: str | None = None) -> None:
    """Builds the graph once and exercises every lazy, repo-keyed cache
    that a worker would otherwise populate (write to) on first use -
    see this module's own docstring, Decision 4 detail 2. `warm_up_seed`
    should be any real seed symbol valid for `repo_path` - its own
    seed-keyed distance-cache entry is incidental (every worker still
    populates its own per-seed entries), the point is exercising the
    four *repo-keyed* caches this one retrieve() call touches.
    """
    global _shared_repo_path, _shared_builder, _shared_feature_stats
    _shared_repo_path = repo_path
    _shared_builder, _tag_matrix = build_pipeline(repo_path)
    _shared_builder.calls_graph  # noqa: B018 - force the lazy @property cache to populate now, not inside a forked worker
    _shared_feature_stats = compute_corpus_feature_stats(_shared_builder)

    warm = PrismEngineCache()
    warm.index(repo_path)  # populates PrismEngineCache's own class-level _process_graph_cache (Gap 5)
    if warm_up_seed is not None:
        warm.retrieve(warm_up_seed, 4000)  # populates the four repo-keyed session caches (Steps 2/4)


@dataclass(frozen=True)
class Cell:
    task: EvaluationTask
    budget: int
    oracle_packages_path: str | None = None
    use_pragmatic_oracle: bool = False


def _process_cell(cell: Cell) -> list[TaskRunRecord]:
    if _shared_builder is None or _shared_repo_path is None:
        raise RuntimeError(
            "parallel_runner's shared state was never prepared in the main process before forking - "
            "call _prepare_shared_state(repo_path) before constructing the Pool"
        )

    engines = _build_engines(cell.task, cell.oracle_packages_path, cell.use_pragmatic_oracle)
    oracle_selected: set[str] | None = None
    records: list[TaskRunRecord] = []

    for engine in engines:
        try:
            engine.index(_shared_repo_path)
        except Exception:
            continue
        try:
            pkg = engine.retrieve(cell.task.seed_symbol, cell.budget)
        except Exception:
            continue

        candidate_symbols = selected_symbols(pkg)
        if engine.name == ORACLE_ENGINE_NAME:
            oracle_selected = candidate_symbols

        records.append(
            TaskRunRecord(
                task_id=cell.task.task_id,
                task_type=cell.task.task_type,
                repo=cell.task.repo,
                engine_name=engine.name,
                budget_tokens=cell.budget,
                tsr_scores=[],  # parallel harness never makes a paid LLM call - dry-run diagnostics only
                diagnostics=compute_diagnostics(pkg, cell.task, _shared_feature_stats, oracle_selected=oracle_selected),
                selected_symbols=sorted(candidate_symbols),
                ground_truth_symbols=sorted(_ground_truth_universe(cell.task)),
            )
        )
    return records


def report_worker_rss_kb() -> tuple[int, int]:
    """`(pid, peak_rss_kb)` for whichever worker process runs this -
    `ru_maxrss` is already the peak (not current) resident set size in
    KB on Linux. Exported (not `_`-prefixed) so a caller can `pool.map`
    it directly to empirically verify copy-on-write is actually holding
    (Decision 4 detail 2's own required verification), not just assumed
    from reading the code."""
    return os.getpid(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def run_evaluation_parallel(
    repo: str,
    budgets: list[int],
    tasks_dir: str,
    workers: int = DEFAULT_WORKERS,
    oracle_packages_path: str | None = None,
    use_pragmatic_oracle: bool = False,
    task_limit: int | None = None,
) -> tuple[EvaluationRun, float]:
    """Returns `(run, wall_time_seconds)` - `wall_time_seconds` measures
    only the parallel sweep itself (cells processed by the pool), not
    `_prepare_shared_state`'s own one-time warm-up in the main process
    (reported separately by the caller). `task_limit` restricts to the
    first N accepted tasks (for the required 4-task smoke test) - `None`
    runs every accepted task for `repo`."""
    ctx = _require_fork_context()

    repo_path = str(resolve(repo))
    load_result = load_tasks_from_dir(tasks_dir)
    tasks = [t for t in load_result.accepted if t.repo == repo]
    if task_limit is not None:
        tasks = tasks[:task_limit]

    warm_up_seed = tasks[0].seed_symbol if tasks else None
    _prepare_shared_state(repo_path, warm_up_seed=warm_up_seed)

    cells = [
        Cell(task=task, budget=budget, oracle_packages_path=oracle_packages_path, use_pragmatic_oracle=use_pragmatic_oracle)
        for task in tasks
        for budget in budgets
    ]

    run = EvaluationRun()
    t0 = time.time()
    with ctx.Pool(processes=workers) as pool:
        for records in pool.imap_unordered(_process_cell, cells):
            run.records.extend(records)
    wall_time = time.time() - t0
    return run, wall_time
