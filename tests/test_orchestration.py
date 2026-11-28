"""Phase 0b: the replay tool (`harness/experiments/r4/replay.py`) against the real agent loop.

A logged run is replayed through `AgentRun` with its recorded reply texts; two replays are compared turn by turn
(tool, arguments, prompt, output, gate) and on their outcome and per-test scores. No model, no repository: the tools
are a deterministic stand-in, so the loop, parser, window, ledger and gate paths are what is exercised.
"""
import json
import os

import pytest

from harness.experiments.r3 import loop as L
from harness.experiments.r4 import replay as R


class Tools:
    """Deterministic shared tools; `outputs` overrides one tool's output (to plant a difference)."""
    done_summary = None

    def __init__(self, outputs=None, tmp="r3run_aaaa"):
        self.outputs, self.tmp = outputs or {}, tmp

    def call(self, tool, **args):
        if tool == "done":
            self.done_summary = args.get("summary", "")
            return "done"
        if tool in self.outputs:
            return self.outputs[tool]
        if tool == "run_tests":
            return f"t.py: 1 passed, 1 failed, 0 skipped\nE  AssertionError at /tmp/{self.tmp}/work/a.py:3 in 0.42s"
        if tool == "edit_file":
            return "edited a.py at line 3: replaced 1 lines with 1"
        return f"{tool} output for {json.dumps(args, sort_keys=True)}"


RECORDED = [
    {"turn": 1, "reply": json.dumps({"tool": "read_file", "args": {"path": "a.py"}}), "prompt_tokens": 100,
     "answer_tokens": 10, "done_reason": "stop"},
    {"turn": 2, "reply": "I think I should edit", "prompt_tokens": 120, "answer_tokens": 5, "done_reason": "stop"},
    {"turn": 3, "reply": json.dumps({"tool": "edit_file", "args": {"path": "a.py", "old": "x", "new": "y"}}),
     "prompt_tokens": 140, "answer_tokens": 12, "done_reason": "stop"},
    {"turn": 4, "reply": json.dumps({"tool": "run_tests", "args": {}}), "prompt_tokens": 160, "answer_tokens": 8,
     "done_reason": "stop"},
    {"turn": 5, "reply": json.dumps({"tool": "done", "args": {"summary": "ok"}}), "prompt_tokens": 180,
     "answer_tokens": 6, "done_reason": "stop"},
]


def _replay(d, recorded=RECORDED, tools=None, env_patch=None):
    """One replay into `d`: the loop with the recorded replies, the full record, a replay.json and a score file."""
    os.makedirs(d, exist_ok=True)
    task = {"id": "t1", "prompt": "remove f"}
    s = L.AgentRun(task, "BASELINE", 42, R.ReplayModel(recorded), tools or Tools(), record_full=True).run(d)
    env = R.environment()
    if env_patch:
        env.update(env_patch)
    json.dump({"run": s["run"], "task_sha": "x", "environment": env}, open(os.path.join(d, "replay.json"), "w"))
    json.dump({**s, "outcome": "unresolved" if s["end"] == "done" else s["stop"],
               "score": {"tests": {"t.py::test_a": "passed", "t.py::test_b": "failed"}}},
              open(os.path.join(d, s["run"] + ".score.json"), "w"))
    return s


def test_replay_model_serves_the_recorded_replies_in_order_then_raises():
    m = R.ReplayModel(RECORDED + [{"turn": 6}])           # a record without a reply (no model call) is skipped
    assert [m([], 1)["text"] for _ in RECORDED] == [t["reply"] for t in RECORDED]
    with pytest.raises(R.ReplayExhausted):
        m([], 1)


def test_two_replays_of_the_same_log_are_identical(tmp_path):
    a, b = _replay(str(tmp_path / "a")), _replay(str(tmp_path / "b"))
    assert a["end"] == b["end"] == "done" and a["turns"] == 5
    rep = R.compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert rep["result"] == "identical" and rep["first_difference"] is None and rep["turns"] == 5
    log = [json.loads(ln) for ln in open(tmp_path / "a" / "t1__BASELINE__42.jsonl")]
    assert log[1]["parse_error"] is True                   # the recorded non-JSON reply went through the parser
    assert all("prompt" in t and "prompt_sha" in t for t in log) and "output" in log[0]


def test_paths_and_timings_differ_between_runs_and_are_normalized_not_ignored(tmp_path):
    _replay(str(tmp_path / "a"), tools=Tools(tmp="r3run_aaaa"))
    _replay(str(tmp_path / "b"), tools=Tools(tmp="r3run_bbbb"))
    rep = R.compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert rep["result"] == "identical_normalized"


def test_a_changed_tool_output_is_reported_at_its_turn(tmp_path):
    _replay(str(tmp_path / "a"))
    _replay(str(tmp_path / "b"), tools=Tools(outputs={"read_file": "a different file body"}))
    rep = R.compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert rep["result"] == "different"
    assert rep["first_difference"]["turn"] == 1 and rep["first_difference"]["field"] == "output"


def test_a_different_environment_is_not_comparable(tmp_path):
    _replay(str(tmp_path / "a"))
    _replay(str(tmp_path / "b"), env_patch={"python": "0.0.0"})
    rep = R.compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert rep["result"] == "not_comparable" and "python" in rep["environment_differences"]


def test_a_run_that_ended_on_a_model_error_replays_to_the_same_end(tmp_path):
    s = _replay(str(tmp_path / "a"), recorded=RECORDED[:3])     # the original died after its third reply
    assert s["end"].startswith("model_error") and s["turns"] == 4 and "no recorded reply for turn 4" in s["end"]


def test_the_environment_record_has_the_same_machine_fields_and_no_secret_values(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-should-not-matter")
    e1 = R.environment()
    monkeypatch.setenv("SOME_API_KEY", "sk-another-value")
    e2 = R.environment()
    for k in ("python", "implementation", "os", "packages", "packages_sha", "env_names", "env_sha", "code_commit"):
        assert k in e1
    assert "SOME_API_KEY" in e1["env_names"] and e1["env_sha"] == e2["env_sha"]     # names only, for secrets
    assert "sk-should-not-matter" not in json.dumps(e1)
    monkeypatch.setenv("PLAIN_SETTING", "1")
    assert R.environment()["env_sha"] != e1["env_sha"]                            # other values do count


def test_scores_are_compared_per_test_id(tmp_path):
    _replay(str(tmp_path / "a"))
    _replay(str(tmp_path / "b"))
    p = tmp_path / "b" / "t1__BASELINE__42.score.json"
    s = json.load(open(p))
    s["score"]["tests"]["t.py::test_b"] = "passed"
    json.dump(s, open(p, "w"))
    rep = R.compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert rep["result"] == "different" and rep["first_difference"]["tests"] == ["t.py::test_b"]
