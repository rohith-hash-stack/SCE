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
