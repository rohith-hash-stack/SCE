"""Phase 2 C1-C3 (`harness/experiments/r3/feedback.py`): the harness feedback every arm gets the same way. A breaking
edit is applied and warned about; a failing test run shows its first real error; a repeated identical call with an
identical answer gets a note. With `feedback` off, the tools answer exactly as before."""
import json

import pytest

from harness.experiments.r3 import loop as L
from harness.experiments.r3.feedback import Repeats, edit_warning, first_error, syntax_problem
from harness.experiments.r3.shared_tools import SharedTools

GOOD = "import types\n\nif X:\n    A = 1\nelse:\n\n    def GenericAlias(cls):\n        return cls\n\n\nclass B:\n    pass\n"
# the delete-05 edit: the function removed, its `else:` left with no body
BROKEN_OLD, BROKEN_NEW = "    def GenericAlias(cls):\n        return cls", ""

# pytest's output in the delete-05 case (reproduced with the harness's command): the error is far from the tail
PYTEST_OUT = (
    "==================================== ERRORS ====================================\n"
    "______________ ERROR at setup of test_proxies[pure-python-module] ______________\n"
    + "\n".join(f"    line {i} of the fixture traceback" for i in range(30)) + "\n"
    'E         File "/work/multidict/_multidict_py.py", line 17\n'
    "E           class istr(str):\n"
    "E           ^^^^^\n"
    "E       IndentationError: expected an indented block after 'else' statement on line 12\n\n"
    "/usr/lib/python3.12/importlib/__init__.py:90: IndentationError\n"
    + "\n".join(f"ERROR tests/test_types.py::test_case_{i}[pure-python-module] -   File \"/tmp/cl..." for i in range(40))
    + "\n13 passed, 2 skipped, 13 errors in 0.19s\n")


class _Env:
    def __init__(self, result):
        self.result = result

    def run_test(self, rel, ids=None):
        return dict(self.result)


def _tools(tmp_path, feedback, result=None):
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "m.py").write_text(GOOD)
    (tmp_path / "pkg" / "a.ts").write_text("export function f(x: number): number {\n  return x;\n}\n")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_m.py").write_text("def test_x():\n    pass\n")
    return SharedTools(str(tmp_path), _Env(result or {}), ["tests/test_m.py"], feedback=feedback)


# ---------------------------------------------------------------------------------------------- C1
def test_breaking_edit_is_applied_with_a_warning(tmp_path):
    t = _tools(tmp_path, feedback=True)
    out = t.call("edit_file", path="pkg/m.py", old=BROKEN_OLD, new=BROKEN_NEW)
    assert out.startswith("edited pkg/m.py at line 7")
    assert "warning: this edit was applied, but pkg/m.py no longer parses" in out
    assert "expected an indented block after 'else' statement on line 5" in out
    assert "GenericAlias" not in (tmp_path / "pkg" / "m.py").read_text()        # applied, not refused
    # a later edit that still leaves it broken says so; the one that fixes it is a normal edit
    out2 = t.call("edit_file", path="pkg/m.py", old="import types\n", new="import types  # x\n")
    assert "still does not parse" in out2
    out3 = t.call("edit_file", path="pkg/m.py", old="else:\n", new="else:\n    pass\n")
    assert "warning" not in out3


def test_js_ts_breaking_edit_and_created_files(tmp_path):
    t = _tools(tmp_path, feedback=True)
    out = t.call("edit_file", path="pkg/a.ts", old="): number {", new="): number")
    assert "no longer parses" in out and "pkg/a.ts" in out
    created = t.call("edit_file", path="pkg/new.py", old="", new="def f(:\n")
    assert created.startswith("created pkg/new.py") and "no longer parses" in created
    ok = t.call("edit_file", path="pkg/ok.py", old="", new="def f():\n    return 1\n")
    assert "warning" not in ok


def test_feedback_off_is_unchanged(tmp_path):
    t = _tools(tmp_path, feedback=False)
    out = t.call("edit_file", path="pkg/m.py", old=BROKEN_OLD, new=BROKEN_NEW)
    assert out == "edited pkg/m.py at line 7: replaced 2 lines with 1"


