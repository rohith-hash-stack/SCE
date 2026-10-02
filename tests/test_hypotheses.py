import math

import pandas as pd
import pytest

from harness.reporting.hypotheses import HYPOTHESES, evaluate_all, evaluate_hypothesis


def _df(rows):
    return pd.DataFrame(rows, columns=["arm", "task_type", "task_id", "repo_id", "task_success", "budget_utilization"])


def _rows(arm, vals, tt="T5_blast_radius", metric_util=0.5):
    return [(arm, tt, f"t{i}", "django" if i % 2 else "fastapi", float(v), metric_util) for i, v in enumerate(vals)]


def test_spec_hypotheses_present_and_wellformed():
    ids = {h["id"]: h for h in HYPOTHESES}
    assert ids["H2"]["min_effect_pp"] == 15 and ids["H1"]["status"] == "specified"
    for h in HYPOTHESES:
        assert {"id", "claim", "task_type", "arms", "metric", "direction", "confidence_required", "status"} <= set(h)
        assert h["arms"][0] in h["direction"] and h["arms"][1] in h["direction"]


def test_effect_threshold_and_direction():
    h2 = next(h for h in HYPOTHESES if h["id"] == "H2")
    big = _df(_rows("arm5", [1] * 20) + _rows("arm1", [0] * 20))
    r = evaluate_hypothesis(h2, big, n_reps=500)
    assert r["confirmed"] is True and r["effect_pp"] == pytest.approx(100.0)
    small = _df(_rows("arm5", [1] * 2 + [0] * 18) + _rows("arm1", [0] * 20))     # +10pp < 15pp
    assert evaluate_hypothesis(h2, small, n_reps=500)["confirmed"] is False
    h7 = next(h for h in HYPOTHESES if h["id"] == "H7")                            # "<" direction
    lower = _df([("arm5", "T2_localization", f"t{i}", "fastapi", 1.0, 0.3) for i in range(10)]
                + [("arm1", "T2_localization", f"t{i}", "fastapi", 1.0, 0.9) for i in range(10)])
    r7 = evaluate_hypothesis(h7, lower, n_reps=300)
    assert r7["confirmed"] is True and r7["effect_pp"] == pytest.approx(60.0)


def test_no_data_is_reported_not_guessed():
    r = evaluate_hypothesis(HYPOTHESES[0], _df([]), n_reps=10)
    assert r["confirmed"] is None and r["status"] == "no_data" and math.isnan(r["effect_pp"])
    assert len(evaluate_all(_df([]), n_reps=10)) == len(HYPOTHESES)
