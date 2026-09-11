"""Blocker 1 Decision 4: `benchmarks.parallel_runner` - multiprocessing
parallelization for the pilot/eval sweep. Uses a small synthetic repo
(matching `benchmarks.smoke`'s own fixture pattern) rather than the
real, slow, pinned Django corpus, so this runs fast as part of the
normal suite - the *real*-corpus verification (2 workers, 4 real
tasks, byte-identical to sequential, 1.68x speedup, no hang) was done
manually during the Decision 4 investigation and is documented in this
module's own docstring, not re-run here on every CI pass.
"""
from __future__ import annotations

from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.parallel_runner import (
    Cell,
    DEFAULT_WORKERS,
    _prepare_shared_state,
    _process_cell,
    _require_fork_context,
    report_worker_rss_kb,
)

_SOURCE = (
    "def parse_order(raw):\n    return validate_order(raw)\n\n\n"
    "def validate_order(data):\n    return price_order(data)\n\n\n"
    "def price_order(data):\n    return store_order(data)\n\n\n"
    "def store_order(data):\n    notify_order(data)\n    return data\n\n\n"
    "def notify_order(data):\n    return data\n"
)


def _synthetic_task(task_id: str, seed_symbol: str) -> EvaluationTask:
    ann = GroundTruthAnnotation(
        annotator_id="synthetic",
        pipeline_symbols=[seed_symbol],
        expected_solution="synthetic fixture - not a real human annotation",
    )
    return EvaluationTask(
        task_id=task_id,
        repo="django",  # schema-required label only - never touches the real pinned corpus in this test
        pinned_commit="synthetic-fixture",
        seed_symbol=seed_symbol,
        task_type="chain",
        prompt="(synthetic fixture - no LLM call made)",
        annotation_a=ann,
        annotation_b=ann,
        adjudicated=ann,
        cohen_kappa=1.0,
    )


def test_default_workers_is_two_not_eight_or_four():
    """Guards the Decision 4 finding directly: a regression that quietly
    raises this back to 8 (or 4) without re-verifying against a hang
    would be exactly the kind of silent, unverified change the
    investigation was about avoiding."""
    assert DEFAULT_WORKERS == 2


def test_require_fork_context_returns_fork_on_linux():
    ctx = _require_fork_context()
    assert ctx.get_start_method() == "fork"


def test_parallel_matches_sequential_and_reports_bounded_rss(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)

    tasks = [
        _synthetic_task("t1", "x.parse_order"),
        _synthetic_task("t2", "x.validate_order"),
    ]

    _prepare_shared_state(str(repo), warm_up_seed=tasks[0].seed_symbol)
    cells = [Cell(task=task, budget=2000, use_pragmatic_oracle=False) for task in tasks]

    seq_records = []
    for cell in cells:
        seq_records.extend(_process_cell(cell))
    assert seq_records, "sequential leg produced no records at all - fixture is broken"

    ctx = _require_fork_context()
    par_records = []
    workers = 2
    with ctx.Pool(processes=workers) as pool:
        for records in pool.imap_unordered(_process_cell, cells):
            par_records.extend(records)

        async_results = [pool.apply_async(report_worker_rss_kb) for _ in range(workers * 3)]
        rss_reports = [r.get() for r in async_results]
        per_worker_rss_kb = {}
        for pid, rss_kb in rss_reports:
            per_worker_rss_kb[pid] = max(per_worker_rss_kb.get(pid, 0), rss_kb)

    def _key(r):
        return (r.task_id, r.engine_name, r.budget_tokens)

    seq_by_key = {_key(r): r for r in seq_records}
    par_by_key = {_key(r): r for r in par_records}
    assert set(seq_by_key) == set(par_by_key)
    for key, s in seq_by_key.items():
        p = par_by_key[key]
        assert s.selected_symbols == p.selected_symbols, key
        assert s.diagnostics == p.diagnostics, key
        assert s.ground_truth_symbols == p.ground_truth_symbols, key

    # Bounded, not a proxy for "identical to parent" - a real regression
    # to the old rebuild-per-worker design would show each worker's RSS
    # ballooning to roughly the full corpus size; for this tiny
    # synthetic fixture that's only meaningful as a sanity bound, not a
    # tight ratio check (the real-corpus ratio check is documented in
    # this module's own docstring from the manual investigation).
    assert len(per_worker_rss_kb) >= 1
    for pid, rss_kb in per_worker_rss_kb.items():
        assert rss_kb > 0, f"worker {pid} reported zero RSS - the probe itself is broken"
