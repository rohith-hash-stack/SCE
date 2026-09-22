"""Fast, no-corpus unit tests for `benchmarks.scripts.kaggle_full_pilot_sweep`'s
own aggregation/rendering logic - the retrieval/scoring machinery it
wraps (`run_two_pass_evaluation`) is already covered by `tests/
test_two_pass_benchmark.py`; this file only exercises what this module
adds on top: aggregation across seeds/budgets/tasks, strict-TSR
recomputation from raw response text, and the dry-run-cell (tsr=None)
exclusion rule.
"""
from __future__ import annotations

import json

from benchmarks.run_two_pass_benchmark import TwoPassCellResult
from benchmarks.scripts.kaggle_full_pilot_sweep import _aggregate, render_summary_report

PIPELINE = ["a.b.seed", "a.b.stage_two"]


def _cell(task_id="t1", budget=2000, seed=42, tsr=1.0, turn2_response=None, model="qwen2.5-coder:14b", cost=0.001) -> TwoPassCellResult:
    if turn2_response is None:
        turn2_response = json.dumps({"reasoning": "x", "symbols": PIPELINE})
    return TwoPassCellResult(
        task_id=task_id, budget=budget, seed=seed, tsr=tsr,
        cpi_turn1_selection=1.0, cpi_end_to_end=1.0, fpr_gt=0.0,
        candidate_count=5, requested_count=2, skipped_hallucinated=[],
        turn1_parsed_ok=True, turn1_prompt_tokens=100, turn2_prompt_tokens=200,
        completion_tokens=50, cost_usd=cost, model=model,
        turn1_response="{}", turn2_response=turn2_response,
    )


class TestAggregate:
    def test_aggregate_over_exact_match_cells(self):
        results = [_cell(seed=42), _cell(seed=43)]
        row = _aggregate(results, {"t1": PIPELINE}, "t1")
        assert row.n_cells == 2
        assert row.tsr_causal == 1.0
        assert row.tsr_strict == 1.0
        assert row.fpr_gt == 0.0
        assert row.mean_total_tokens == 100 + 200 + 50
        assert row.models_seen == ("qwen2.5-coder:14b",)

    def test_aggregate_recomputes_strict_tsr_independently_of_stored_causal_tsr(self):
        """A causal-only success (extra legitimate symbols beyond the
        pipeline) must show tsr_causal=1.0 but tsr_strict<1.0 - the
        granularity-trap distinction this whole sweep exists to surface,
        not something _aggregate should paper over."""
        thorough_response = json.dumps({"reasoning": "x", "symbols": PIPELINE + ["a.b.extra_legit_stage"]})
        results = [_cell(turn2_response=thorough_response, tsr=1.0)]
        row = _aggregate(results, {"t1": PIPELINE}, "t1")
        assert row.tsr_causal == 1.0
        assert row.tsr_strict == 0.0

    def test_aggregate_excludes_dry_run_cells_from_scored_metrics_but_still_counts_them(self):
        results = [_cell(tsr=None, turn2_response="", cost=None), _cell(tsr=1.0)]
        row = _aggregate(results, {"t1": PIPELINE}, "t1")
        assert row.n_cells == 2
        assert row.tsr_causal == 1.0  # only the scored cell counted

    def test_aggregate_on_empty_results_reports_n_and_none_metrics(self):
        row = _aggregate([], {}, "empty")
        assert row.n_cells == 0
        assert row.tsr_causal is None
        assert row.tsr_strict is None


class TestRenderSummaryReport:
    def test_report_contains_grand_total_per_task_and_per_task_budget_sections(self):
        results = [
            _cell(task_id="task_a", budget=2000, seed=42),
            _cell(task_id="task_a", budget=4000, seed=42),
            _cell(task_id="task_b", budget=2000, seed=42),
        ]
        pipeline_by_task = {"task_a": PIPELINE, "task_b": PIPELINE}
        report = render_summary_report(results, pipeline_by_task)
        assert "## Grand total" in report
        assert "## Per-task" in report
        assert "## Per task / budget" in report
        assert "task_a" in report
        assert "task_b" in report
        assert "task_a @ 2000" in report
        assert "task_a @ 4000" in report

    def test_report_handles_a_task_with_no_ground_truth_pipeline_lookup(self):
        """A stale/renamed task_id in the checkpoint that no longer
        matches anything in pipeline_by_task_id must not crash the
        report - score_debug against an empty pipeline is its own
        documented vacuous-truth case, not a KeyError."""
        results = [_cell(task_id="unknown_task")]
        report = render_summary_report(results, {})
        assert "unknown_task" in report
