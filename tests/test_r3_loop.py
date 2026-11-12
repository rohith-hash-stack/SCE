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
    assert s["done_called"] and s["stop"] == "done" and s["tool_calls"] == {"done": 1}
    s = L.AgentRun(task(), "BASELINE", 42, Model([]), Shared(), turn_cap=3).run(str(tmp_path / "b"))
    assert s["end"] == "turn_cap" and s["turns"] == 3
    assert not s["done_called"] and s["stop"] == "stuck" and s["tool_calls"] == {"read_file": 3}
    first = json.loads(open(tmp_path / "b" / "t1__BASELINE__42.jsonl").readline())
    assert first["output_head"] == "x" * 10                   # the start of each tool output is logged
    old = L.BUDGET_TOKENS
    try:
        L.BUDGET_TOKENS = 2500
        s = L.AgentRun(task(), "BASELINE", 42, Model([]), Shared()).run(str(tmp_path / "c"))
        assert s["end"] == "budget" and s["stop"] == "budget"
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


def test_plan_mode_is_opt_in_and_keeps_the_plan_in_the_history(tmp_path):
    """The planning investigation (r4/PLANNING_INVESTIGATION.md): off by default; on, the plan is logged and replayed."""
    assert "Planning:" not in L.system_prompt("PRISM") and "Planning:" in L.system_prompt("PRISM", plan=True)
    first = json.dumps({"plan": "I will read a because it holds the target. Then edit it.", "tool": "read_file",
                        "args": {"path": "a"}})
    m = Model([first, json.dumps({"plan": "I will finish.", "tool": "done", "args": {}})])
    L.AgentRun(task(), "BASELINE", 42, m, Shared(), plan=True).run(str(tmp_path / "p"))
    assert "Planning:" in m.seen[0][0]["content"]
    assert any("I will read a because" in msg["content"] for msg in m.seen[1] if msg["role"] == "assistant")
    log = [json.loads(ln) for ln in open(tmp_path / "p" / "t1__BASELINE__42.jsonl")]
    assert log[0]["plan"].startswith("I will read a")
    m2 = Model([first, json.dumps({"tool": "done", "args": {}})])                     # off: the plan is not replayed
    L.AgentRun(task(), "BASELINE", 42, m2, Shared()).run(str(tmp_path / "q"))
    assert not any("I will read" in msg["content"] for msg in m2.seen[1] if msg["role"] == "assistant")


class Gate:
    """A stand-in for r4/done_gate.DoneGate: rejects the first `n` dones."""
    def __init__(self, n):
        self.n, self.calls = n, 0

    def check(self):
        self.calls += 1
        return (self.calls > self.n), ("done" if self.calls > self.n else "Done rejected: still defined. Continue.")


class EditShared(Shared):
    def call(self, tool, **args):
        return "edited a at line 1: replaced 1 lines with 1" if tool == "edit_file" else super().call(tool, **args)


def test_gate_rejects_done_feeds_back_and_accepts_later(tmp_path):
    """Amendment R4-SANITY-2: a rejected done costs a turn, its message is the tool output, and the run continues."""
    done = json.dumps({"tool": "done", "args": {"summary": "x"}})
    edit = json.dumps({"tool": "edit_file", "args": {"path": "a", "old": "x", "new": "y"}})
    m = Model([done, edit, done])
    s = L.AgentRun(task(), "BASELINE", 42, m, EditShared(), gate=Gate(1)).run(str(tmp_path / "a"))
    assert s["end"] == "done" and s["turns"] == 3 and s["gate_rejections"] == 1
    assert any("Done rejected" in msg["content"] for msg in m.seen[1] if msg["role"] == "user")
    log = [json.loads(ln) for ln in open(tmp_path / "a" / "t1__BASELINE__42.jsonl")]
    assert log[0]["gate"]["accepted"] is False and log[2]["gate"]["accepted"] is True


def test_three_rejected_dones_without_an_edit_end_stuck_done_and_an_edit_resets_the_count(tmp_path):
    done = json.dumps({"tool": "done", "args": {}})
    edit = json.dumps({"tool": "edit_file", "args": {"path": "a", "old": "x", "new": "y"}})
    s = L.AgentRun(task(), "BASELINE", 42, Model([done] * 5), EditShared(), gate=Gate(99)).run(str(tmp_path / "b"))
    assert s["end"] == "stuck_done" and s["stop"] == "stuck_done" and s["turns"] == 3
    s = L.AgentRun(task(), "BASELINE", 42, Model([done, done, edit, done, done, done]), EditShared(),
                   gate=Gate(99)).run(str(tmp_path / "c"))
    assert s["end"] == "stuck_done" and s["turns"] == 6
    s = L.AgentRun(task(), "BASELINE", 42, Model([done]), Shared()).run(str(tmp_path / "d"))   # no gate: R3a
    assert s["end"] == "done" and "gate_rejections" not in s


def test_progress_ledger_is_appended_every_turn_from_tool_results_and_survives_the_window(tmp_path):
    """R4-SANITY-4: the harness-built summary (r4/progress_ledger.py), off by default."""
    from harness.experiments.r4.progress_ledger import ProgressLedger
    edit = json.dumps({"tool": "edit_file", "args": {"path": "a", "old": "x", "new": "y"}})
    big = json.dumps({"tool": "read_file", "args": {"path": "a", "size": 60000}})
    m = Model([edit, big, big, json.dumps({"tool": "done", "args": {}})])
    L.AgentRun(task(), "BASELINE", 42, m, EditShared(), turn_cap=10, ledger=ProgressLedger(10)).run(str(tmp_path / "l"))
    assert not any("Progress so far" in msg["content"] for msg in m.seen[0])          # turn 1: nothing done yet
    last = m.seen[-1][-1]["content"]
    assert "Progress so far (from the tool results, not from you). This is turn 4 of 10." in last
    assert "edits applied (1): a line 1" in last and "tests: you have not run them yet" in last
    m2 = Model([edit, json.dumps({"tool": "done", "args": {}})])
    L.AgentRun(task(), "BASELINE", 42, m2, EditShared()).run(str(tmp_path / "m"))     # off: R3a
    assert not any("Progress so far" in msg["content"] for turn in m2.seen for msg in turn)


def test_progress_ledger_records_rejections_tests_and_done():
    from harness.experiments.r4.progress_ledger import ProgressLedger
    lg = ProgressLedger(28)
    lg.update(2, "edit_file", {"path": "h.py"}, "error: the text to replace occurs 3 times in h.py, at lines 5, 9")
    lg.update(3, "run_tests", {}, "t.py: 3 passed, 1 failed, 0 skipped\nfailing:\n- test_x\nwarning: 2 tests in this file "
              "that ran at the start did not run now")
    lg.update(4, "done", {}, "Done rejected", {"accepted": False, "message": "Done rejected: x\n- `f` is still defined in "
                                                                             "h.py at line 7\nContinue."})
    r = lg.render(5)
    assert "edits applied: none yet" in r and "h.py: duplicate match ×1" in r
    assert "your last test run (turn 3): t.py: 3 passed, 1 failed, 0 skipped | warning: 2 tests" in r
    assert "done rejected 1× (last at turn 4: `f` is still defined in h.py at line 7)" in r
    assert "caller" not in r.lower()
