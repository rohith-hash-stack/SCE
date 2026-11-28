"""POC 2 guards (`--guards`, every arm alike; AMENDMENTS.md "Phase 3, POC 2"): the loop breaker, reference lists kept
whole under compaction, and `edit_file`'s `occurrence`. With guards off, every earlier phase runs exactly as before."""
import json

import pytest

from harness.experiments.r3 import loop as L
from harness.experiments.r3.compaction import REFERENCE_TOOLS, compact
from harness.experiments.r3.feedback import Repeats
from harness.experiments.r3.shared_tools import SharedTools

from tests.test_phase2_feedback import D, R, T, _Index, _Scripted, _Shared

ARMS = ["BASELINE", "PRISM", "PRISM_ONLY", "BASELINE_LSP", "PRISM_LSP"]


def _log(tmp_path, version):
    return [json.loads(ln) for ln in open(tmp_path / f"t__{version}__42.jsonl")]


# ---------------------------------------------------------------------------------------------- the loop breaker
@pytest.mark.parametrize("version", ARMS)
def test_third_identical_call_is_not_answered_again(tmp_path, version):
    L.AgentRun({"id": "t", "prompt": "p"}, version, 42, _Scripted([R, R, R, R, D]), _Shared(),
               feedback=True, guards=True).run(str(tmp_path))
    log = _log(tmp_path, version)
    assert "repeat_of_turn" not in log[0]
    assert log[1]["repeat_of_turn"] == 1 and "loop_refused" not in log[1]            # the second: answered, a note
    assert log[2]["loop_refused"] is True and log[2]["output_head"].startswith("not answered again")
    assert "turns 1, 2" in log[2]["output_head"]
    assert log[3]["loop_refused"] is True and "turns 1, 2, 3" in log[3]["output_head"]


def test_a_changed_answer_resets_the_count(tmp_path):
    # run_tests answers differently every time (`_Shared`): never refused
    L.AgentRun({"id": "t", "prompt": "p"}, "BASELINE_LSP", 42, _Scripted([T, T, T, D]), _Shared(),
               feedback=True, guards=True).run(str(tmp_path))
    assert all("loop_refused" not in x for x in _log(tmp_path, "BASELINE_LSP"))


def test_loop_breaker_on_the_reference_tools(tmp_path):
    call = json.dumps({"tool": "prism_find_references", "args": {"symbol": "pkg.m.f"}})
    L.AgentRun({"id": "t", "prompt": "p"}, "PRISM_LSP", 42, _Scripted([call, call, call, D]), _Shared(), _Index(),
               feedback=True, guards=True).run(str(tmp_path))
    assert _log(tmp_path, "PRISM_LSP")[2]["loop_refused"] is True


def test_no_loop_breaker_without_guards(tmp_path):
    L.AgentRun({"id": "t", "prompt": "p"}, "BASELINE_LSP", 42, _Scripted([R, R, R, D]), _Shared(),
               feedback=True).run(str(tmp_path))
    log = _log(tmp_path, "BASELINE_LSP")
    assert all("loop_refused" not in x for x in log)
    assert log[2]["repeat_of_turn"] == 2                                         # Phase 2 C3 as before: a note


def test_repeats_same_unit():
    r = Repeats()
    a = {"path": "a.py"}
    assert r.same(1, "read_file", a, "x") == []
    assert r.same(2, "read_file", a, "x") == [1]
    assert r.same(3, "read_file", a, "y") == []                                  # the file changed
    assert r.same(4, "read_file", a, "y") == [3]
    assert r.same(5, "read_file", a, "y") == [3, 4]
    assert r.check(6, "read_file", a, "y") == 5                                  # check() still agrees
    assert "turns 3, 4" in Repeats.refusal([3, 4])


# ---------------------------------------------------------------------------------------------- compaction
def _hist(tool, n_lines):
    out = "\n".join(f"pkg/m{i}.py:{i}: used in a call" for i in range(n_lines))
    h = [{"role": "user", "content": out, "tool_output": True, "tool": tool, "args": {"symbol": "f"}, "turn": 1}]
    for t in range(2, 10):                                                       # 8 newer outputs
        h.append({"role": "user", "content": f"pkg/x.py (lines 1-50 of 90)\n" + "y = 1\n" * 200, "tool_output": True,
                  "tool": "read_file", "args": {"path": f"x{t}.py"}, "turn": t})
    return h


