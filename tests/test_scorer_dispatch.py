"""score() dispatch over all five task types, the Arm-0 NaN rule, T5
fractional recall, and the uniform-candidates rule."""
import math

import pytest

from harness.scoring import registry as R
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.scoring.fairness import RankingViolation
from harness.scoring.scorer import ScoreResult, score
from harness.tasks.schema import BlastRadiusTask, GroundTruth
from harness.tasks.synthetic import CALLERS, TARGET, synthetic_tasks


@pytest.fixture
def tasks(tmp_path):
    (tmp_path / "fastapi" / "dependencies").mkdir(parents=True)
    (tmp_path / "fastapi" / "dependencies" / "utils.py").write_text(
        "def get_typed_annotation(a, g):\n    return a\n"
        "def get_typed_signature(c):\n    return get_typed_annotation(c, {})\n"
        "def get_typed_return_annotation(c):\n    return get_typed_annotation(c, {})\n")
    return {t.task_type: t for t in synthetic_tasks(str(tmp_path))}


def _ctx(task, symbols_per_item, arm="arm1", budget=13_000):
    items = [DeliveredItem(f"src:{i}", "code " * 10, 10, i, "code_chunk", syms)
             for i, syms in enumerate(symbols_per_item, start=1)]
    return DeliveredContext(arm, task.task_id, items, 10 * len(items), budget, {})


def _ans(task, symbols, arm="arm1", text=""):
    return NormalizedAnswer(arm, task.task_id, text, text, symbols, "code_block", True, 5, 0.1)


def test_every_type_dispatches_and_every_metric_computes(tasks):
    for tt, task in tasks.items():
        ctx = _ctx(task, [[TARGET], CALLERS[:1]])
        res = score(task, ctx, _ans(task, [TARGET]))
        assert isinstance(res, ScoreResult) and res.task_type == tt
        row = res.to_dict()
        for m in R.universal_metrics():
            v = row[m]
            assert v is not None and (isinstance(v, bool) or isinstance(v, (int, float))), m
        for m in R.task_specific_metrics(tt):
            assert m in res.task_specific, (tt, m)
    assert tasks["T3_codegen"] and score(tasks["T3_codegen"], _ctx(tasks["T3_codegen"], [[]]),
                                         _ans(tasks["T3_codegen"], [])).task_specific["stub"] is True


def test_t1_without_judge_is_nan_not_crash_and_judge_is_used(tasks):
    t1 = tasks["T1_conceptual"]
    res = score(t1, _ctx(t1, [[TARGET]]), _ans(t1, [TARGET]))
    assert math.isnan(res.tsr) and res.task_success is False
    assert "must NOT be a Qwen model" in res.task_specific["judge_status"]
    judged = score(t1, _ctx(t1, [[TARGET]]), _ans(t1, [TARGET]),
                   judge=lambda t, a, c: {"faithfulness": 0.9, "answer_relevancy": 0.6})
    assert judged.tsr == 0.6 and judged.task_success


def test_t2_is_answer_based_and_acc5_is_only_a_diagnostic(tasks):
    t2 = tasks["T2_localization"]
    hit = score(t2, _ctx(t2, [[TARGET]]), _ans(t2, ["get_typed_annotation"]))   # bare-name match
    assert hit.tsr == 1.0 and hit.task_specific["acc_at_5_retrieval"] == 1.0
    deep = score(t2, _ctx(t2, [[], [], [], [], [], [TARGET]]), _ans(t2, [TARGET]))  # gold at rank 6
    assert deep.tsr == 1.0 and deep.task_success is True                       # answer named the gold
    assert deep.task_specific["acc_at_5_retrieval"] == 0.0                      # retrieval missed top-5
    miss = score(t2, _ctx(t2, [[TARGET]]), _ans(t2, ["fastapi.other"]))
    assert miss.tsr == 0.0 and miss.task_specific["acc_at_5_retrieval"] == 1.0


def _t2(tmp_path, gold):
    from harness.tasks.schema import LocalizationTask
    return LocalizationTask(task_id="t2x", repo_id="r", repo_root=str(tmp_path), query="Where is foo.bar?",
                            ground_truth=GroundTruth(pipeline_symbols=gold))


def test_arm0_can_score_nonzero_on_t2(tmp_path):
    """Arm 0 delivers no items but can still name a gold symbol from
    parametric knowledge. task_success must be able to return 1.0 for Arm 0
    on T2, otherwise Retrieval Lift is undefined."""
    task = _t2(tmp_path, ["foo.bar"])
    ctx = DeliveredContext("arm0", task.task_id, [], 0, 0, {})
    res = score(task, ctx, _ans(task, ["foo.bar"], arm="arm0"))
    assert res.task_success is True and res.tsr == 1.0
    assert res.task_specific["acc_at_5_retrieval"] == 0.0


