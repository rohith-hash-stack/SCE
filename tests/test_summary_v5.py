"""Candidate v5: v4, plus escalation of long functions with short summaries,
plus each escalated function's `effects` in the text search indexes."""
from __future__ import annotations

import re

import pytest

from prism.cli import build_pipeline
from prism.summaries import index as S
from prism.summaries.pipeline import build_index, candidate_pipeline, escalate, long_short

LONG_BODY = "".join(f"    y = y + {i}\n" for i in range(22))
SOURCE = ("def op0(x):\n    return x + 0\n\n\n"                      # short, name-restating summary -> v4 and v5
          "def op1(x):\n    return x + 1\n\n\n"                      # short, good summary -> neither
          f"def big(x):\n    y = x\n{LONG_BODY}    return y\n\n\n"   # long, short good summary -> v5 only
          f"def huge(x):\n    y = x\n{LONG_BODY}    return y\n")     # long, long summary -> neither
PASS1 = {"op0": "Runs the op.", "op1": "Adds a fixed offset to the number it receives and hands the sum back",
         "big": "Accumulates many increments into a running total", "huge":
         "Accumulates twenty two constant increments into a running total and returns the final value"}
REWRITE = "Starts from the input and adds twenty two constants one after another, returning the grand total"
EFFECTS = "computes a running sum of constants; returns the total"


class LLM:
    def __init__(self):
        self.summaries = 0
        self.escalations: list[str] = []

    def json(self, prompt, max_out, seed=42):
        if prompt.startswith("The sentence below"):
            return {"summary": PASS1["op1"]}
        if "its source code follows" in prompt:
            self.escalations.append(re.search(r"^Qualified Name: (\S+)$", prompt, re.M).group(1))
            return {"effects": EFFECTS, "summary": REWRITE}
        names = re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)
        self.summaries += len(names)
        return {q: PASS1[q.rsplit(".", 1)[-1]] for q in names}


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app/ops.py").write_text(SOURCE)
    builder = build_pipeline(str(tmp_path), use_cache=False)[0]
    build_index(builder, str(tmp_path), llm=LLM())  # v2
    return builder, tmp_path


def _functions(root):
    return S.read_index(S.index_path(str(root)))["functions"]


def test_the_size_trigger():
    assert long_short("Accumulates many increments", (1, 20), (20, 10))
    assert not long_short("Accumulates many increments", (1, 19), (20, 10))
    assert not long_short(" ".join(["word"] * 10), (1, 40), (20, 10))
    assert not long_short("Short.", (1, 40), None)


def test_v4_to_v5_reuses_v4_escalations_and_escalates_long_short_functions(repo):
    builder, root = repo
    llm = LLM()
    build_index(builder, str(root), llm=llm, candidate="v4")
    assert llm.escalations == ["app.ops.op0"]
    assert build_index(builder, str(root), candidate="v5", dry_run=True)["to_escalate"] == 1
    llm = LLM()
    counts = build_index(builder, str(root), llm=llm, candidate="v5")
    assert llm.escalations == ["app.ops.big"] and llm.summaries == 0  # op0 is v4's, reused
    assert counts["escalated"] == 1
    data = S.read_index(S.index_path(str(root)))
    assert data["pipeline"] == candidate_pipeline("v5") and data["pipeline"]["index_effects"]
    f = data["functions"]
    assert f["app.ops.big"]["summary"] == REWRITE and f["app.ops.big"]["pass1"] == PASS1["big"]
    assert f["app.ops.op0"]["escalated"] and "escalation_tried" not in f["app.ops.huge"]
    state = S.state_for(builder, str(root))
    assert state.summaries["app.ops.big"] == f"{REWRITE} {EFFECTS}"  # the effects are searchable
    assert state.summaries["app.ops.op1"] == PASS1["op1"] and state.weight == 0.0  # unvalidated
    llm = LLM()
    build_index(builder, str(root), llm=llm, candidate="v5")
    assert not llm.escalations


def test_v2_to_v5_directly_escalates_both_triggers(repo):
    builder, root = repo
    llm = LLM()
    build_index(builder, str(root), llm=llm, candidate="v5")
    assert sorted(llm.escalations) == ["app.ops.big", "app.ops.op0"]


def test_effects_are_not_indexed_for_v4_or_unescalated_entries():
    entry = {"summary": "S.", "effects": "E", "escalation_tried": True}
    assert S.search_text(entry, candidate_pipeline("v4")) == "S."
    assert S.search_text(entry, candidate_pipeline("v5")) == "S. E"
    assert S.search_text({"summary": "S.", "effects": "E"}, candidate_pipeline("v5")) == "S."


def test_a_rewrite_must_clear_the_size_trigger_to_be_kept():
    card = "Qualified Name: app.ops.big\nDirect Callees: none"

    class Fixed:
        def __init__(self, reply):
            self.reply = reply

        def json(self, prompt, max_out, seed=42):
            return self.reply

    short = "Adds up many constants into a total"  # still under 10 words
    assert escalate(Fixed({"summary": short, "effects": "e"}), PASS1["big"], card, "b", ("restates_name",),
                    (1, 25), (20, 10)) == (None, "e")
    assert escalate(Fixed({"summary": REWRITE, "effects": "e"}), PASS1["big"], card, "b", ("restates_name",),
                    (1, 25), (20, 10)) == (REWRITE, "e")
