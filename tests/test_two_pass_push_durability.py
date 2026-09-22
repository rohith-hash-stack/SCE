"""Pilot-4 prep, Fix 4: `benchmarks.run_two_pass_benchmark._push_checkpoint_two_pass`
- the two-pass harness's own copy of `benchmarks.runner._push_checkpoint`'s
periodic git-push durability, keyed off `TWO_PASS_RESULTS_BRANCH`
instead of `PILOT_RESULTS_BRANCH`. Pure - no network, no real git
invocation (mocked, exactly like `tests/test_runner_checkpoint.py`'s
own `_push_checkpoint` tests).
"""
from __future__ import annotations

from unittest.mock import patch

from benchmarks.run_two_pass_benchmark import _push_checkpoint_two_pass


def test_push_checkpoint_disabled_when_env_unset(monkeypatch):
    """No TWO_PASS_RESULTS_BRANCH means no push at all - not even an
    attempted subprocess call - since there's no default branch to
    push to."""
    monkeypatch.delenv("TWO_PASS_RESULTS_BRANCH", raising=False)

    with patch("benchmarks.run_two_pass_benchmark.subprocess.run") as mock_run:
        _push_checkpoint_two_pass(100)

    mock_run.assert_not_called()


def test_push_checkpoint_attempted_when_env_set(monkeypatch):
    """With TWO_PASS_RESULTS_BRANCH set, _push_checkpoint_two_pass
    attempts the add/commit/push subprocess calls against reports/
    (not reports/pilot/ - this harness's own checkpoint can live under
    either reports/pilot/ or reports/pilot_two_pass_full/ depending on
    the caller) - mocked here, never a real git invocation."""
    monkeypatch.setenv("TWO_PASS_RESULTS_BRANCH", "fake-two-pass-branch")

    with patch("benchmarks.run_two_pass_benchmark.subprocess.run") as mock_run:
        _push_checkpoint_two_pass(100)

    assert mock_run.called
    commands = [call.args[0] for call in mock_run.call_args_list]
    assert any(cmd[:2] == ["git", "-C"] and "add" in cmd and "reports/" in cmd for cmd in commands)
    assert any(cmd[:2] == ["git", "-C"] and "commit" in cmd for cmd in commands)
    assert any(cmd[:2] == ["git", "-C"] and "push" in cmd for cmd in commands)
    push_cmd = next(cmd for cmd in commands if "push" in cmd)
    assert push_cmd[-1] == "HEAD:fake-two-pass-branch"


def test_push_checkpoint_swallows_subprocess_failure(monkeypatch):
    """A failed git call (network down, nothing to commit, rejected
    push) must never propagate out of _push_checkpoint_two_pass - the
    run itself must survive it."""
    monkeypatch.setenv("TWO_PASS_RESULTS_BRANCH", "fake-two-pass-branch")

    with patch("benchmarks.run_two_pass_benchmark.subprocess.run", side_effect=OSError("no network")):
        _push_checkpoint_two_pass(100)  # must not raise