@pytest.mark.parametrize("tool", REFERENCE_TOOLS)
def test_reference_lists_are_kept_whole_with_guards(tool):
    ntok = lambda s: len(s) // 4
    h = _hist(tool, 30)
    cut, _ = compact(h, ntok)
    assert cut[0].get("compacted") == "summarised" and "pkg/m12.py" not in cut[0]["content"]   # the POC's cut
    whole, _ = compact(h, ntok, whole=REFERENCE_TOOLS)
    assert "compacted" not in whole[0] and "pkg/m29.py" in whole[0]["content"]


def test_other_index_tools_are_still_summarised_with_guards():
    ntok = lambda s: len(s) // 4
    cut, _ = compact(_hist("prism_search", 30), ntok, whole=REFERENCE_TOOLS)
    assert cut[0].get("compacted") == "summarised"


# ---------------------------------------------------------------------------------------------- edit_file occurrence
TWIN = "def a():\n    return 1\n\n\ndef b():\n    return 1\n\n\ndef c():\n    return 1\n"


def _shared(tmp_path, guards):
    (tmp_path / "m.py").write_text(TWIN)
    return SharedTools(str(tmp_path), None, [], guards=guards, feedback=True)


def test_occurrence_picks_one_of_several_identical_spans(tmp_path):
    s = _shared(tmp_path, guards=True)
    out = s.call("edit_file", path="m.py", old="    return 1\n", new="    return 2\n", occurrence=2)
    assert out.startswith("edited m.py at line 6")
    assert (tmp_path / "m.py").read_text() == TWIN.replace("def b():\n    return 1", "def b():\n    return 2")


def test_occurrence_errors(tmp_path):
    s = _shared(tmp_path, guards=True)
    msg = s.call("edit_file", path="m.py", old="    return 1\n", new="x")
    assert msg.startswith("error:") and "at lines 2, 6, 10" in msg and "`occurrence`" in msg
    assert "must be 1 to 3" in s.call("edit_file", path="m.py", old="    return 1\n", new="x", occurrence=4)
    assert "must be 1" in s.call("edit_file", path="m.py", old="def b", new="def B", occurrence=2)
    # "\n\n\n" holds two overlapping "\n\n": `occurrence` cannot say which is meant, so it is refused
    assert "overlap" in s.call("edit_file", path="m.py", old="\n\n", new="\n", occurrence=1)
    assert (tmp_path / "m.py").read_text() == TWIN


def test_unique_span_with_occurrence_one(tmp_path):
    s = _shared(tmp_path, guards=True)
    assert s.call("edit_file", path="m.py", old="def b", new="def B", occurrence=1).startswith("edited")


def test_occurrence_is_not_a_tool_argument_without_guards(tmp_path):
    s = _shared(tmp_path, guards=False)
    assert s.call("edit_file", path="m.py", old="def b", new="def B", occurrence=1).startswith(
        "error: bad arguments for edit_file")
    msg = s.call("edit_file", path="m.py", old="    return 1\n", new="x")
    assert "occurrence" not in msg


# ---------------------------------------------------------------------------------------------- the prompts
@pytest.mark.parametrize("version", ARMS)
def test_tool_docs_with_and_without_guards(version):
    assert "occurrence" not in L.tool_docs(version) and "occurrence" not in json.dumps(L.native_tools(version))
    assert "occurrence" in L.tool_docs(version, guards=True)
    fn = {t["function"]["name"]: t["function"] for t in L.native_tools(version, guards=True)}
    assert "occurrence" in fn["edit_file"]["parameters"]["properties"]
    assert fn["edit_file"]["parameters"]["required"] == ["path", "old", "new"]
    # only edit_file's entry changes
    plain = L.tool_docs(version).splitlines()
    guarded = L.tool_docs(version, guards=True).splitlines()
    assert [a for a, b in zip(plain, guarded) if a != b] == [x for x in plain if x.startswith("- edit_file")]
