"""R3's agent loop: reply parsing, the window rule, budget and turn-cap ends, resume (no model, no repository)."""
from __future__ import annotations

import json
import os

from harness.experiments.r3 import loop as L


class Shared:
    done_summary = None

    def call(self, tool, **args):
        if tool == "done":
            self.done_summary = args.get("summary", "")
            return "done"
        return "x" * args.get("size", 10)


class Model:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, msgs, seed):
        self.seen.append(msgs)
        text = self.replies.pop(0) if self.replies else json.dumps({"tool": "read_file", "args": {"path": "a"}})
        return {"text": text, "prompt_tokens": 1000, "answer_tokens": 50, "done_reason": "stop", "seconds": 0.0}


def task():
    return {"id": "t1", "prompt": "do the thing"}


def test_parse_call_accepts_one_json_tool_call_only():
    assert L.parse_call('{"tool": "grep", "args": {"pattern": "x"}}') == ("grep", {"pattern": "x"})
    assert L.parse_call('```json\n{"tool": "done", "args": {}}\n```') == ("done", {})
    assert L.parse_call('{"tool": "done"}') == ("done", {})
    assert L.parse_call("I will read the file") is None
    assert L.parse_call('{"args": {}}') is None
    assert L.parse_call('{"tool": "grep", "args": "x"}') is None


def test_prompts_differ_only_in_the_tool_list():
    p, b = L.system_prompt("PRISM"), L.system_prompt("BASELINE")
    assert p.split("Tools:")[0] == b.split("Tools:")[0]
    assert "prism_change_context" in p and "prism_change_context" not in b
    assert "related_code" in b and "related_code" not in p


def test_window_stubs_oldest_tool_outputs_first_then_drops_turns():
    run = L.AgentRun(task(), "PRISM", 42, Model([]), Shared())
    big = "y" * 4 * 6000                                    # ~6,000 tokens each (len // 4 without a tokenizer)
    hist = []
    for i in range(4):
        hist += [{"role": "assistant", "content": f'{{"tool": "read_file", "args": {{"i": {i}}}}}'},
                 {"role": "user", "content": big, "tool_output": True, "tool": "read_file"}]
    msgs, stubbed = run.window("sys", "task", hist)
    assert stubbed >= 1
    assert "omitted to fit the window" in msgs[3]["content"]          # the oldest output is the first stubbed
    assert msgs[-1]["content"] == big                                 # the newest output is kept whole
    assert sum(run.n(m["content"]) for m in msgs) + L.ANSWER_TOKENS <= L.NUM_CTX


def test_run_ends_on_done_turn_cap_and_budget(tmp_path):
    r = L.AgentRun(task(), "BASELINE", 42, Model([json.dumps({"tool": "done", "args": {"summary": "ok"}})]), Shared())
    s = r.run(str(tmp_path / "a"))
    assert s["end"] == "done" and s["turns"] == 1 and s["done_summary"] == "ok"
    s = L.AgentRun(task(), "BASELINE", 42, Model([]), Shared(), turn_cap=3).run(str(tmp_path / "b"))
    assert s["end"] == "turn_cap" and s["turns"] == 3
    old = L.BUDGET_TOKENS
    try:
        L.BUDGET_TOKENS = 2500
        s = L.AgentRun(task(), "BASELINE", 42, Model([]), Shared()).run(str(tmp_path / "c"))
        assert s["end"] == "budget"
    finally:
        L.BUDGET_TOKENS = old


def test_bad_reply_costs_its_turn_and_unknown_tools_are_refused(tmp_path):
    m = Model(["not json", json.dumps({"tool": "prism_ask", "args": {"question": "q"}}),
               json.dumps({"tool": "done", "args": {}})])
    s = L.AgentRun(task(), "BASELINE", 42, m, Shared()).run(str(tmp_path))
    turns = [json.loads(x) for x in open(os.path.join(tmp_path, "t1__BASELINE__42.jsonl"))]
    assert s["turns"] == 3 and turns[0]["parse_error"]
    assert m.seen[1][-1]["content"] == L.FORMAT_REMINDER
    assert turns[1]["tool"] == "prism_ask" and turns[1]["output_tokens"] > 0   # refused: not a baseline tool


def test_a_finished_run_is_not_repeated(tmp_path):
    L.AgentRun(task(), "PRISM", 7, Model([json.dumps({"tool": "done", "args": {}})]), Shared()).run(str(tmp_path))
    m = Model([])
    s = L.AgentRun(task(), "PRISM", 7, m, Shared()).run(str(tmp_path))
    assert s["end"] == "done" and m.seen == []
