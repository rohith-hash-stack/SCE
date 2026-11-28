"""The builders' `_run` kills the whole process group, so a test's leftover children do not outlive the call."""
import os
import time

from harness.experiments.r3.jsbuild import _run


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # a zombie still answers kill(0); it holds no memory
        return open(f"/proc/{pid}/stat").read().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_leftover_child_is_killed(tmp_path):
    pidfile = tmp_path / "pid"
    rc, out, _ = _run(["sh", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {pidfile}; echo ok"], str(tmp_path), None, 30)
    assert rc == 0 and "ok" in out
    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid)


def test_timeout_kills_group(tmp_path):
    pidfile = tmp_path / "pid"
    rc, out, secs = _run(["sh", "-c", f"sleep 300 & echo $! > {pidfile}; sleep 300"], str(tmp_path), None, 1)
    assert rc == 124 and secs < 30
    pid = int(pidfile.read_text())
    time.sleep(0.5)
    assert not _alive(pid)
