"""G43 regression coverage: `engine_commit_hash()` was HEAD-only
(`git rev-parse HEAD`), so it could not distinguish "the committed code
at this HEAD" from "this HEAD plus an uncommitted edit to a tracked
file" - a `prism.cache.sqlite_cache` disk row written while an
uncommitted edit to a four-axis module was in effect could be served
again later at the same HEAD (once the edit was committed or reverted),
carrying a value computed under different code than a fresh call would
produce. Fixed by folding a hash of `git status --porcelain` + `git
diff` output into the key alongside HEAD.

Runs real git operations against a hermetic temp repo standing in for
the SCE checkout - never mutates the real repository's own source
files - by monkeypatching `_cache_keys._run_git_head`/
`_run_git_status_hash` to redirect their `cwd` argument, so the
composition logic under test (`engine_commit_hash()` itself: head-only
when clean, `head+dirty[:8]` when dirty) runs unmodified against real
git state.
"""
from __future__ import annotations

import subprocess

import pytest

from prism.cache import sqlite_cache
from prism.traversal import _cache_keys

_SYMBOLS = [{"qualified_name": "svc.foo", "kind": "function"}]
_BITMASKS = {"svc.foo": 42}

_original_run_git_head = _cache_keys._run_git_head
_original_run_git_status_hash = _cache_keys._run_git_status_hash


@pytest.fixture(autouse=True)
def _clear_engine_commit_hash_cache():
    _cache_keys.engine_commit_hash.cache_clear()
    yield
    _cache_keys.engine_commit_hash.cache_clear()


def _init_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True, capture_output=True)
    (path / "extractor.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _redirect_to(engine_repo, monkeypatch):
    monkeypatch.setattr(_cache_keys, "_run_git_head", lambda cwd: _original_run_git_head(engine_repo))
    monkeypatch.setattr(_cache_keys, "_run_git_status_hash", lambda cwd: _original_run_git_status_hash(engine_repo))


def test_cache_row_written_under_clean_head_misses_after_a_dirty_edit_and_hits_again_after_revert(tmp_path, monkeypatch):
    engine_repo = tmp_path / "engine_repo"
    engine_repo.mkdir()
    _init_repo(engine_repo)
    _redirect_to(engine_repo, monkeypatch)

    data_repo = str(tmp_path / "data_repo")

    # Write a cache row under clean HEAD.
    sqlite_cache.save_file_cache_entry(data_repo, "svc.py", "content-hash-1", 0.0, _SYMBOLS, _BITMASKS, [])
    assert sqlite_cache.load_file_cache_entry(data_repo, "svc.py", "content-hash-1") is not None

    # Make an uncommitted edit to extractor.py and let the next call see it
    # (engine_commit_hash() is itself process-cached, same as the real fix).
    (engine_repo / "extractor.py").write_text("x = 1\ny = 2  # uncommitted\n")
    _cache_keys.engine_commit_hash.cache_clear()

    # The next read misses - the key now differs from the one the row was written under.
    assert sqlite_cache.load_file_cache_entry(data_repo, "svc.py", "content-hash-1") is None

    # Revert the edit.
    (engine_repo / "extractor.py").write_text("x = 1\n")
    _cache_keys.engine_commit_hash.cache_clear()

    # The next read hits again - the key matches the original.
    entry = sqlite_cache.load_file_cache_entry(data_repo, "svc.py", "content-hash-1")
    assert entry is not None
    assert entry["feature_bitmasks"] == _BITMASKS


def test_dirty_key_extends_rather_than_replaces_the_clean_head_key(tmp_path, monkeypatch):
    engine_repo = tmp_path / "engine_repo"
    engine_repo.mkdir()
    _init_repo(engine_repo)
    _redirect_to(engine_repo, monkeypatch)

    clean_key = _cache_keys.engine_commit_hash()
    _cache_keys.engine_commit_hash.cache_clear()

    (engine_repo / "extractor.py").write_text("x = 1\ny = 2  # uncommitted\n")
    dirty_key = _cache_keys.engine_commit_hash()

    assert dirty_key != clean_key
    assert dirty_key.startswith(clean_key + "+")


def test_non_git_directory_degrades_to_a_no_op(tmp_path):
    """Graceful fallback: an installed (non-git) checkout must not error
    or otherwise change `engine_commit_hash()`'s existing HEAD-only
    behavior."""
    assert _cache_keys._run_git_status_hash(tmp_path) == ""
