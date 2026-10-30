"""R1's token-matched baselines (harness/experiments/r1)."""
from __future__ import annotations

from harness.experiments.r1 import pad


def _count(text: str) -> int:
    return len(text.split())


def test_fill_stops_within_the_target_and_tops_up_with_a_partial_item():
    items = [(f"q{i}", f"`q{i}`\n```\n" + "\n".join(f"line{i}_{j} x" for j in range(10)) + "\n```") for i in range(5)]
    base = "word " * 10
    target = 10 + 2 + 55  # the base, the heading, about two and a half items
    got = pad._fill(_count, base, "HEAD here\n", [("", items)], target)
    assert got["items"] == ["q0", "q1"] and got["partial"] == "q2"
    assert _count(base + "\n" + got["block"]) <= target
    assert _count(base + "\n" + got["block"]) >= (1 - pad.BAND) * target - 5


def test_fill_groups_open_with_their_title():
    got = pad._fill(_count, "b", "H\n", [("", [("a", "one two")]), ("\n\nMORE\n\n", [("c", "three four")])], 100)
    assert got["block"] == "H\none two\n\nMORE\n\nthree four" and got["items"] == ["a", "c"]


def test_pad_prompt_is_the_base_prompt_plus_the_frozen_block(monkeypatch):
    monkeypatch.setattr(pad, "base_prompt", lambda kind, task, version: f"<{version} prompt>")
    pads = {"t1": {"FILE_BM25PAD": {"block": "PADDING"}, "GREP_PAD": {"block": ""}}}
    assert pad.pad_prompt("forward", {"id": "t1"}, "FILE_BM25PAD", pads) == "<FILE prompt>\nPADDING"
    assert pad.pad_prompt("blast", {"id": "t1"}, "GREP_PAD", pads) == "<GREP prompt>"


def test_r1_plan_and_analysis_on_made_up_scores(tmp_path, monkeypatch):
    from harness.experiments.r1 import analyze, run
    assert run.versions("forward") == ("FILE", "PRISM", "FILE_BM25PAD", "FILE_GREPPAD")
    keys = [c[3] for c in run.planned(3, "newfn", "werkzeug")]
    assert keys and all(k.endswith("@1001") for k in keys) and len(keys) == 3 * len(run.tasks_of("newfn", "werkzeug"))
    assert sum(len(run.planned(2, k, r)) for k in run.KINDS for r in run.repos_of(k)) == 30
    # two tasks, every version at seed 42 only: PRISM reuses more than the pad
    monkeypatch.setattr(analyze, "repos_of", lambda kind: ["r"])
    monkeypatch.setattr(analyze, "tasks_of", lambda kind, repo: [{"id": "a"}, {"id": "b"}])
    rs = [{"task": t, "repo": "werkzeug", "version": v, "seed": "42", "reuse": {"PRISM": 1.0}.get(v, 0.5),
           "invented": 0.0, "parses": True, "fence_parses": True, "done_reason": "stop", "raw_sha": v + t}
          for t in ("a", "b") for v in run.versions("newfn")]
    assert analyze.complete_seeds("newfn", rs) == ["42"]
    c = analyze.compare(rs, "FILE_BM25PAD", "PRISM", analyze.METRICS["newfn"]["reuse"], ["42"])
    assert (c["tasks"], c["a_mean"], c["b_mean"]) == (2, 0.5, 1.0)
