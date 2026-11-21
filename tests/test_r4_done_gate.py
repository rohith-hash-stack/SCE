"""Amendment R4-SANITY-2: the verification gate at done (`harness/experiments/r4/done_gate.py`)."""
import os
import subprocess

from harness.experiments.r3.runner import snapshot_tree
from harness.experiments.r4.done_gate import DoneGate


class Env:
    """Fake test runner: a test file's lines are `name: ok|fail`; a line `BROKEN` makes the file fail to load."""

    def __init__(self, work):
        self.work = work

    def run_test(self, rel):
        text = open(os.path.join(self.work, rel)).read()
        if "BROKEN" in text:
            return {"parsed": True, "failed": 1, "tests": {"::" + rel: "failed"},
                    "text": "E   ImportError: cannot import name 'gone'"}
        tests = {}
        for ln in text.splitlines():
            if ":" in ln:
                name, st = (x.strip() for x in ln.split(":", 1))
                tests[name] = "passed" if st == "ok" else "failed"
        return {"parsed": True, "failed": sum(s == "failed" for s in tests.values()), "tests": tests,
                "text": "E   AssertionError: boom" if "failed" in tests.values() else ""}


def _repo(tmp_path, files):
    w = str(tmp_path / "w")
    os.makedirs(w)
    for rel, text in files.items():
        p = os.path.join(w, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)
    for c in (["init", "-q"], ["add", "-A"], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "s"]):
        subprocess.run(["git", *c], cwd=w, check=True, capture_output=True)
    return w, snapshot_tree(w)


SRC = "def keep():\n    return 1\n\n\ndef gone():\n    return 2\n"


def _delete_task():
    return {"family": "delete", "must_be_absent": "pkg/m.py::gone",
            "visible_start_pass": {"tests/t.txt": ["test_keep", "test_gone"]}}


def test_no_change_is_rejected(tmp_path):
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_keep: ok\ntest_gone: ok\n"})
    ok, msg = DoneGate(_delete_task(), w, Env(w), st).check()
    assert not ok and "no file has been changed" in msg


def test_delete_target_still_defined_is_rejected_with_its_line(tmp_path):
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_keep: ok\ntest_gone: ok\n"})
    open(os.path.join(w, "pkg/m.py"), "a").write("\n# touched\n")
    ok, msg = DoneGate(_delete_task(), w, Env(w), st).check()
    assert not ok and "`gone` is still defined in pkg/m.py at line 5" in msg


def test_delete_regression_and_broken_file_are_rejected_deleted_tests_are_not(tmp_path):
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_keep: ok\ntest_gone: ok\n"})
    open(os.path.join(w, "pkg/m.py"), "w").write("def keep():\n    return 1\n")
    open(os.path.join(w, "tests/t.txt"), "w").write("test_keep: fail\ntest_gone: ok\n")
    ok, msg = DoneGate(_delete_task(), w, Env(w), st).check()
    assert not ok and "1 tests that passed at the start now fail: tests/t.txt::test_keep" in msg
    assert "AssertionError" in msg and "call" not in msg.lower().replace("calls", "")   # no call sites
    open(os.path.join(w, "tests/t.txt"), "w").write("BROKEN\n")
    ok, msg = DoneGate(_delete_task(), w, Env(w), st).check()
    assert not ok and "test file tests/t.txt now fails to load or run" in msg and "ImportError" in msg
    open(os.path.join(w, "tests/t.txt"), "w").write("test_keep: ok\n")            # the target's test was deleted
    assert DoneGate(_delete_task(), w, Env(w), st).check() == (True, "done")
    os.remove(os.path.join(w, "tests/t.txt"))                                   # the whole file deleted
    assert DoneGate(_delete_task(), w, Env(w), st).check()[0]


