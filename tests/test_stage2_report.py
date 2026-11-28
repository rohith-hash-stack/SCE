"""Stage 2 of the v4 validation: the pooled stop rule and the per-set comparisons."""
from harness.experiments.purpose_search import stage2


def _rows(ranks: dict) -> dict:
    return {a: {q: {"ranks": {"g": r}, "recall@5": float(bool(r) and r <= 5), "recall@20": 1.0, "mrr": 1 / r if r else 0.0}
                for q, r in qs.items()} for a, qs in ranks.items()}


def test_v4_is_dropped_when_it_is_worse_than_better_against_v2_pooled():
    base = {"q1": 9, "q2": 9, "q3": 9}
    rows = {s: _rows({"BASE": base, "V2": {"q1": 2, "q2": 3, "q3": 4}, "V4": {"q1": 1, "q2": 5, "q3": 6}})
            for s in stage2.SETS}
    r = stage2.report({"rows": rows, "summaries_in_pool": {}, "gold_not_in_pool": {}})
    assert r["pooled"]["V4 vs V2"]["better"] == 4 and r["pooled"]["V4 vs V2"]["worse"] == 8
    assert r["verdict"] == "DROP v4 before set 6" and r["per_set"]["set2"]["V4 vs BASE"]["better"] == 3
    rows = {s: _rows({"BASE": base, "V2": {"q1": 2, "q2": 3, "q3": 4}, "V4": {"q1": 2, "q2": 3, "q3": 4}}) for s in stage2.SETS}
    assert stage2.report({"rows": rows, "summaries_in_pool": {}, "gold_not_in_pool": {}})["verdict"] == "CONTINUE to set 6"
