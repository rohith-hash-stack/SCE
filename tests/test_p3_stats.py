"""Phase P3 statistics (harness/experiments/retrieval_pivot/p3_stats.py)."""
from __future__ import annotations

import json

from harness.experiments.retrieval_pivot import p3_stats as P

TASKS = [f"l4_fwd_t{i:02d}" for i in range(10)]


def _cell(task, arm, seed, f1, unknown=(), names=3):
    reply = json.dumps({"affected_symbols": [f"m.f{i}" for i in range(names)]})
    return {"task_id": task, "family": "forward_blast", "arm": arm, "seed": seed, "status": "ok", "reply": reply,
            "tokens": {"context": 1000}, "score": {"f1": f1, "precision": f1, "recall": f1, "unknown_names": list(unknown)}}


def _run(tmp_path, f1_of, unknown_of=lambda arm: ()):
    cells = [_cell(t, arm, s, f1_of(arm, i), unknown_of(arm)) for arm in P.ARMS for i, t in enumerate(TASKS) for s in (42, 43)]
    (tmp_path / "cells.jsonl").write_text("\n".join(json.dumps(c) for c in cells) + "\n")
    return P.analyse(tmp_path)


def test_the_chain_passes_when_reach_wins_compression_holds_and_profiles_win(tmp_path):
    base = {"R0_A0": 0.3, "R1_A0": 0.5, "R1_A1n": 0.5, "R1_A4": 0.5, "R1_A5": 0.6}
    r = _run(tmp_path, lambda arm, i: base[arm.removeprefix("p3_").split("@")[0]] + 0.001 * i)
    d = r["decision"]
    assert [s["passes"] for s in d["steps"]] == [True, True, True] and d["winner"] == "R1_A5"
    assert d["steps"][0]["better"] == 10 and d["steps"][0]["sign_p"] < 0.01
    assert r["win_loss"]["p3_R1_A5@8k"]["p3_R0_A0@8k"]["better"] == 10
    assert "**Winner: R1_A5** (exploratory: not a decision)" in (tmp_path / "P3_STATS.md").read_text()


def test_the_chain_stops_at_the_first_failure_and_ties_do_not_pass(tmp_path):
    base = {"R0_A0": 0.3, "R1_A0": 0.5, "R1_A1n": 0.4, "R1_A4": 0.5, "R1_A5": 0.9}
    d = _run(tmp_path, lambda arm, i: base[arm.removeprefix("p3_").split("@")[0]])["decision"]
    assert [s["step"] for s in d["steps"]] == ["reach", "compression"] and not d["steps"][1]["passes"]
    assert d["winner"] == "R1_A0"  # profiles are never tested after compression fails


def test_a_fabricating_arm_is_disqualified(tmp_path):
    base = {"R0_A0": 0.3, "R1_A0": 0.5, "R1_A1n": 0.5, "R1_A4": 0.5, "R1_A5": 0.5}
    r = _run(tmp_path, lambda arm, i: base[arm.removeprefix("p3_").split("@")[0]] + 0.001 * i,
             unknown_of=lambda arm: ("x.fake",) if arm == "p3_R1_A0@8k" else ())
    assert r["arms"]["p3_R1_A0@8k"]["fabrication_rate"] == round(1 / 3, 4) and r["arms"][P.BASELINE]["fabrication_rate"] == 0
    d = r["decision"]
    assert d["steps"][0]["disqualified"] and not d["steps"][0]["passes"] and d["winner"] == "R0_A0"


def test_a_resumed_run_counts_each_cell_once_and_ignores_other_arms(tmp_path):
    a = _cell(TASKS[0], "p3_R0_A0@8k", 42, 0.1)
    b = dict(a, score={**a["score"], "f1": 0.9})  # the same cell, run again
    other = dict(a, arm="prism_full")
    (tmp_path / "cells.jsonl").write_text("\n".join(json.dumps(c) for c in (a, b, other)) + "\n")
    cells = P.load_cells(tmp_path)
    assert len(cells) == 1 and cells[0]["score"]["f1"] == 0.9
