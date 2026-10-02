import math
from types import SimpleNamespace

import pandas as pd
import pytest

from harness.reporting.summary import (assert_reporting_rules, per_type_sample_sizes, retrieval_efficiency,
                                       retrieval_lift, summary_table)


def test_lift_and_efficiency():
    assert retrieval_lift(0.6, 0.4) == pytest.approx(0.5)
    assert math.isnan(retrieval_lift(0.6, 0.0))
    assert retrieval_efficiency(0.6, 0.2, 1.0) == pytest.approx(0.5)
    assert math.isnan(retrieval_efficiency(0.6, 0.5, 0.5))


def _s(arm, tid, tsr, tt="T5_blast_radius", repo="django"):
    spec = {"recall_at_5": tsr, "false_negative_rate": 1 - tsr} if tt == "T5_blast_radius" else {"acc_at_5": tsr}
    base = dict(arm=arm, task_id=tid, task_type=tt, repo_id=repo, seed=42, tsr=tsr, task_success=tsr >= 0.5,
                task_specific=spec)
    for m in ("uniform_cpi", "cleanliness", "context_precision", "context_recall", "mrr_at_5", "mrr_at_10",
              "ndcg_at_5", "ndcg_at_10", "map", "p_at_5", "r_at_5", "f1_at_5", "relevant_token_density",
              "hallucination_rate", "budget_utilization"):
        base[m] = 0.5
    return SimpleNamespace(**base)


def test_summary_is_stratified_with_cis_and_no_overall_row():
    res = ([_s("arm0", f"t{i}", 0.2) for i in range(4)] + [_s("arm1", f"t{i}", 0.5) for i in range(4)]
           + [_s("oracle", f"t{i}", 0.8) for i in range(4)] + [_s("arm1", "f1", 1.0, tt="T2_localization", repo="fastapi")])
    df = summary_table(res, n_reps=200)
    assert set(map(tuple, df[["arm", "task_type", "corpus"]].values)) == {
        ("arm0", "T5_blast_radius", "django"), ("arm1", "T5_blast_radius", "django"),
        ("oracle", "T5_blast_radius", "django"), ("arm1", "T2_localization", "fastapi")}
    r1 = df[(df.arm == "arm1") & (df.task_type == "T5_blast_radius")].iloc[0]
    assert r1.mean_tsr == pytest.approx(0.5) and r1.retrieval_lift == pytest.approx(1.5)
    assert r1.retrieval_efficiency == pytest.approx(0.5) and r1.n_tasks == 4
    assert "mean_recall_at_5" in df.columns and "ci_low_recall_at_5" in df.columns
    t2 = df[df.task_type == "T2_localization"].iloc[0]
    assert pd.isna(t2.mean_recall_at_5)                      # T5 metric not computed for a T2 row
    assert not any("overall" in c or "composite" in c for c in df.columns)


def test_rules_reject_overall_rows_and_bare_means():
    df = pd.DataFrame([{"arm": "overall", "task_type": "T2_localization", "corpus": "x", "n_tasks": 1}])
    with pytest.raises(AssertionError):
        assert_reporting_rules(df)
    df = pd.DataFrame([{"arm": "arm1", "task_type": "T2_localization", "corpus": "x", "n_tasks": 1, "mean_tsr": 0.5}])
    with pytest.raises(AssertionError, match="CI"):
        assert_reporting_rules(df)


def test_sample_sizes():
    df = pd.DataFrame({"arm": ["a", "a", "b"], "task_type": ["T2_localization"] * 3, "corpus": ["c"] * 3,
                       "task_id": ["t1", "t1", "t2"]})
    n = per_type_sample_sizes(df)
    assert n[n.arm == "a"].iloc[0].n_cells == 2 and n[n.arm == "a"].iloc[0].n_tasks == 1