def test_retrieval_lift_computable_on_t2_after_fix(tmp_path):
    """Regression: with Arm 0 right on at least one T2 task, the Arm 0 mean is
    non-zero, so Retrieval Lift has a non-zero denominator and is finite."""
    from harness.reporting.summary import summary_table
    results = []
    for i in range(4):
        task = _t2(tmp_path, [f"pkg.mod.f{i}"])
        task.task_id = f"t2_{i}"
        gold = task.ground_truth.pipeline_symbols[0]
        a0 = DeliveredContext("arm0", task.task_id, [], 0, 0, {})
        results.append(score(task, a0, _ans(task, [gold] if i == 0 else ["pkg.other"], arm="arm0")))
        a1 = DeliveredContext("arm1", task.task_id, [DeliveredItem("s", "c", 5, 1, "code_chunk", [gold])], 5, 13_000, {})
        results.append(score(task, a1, _ans(task, [gold] if i < 3 else ["pkg.other"], arm="arm1")))
    df = summary_table(results, n_reps=200)
    a0 = df[df.arm == "arm0"].iloc[0]
    a1 = df[df.arm == "arm1"].iloc[0]
    assert a0.mean_tsr == pytest.approx(0.25)                 # denominator non-zero
    assert math.isfinite(a1.retrieval_lift) and a1.retrieval_lift == pytest.approx((0.75 - 0.25) / 0.25)


def test_t2_any_rule_is_the_degenerate_alternative(tmp_path, monkeypatch):
    """Naming only the seed (which every real T2 prompt contains) passes
    under "any" but not under the default "all"."""
    from harness import config as C
    task = _t2(tmp_path, ["pkg.seed", "pkg.stage2", "pkg.stage3"])
    ctx = DeliveredContext("arm0", task.task_id, [], 0, 0, {})
    echo = _ans(task, ["pkg.seed"], arm="arm0")
    assert C.T2_ANSWER_RULE == "all"
    res = score(task, ctx, echo)
    assert res.tsr == 0.0 and res.task_specific["answer_names_any_gold"] == 1.0
    monkeypatch.setattr(C, "T2_ANSWER_RULE", "any")
    assert score(task, ctx, echo).tsr == 1.0


def test_t5_is_fractional_recall(tmp_path):
    gold = [f"m.caller_{i}" for i in range(10)]
    t5 = BlastRadiusTask(task_id="b", repo_id="r", repo_root=str(tmp_path), query="q",
                         ground_truth=GroundTruth(pipeline_symbols=gold))
    res = score(t5, _ctx(t5, [gold[:3]]), _ans(t5, gold[:8]))
    assert res.tsr == pytest.approx(0.8)                      # 8 of 10, not 1 and not 0
    assert res.task_specific["false_negative_rate"] == pytest.approx(0.2)
    assert res.task_specific["recall_at_5"] == pytest.approx(0.3)
    assert res.task_success is True                           # secondary view: 0.8 >= 0.5
    low = score(t5, _ctx(t5, [gold[:3]]), _ans(t5, gold[:3]))
    assert low.tsr == pytest.approx(0.3) and low.task_success is False


def test_empty_delivered_set_gives_nan_cleanliness_not_one(tasks):
    t2 = tasks["T2_localization"]
    ctx = DeliveredContext("arm0", t2.task_id, [], 0, 0, {})
    res = score(t2, ctx, _ans(t2, [TARGET], arm="arm0"))
    assert math.isnan(res.cleanliness) and math.isnan(res.context_precision)
    assert math.isnan(res.relevant_token_density) and math.isnan(res.budget_utilization)
    assert res.context_recall == 0.0 and res.mrr_at_5 == 0.0 and res.over_budget is False


def test_ranking_is_verified_on_every_score(tasks):
    t2 = tasks["T2_localization"]
    ctx = _ctx(t2, [[TARGET], []])
    ctx.items[1].rank = 5
    with pytest.raises(RankingViolation):
        score(t2, ctx, _ans(t2, []))


def test_hallucination_candidates_are_universe_not_delivered(tasks):
    t2 = tasks["T2_localization"]
    # the arm delivered a symbol that doesn't exist; the answer repeats it.
    ctx = _ctx(t2, [[TARGET, "fastapi.dependencies.utils.ghost_function"]])
    res = score(t2, ctx, _ans(t2, [TARGET, "fastapi.dependencies.utils.ghost_function"]))
    assert res.hallucination_rate == 0.5
    assert res.hallucination_breakdown["module_import"] == ["fastapi.dependencies.utils.ghost_function"]


def test_mismatched_cell_rejected(tasks):
    t2 = tasks["T2_localization"]
    with pytest.raises(ValueError):
        score(t2, _ctx(t2, []), _ans(t2, [], arm="arm5"))
