"""`feature/two-pass-root-imports-wiring` (Phase D, Category 5): the
`root_imports`-gated Turn 2a/2b/3 branch woven into `run_two_pass_
benchmark.run_two_pass_cell`, alongside its existing manual Turn 1/Turn
2 orchestration - never a call to `PrismEngine.retrieve_two_or_three_
pass` itself (see that function's own docstring for why).

Uses the real, pinned, already-cached Express corpus (fast - unlike
`tests/test_two_pass_benchmark.py`'s Django-corpus integration test,
this doesn't need `@pytest.mark.slow`) for the true end-to-end cases,
plus small synthetic fixtures for the pure-function pieces.
"""
from __future__ import annotations

import json

from benchmarks.corpora.resolver import resolve
from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.run_two_pass_benchmark import (
    _external_manifest_for_task,
    _hydrate_external,
    _request_all_external_candidates,
    run_two_pass_cell,
)
from pathlib import Path

from prism.engine import PrismEngine

EXPRESS_TASKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "ground_truth" / "tasks" / "express"


def _load_task(task_id: str):
    result = load_tasks_from_dir(EXPRESS_TASKS_DIR)
    assert not result.rejected, f"ground-truth tasks failed to load: {result.rejected}"
    tasks = {t.task_id: t for t in result.accepted}
    return tasks[task_id]


def _express_engine() -> PrismEngine:
    return PrismEngine.from_repo(str(resolve("express")))


# --------------------------------------------------------------------- #
# Schema: `root_imports` is additive and defaults to empty.
# --------------------------------------------------------------------- #
#: Every Express task that legitimately declares a real, verified
#: Category-5 external dependency (`feature/express-task-expansion`
#: grew this from Task 7 alone to ten) - kept as an explicit allowlist
#: rather than inferring it from the loaded tasks themselves, so this
#: test still catches a plain internal task accidentally picking up a
#: stray `root_imports` entry during authoring.
_CATEGORY_5_EXPRESS_TASK_IDS = frozenset({
    "express_t02_007_etag_external_dependency",
    "express_t02_009_content_negotiation",
    "express_t02_012_cookie_signing",
    "express_t02_014_top_level_dispatch",
    "express_t02_015_path_to_regexp_alias_mismatch",
    "express_t02_016_send_file_streaming",
    "express_t02_017_content_disposition",
    "express_t02_018_type_is_alias_mismatch",
    "express_t02_019_range_parser",
    "express_t02_020_query_string_parsing",
})


def test_every_non_category_5_express_task_has_empty_root_imports():
    result = load_tasks_from_dir(EXPRESS_TASKS_DIR)
    for task in result.accepted:
        if task.task_id not in _CATEGORY_5_EXPRESS_TASK_IDS:
            assert task.root_imports == [], task.task_id


def test_task_7_declares_the_real_root_import():
    task = _load_task("express_t02_007_etag_external_dependency")
    assert task.root_imports == ["etag"]


# --------------------------------------------------------------------- #
# `_request_all_external_candidates` - the dry-run's own zero-LLM Turn
# 2b stand-in.
# --------------------------------------------------------------------- #
def test_request_all_external_candidates_parses_every_manifest_row():
    manifest = (
        "<external_candidate_index>\n"
        "etag.index.etag|external|function|declare function etag(entity, options);\n"
        "cookie.index.parse|external|function|declare function parse(str);\n"
        "</external_candidate_index>"
    )
    assert _request_all_external_candidates(manifest) == ["etag.index.etag", "cookie.index.parse"]


def test_request_all_external_candidates_empty_manifest_returns_empty():
    manifest = "<external_candidate_index>\n</external_candidate_index>"
    assert _request_all_external_candidates(manifest) == []


# --------------------------------------------------------------------- #
# `_external_manifest_for_task` / `_hydrate_external` - real, against
# the real Express corpus.
# --------------------------------------------------------------------- #
def test_external_manifest_for_task_resolves_the_real_etag_dependency():
    engine = _express_engine()
    task = _load_task("express_t02_007_etag_external_dependency")
    manifest_text, candidates = _external_manifest_for_task(engine, task, [])
    assert candidates == {"etag.index.etag"}
    assert "declare function etag" in manifest_text


def test_external_manifest_for_task_is_empty_with_no_root_imports():
    engine = _express_engine()
    task = _load_task("express_t02_001_app_bootstrap_defaults")
    _manifest_text, candidates = _external_manifest_for_task(engine, task, [])
    assert candidates == set()


