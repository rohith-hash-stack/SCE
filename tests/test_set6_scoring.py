"""Set 6: the frozen dataset and the pre-registered V5 rule."""
import hashlib
import json

from harness.experiments.heldout_retrieval.set6 import score
from harness.experiments.heldout_retrieval.set6.score import HERE


def _row(rank):
    return {"ranks": {"g": rank}, "recall@5": float(bool(rank) and rank <= 5), "recall@20": 1.0, "mrr": 1 / rank if rank else 0.0}


def test_v5_qualifies_only_when_it_beats_both_v2_and_base():
    ids = [f"q{i}" for i in range(20)]
    base = {q: _row(9) for q in ids}
    v2 = {q: _row(4) for q in ids}
    v5 = {q: _row(2 if i < 15 else 4) for i, q in enumerate(ids)}  # 15 better, 5 ties vs V2
    d = score.decide({"BASE": base, "V2": v2, "V4": v2, "V5": v5})
    assert d["V5 vs V2"]["passes"] and d["V5 vs BASE"]["passes"] and d["verdict"] == "V5 QUALIFIES"
    v5 = {q: _row(2 if i < 6 else (5 if i < 9 else 4)) for i, q in enumerate(ids)}  # 6 better / 3 worse: p = 0.51
    d = score.decide({"BASE": base, "V2": v2, "V4": v2, "V5": v5})
    assert not d["V5 vs V2"]["passes"] and d["verdict"].startswith("V5 does not qualify")


def test_the_dataset_is_frozen_with_matching_hash_and_120_questions():
    ds = json.loads((HERE / "dataset.json").read_text())
    assert ds["status"] == "frozen" and len(ds["questions"]) == 120
    assert hashlib.sha256(json.dumps(ds["questions"], sort_keys=True).encode()).hexdigest() == ds["questions_sha256"]
    assert {q["repo"] for q in ds["questions"]} == {"httpx", "rich", "elysia", "oclif"}
