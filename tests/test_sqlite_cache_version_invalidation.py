"""G39 regression coverage: `prism.cache.sqlite_cache`'s `file_cache_v2`
must miss (and force a real recompute) when the *engine's* version
changes, not just when a file's own content changes.

Before this fix, the cache key was `content_hash` alone - a row
written under one Prism build/grammar set would be served forever to
a later build/grammar set as long as the source file itself never
changed, silently returning stale `feature_bitmasks`/`local_data_flow`
computed by code that may no longer exist. Fixed by folding
`engine_commit_hash()`/`grammar_version()` into the value actually
compared against the stored `content_hash` column.
"""
from __future__ import annotations

from prism.cache import sqlite_cache

_SYMBOLS = [{"qualified_name": "svc.foo", "kind": "function"}]
_BITMASKS = {"svc.foo": 42}
_DATA_FLOW = [("svc.foo", "svc.bar", 0.9)]


def _write(repo, content_hash="abc123"):
    sqlite_cache.save_file_cache_entry(
        repo, "svc.py", content_hash, 0.0, _SYMBOLS, _BITMASKS, _DATA_FLOW
    )


def test_hit_after_write_with_unchanged_versions(tmp_path, monkeypatch):
    repo = str(tmp_path)
    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v1")
    monkeypatch.setattr(sqlite_cache, "grammar_version", lambda: "grammar-v1")

    _write(repo)
    entry = sqlite_cache.load_file_cache_entry(repo, "svc.py", "abc123")

    assert entry is not None
    assert entry["feature_bitmasks"] == _BITMASKS


def test_engine_version_bump_forces_a_miss(tmp_path, monkeypatch):
    repo = str(tmp_path)
    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v1")
    monkeypatch.setattr(sqlite_cache, "grammar_version", lambda: "grammar-v1")
    _write(repo)

    # Same file content_hash, same grammar - only the engine build changed.
    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v2")
    entry = sqlite_cache.load_file_cache_entry(repo, "svc.py", "abc123")

    assert entry is None, "a stale row from an older engine build must not be served"


def test_grammar_version_bump_forces_a_miss(tmp_path, monkeypatch):
    repo = str(tmp_path)
    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v1")
    monkeypatch.setattr(sqlite_cache, "grammar_version", lambda: "grammar-v1")
    _write(repo)

    # Same file content_hash, same engine build - only the installed
    # tree-sitter grammar set changed.
    monkeypatch.setattr(sqlite_cache, "grammar_version", lambda: "grammar-v2")
    entry = sqlite_cache.load_file_cache_entry(repo, "svc.py", "abc123")

    assert entry is None, "a stale row from a different grammar set must not be served"


def test_reverting_the_version_bump_restores_the_hit(tmp_path, monkeypatch):
    """Not just "always misses after any change" - the miss is real and
    version-specific, not an accidental permanent invalidation."""
    repo = str(tmp_path)
    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v1")
    monkeypatch.setattr(sqlite_cache, "grammar_version", lambda: "grammar-v1")
    _write(repo)

    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v2")
    assert sqlite_cache.load_file_cache_entry(repo, "svc.py", "abc123") is None

    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v1")
    entry = sqlite_cache.load_file_cache_entry(repo, "svc.py", "abc123")
    assert entry is not None
    assert entry["feature_bitmasks"] == _BITMASKS


def test_unchanged_content_hash_with_unchanged_versions_still_matches_across_a_rewrite(tmp_path, monkeypatch):
    """A rewrite under the *same* engine/grammar versions (the ordinary
    warm-cache case, nothing to do with G39) must still hit - this
    fix must not turn every read into an accidental miss."""
    repo = str(tmp_path)
    monkeypatch.setattr(sqlite_cache, "engine_commit_hash", lambda: "engine-v1")
    monkeypatch.setattr(sqlite_cache, "grammar_version", lambda: "grammar-v1")

    _write(repo)
    _write(repo)  # simulate a second pass writing the same row again
    entry = sqlite_cache.load_file_cache_entry(repo, "svc.py", "abc123")

    assert entry is not None
    assert entry["feature_bitmasks"] == _BITMASKS
