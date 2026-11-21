import math

import pytest

from harness.scoring.canonical import DeliveredItem
from harness.scoring.scorer import (average_precision, gold_covered_in_top, mrr_at, ndcg_at,
                                    relevance_vector)


def _items(*symbol_lists):
    return [DeliveredItem(f"s{i}", "", 1, i, "code_chunk", list(s)) for i, s in enumerate(symbol_lists, 1)]


def test_relevance_counts_only_new_gold_coverage():
    items = _items(["x"], ["a"], ["a"], ["b", "a"], ["z"])
    assert relevance_vector(items, {"a", "b"}) == [0, 1, 0, 1, 0]


def test_mrr():
    assert mrr_at([0, 0, 1, 1], 5) == pytest.approx(1 / 3)
    assert mrr_at([0, 0, 0, 0, 0, 1], 5) == 0.0
    assert mrr_at([0, 0, 0, 0, 0, 1], 10) == pytest.approx(1 / 6)


def test_ndcg_against_hand_computation():
    # gold size 2, relevant at ranks 2 and 4
    rel = [0, 1, 0, 1, 0]
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at(rel, 5, 2) == pytest.approx(dcg / idcg)
    assert ndcg_at([1, 1], 5, 2) == pytest.approx(1.0)
    assert math.isnan(ndcg_at([1], 5, 0))


def test_average_precision():
    # relevant at 1 and 3 of 3 gold: (1/1 + 2/3) / 3
    assert average_precision([1, 0, 1], 3) == pytest.approx((1 + 2 / 3) / 3)
    assert average_precision([0, 0], 2) == 0.0


def test_gold_covered_in_top_k():
    items = _items(["a"], ["x"], ["b"], ["x"], ["x"], ["c"])
    assert gold_covered_in_top(items, {"a", "b", "c"}, 5) == {"a", "b"}
    assert gold_covered_in_top(items, {"a", "b", "c"}, 10) == {"a", "b", "c"}


def test_p_r_f1_at_5_via_score(tmp_path):
    from harness.scoring.canonical import DeliveredContext, NormalizedAnswer
    from harness.scoring.scorer import score
    from harness.tasks.schema import GroundTruth, LocalizationTask
    task = LocalizationTask(task_id="t", repo_id="r", repo_root=str(tmp_path), query="q",
                            ground_truth=GroundTruth(pipeline_symbols=["a", "b", "c", "d"]))
    items = _items(["a"], ["x"], ["b"], ["x"], ["x"], ["c"])
    ctx = DeliveredContext("arm1", "t", items, 6, 13000, {})
    res = score(task, ctx, NormalizedAnswer("arm1", "t", "", "", [], "plain_text", False, 0, 0.0))
    assert res.p_at_5 == pytest.approx(2 / 5) and res.r_at_5 == pytest.approx(2 / 4)
    assert res.f1_at_5 == pytest.approx(2 * 0.4 * 0.5 / 0.9)
    assert res.uniform_cpi == pytest.approx(3 / 4)
