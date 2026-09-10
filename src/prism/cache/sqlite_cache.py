"""v1.1 Part 4: SQLite Cache Schema Upgrade (v2) - `file_cache_v2`.

**Scope, stated honestly up front**: this is a *per-file* cache for the
v1.1 four-axis extraction layer specifically (`prism.semantics.substance`/
`form`/`output` and `prism.traversal`'s data-flow/guard indicators) - it
is not a second, competing whole-repository index cache.
`prism.runtime.index_cache` (Item 12, first post-implementation audit)
already exists for that, and its own module docstring documents at
length *why* a per-file cache is unsafe for Pass 2's cross-file call
linking (a changed file can change what an *unrelated* file's call sites
resolve to). That reasoning does not carry over unchanged here, because
what this cache stores is different in kind:

  - `feature_bitmasks` for `SINK_*`/`FORM_*`/`OUTPUT_*` bits
    (`prism.semantics.substance`'s *direct* sinks, `form`, `output`) are
    computed purely from one function's own AST body plus its own file's
    import map - genuinely file-local, unaffected by anything in any
    other file changing.
  - `local_data_flow` edges are computed from one function's own AST body
    plus the *already-linked* `G_C` graph's out-edges for that one
    function (`prism.traversal._data_flow_common._resolve_call_sites`) -
    the specific *symbol names* this file's own call sites resolve to
    could in principle shift if an unrelated file's changes altered
    cross-file resolution, so a `local_data_flow` cache entry is only
    trusted when the *whole-repository* index cache
    (`prism.runtime.index_cache`) is *also* a hit for the same run (i.e.
    nothing anywhere in the repository changed) - `load_file_cache_entry`
    takes a `require_whole_repo_cache_hit` flag for exactly this, and
    `prism.semantics.substance`'s transitive (one-hop wrapper) sink
    propagation and `prism.semantics.role`'s fan-in/fan-out Role bits are
    *never* persisted here at all (both are graph-shaped, not file-
    shaped, and cheap enough to recompute fresh from the cached direct
    bits every time - see `prism.semantics.extractor`).

Schema:

    CREATE TABLE IF NOT EXISTS file_cache_v2 (
        relative_path TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        mtime REAL NOT NULL,
        serialized_symbols BLOB NOT NULL,
        feature_bitmasks BLOB NOT NULL,
        local_data_flow BLOB NOT NULL
    )

On a file scan: if `(relative_path, content_hash)` matches an existing
row, the symbols/feature-bitmasks/local-data-flow are deserialized
directly from SQLite and tree-sitter/AST extraction is skipped entirely
for that file; a changed or new file always falls through to a real
extraction pass (which then overwrites/inserts its own row for next
time).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

#: Bumped whenever the serialized row shape changes - an old cache row
#: from a prior schema version is simply treated as a miss (never a
#: deserialization crash), the same convention `prism.runtime.
#: index_cache`'s own `_SCHEMA_VERSION` already establishes.
SCHEMA_VERSION = 2


def sqlite_cache_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "cache" / "features_v2.db"


def _connect(repo_root: str) -> sqlite3.Connection:
    path = sqlite_cache_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS file_cache_v2 ("
        "relative_path TEXT PRIMARY KEY, "
        "content_hash TEXT NOT NULL, "
        "mtime REAL NOT NULL, "
        "schema_version INTEGER NOT NULL, "
        "serialized_symbols BLOB NOT NULL, "
        "feature_bitmasks BLOB NOT NULL, "
        "local_data_flow BLOB NOT NULL"
        ")"
    )
    return conn


def save_file_cache_entry(
    repo_root: str,
    relative_path: str,
    content_hash: str,
    mtime: float,
    serialized_symbols: list[dict],
    feature_bitmasks: dict[str, int],
    local_data_flow: list[tuple[str, str, float]],
) -> None:
    """Persists one file's own v1.1 extraction results. Never raises on a
    write failure (read-only filesystem, full disk) - same "cache is an
    optimization, not a correctness dependency" contract `prism.runtime.
    index_cache.save_pipeline_to_cache` already establishes.
    """
    try:
        conn = _connect(repo_root)
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO file_cache_v2 "
                    "(relative_path, content_hash, mtime, schema_version, serialized_symbols, feature_bitmasks, local_data_flow) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        relative_path,
                        content_hash,
                        mtime,
                        SCHEMA_VERSION,
                        json.dumps(serialized_symbols),
                        json.dumps(feature_bitmasks),
                        json.dumps(local_data_flow),
                    ),
                )
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return


def load_file_cache_entry(
    repo_root: str,
    relative_path: str,
    content_hash: str,
) -> dict | None:
    """Returns `{"serialized_symbols": ..., "feature_bitmasks": ...,
    "local_data_flow": ...}` iff a row exists for `relative_path` with a
    matching `content_hash` and the current `SCHEMA_VERSION` - `None` on
    any miss, corruption, or read failure (never raises).

    Callers that also need `local_data_flow` to be trustworthy (not just
    `feature_bitmasks`) must additionally confirm nothing else in the
    repository changed this run - see this module's own docstring for
    why - typically by checking `prism.runtime.index_cache`'s own
    whole-repository cache was *also* a hit before trusting the
    `local_data_flow` half of a returned entry.
    """
    path = sqlite_cache_path(repo_root)
    if not path.exists():
        return None
    try:
        conn = _connect(repo_root)
        try:
            row = conn.execute(
                "SELECT content_hash, schema_version, serialized_symbols, feature_bitmasks, local_data_flow "
                "FROM file_cache_v2 WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    cached_hash, schema_version, symbols_json, bitmasks_json, data_flow_json = row
    if cached_hash != content_hash or schema_version != SCHEMA_VERSION:
        return None
    try:
        return {
            "serialized_symbols": json.loads(symbols_json),
            "feature_bitmasks": {k: int(v) for k, v in json.loads(bitmasks_json).items()},
            "local_data_flow": [tuple(e) for e in json.loads(data_flow_json)],
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def invalidate_file_cache_entry(repo_root: str, relative_path: str) -> None:
    try:
        conn = _connect(repo_root)
        try:
            with conn:
                conn.execute("DELETE FROM file_cache_v2 WHERE relative_path = ?", (relative_path,))
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return


def clear_file_cache(repo_root: str) -> None:
    try:
        conn = _connect(repo_root)
        try:
            with conn:
                conn.execute("DELETE FROM file_cache_v2")
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return