def test_hydrate_external_appends_the_real_node_to_the_package():
    engine = _express_engine()
    task = _load_task("express_t02_007_etag_external_dependency")
    pkg, _skipped = engine.retrieve_requested(task.seed_symbol, 2000, [], {task.seed_symbol})
    _manifest_text, candidates = _external_manifest_for_task(engine, task, [])
    pkg2, skipped = _hydrate_external(engine, pkg, sorted(candidates), 500)
    assert skipped == []
    ids = [n.id for n in pkg2.nodes]
    assert "etag.index.etag" in ids
    assert "lib.utils.createETagGenerator" in ids


def test_hydrate_external_reports_a_hallucinated_request_as_skipped():
    engine = _express_engine()
    task = _load_task("express_t02_007_etag_external_dependency")
    pkg, _skipped = engine.retrieve_requested(task.seed_symbol, 2000, [], {task.seed_symbol})
    pkg2, skipped = _hydrate_external(engine, pkg, ["not.a.real.external.symbol"], 500)
    assert "not.a.real.external.symbol" in skipped
    assert pkg2.nodes == pkg.nodes


# --------------------------------------------------------------------- #
# End-to-end: `run_two_pass_cell` in `--dry-run` mode (client=None).
# --------------------------------------------------------------------- #
def test_dry_run_cell_resolves_the_real_external_dependency():
    engine = _express_engine()
    task = _load_task("express_t02_007_etag_external_dependency")
    result = run_two_pass_cell(engine, None, task, 2000, None, None)
    assert result.external_candidate_count == 1
    assert result.external_requested_count == 1
    assert result.external_skipped_hallucinated == []
    assert result.tsr is None  # dry-run: no LLM call happened


def test_dry_run_cell_for_a_non_category_5_task_has_zero_external_fields():
    engine = _express_engine()
    task = _load_task("express_t02_001_app_bootstrap_defaults")
    result = run_two_pass_cell(engine, None, task, 2000, None, None)
    assert result.external_candidate_count == 0
    assert result.external_requested_count == 0
    assert result.external_skipped_hallucinated == []


def test_dry_run_cli_end_to_end_against_task_7(tmp_path):
    """The exact real command the user ran to verify this branch -
    `python -m benchmarks.run_two_pass_benchmark --repo express --tasks
    express_t02_007_etag_external_dependency --dry-run`, checked here as
    a real subprocess-free, in-process CLI call so it's covered by the
    fast suite rather than only ever run by hand."""
    from benchmarks.run_two_pass_benchmark import main

    output_path = tmp_path / "results.json"
    exit_code = main(
        [
            "--repo", "express",
            "--tasks", "express_t02_007_etag_external_dependency",
            "--dry-run",
            "--budgets", "2000", "4000",
            "--output", str(output_path),
        ]
    )
    assert exit_code == 0
    results = json.loads(output_path.read_text())
    assert len(results) == 2
    for cell in results:
        assert cell["external_candidate_count"] == 1
        assert cell["external_requested_count"] == 1
        assert cell["external_skipped_hallucinated"] == []


# --------------------------------------------------------------------- #
# `run_two_pass_evaluation`'s own internal_budget/external_budget split
# is gated strictly on `root_imports` - never touches a task without it.
# --------------------------------------------------------------------- #
def test_non_category_5_task_uses_the_full_budget_internally(monkeypatch):
    """A task with no `root_imports` must retrieve against the *full*
    requested budget, not a reduced `split_budget_for_external`
    fraction of it - this feature must not silently shrink every other
    task's own internal budget. Captures the real `budget` argument
    `retrieve_requested` is actually called with, rather than inferring
    it indirectly from the packed result."""
    engine = _express_engine()
    task = _load_task("express_t02_001_app_bootstrap_defaults")
    seen_budgets = []
    original = engine.retrieve_requested

    def spy(seed_id, budget, *args, **kwargs):
        seen_budgets.append(budget)
        return original(seed_id, budget, *args, **kwargs)

    monkeypatch.setattr(engine, "retrieve_requested", spy)
    run_two_pass_cell(engine, None, task, 2000, None, None)
    assert seen_budgets == [2000]


def test_category_5_task_splits_the_budget_for_external(monkeypatch):
    """A task that *does* declare `root_imports` must retrieve
    internally against `split_budget_for_external(budget)`'s own
    internal share, not the full budget - the same real up-front
    reservation `PrismEngine.retrieve_two_or_three_pass` itself applies,
    now mirrored here."""
    from prism.packer.submodular_knapsack import split_budget_for_external

    engine = _express_engine()
    task = _load_task("express_t02_007_etag_external_dependency")
    expected_internal, _expected_external = split_budget_for_external(2000)
    seen_budgets = []
    original = engine.retrieve_requested

    def spy(seed_id, budget, *args, **kwargs):
        seen_budgets.append(budget)
        return original(seed_id, budget, *args, **kwargs)

    monkeypatch.setattr(engine, "retrieve_requested", spy)
    run_two_pass_cell(engine, None, task, 2000, None, None)
    assert seen_budgets == [expected_internal]
