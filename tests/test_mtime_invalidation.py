"""Bookmark 1 Item 1: mtime-based file_hash_set invalidation.

Prism's whole staleness bug (works for batch CLI, broken for a
persistent MCP session): the old `target_repo_file_signature` used a
git-HEAD-only fast path, which detects a new commit but is blind to an
uncommitted edit - the exact case a live session hits on its first
file change. `_compute_file_hash_set` (`prism.traversal._cache_keys`)
now layers a real per-file mtime/size/hash scan underneath the
git-HEAD check specifically to catch that case, while still skipping
the expensive re-hash for files whose (mtime, size) haven't moved and
are safely stale (not a same-second or `cp -p`-style ambiguous edit).
"""
from __future__ import annotations

import os
import time

from prism.traversal._cache_keys import target_repo_file_signature

_SOURCE = "def parse_order(raw):\n    return raw\n\n\ndef store_order(data):\n    return data\n"


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    return repo


def test_edit_without_commit_invalidates(tmp_path):
    """The bug this whole item exists to fix: an uncommitted edit in a
    live (non-git) working tree must produce a different signature."""
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE + "\ndef new_function():\n    return 1\n")
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2


def test_touch_without_content_change_is_a_hit(tmp_path):
    """A `touch` (mtime moves, content doesn't) must NOT invalidate -
    the signature is built only from content hashes, never mtime/size."""
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    os.utime(repo / "x.py", None)  # bump mtime, content unchanged
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 == sig_2


def test_added_file_invalidates(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "y.py").write_text("def extra():\n    return 2\n")
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2


def test_deleted_file_invalidates(tmp_path):
    repo = _repo(tmp_path)
    (repo / "y.py").write_text("def extra():\n    return 2\n")
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "y.py").unlink()
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2


def test_mtime_skip_reuses_cached_hash_without_rereading_file(tmp_path, monkeypatch):
    """The actual performance mechanism: once a file's (mtime, size) is
    known-stable (older than the last scan by the stability window),
    its content must NOT be re-read/re-hashed on the next call."""
    import prism.traversal._cache_keys as ck

    repo = _repo(tmp_path)
    target_repo_file_signature(str(repo))  # cold: populates _REPO_SCAN_STATE

    # Push the recorded "last scan time" far into the future so every
    # tracked file's real mtime is unambiguously older than the
    # stability window, exactly as a real second call well after the
    # first would see it.
    resolved = str(repo.resolve())
    state = ck._REPO_SCAN_STATE[resolved]
    state.last_scan_time = time.time() + 1000.0

    call_count = {"n": 0}
    real_hash = ck._hash_file_bytes

    def counting_hash(path):
        call_count["n"] += 1
        return real_hash(path)

    monkeypatch.setattr(ck, "_hash_file_bytes", counting_hash)
    target_repo_file_signature(str(repo))
    assert call_count["n"] == 0, "a stable (mtime, size) match must skip re-hashing entirely"


def test_ambiguous_recent_mtime_forces_hash(tmp_path, monkeypatch):
    """Same-second-edit / cp -p edge case: a cached (mtime, size) match
    whose mtime is NOT safely older than the last scan must still be
    hashed, not trusted blindly."""
    import prism.traversal._cache_keys as ck

    repo = _repo(tmp_path)
    target_repo_file_signature(str(repo))

    resolved = str(repo.resolve())
    state = ck._REPO_SCAN_STATE[resolved]
    # Simulate "we just scanned a moment ago" - any file's mtime looks
    # recent/ambiguous relative to this, even with unchanged (mtime, size).
    state.last_scan_time = time.time() + 0.01

    call_count = {"n": 0}
    real_hash = ck._hash_file_bytes

    def counting_hash(path):
        call_count["n"] += 1
        return real_hash(path)

    monkeypatch.setattr(ck, "_hash_file_bytes", counting_hash)
    target_repo_file_signature(str(repo))
    assert call_count["n"] >= 1, "an ambiguous (recent) mtime must force a real hash, not a trusted skip"


def test_git_head_change_forces_full_rescan_and_invalidates(tmp_path):
    """First-pass fast path: a git-tracked repo's committed change is
    still detected (the case the old git-HEAD-only design handled) -
    a full rescan after a new commit must reflect the new content."""
    import subprocess

    repo = tmp_path / "gitrepo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE + "\ndef added():\n    return 3\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, check=True)

    sig_2 = target_repo_file_signature(str(repo))
    assert sig_1 != sig_2


def test_scan_performance_on_django_sized_repo():
    """Performance target: < 100ms warm-path scan on a ~2,700-file
    real corpus. Stop condition: must not exceed 500ms."""
    from benchmarks.corpora.resolver import resolve

    repo_path = str(resolve("django"))
    target_repo_file_signature(repo_path)  # cold: populate _REPO_SCAN_STATE first

    t0 = time.time()
    target_repo_file_signature(repo_path)  # warm path
    elapsed = time.time() - t0

    assert elapsed < 0.5, f"STOP CONDITION: warm scan took {elapsed:.3f}s, exceeds the 500ms hard cap"
    assert elapsed < 0.1, f"target not met: warm scan took {elapsed:.3f}s, target < 100ms"
