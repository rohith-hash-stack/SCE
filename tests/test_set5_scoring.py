"""Set 5's pre-registered rules (harness/experiments/heldout_retrieval/set5), on synthetic rows."""
import pytest

from harness.experiments.heldout_retrieval.set5.score import decide, main


def _rows(ranks):
    return {f"q{i}": {"ranks": {"g": r}, "recall@5": 1.0 if r and r <= 5 else 0.0, "mrr": 1 / r if r else 0.0}
            for i, r in enumerate(ranks)}


def test_rule1_keeps_the_exclusion_unless_it_is_inferior_and_rule2_needs_a_win():
    same, better, worse = [8] * 30, [3] * 30, [12] * 30
    d = decide({"A": _rows(same), "B": _rows(same), "C": _rows(better)})
    assert d["rule1"]["verdict"].startswith("KEEP") and d["rule2"]["verdict"].startswith("CONFIRMED")  # ties: not inferior
    d = decide({"A": _rows(same), "B": _rows(worse), "C": _rows(worse)})
    assert d["rule1"]["verdict"].startswith("REVERT") and d["rule2"]["verdict"].startswith("NOT CONFIRMED")
    d = decide({"A": _rows([4] * 30), "B": _rows([4] * 29 + [6]), "C": _rows([4] * 29 + [6])})  # 1 of 30 loses R@5
    assert d["rule1"]["recall5_delta"] == -0.033 and d["rule1"]["verdict"].startswith("REVERT")


def test_scoring_refuses_a_draft_set(tmp_path, monkeypatch):
    import json
    from harness.experiments.heldout_retrieval.set5 import score
    frozen = json.loads((score.HERE / "dataset.json").read_text())
    (tmp_path / "dataset.json").write_text(json.dumps({**frozen, "status": "draft"}))
    monkeypatch.setattr(score, "HERE", tmp_path)
    with pytest.raises(SystemExit, match="not frozen"):
        main([str(tmp_path), str(tmp_path / "out")])


def test_the_committed_set_is_frozen_with_its_questions_hash():
    import hashlib
    import json
    from harness.experiments.heldout_retrieval.set5 import score
    ds = json.loads((score.HERE / "dataset.json").read_text())
    assert ds["status"] == "frozen" and len(ds["questions"]) == 30
    assert hashlib.sha256(json.dumps(ds["questions"], sort_keys=True).encode()).hexdigest() == ds["questions_sha256"]
