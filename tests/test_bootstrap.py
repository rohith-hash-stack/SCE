import math
from types import SimpleNamespace

import numpy as np
import pytest

from harness.scoring.bootstrap import (bootstrap_ci, holm_bonferroni, paired_difference,
                                       pairwise_comparisons, resample_plan, task_values)


def _s(arm, repo, tid, v, tt="T2_localization", seed=0):
    return SimpleNamespace(arm=arm, repo_id=repo, task_id=tid, task_type=tt, seed=seed, tsr=v,
                           task_specific={"recall_at_5": v})


def _arm(arm, vals, repos=("fastapi", "django")):
    return [_s(arm, r, f"{r}_t{i}", v) for r in repos for i, v in enumerate(vals)]


def test_plan_is_identical_across_arms_paired():
    a = task_values(_arm("arm1", [1, 0, 1, 1]), "tsr")
    b = task_values(_arm("arm5", [0, 0, 1, 0]), "tsr")
    tasks_a = {r: sorted(v) for r, v in a.items()}
    tasks_b = {r: sorted(v) for r, v in b.items()}
    pa, pb = resample_plan(tasks_a, 200, 7), resample_plan(tasks_b, 200, 7)
    assert pa.keys() == pb.keys()
    for repo in pa:
        assert np.array_equal(pa[repo], pb[repo])        # identical resampled indices per rep
        assert pa[repo].shape == (200, 4)                # tasks resampled within this repo only


def test_repos_are_fixed_not_resampled():
    # repo A always 1, repo B always 0, equal size -> every replicate mean is exactly 0.5
    scores = [_s("arm1", "A", f"a{i}", 1.0) for i in range(5)] + [_s("arm1", "B", f"b{i}", 0.0) for i in range(5)]
    mean, lo, hi = bootstrap_ci(scores, "tsr", n_reps=500)
    assert mean == lo == hi == 0.5


def test_seeds_are_averaged_per_task():
    scores = [_s("arm1", "A", "t1", v, seed=k) for k, v in enumerate([1, 0, 1])] + [_s("arm1", "A", "t2", 0.0)]
    assert task_values(scores, "tsr") == {"A": {"t1": pytest.approx(2 / 3), "t2": 0.0}}


def test_ci_converges_on_100_synthetic_scores():
    rng = np.random.default_rng(0)
    scores = [_s("arm1", "R", f"t{i}", float(rng.random() < 0.6)) for i in range(100)]
    mean, lo, hi = bootstrap_ci(scores, "tsr", n_reps=4000)
    assert lo < mean < hi and (hi - lo) < 0.25
    _, lo2, hi2 = bootstrap_ci(scores, "tsr", n_reps=4000, seed=99)
    assert abs(lo - lo2) < 0.03 and abs(hi - hi2) < 0.03        # stable across bootstrap seeds


def test_within_type_only():
    mixed = [_s("arm1", "R", "a", 1.0), _s("arm1", "R", "b", 1.0, tt="T5_blast_radius")]
    with pytest.raises(ValueError, match="within one task type"):
        bootstrap_ci(mixed, "tsr")


def test_paired_difference_and_nan_tasks():
    a = _arm("arm1", [1, 1, 1, 1, 0])
    b = _arm("arm5", [0, 0, 0, 1, 0])
    r = paired_difference(a, b, "tsr", n_reps=2000)
    assert r.diff == pytest.approx(0.6) and r.ci_low > 0 and r.p_value < 0.05 and r.n_tasks == 10
    b[0].tsr = float("nan")                                    # task not applicable for arm5
    assert paired_difference(a, b, "tsr", n_reps=200).n_tasks == 9
    assert math.isnan(bootstrap_ci([_s("x", "R", "t", float("nan"))], "tsr")[0])
    assert bootstrap_ci(a, "recall_at_5", n_reps=100)[0] == pytest.approx(0.8)   # task_specific metric


def test_holm():
    adj = holm_bonferroni([0.01, 0.04, 0.03, float("nan")])
    assert adj[0] == pytest.approx(0.03) and adj[2] == pytest.approx(0.06) and adj[1] == pytest.approx(0.06)
    assert math.isnan(adj[3])
    res = pairwise_comparisons({"a": _arm("a", [1, 1, 1]), "b": _arm("b", [0, 0, 0]), "c": _arm("c", [1, 0, 1])},
                               "tsr", n_reps=500)
    assert len(res) == 3 and all(r.p_holm >= r.p_value for r in res)
