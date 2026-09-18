"""Phase H, Issue #45: deterministic content-hash AST cache.

`ConcreteGraphBuilder.pass1_collect_definitions` (`prism.graph.
concrete_builder`) calls `tree_sitter_loader.parse_file` once per file,
every time a `ConcreteGraphBuilder` is constructed - `_parsed_files` is
an in-memory dict scoped to that one instance (see its own docstring),
not shared across instances. Within a single process, the same
unchanged file is routinely re-parsed from scratch many times over:
a benchmark loop rebuilding the same corpus repeatedly, the test suite
calling `build_pipeline` on the same fixture repo across many tests,
or an MCP server instance re-indexing after an unrelated file elsewhere
in the repo changed. Tree-sitter parsing is real, non-trivial work
(a fresh parse of every file's full source, every time) - this module
gives repeated `parse_file` calls against byte-identical content a real
cache hit instead.

Deliberately in-memory only, not persisted to disk: a tree-sitter
`Node`/`Tree` is not picklable, and `prism.runtime.index_cache`'s own
docstring already documents exactly this constraint as the reason its
own (separate, whole-repo, symbol-table-level) cache declines to
persist parsed ASTs across processes. A disk-backed AST cache would
need its own serialize/reparse-on-load strategy - out of scope here;
this module addresses the same-process redundant-reparse waste, which
is where it actually occurs (repo re-indexing within one long-lived
process), not cross-process persistence.

Cache key: `f"{posix_path}:{sha256(content).hexdigest()}"` - content-
addressed, not mtime-addressed, so a file that changes and changes back
(or two files with identical content) behave exactly as their bytes
say they should, with no clock-skew or touch-without-edit false
invalidation/false hit. `PRISM_DISABLE_CACHE=1` bypasses the cache
completely (checked fresh on every call, not once at import time, so a
test can toggle it via `monkeypatch.setenv` mid-run) - every call
becomes a real, uncached `parse_source`, for deterministic test
isolation or to rule the cache out as a variable when debugging a
parse-related issue.
"""
from __future__ import annotations

import hashlib
import os
import threading

from prism.parser.tree_sitter_loader import ParsedFile, parse_source

_cache: dict[str, ParsedFile] = {}
_lock = threading.Lock()


def _cache_disabled() -> bool:
    return os.environ.get("PRISM_DISABLE_CACHE") == "1"


def _posix_path(path: str) -> str:
    return path.replace("\\", "/")


def cache_key(path: str, content: bytes) -> str:
    """`f"{posix_path}:{sha256}"` - exposed for tests/diagnostics that
    want to assert a specific entry's presence directly, not just
    observe cache-hit behavior indirectly."""
    digest = hashlib.sha256(content).hexdigest()
    return f"{_posix_path(path)}:{digest}"


def parse_file_cached(path: str) -> ParsedFile | None:
    """Same contract as `tree_sitter_loader.parse_file`: `None` for an
    unregistered extension or an unreadable path, a real `ParsedFile`
    otherwise - this is a drop-in replacement for that function's own
    call sites, not a new API shape callers need to adapt to.

    Reads `path` itself (rather than accepting pre-read bytes) so the
    content hash always reflects the exact bytes about to be parsed -
    the same file-read `parse_file` already does internally, just done
    once here up front so it can also serve as the cache key input.
    """
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError:
        return None

    if _cache_disabled():
        return parse_source(path, content)

    key = cache_key(path, content)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    parsed = parse_source(path, content)
    if parsed is not None:
        with _lock:
            _cache[key] = parsed
    return parsed


def clear_cache() -> None:
    """Test/diagnostic use: drop every cached entry so the next
    `parse_file_cached` call for any path is guaranteed a real miss."""
    with _lock:
        _cache.clear()


def cache_size() -> int:
    with _lock:
        return len(_cache)
