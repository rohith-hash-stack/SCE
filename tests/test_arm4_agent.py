"""Arm 4 (Claude-Code-style agent loop): the tool format and parser, the
tools and their caps, path containment, duplicate suppression, compaction,
answer-in-batch, the forced answer, and the pipeline's self-answer path.
The four bug traps each have a test, marked TRAP."""
import json
import os
import stat
import sys

import pytest

from harness import config as C
from harness.arms.arm4_agent import AGENT_SYSTEM_PROMPT, Arm4AgentLoop, ToolResult, call_key, parse_tool_calls
from harness.llm import Completion
from harness.scoring.adapters import adapt


class Words:
    name = "words"
    def count(self, t): return len(t.split())


def tools(*calls):
    return "".join(f"<tools>{json.dumps({'name': n, 'arguments': a})}</tools>" for n, a in calls)


ANSWER = ("answer", {"response": '```json\n{"reasoning": "r", "symbols": ["pkg.mod.f"]}\n```'})


class Scripted:
    """Returns the scripted turns in order; records each conversation."""
    def __init__(self, *turns):
        self.turns, self.seen, self.purposes = list(turns), [], []

    def chat(self, messages, max_tokens=0, seed=None, purpose=""):
        self.seen.append(messages)
        self.purposes.append(purpose)
        return Completion(self.turns.pop(0), 100, 20, 0.5, model="m", finish_reason="stop", purpose=purpose)

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        self.purposes.append(purpose)
        return Completion('```json\n{"reasoning": "forced", "symbols": ["pkg.mod.f"]}\n```', 1, 5, 0.1, purpose=purpose)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("def f():\n    return g()\n\n\nclass K:\n    def m(self):\n        return 1\n")
    (root / "pkg" / "long.py").write_text("\n".join(f"x{i} = {i}" for i in range(400)) + "\n")
    for k in range(3):                                   # 25 matches in each of 3 files
        (root / "pkg" / f"many{k}.py").write_text("\n".join(f"hit_{j} = 'needle'" for j in range(25)) + "\n")
    (tmp_path / "secret.txt").write_text("outside")
    return root


def arm_for(repo, llm, **kw):
    arm = Arm4AgentLoop(llm=llm, tokenizer=Words(), **kw)
    arm.index(str(repo), {})
    return arm


SEED = {"task_id": "t", "task_type": "T2_localization", "seed_symbol": "pkg.mod.f"}


# ------------------------------------------------------------- format
def test_system_prompt_uses_native_tools_format_with_examples():
    assert "<tools>" in AGENT_SYSTEM_PROMPT and "<tool_call>" not in AGENT_SYSTEM_PROMPT
    assert AGENT_SYSTEM_PROMPT.count("<tools>{") >= 4                  # few-shot examples, incl. answer


def test_TRAP_every_tools_block_in_a_turn_is_parsed():
    text = ("I'll search twice. " + tools(("grep", {"pattern": "a"}))
            + " and " + tools(("glob", {"pattern": "**/*.py"}))
            + '<tools>[{"name": "read", "arguments": {"path": "x.py"}}, {"name": "read", "arguments": {"path": "y.py"}}]</tools>'
            + "<tools>{not json}</tools>")
    calls, blocks, bad = parse_tool_calls(text)
    assert [c["name"] for c in calls] == ["grep", "glob", "read", "read"] and (blocks, bad) == (4, 1)


