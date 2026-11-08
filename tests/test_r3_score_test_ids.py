"""R3 scoring: a test id that records the gate's worktree path matches the same test in a run's worktree (r3a-B-71)."""
import os

from harness.experiments.r3.runner import id_status


def test_gate_worktree_path_in_an_id_is_read_as_the_run_worktree():
    work = "/x/r3run_abc/work"
    gate_id = "t::test_ctx[/tmp/r3gate_B_ifmlzv6d/w/tests/console/test_app.py---directory]"
    tests = {f"t::test_ctx[{os.path.abspath(work)}/tests/console/test_app.py---directory]": "passed"}
    assert id_status(tests, gate_id, work) == "passed"


def test_exact_ids_are_unchanged_and_absent_ids_are_missing():
    tests = {"t::a[/invalid/path--C]": "failed"}
    assert id_status(tests, "t::a[/invalid/path--C]", "/w") == "failed"
    assert id_status(tests, "t::b", "/w") == "missing"
    assert id_status({}, "t::c[/tmp/r3gate_B_q/w/x.py]", "/w") == "missing"
