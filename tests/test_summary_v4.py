"""Candidate v4: v2 summaries (pass 1) plus an escalation pass - a summary
that restates the function's name is written again from the card and the
function's own source (at most BODY_CAP lines), effects first."""
from __future__ import annotations

import json
import re

import pytest

from prism.cli import build_pipeline
from prism.summaries import index as S
from prism.summaries.pipeline import BODY_CAP, ESCALATION_PROMPT, build_index, candidate_pipeline, escalate, function_body

OPS = "".join(f"def op{i}(x):\n    return x + {i}\n\n\n" for i in range(6))
GOOD = "Adds a fixed offset to the number it receives and hands the sum back"
BETTER = "Takes a number, adds a small constant offset to it and returns the total"
EFFECTS = "computes x plus a constant; returns the sum"


class LLM:
    """Pass 1 restates the name for op0-op2; escalation answers `escalated`."""

    def __init__(self, escalated=BETTER):
        self.summaries = self.repairs = 0
        self.escalations: list[str] = []
        self.escalated = escalated

    def json(self, prompt, max_out, seed=42):
        if prompt.startswith("The sentence below"):
            self.repairs += 1
            return {"summary": GOOD}
        if "its source code follows" in prompt:
            self.escalations.append(prompt)
            return {"effects": EFFECTS, "summary": self.escalated}
        names = re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)
        self.summaries += len(names)
        return {q: ("Runs the op." if q.rsplit(".", 1)[-1] in ("op0", "op1", "op2") else GOOD) for q in names}


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app/ops.py").write_text(OPS)
    return build_pipeline(str(tmp_path), use_cache=False)[0], tmp_path


def _functions(root):
    return S.read_index(S.index_path(str(root)))["functions"]


def test_v4_escalates_only_name_restating_summaries_with_their_source(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM())  # v2: "Runs the op." passes the v2 lint
    v2 = _functions(root)
    plan = build_index(builder, str(root), candidate="v4", dry_run=True)
    assert plan["to_summarize"] == 0 and plan["to_check"] == 6 and plan["to_escalate"] == 3
    llm = LLM()
    counts = build_index(builder, str(root), llm=llm, candidate="v4")
    assert llm.summaries == 0 and llm.repairs == 0 and len(llm.escalations) == 3  # no re-summarizing, no v2 re-repair
    assert counts["escalation_tried"] == 3 and counts["escalated"] == 3
    assert all("return x + " in p and "Qualified Name: app.ops.op" in p for p in llm.escalations)
    data = S.read_index(S.index_path(str(root)))
    assert data["pipeline"] == candidate_pipeline("v4") and data["pipeline"]["lint"] == "v4"
    assert data["pipeline"]["body_cap"] == BODY_CAP
    f = data["functions"]
    assert f["app.ops.op0"] | {} == {**f["app.ops.op0"], "summary": BETTER, "pass1": "Runs the op.", "effects": EFFECTS,
                                     "escalated": True, "escalation_tried": True, "checked": True}
    assert {q: e["summary"] for q, e in f.items() if not e.get("escalated")} == \
        {q: v2[q]["summary"] for q in v2 if q.rsplit(".", 1)[-1] not in ("op0", "op1", "op2")}
    state = S.state_for(builder, str(root))
    assert state.coverage == 1.0 and not state.locked and state.weight == 0.0  # unvalidated: search ignores it
    llm = LLM()
    build_index(builder, str(root), llm=llm, candidate="v4")
    assert not llm.escalations and llm.summaries == 0  # a rerun redoes nothing


def test_an_escalation_that_is_no_better_is_dropped_and_not_retried(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM())
    llm = LLM(escalated="Runs the op with `x`.")  # an identifier: fails the v2 lint
    assert build_index(builder, str(root), llm=llm, candidate="v4")["escalated"] == 0
    e = _functions(root)["app.ops.op0"]
    assert e["summary"] == "Runs the op." and e["escalation_tried"] and e["checked"] and "pass1" not in e
    assert e["effects"] == EFFECTS
    llm = LLM()
    build_index(builder, str(root), llm=llm, candidate="v4")
    assert not llm.escalations


def test_a_changed_body_reverts_to_pass1_and_escalates_again(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM())
    build_index(builder, str(root), llm=LLM(), candidate="v4")
    (root / "app/ops.py").write_text(OPS.replace("return x + 0", "return x + 100"))
    builder = build_pipeline(str(root), use_cache=False)[0]
    llm = LLM(escalated="Takes a number, adds one hundred to it and returns the resulting total")
    build_index(builder, str(root), llm=llm, candidate="v4")
    assert len(llm.escalations) == 1 and "return x + 100" in llm.escalations[0]
    e = _functions(root)["app.ops.op0"]
    assert e["pass1"] == "Runs the op." and "one hundred" in e["summary"]
    assert _functions(root)["app.ops.op1"]["summary"] == BETTER  # unchanged body: kept


def test_going_back_from_v4_to_v2_starts_over(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM(), candidate="v4")
    llm = LLM()
    assert build_index(builder, str(root), llm=llm)["kept"] == 0 and llm.summaries == 6
    assert S.read_index(S.index_path(str(root)))["pipeline"] == S.locked_pipeline()
    assert not any("escalated" in e for e in _functions(root).values())


def test_a_harness_cache_installed_as_v4_is_escalated_not_re_repaired(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM())
    cache = {q: {"key": e["key"], "summary": e["summary"]} for q, e in _functions(root).items()}
    S.index_path(str(root)).unlink()
    llm = LLM()
    counts = build_index(builder, str(root), llm=llm, candidate="v4", from_cache=cache)
    assert counts["imported"] == 6 and llm.repairs == 0 and len(llm.escalations) == 3


def test_the_body_is_capped_and_the_escalation_never_makes_a_summary_worse():
    lines = [f"line {i}" for i in range(1, 101)]
    body = function_body(lines, (11, 100))
    assert body.splitlines()[0] == "line 11" and body.splitlines()[BODY_CAP - 1] == f"line {10 + BODY_CAP}"
    assert body.endswith(f"... ({90 - BODY_CAP} more lines)") and function_body(lines, (1, 3)) == "line 1\nline 2\nline 3"
    card = "Qualified Name: app.ops.op0\nDirect Callees: none"

    class Fixed:
        def __init__(self, reply):
            self.reply = reply

        def json(self, prompt, max_out, seed=42):
            assert prompt == ESCALATION_PROMPT.format(card=card, body="return x")
            return self.reply

    assert escalate(Fixed({"effects": "e", "summary": BETTER}), "Runs the op.", card, "return x", ("restates_name",)) == (BETTER, "e")
    assert escalate(Fixed({"summary": "Runs it."}), "Runs the op.", card, "return x", ("restates_name",)) == (None, "")
    assert escalate(Fixed(None), "Runs the op.", card, "return x", ("restates_name",)) == (None, "")
    assert json.loads(json.dumps(candidate_pipeline("v4")))["escalation_sha"]
