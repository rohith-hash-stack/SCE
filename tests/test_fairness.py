import pytest

from harness.scoring.canonical import DeliveredItem
from harness.scoring.fairness import (RankingViolation, iso_token_slice, rank_alphabetically,
                                      rerank_consecutive, verify_ranking)


def _it(rank, tokens=10, sid=None):
    return DeliveredItem(sid or f"f.py:{rank}", "w " * tokens, tokens, rank, "code_chunk", [])


def test_verify_ranking_accepts_1_to_n_and_rejects_gaps_dupes():
    verify_ranking([])
    verify_ranking([_it(2), _it(1), _it(3)])
    for bad in ([_it(1), _it(3)], [_it(1), _it(1)], [_it(0), _it(1)]):
        with pytest.raises(RankingViolation):
            verify_ranking(bad)


def test_rerank_and_alphabetical():
    items = rerank_consecutive([_it(7), _it(2), _it(5)])
    assert [i.rank for i in items] == [1, 2, 3] and items[0].source_id == "f.py:2"
    meta = {}
    alpha = rank_alphabetically([_it(1, sid="b"), _it(2, sid="a")], meta)
    assert [i.source_id for i in alpha] == ["a", "b"] and meta["ranking_method"] == "alphabetical"
    verify_ranking(alpha)


def test_iso_token_slice_is_rank_prefix():
    items = [_it(1, 100), _it(2, 50), _it(3, 10)]
    assert [i.rank for i in iso_token_slice(items, 150)] == [1, 2]
    # stops at the first item that doesn't fit (a prefix, not a knapsack)
    assert [i.rank for i in iso_token_slice(items, 99)] == []

    class Words:
        def count(self, t): return len(t.split())
    assert [i.rank for i in iso_token_slice(items, 160, Words())] == [1, 2, 3]
