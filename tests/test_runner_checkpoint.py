"""Phase 1.4 (DeepSeek pilot setup) regression coverage for
`benchmarks.runner`'s checkpointing helpers: `_cell_key`,
`load_checkpoint`, `save_checkpoint`, `_push_checkpoint`. Pure,
filesystem-only - no network, no LLM client, no real corpus (the git
subprocess calls in `_push_checkpoint`'s own tests are mocked, never
real).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from benchmarks.runner import _cell_key, _push_checkpoint, load_checkpoint, save_checkpoint


def test_cell_key_is_a_plain_pipe_joined_string():
    assert _cell_key("django_t02_001", "prism_v11", 4000, 42) == "django_t02_001|prism_v11|4000|42"


def test_load_checkpoint_missing_file_returns_empty():
    checkpoint = load_checkpoint("/nonexistent/path/checkpoint.json")
    assert checkpoint == {"cells": {}}


def test_load_checkpoint_corrupt_json_returns_empty(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("{not valid json")

    assert load_checkpoint(str(path)) == {"cells": {}}


def test_load_checkpoint_wrong_shape_returns_empty(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(["not", "a", "dict", "with", "cells"]))

    assert load_checkpoint(str(path)) == {"cells": {}}


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "checkpoint.json")
    checkpoint = {
        "cells": {
            "django_t02_001|prism_v11|4000|42": {
                "score": 1.0, "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.0002,
            }
        }
    }

    save_checkpoint(path, checkpoint)
    loaded = load_checkpoint(path)

    assert loaded == checkpoint


def test_save_checkpoint_creates_parent_directories(tmp_path):
    path = tmp_path / "reports" / "pilot" / "checkpoint.json"
    assert not path.parent.exists()

    save_checkpoint(str(path), {"cells": {}})

    assert path.exists()


def test_push_checkpoint_is_a_noop_when_env_var_unset(monkeypatch):
    """fix-push-inside-runner: no PILOT_RESULTS_BRANCH means no push at
    all - not even an attempted subprocess call - since there's no
    default branch to push to."""
    monkeypatch.delenv("PILOT_RESULTS_BRANCH", raising=False)

    with patch("benchmarks.runner.subprocess.run") as mock_run:
        _push_checkpoint(100)

    mock_run.assert_not_called()


def test_push_checkpoint_attempts_subprocess_when_env_var_set(monkeypatch):
    """With PILOT_RESULTS_BRANCH set, _push_checkpoint attempts the
    add/commit/push subprocess calls - mocked here, never a real git
    invocation."""
    monkeypatch.setenv("PILOT_RESULTS_BRANCH", "fake-results-branch")

    with patch("benchmarks.runner.subprocess.run") as mock_run:
        _push_checkpoint(100)

    assert mock_run.called
    commands = [call.args[0] for call in mock_run.call_args_list]
    assert any(cmd[:2] == ["git", "-C"] and "add" in cmd for cmd in commands)
    assert any(cmd[:2] == ["git", "-C"] and "commit" in cmd for cmd in commands)
    assert any(cmd[:2] == ["git", "-C"] and "push" in cmd for cmd in commands)
    push_cmd = next(cmd for cmd in commands if "push" in cmd)
    assert push_cmd[-1] == "HEAD:fake-results-branch"


def test_push_checkpoint_swallows_subprocess_failure(monkeypatch):
    """A failed git call (network down, nothing to commit, rejected
    push) must never propagate out of _push_checkpoint - the pilot run
    itself must survive it."""
    monkeypatch.setenv("PILOT_RESULTS_BRANCH", "fake-results-branch")

    with patch("benchmarks.runner.subprocess.run", side_effect=OSError("no network")):
        _push_checkpoint(100)  # must not raise


def test_default_checkpoint_path_is_never_written_by_tests():
    """fix-test-checkpoint-isolation: proves the session-wide guard
    (`tests/conftest.py`'s autouse `_guard_default_pilot_checkpoint_path`
    fixture) actually fires - a direct `save_checkpoint(DEFAULT_
    CHECKPOINT_PATH, ...)` call (exactly what a test that forgets its
    own `checkpoint_path=` would fall back to) must raise instead of
    touching the real pilot-1 baseline file, and the error must name
    this test so a real violation elsewhere in the suite is easy to
    trace back to its source.

    This is the guard's own self-test, not a scan of the rest of the
    suite: the fixture protects every other test by construction
    (session-scoped, autouse, active before any test body runs), so
    there is nothing this one test could additionally observe about
    another test's own execution - proving the interception mechanism
    itself is real and armed is the actual coverage gap worth closing.
    """
    import benchmarks.runner as runner_module

    with pytest.raises(AssertionError, match="pilot-1 baseline checkpoint"):
        runner_module.save_checkpoint(runner_module.DEFAULT_CHECKPOINT_PATH, {"cells": {}})
