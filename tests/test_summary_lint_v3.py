"""Candidate v3: a stricter summary lint feeding the same repair pass.

v2 (locked, shipped) is unchanged. v3 also flags summaries that only restate
the function's name and summaries under the prompt's 8-word floor; it reuses
v2 summaries (same prompt, so same card keys) and repairs the flagged ones.
"""
from __future__ import annotations

import re

import pytest

from prism.cli import build_pipeline
from prism.summaries import index as S
from prism.summaries.lint import EXTRA_FLAGS, FLAGS, content_words, lint, problems, rates
from prism.summaries.pipeline import build_index

CARD = "Qualified Name: router.smart-router.router.SmartRouter.match\nDirect Callees: none"
THIN = "Matches a method and path to a result."
RICH = "Tries each configured router in turn and keeps the first one that accepts every route."


def test_v2_lint_is_unchanged_and_v3_adds_two_flags():
    assert set(lint(THIN, CARD)) == set(FLAGS) and not any(lint(THIN, CARD).values())
    strict = lint(THIN, CARD, extra=EXTRA_FLAGS)
    assert set(strict) == set(FLAGS + EXTRA_FLAGS) and strict["restates_name"] and not strict["too_short"]
    assert not any(lint(RICH, CARD, extra=EXTRA_FLAGS).values())
    assert lint("Compares two values securely.", "Qualified Name: utils.buffer.timingSafeEqual\nDirect Callees: none", extra=EXTRA_FLAGS)["too_short"]
    assert set(rates([(THIN, CARD)])) == set(FLAGS) | {"any", "n"} and rates([(THIN, CARD)], extra=EXTRA_FLAGS)["restates_name"] == 1.0
    assert set(lint("Short.", CARD, extra=("restates_name",))) == set(FLAGS) | {"restates_name"}  # v3-name: no length rule


def test_name_words_and_generic_filler_are_not_content():
    # "matches" is the name, "method", "result", "values", "using" are filler
    assert content_words(THIN, "x.SmartRouter.match") == {"path"}
    assert content_words("Returns given values using options.", "m.f") == set()
    assert {"router", "rout", "accept"} & content_words(RICH, "x.SmartRouter.match")


def test_problems_tell_the_repair_what_to_fix():
    msgs = problems("Sorts items.", "Qualified Name: utils.accept.sortItems\nDirect Callees: none", extra=EXTRA_FLAGS)
    assert any("restates the function's name" in m for m in msgs) and any("shorter than 8 words" in m for m in msgs)
    assert problems(THIN, CARD) == []  # v2 does not flag it


FILES = {"app/__init__.py": "", "app/ops.py": "".join(f"def op{i}(x):\n    return x + {i}\n\n\n" for i in range(6))}
GOOD = "Adds a fixed offset to the number it receives and hands the sum back"


class LLM:
    """First answers restate the name for op0-op2; repairs return GOOD."""

    def __init__(self):
        self.summaries = self.repairs = 0

    def json(self, prompt, max_out, seed=42):
        if prompt.startswith("The sentence below"):
            self.repairs += 1
            return {"summary": GOOD}
        names = re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)
        self.summaries += len(names)
        return {q: ("Runs the op." if q.rsplit(".", 1)[-1] in ("op0", "op1", "op2") else GOOD) for q in names}


@pytest.fixture()
def repo(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return build_pipeline(str(tmp_path), use_cache=False)[0], tmp_path


def test_v3_reuses_v2_summaries_and_repairs_only_the_flagged_ones(repo):
    builder, root = repo
    llm = LLM()
    build_index(builder, str(root), llm=llm)  # v2: "Runs the op." passes the v2 lint
    assert llm.summaries == 6 and llm.repairs == 0
    assert S.state_for(builder, str(root)).weight == S.SUMMARY_WEIGHT
    plan = build_index(builder, str(root), candidate="v3", dry_run=True)
    assert plan["to_summarize"] == 0 and plan["to_check"] == 6 and plan["to_repair"] == 3
    llm = LLM()
    counts = build_index(builder, str(root), llm=llm, candidate="v3")
    assert llm.summaries == 0 and llm.repairs == 3 and counts["repaired"] == 3  # no re-summarizing
    data = S.read_index(S.index_path(str(root)))
    assert data["pipeline"]["lint"] == "v3" and all(e["checked"] for e in data["functions"].values())
    assert data["functions"]["app.ops.op0"]["summary"] == GOOD
    state = S.state_for(builder, str(root))
    assert state.coverage == 1.0 and not state.locked and state.weight == 0.0  # unvalidated: search ignores it


def test_going_back_from_v3_to_v2_starts_over(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM(), candidate="v3")
    llm = LLM()
    counts = build_index(builder, str(root), llm=llm)  # v2 must not inherit v3-repaired text
    assert counts["kept"] == 0 and llm.summaries == 6
    assert S.read_index(S.index_path(str(root)))["pipeline"] == S.locked_pipeline()


def test_a_harness_cache_installed_as_v3_is_re_linted(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM())
    cache = {q: {"key": e["key"], "summary": e["summary"]} for q, e in S.read_index(S.index_path(str(root)))["functions"].items()}
    S.index_path(str(root)).unlink()
    plan = build_index(builder, str(root), candidate="v3", from_cache=cache, dry_run=True)
    assert plan["imported"] == 6 and plan["to_check"] == 6 and plan["to_repair"] == 3


def test_v3_name_repairs_only_name_restating_summaries(repo):
    builder, root = repo
    build_index(builder, str(root), llm=LLM())
    assert build_index(builder, str(root), candidate="v3-name", dry_run=True)["to_repair"] == 3
    with pytest.raises(ValueError, match="unknown candidate"):
        build_index(builder, str(root), candidate="v4", dry_run=True)