# ---------------------------------------------------------------------------------------------- C2
def _failing_result(text):
    return {"parsed": True, "passed": 13, "failed": 13, "skipped": 2,
            "tests": {f"tests.test_types::test_case_{i}": "failed" for i in range(13)},
            "tail": text[-1200:], "text": text}


def test_run_tests_shows_the_first_real_error(tmp_path):
    assert "IndentationError" not in PYTEST_OUT[-1200:]                         # what the agent got before
    text = PYTEST_OUT.replace("/work/", str(tmp_path) + "/")                   # the error names a file in the repo
    t = _tools(tmp_path, feedback=True, result=_failing_result(text))
    out = t.call("run_tests")
    assert "first error in the output:" in out
    assert 'File "multidict/_multidict_py.py", line 17' in out                  # the work dir prefix removed
    assert "IndentationError: expected an indented block after 'else' statement on line 12" in out
    off = _tools(tmp_path, feedback=False, result=_failing_result(text)).call("run_tests")
    assert "first error in the output:" not in off and "IndentationError" not in off


def test_first_error_cases():
    assert first_error("12 passed in 0.1s\n") is None
    js = "FAIL test/a.test.ts\n  SyntaxError: Unexpected token (3:5)\n    at foo\n"
    assert first_error(js).strip() == "SyntaxError: Unexpected token (3:5)"
    tb = ('Traceback (most recent call last):\n  File "src/x.py", line 3, in <module>\n    import y\n'
          "ModuleNotFoundError: No module named 'y'\n")
    fe = first_error(tb)
    assert fe.startswith('  File "src/x.py", line 3') and fe.endswith("No module named 'y'")
    assert first_error("tests/test_a.py:12: AssertionError\n") is None          # a location line, not the error


# ---------------------------------------------------------------------------------------------- C3
class _Shared:
    done_summary = None

    def __init__(self):
        self.n = 0

    def call(self, tool, **args):
        if tool == "done":
            return "done"
        if tool == "run_tests":
            self.n += 1
            return f"run {self.n}"                # a changing answer: never a repeat
        return f"{tool} {json.dumps(args, sort_keys=True)}"


class _Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, msgs, seed):
        return {"text": self.replies.pop(0), "thinking": None, "prompt_tokens": 10, "answer_tokens": 2,
                "done_reason": None, "seconds": 0.0}


R = '{"tool": "read_file", "args": {"path": "a.py"}}'
T = '{"tool": "run_tests", "args": {}}'
D = '{"tool": "done", "args": {"summary": "ok"}}'


@pytest.mark.parametrize("version", ["BASELINE", "PRISM", "INJECTED"])
def test_repeat_note_in_every_arm(tmp_path, version):
    L.AgentRun({"id": "t", "prompt": "p"}, version, 42, _Scripted([R, T, R, T, D]), _Shared(),
               feedback=True).run(str(tmp_path))
    log = [json.loads(ln) for ln in open(tmp_path / f"t__{version}__42.jsonl")]
    assert "repeat_of_turn" not in log[0] and log[2]["repeat_of_turn"] == 1
    assert "repeat_of_turn" not in log[3]                                       # run_tests answered differently


def test_no_repeat_note_without_feedback(tmp_path):
    L.AgentRun({"id": "t", "prompt": "p"}, "BASELINE", 42, _Scripted([R, R, D]), _Shared()).run(str(tmp_path))
    log = [json.loads(ln) for ln in open(tmp_path / "t__BASELINE__42.jsonl")]
    assert all("repeat_of_turn" not in x for x in log)


def test_repeats_unit():
    r = Repeats()
    assert r.check(1, "grep", {"pattern": "x"}, "a") is None
    assert r.check(2, "grep", {"pattern": "x"}, "a") == 1
    assert r.check(3, "grep", {"pattern": "x"}, "b") is None                    # the answer changed
    assert r.check(4, "grep", {"pattern": "x"}, "b") == 3
    assert "turn 3" in Repeats.note(3)


def test_syntax_problem_and_warning_units():
    assert syntax_problem("a.py", "x = 1\n") is None and syntax_problem("a.md", "((") is None
    assert syntax_problem("a.py", "def f(:\n").startswith("line 1")
    assert edit_warning("a.py", "x = 1\n", "x = 2\n") == ""
