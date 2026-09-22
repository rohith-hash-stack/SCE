"""Pilot-4 prep, Script 2: `scripts/apply_gate.py`. `scripts/` isn't an
installed package, so this imports the script directly via its file
path (same pattern `tests/test_merge_pilot_checkpoints.py` already
uses). Needs `benchmarks.reporting.bootstrap` (numpy) - real,
deterministic bootstrap runs via a fixed seed, not mocked, since the
whole point is proving the real statistical machinery gives the right
decision on unambiguous synthetic data.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "apply_gate.py"
_spec = importlib.util.spec_from_file_location("apply_gate", _SCRIPT_PATH)
apply_gate = importlib.util.module_from_spec(_spec)
sys.modules["apply_gate"] = apply_gate
_spec.loader.exec_module(apply_gate)

compute_gate_metrics = apply_gate.compute_gate_metrics
apply_decision_rule = apply_gate.apply_decision_rule

BASELINE = "baseline_bfs_bidirectional"
PRISM = "prism_two_pass"


def _cell(task_id, budget, seed, engine, tsr, cpi_answer):
    return {
        "task_id": task_id, "engine": engine, "budget": budget, "seed": seed,
        "tsr": tsr, "cpi_retrieval": cpi_answer, "cpi_answer": cpi_answer, "fpr_gt": 0.0, "model": "x",
    }


def _paired_cells(n: int, baseline_tsr: float, prism_tsr: float, baseline_cpi: float, prism_cpi: float) -> dict:
    """`n` (task_id, budget, seed) pairs, each present for both
    `BASELINE` and `PRISM`, with the given constant per-metric values -
    zero variance across pairs, so the bootstrap CI is a tight point
    around the real delta regardless of resample draw (never flaky)."""
    cells = {}
    for i in range(n):
        task_id, budget, seed = f"task_{i}", 4000, i
        cells[f"{task_id}|{BASELINE}|{budget}|{seed}"] = _cell(task_id, budget, seed, BASELINE, baseline_tsr, baseline_cpi)
        cells[f"{task_id}|{PRISM}|{budget}|{seed}"] = _cell(task_id, budget, seed, PRISM, prism_tsr, prism_cpi)
    return cells


def test_gate_passes_when_both_deltas_clear():
    """Both TSR and CPI_answer clear the default 15pp threshold with a
    real margin (100pp, zero variance) - PASS."""
    cells = _paired_cells(20, baseline_tsr=0.0, prism_tsr=1.0, baseline_cpi=0.0, prism_cpi=1.0)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "PASS"
    assert gate_metrics["tsr_excludes_zero"]
    assert gate_metrics["cpi_excludes_zero"]


def test_gate_expands_when_one_below_threshold():
    """TSR clears the threshold (20pp, real effect); CPI_answer sits at
    10pp - not below the 5pp STOP floor (so not MIXED), not clearing
    its own 15pp threshold either (so not PASS) - genuinely ambiguous,
    EXPAND."""
    cells = _paired_cells(20, baseline_tsr=0.0, prism_tsr=0.20, baseline_cpi=0.0, prism_cpi=0.10)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "EXPAND"
    assert gate_metrics["delta_tsr_pp"] == pytest.approx(20.0)
    assert gate_metrics["delta_cpi_pp"] == pytest.approx(10.0)


def test_gate_stops_when_both_below_5():
    """Both deltas are 0pp (prism identical to baseline) - well below
    the 5pp STOP floor for both metrics."""
    cells = _paired_cells(20, baseline_tsr=0.5, prism_tsr=0.5, baseline_cpi=0.5, prism_cpi=0.5)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "STOP"
    assert gate_metrics["delta_tsr_pp"] == 0.0
    assert gate_metrics["delta_cpi_pp"] == 0.0


def test_gate_reports_undetermined_when_field_missing():
    """Every paired cell has tsr=None (a real, if degenerate, "the
    field is missing" case, not a wholly absent engine) - zero usable
    paired deltas, compute_gate_metrics returns None, UNDETERMINED."""
    cells = {}
    for i in range(5):
        task_id, budget, seed = f"task_{i}", 4000, i
        cells[f"{task_id}|{BASELINE}|{budget}|{seed}"] = _cell(task_id, budget, seed, BASELINE, None, 0.5)
        cells[f"{task_id}|{PRISM}|{budget}|{seed}"] = _cell(task_id, budget, seed, PRISM, None, 0.5)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "UNDETERMINED"
    assert gate_metrics is None


def test_gate_undetermined_when_prism_engine_entirely_absent():
    """No prism_two_pass cells at all (e.g. a merge run against
    pilot-1's own single-pass checkpoint alone, no two-pass file) -
    also UNDETERMINED, the scenario this whole gate closes out on."""
    cells = {}
    for i in range(5):
        task_id, budget, seed = f"task_{i}", 4000, i
        cells[f"{task_id}|{BASELINE}|{budget}|{seed}"] = _cell(task_id, budget, seed, BASELINE, 0.5, 0.5)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "UNDETERMINED"


def test_gate_reports_mixed_when_tsr_strong_cpi_weak():
    """ΔTSR clears its own threshold with a real margin (30pp, CI
    excludes zero); ΔCPI_answer is below the 5pp STOP floor (2pp) -
    one metric shows a clear effect, the other shows none. MIXED, not
    EXPAND (an earlier version of this script had no MIXED outcome and
    folded this exact case into an EXPAND catch-all)."""
    cells = _paired_cells(20, baseline_tsr=0.0, prism_tsr=0.30, baseline_cpi=0.0, prism_cpi=0.02)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "MIXED"
    assert "ΔTSR" in reason and "clear effect" in reason
    assert "ΔCPI_answer" in reason


def test_gate_reports_mixed_when_cpi_strong_tsr_weak():
    """The mirror image: ΔCPI_answer clears its own threshold with a
    real margin (25pp, CI excludes zero); ΔTSR is below the 5pp STOP
    floor (1pp). Still MIXED, with the explanation naming CPI_answer as
    the metric that showed the effect this time - the reason string
    must correctly identify *which* metric was which, not just that
    the outcome is MIXED."""
    cells = _paired_cells(20, baseline_tsr=0.0, prism_tsr=0.01, baseline_cpi=0.0, prism_cpi=0.25)

    gate_metrics = compute_gate_metrics(cells, BASELINE, PRISM, n_resamples=10_000, bootstrap_seed=42)
    decision, reason = apply_decision_rule(gate_metrics, delta_tsr_threshold=15.0, delta_cpi_threshold=15.0)

    assert decision == "MIXED"
    assert "ΔCPI_answer" in reason and "clear effect" in reason
    assert "ΔTSR" in reason