def test_feature_checks_the_start_tests_and_puts_the_models_versions_back(tmp_path):
    w, st = _repo(tmp_path, {"pkg/m.py": "X = 0\n", "tests/f.txt": "test_new: fail\ntest_old: ok\n"})
    task = {"family": "feature", "test_files": ["tests/f.txt"], "visible_tests": ["tests/f.txt"],
            "fail_to_pass": {"tests/f.txt": ["test_new"]}, "pass_to_pass": {"tests/f.txt": ["test_old"]}}
    open(os.path.join(w, "tests/f.txt"), "w").write("test_new: ok\ntest_old: ok\n")   # the model "fixes" the test
    ok, msg = DoneGate(task, w, Env(w), st).check()
    assert not ok and "1 of the listed tests still fail: tests/f.txt::test_new" in msg
    assert open(os.path.join(w, "tests/f.txt")).read() == "test_new: ok\ntest_old: ok\n"   # its version is back


def test_feature_passes_when_the_listed_tests_pass(tmp_path):
    """With the fake runner a test's outcome is written in the test file, so the 'source fix' is simulated by a start
    state whose test already reads ok: the gate accepts once a file changed and every listed test passes."""
    w, st = _repo(tmp_path, {"pkg/m.py": "X = 0\n", "tests/f.txt": "test_new: ok\ntest_old: ok\n"})
    task = {"family": "feature", "test_files": ["tests/f.txt"], "visible_tests": ["tests/f.txt"],
            "fail_to_pass": {"tests/f.txt": ["test_new"]}, "pass_to_pass": {"tests/f.txt": ["test_old"]}}
    open(os.path.join(w, "pkg/m.py"), "w").write("X = 1\n")
    assert DoneGate(task, w, Env(w), st).check() == (True, "done")


def test_no_change_also_states_the_delete_target_status(tmp_path):
    """R4-SANITY-3 fix 1: the model that believes the target does not exist is told it does, and where."""
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_keep: ok\ntest_gone: ok\n"})
    t = {**_delete_task(), "target_display": "the function `gone`"}
    ok, msg = DoneGate(t, w, Env(w), st).check()
    assert not ok and "no file has been changed" in msg and "the function `gone` is still defined in pkg/m.py at line 5" in msg


def test_vanished_tests_in_an_unchanged_test_file_are_a_regression_not_a_deletion(tmp_path):
    """R4-SANITY-4 bug 2 (sanity 3's zod): some start-passing tests stop running while the file is untouched."""
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_keep: ok\ntest_gone: ok\n"})
    open(os.path.join(w, "pkg/m.py"), "w").write("def keep():\n    return 1\n")

    class HalfEnv(Env):                      # one of the suite's two copies no longer runs
        def run_test(self, rel):
            return {"parsed": True, "failed": 0, "tests": {"test_keep": "passed"},
                    "text": "Transform failed with 1 error:\nm.py:1:1: ERROR: Expected \":\" but found \"this\""}
    ok, msg = DoneGate(_delete_task(), w, HalfEnv(w), st).check()
    assert not ok and "1 tests that passed at the start did not run" in msg and "tests/t.txt::test_gone" in msg
    assert "ERROR: Expected" in msg


def test_a_test_the_model_removed_from_the_file_still_counts_as_deleted(tmp_path):
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_keep: ok\ntest_gone: ok\n"})
    open(os.path.join(w, "pkg/m.py"), "w").write("def keep():\n    return 1\n")
    open(os.path.join(w, "tests/t.txt"), "w").write("test_keep: ok\n")
    assert DoneGate(_delete_task(), w, Env(w), st).check() == (True, "done")


def test_a_parametrised_case_the_model_removed_counts_as_deleted(tmp_path):
    """litestar delete-09: the developer drops two parametrize cases; the test function stays and its other cases run."""
    w, st = _repo(tmp_path, {"pkg/m.py": SRC, "tests/t.txt": "test_p[a]: ok\ntest_p[b]: ok\ntest_p[c]: ok\n"})
    task = {"family": "delete", "must_be_absent": "pkg/m.py::gone",
            "visible_start_pass": {"tests/t.txt": ["test_p[a]", "test_p[b]", "test_p[c]"]}}
    open(os.path.join(w, "pkg/m.py"), "w").write("def keep():\n    return 1\n")
    open(os.path.join(w, "tests/t.txt"), "w").write("test_p[c]: ok\n")
    assert DoneGate(task, w, Env(w), st).check() == (True, "done")