def test_all_blocks_unparseable_marks_the_turn_is_error(repo):
    llm = Scripted("<tools>{bad</tools><tools>also bad}</tools>", tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    step = ctx.build_meta["trajectory"][0]
    assert step["is_error"] is True and step["unparseable_blocks"] == 2 and ctx.build_meta["parse_error_turns"] == 1
    assert "could be parsed" in llm.seen[1][-1]["content"]              # the model is told
    assert ctx.build_meta["forced_answer"] is False


def test_hermes_blocks_are_counted_not_executed(repo):
    llm = Scripted('<tool_call>{"name": "grep", "arguments": {"pattern": "f"}}</tool_call>', tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    assert ctx.build_meta["hermes_blocks"] == 1 and ctx.build_meta["tool_calls"] == 0


# -------------------------------------------------------------- tools
def test_TRAP_grep_is_capped_per_file_by_rg_and_in_total_after(repo, monkeypatch):
    import subprocess
    seen = {}
    real = subprocess.run

    def spy(cmd, **kw):
        seen["cmd"] = cmd
        return real(cmd, **kw)

    monkeypatch.setattr("harness.arms.arm4_agent.subprocess.run", spy)
    arm = arm_for(repo, None)
    stats = {"timeouts": 0, "path_rejected": 0, "grep_empty": 0}
    text, extra = arm.tool_grep({"pattern": "needle"}, stats)
    assert seen["cmd"][seen["cmd"].index("-m") + 1] == str(C.AGENT_GREP_MAX_LINES)   # rg -m: per file only
    shown = [ln for ln in text.splitlines() if not ln.startswith("[")]
    assert len(shown) == C.AGENT_GREP_MAX_LINES == 30                 # 75 matches -> 30 in total
    assert text.endswith("[45 more matching lines not shown]") and len(extra["hits"]) == 30


def test_read_is_capped_and_errors_are_soft(repo):
    arm = arm_for(repo, None)
    stats = {"path_rejected": 0, "read_nonexistent": 0, "read_out_of_bounds": 0}
    text, extra = arm.tool_read({"path": "pkg/long.py", "start_line": 10, "end_line": 900}, stats)
    assert extra["lines"] == (10, 10 + C.AGENT_READ_MAX_LINES - 1) and "10: x9 = 9" in text
    assert arm.tool_read({"path": "pkg/nope.py"}, stats)[1]["is_error"] and stats["read_nonexistent"] == 1
    assert arm.tool_read({"path": "pkg/mod.py", "start_line": 99}, stats)[1]["is_error"]
    assert stats["read_out_of_bounds"] == 1


def test_paths_escaping_the_repo_are_rejected(repo):
    os.symlink(repo.parent / "secret.txt", repo / "pkg" / "link.txt")
    arm = arm_for(repo, None)
    stats = {"path_rejected": 0, "read_nonexistent": 0, "read_out_of_bounds": 0, "timeouts": 0, "grep_empty": 0}
    for path in ("../secret.txt", str(repo.parent / "secret.txt"), "pkg/link.txt"):
        text, extra = arm.tool_read({"path": path}, stats)
        assert extra.get("is_error") and "outside" in text
    assert arm.tool_grep({"pattern": "x", "path": ".."}, stats)[1]["is_error"]
    assert arm.tool_glob({"pattern": "../*"}, stats)[1]["is_error"]
    assert stats["path_rejected"] == 5


def test_TRAP_tool_timeout_is_a_soft_failure(repo, tmp_path):
    slow = tmp_path / "slow_rg"
    slow.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    llm = Scripted(tools(("grep", {"pattern": "f"})), tools(ANSWER))
    ctx = arm_for(repo, llm, rg=str(slow), tool_timeout=0.3).retrieve("q", SEED)
    assert ctx.build_meta["timeouts"] == 1 and ctx.build_meta["forced_answer"] is False   # the loop went on
    assert "timed out" in llm.seen[1][-1]["content"]


# --------------------------------------------------------------- loop
def test_duplicate_calls_are_suppressed_by_sorted_arguments(repo):
    assert call_key("grep", {"pattern": "f", "glob": "*.py"}) == call_key("grep", {"glob": "*.py", "pattern": "f"})
    llm = Scripted(tools(("grep", {"pattern": "def f", "glob": "*.py"})),
                   tools(("grep", {"glob": "*.py", "pattern": "def f"})), tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    assert ctx.build_meta["duplicates"] == 1 and ctx.build_meta["tool_calls"] == 1
    assert "same as result r1.1" in llm.seen[2][-1]["content"]


def test_calls_per_turn_and_turn_caps(repo):
    six = tools(*[("read", {"path": "pkg/mod.py", "start_line": i}) for i in range(1, 7)])
    llm = Scripted(six, *[tools(("glob", {"pattern": f"**/many{i}.py"})) for i in range(C.AGENT_MAX_TURNS)])
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    assert ctx.build_meta["calls_over_cap"] == 1                        # the 6th call of turn 1
    assert len(llm.seen) == C.AGENT_MAX_TURNS and ctx.build_meta["forced_answer"] is True
    assert ctx.build_meta["turn_count"] == C.AGENT_MAX_TURNS + 1         # + the forced answer


def test_answer_in_batch_runs_siblings_first_and_does_not_deliver_them(repo):
    llm = Scripted(tools(("grep", {"pattern": "def f"})),
                   tools(("read", {"path": "pkg/mod.py"}), ANSWER, ("grep", {"pattern": "class K"})))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    last = ctx.build_meta["trajectory"][-1]
    assert last["answered"] and [c["tool"] for c in last["calls"]] == ["read", "grep"]   # executed, before the answer
    assert ctx.build_meta["executed_after_answer"] == 2
    assert [it.source_id for it in ctx.items] == ["r1.1"]               # only what reached the model
    assert ctx.items[0].symbols == ["pkg.mod.f"] and ctx.items[0].kind == "tool_result_grep"


def test_TRAP_compaction_keeps_the_last_reads_by_msg_id_not_position(repo):
    arm = arm_for(repo, None)
    reads = [ToolResult(f"r{t}.1", "read", {"path": "pkg/long.py"}, f"read {t}", files=["pkg/long.py"],
                        lines=(1, 10)) for t in (1, 2, 3, 4, 10)]
    grep = ToolResult("r5.1", "grep", {"pattern": "x"}, "pkg/long.py:1:x", hits=[("pkg/long.py", 1)])
    shuffled = [reads[4], grep, reads[0], reads[3], reads[1], reads[2]]   # list order != creation order
    assert arm.compact(shuffled) == 3                                      # r1.1, r2.1 and the grep
    kept = {r.msg_id for r in shuffled if r.digest is None}
    assert kept == {"r3.1", "r4.1", "r10.1"}                               # "r10" sorts after "r4" numerically
    assert grep.digest.startswith("[digested grep") and "pkg/long.py (1)" in grep.digest


def test_compaction_triggers_at_the_context_fraction(repo, monkeypatch):
    monkeypatch.setattr(C, "CONTEXT_WINDOW", 2000)                  # compaction above 1,200 (word) tokens
    monkeypatch.setattr(C, "GENERATION_RESERVE", 0)
    reads = [tools(("read", {"path": "pkg/long.py", "start_line": s, "end_line": s + 39}))
             for s in (1, 41, 81, 121, 161, 201)]
    llm = Scripted(*reads, tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    assert ctx.build_meta["compactions"] >= 1 and ctx.build_meta["digested"] >= 1
    # compaction is threshold-triggered: the oldest reads are digested; reads added after the last
    # compaction stay verbatim (the model saw them so)
    digested = [it.source_id for it in ctx.items if it.kind == "tool_result_digest"]
    verbatim = [it.source_id for it in ctx.items if it.kind == "tool_result_read"]
    assert digested == ["r1.1", "r2.1"] and verbatim == ["r3.1", "r4.1", "r5.1", "r6.1"]
    digest = next(it for it in ctx.items if it.kind == "tool_result_digest")
    assert digest.provenance["original"].startswith("1: x0 = 0")         # the original is kept in provenance


def test_no_tool_call_turn_gets_a_notice(repo):
    llm = Scripted("I think the answer is f.", tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    assert ctx.build_meta["no_tool_turns"] == 1 and "No tool call found" in llm.seen[1][-1]["content"]


# ------------------------------------------------- pipeline and adapter
def _task():
    from harness.tasks.synthetic import synthetic_tasks
    return {t.task_type: t for t in synthetic_tasks("/nonexistent")}["T2_localization"]


@pytest.mark.parametrize("answers", [True, False])
def test_pipeline_uses_the_agent_answer_or_forces_one(repo, answers):
    from harness.pipeline import Pipeline
    turns = [tools(("grep", {"pattern": "def f"})), tools(ANSWER)] if answers else \
        [tools(("glob", {"pattern": f"**/many{i % 3}.py", "n": i})) for i in range(C.AGENT_MAX_TURNS)]
    llm = Scripted(*turns)
    arm = arm_for(repo, llm)
    out = Pipeline({"arm4": arm}, llm, Words()).run_cell("arm4", _task(), seed=42)
    assert ("answer" in llm.purposes) is (not answers)                  # no separate answer call when it answered
    assert out.ans.extraction_success and out.ans.answer_symbols == ["pkg.mod.f"]
    assert out.ans.extraction_method == ("answer_tool" if answers else "code_block")
    assert all(it.symbol_provenance == "tool_result" for it in out.ctx.items)


def test_adapter_rejects_foreign_kinds_and_missing_meta():
    from harness.scoring.canonical import DeliveredContext, DeliveredItem
    task = _task()
    meta = {"turn_count": 2, "tool_calls": 1, "forced_answer": False, "trajectory": []}

    def raw(items, m):
        ctx = DeliveredContext("arm4", task.task_id, items, sum(i.token_count for i in items), 13_000, m)
        return {"bundle": ctx.to_dict(), "completion": {"text": '{"symbols": ["a.b"]}'}}

    ok = [DeliveredItem("r1.1", "x", 1, 1, "tool_result_read", ["a.b"])]
    assert adapt("arm4", raw(ok, meta), task)[1].extraction_method == "answer_tool"
    with pytest.raises(ValueError, match="kinds"):
        adapt("arm4", raw([DeliveredItem("c", "x", 1, 1, "code_chunk", [])], meta), task)
    with pytest.raises(ValueError, match="trajectory"):
        adapt("arm4", raw(ok, {k: v for k, v in meta.items() if k != "trajectory"}), task)


def test_trajectory_records_call_outcomes_for_tool_fpr(repo):
    from harness.scoring.agent_diagnostics import tool_fpr
    llm = Scripted(tools(("grep", {"pattern": "zzz_no_such_text"}), ("read", {"path": "pkg/nope.py"}),
                         ("read", {"path": "pkg/mod.py", "start_line": 500})),
                   tools(("grep", {"pattern": "zzz_no_such_text"}), ("read", {"path": "pkg/mod.py"})), tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    out = tool_fpr(ctx.build_meta["trajectory"])
    assert (out["total_calls"], out["grep_empty"], out["read_nonexistent"], out["read_out_of_bounds"],
            out["redundant"]) == (5, 1, 1, 1, 1)
    assert out["fpr"] == 3 / 5


# ---- M3 fixes: direct answers, the contract in the answer tool, raw turn text ----
DIRECT = '```json\n{"reasoning": "r", "symbols": ["pkg.mod.f", "pkg.mod.K.m"]}\n```'


def test_fix1_a_no_tool_turn_that_parses_as_an_answer_ends_the_loop(repo):
    llm = Scripted(tools(("grep", {"pattern": "def f"})), DIRECT, tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", SEED)
    meta, last = ctx.build_meta, ctx.build_meta["trajectory"][-1]
    assert len(llm.seen) == 2 and meta["turn_count"] == 2                   # stopped at the answering turn
    assert last["is_final"] is True and last["answer_source"] == "direct" and meta["forced_answer"] is False
    assert meta["agent_answer"]["text"] == DIRECT and meta["direct_answers"] == 1 and meta["no_tool_turns"] == 0


@pytest.mark.parametrize("text,task_type", [("I will grep for f first.", "T2_localization"),
                                            ('```json\n{"reasoning": "r", "symbols": []}\n```', "T2_localization"),
                                            ("It evaluates pkg.mod.f.", "T1_conceptual")])
def test_fix1_narration_empty_answers_and_prose_tasks_are_not_direct_answers(repo, text, task_type):
    llm = Scripted(text, tools(ANSWER))
    ctx = arm_for(repo, llm).retrieve("q", {**SEED, "task_type": task_type})
    first = ctx.build_meta["trajectory"][0]
    assert first["is_final"] is False and ctx.build_meta["no_tool_turns"] == 1
    assert ctx.build_meta["trajectory"][-1]["answer_source"] == "answer_tool"


def test_fix2_the_answer_tool_owns_the_response_contract():
    from harness.arms.arm4_agent import agent_system_prompt, agent_task_message
    from harness.arms.base import RESPONSE_CONTRACTS
    for tt, contract in RESPONSE_CONTRACTS.items():
        system = agent_system_prompt(tt)
        before, _, answer_tool = system.partition("- answer:")
        first_line = contract.split("\n")[0]
        assert first_line in answer_tool.split("Example (a different repository)")[0]   # in the answer tool...
        assert first_line not in before and system.count(first_line) == 1               # ...and only there
        assert "Do not produce a final answer without calling the answer tool." in before
    message = agent_task_message("Where is f?")
    assert "Response format" not in message and "nothing else" not in message and "call answer" in message


def test_fix3_every_turn_raw_text_is_in_the_bundle(repo, tmp_path):
    from harness.pipeline import Pipeline
    turns = ["Let me think about where f lives.", tools(("grep", {"pattern": "def f"})), "<tools>{oops</tools>",
             tools(ANSWER)]
    llm = Scripted(*turns)
    Pipeline({"arm4": arm_for(repo, llm)}, llm, Words(), out_dir=tmp_path).run_cell("arm4", _task(), seed=42)
    (bundle,) = (tmp_path / "bundles").glob("arm4_*.json")
    traj = json.loads(bundle.read_text())["bundle"]["build_meta"]["trajectory"]
    assert [s["raw_text"] for s in traj] == turns
    assert [s["is_final"] for s in traj] == [False, False, False, True]
    assert traj[1]["parsed_calls"] == [{"name": "grep", "arguments": {"pattern": "def f"}}] and traj[2]["parsed_calls"] == []
