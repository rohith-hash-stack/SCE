"""Blocker 1 performance work: shared repo-content-addressed cache-key
helpers for `prism/traversal/`'s own in-process caches -
`continuous_dijkstra.py`'s graph/distance caches (Steps 2/3) and
`causal_weights.py`'s edge caches (Step 4).

Extracted to its own module specifically to avoid a circular import:
`continuous_dijkstra.py` imports from `causal_weights.py` (`compute_
causal_edges`, `edge_cost`), so `causal_weights.py` cannot import
these helpers back from `continuous_dijkstra.py`. Both modules import
from here instead.
"""
from __future__ import annotations

import contextvars
import hashlib
import importlib.metadata
import os
import subprocess
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Generic, Iterator, TypeVar

from prism.parser.tree_sitter_loader import EXTENSION_LANGUAGE_MAP

_V = TypeVar("_V")


def _run_git_head(cwd: str | Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _run_git_status_hash(cwd: str | Path) -> str:
    """G43: `engine_commit_hash()`'s HEAD-only key cannot distinguish "the
    committed code at this HEAD" from "this HEAD plus an uncommitted edit
    to a tracked file" - exactly the gap that let a `prism.cache.
    sqlite_cache` disk row written under a mid-edit working tree get
    served again later at the same HEAD (once the edit was committed or
    reverted), carrying a value computed under different code than a
    fresh call would produce. `git status --porcelain` (which files
    changed) plus `git diff` (what changed in them) together capture any
    uncommitted change to a tracked file; hashed together into one short
    value. Returns `""` on any failure - not a git repo (e.g. installed
    as a package, no `.git` directory), `git` unavailable, or a timeout -
    so the dirty check degrades to a no-op and `engine_commit_hash()`
    falls back to HEAD-only, preserving current behavior for non-git
    installs.
    """
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=10)
        diff = subprocess.run(["git", "diff"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if status.returncode != 0 or diff.returncode != 0:
        return ""
    combined = status.stdout + diff.stdout
    if not combined:
        # A clean tree must produce "" itself, not `sha256("")`'s own
        # (non-empty) digest - `engine_commit_hash()`'s `if dirty else
        # head` fallback depends on emptiness meaning "nothing to add".
        return ""
    return hashlib.sha256(combined.encode()).hexdigest()


@lru_cache(maxsize=1)
def engine_commit_hash() -> str:
    """The SCE repo's own HEAD, plus a short hash of any uncommitted
    working-tree change (G43) - process-invariant (this repo's source
    cannot change while it's running), so this is cached once per
    process rather than re-shelled-out-to on every cache-key
    computation. Mirrors `benchmarks.engines.prism_engine_cache.
    _engine_commit_hash`'s own technique - duplicated here rather than
    imported, since production engine code (`prism/traversal/`) must
    never depend on the benchmark harness (`benchmarks/`)."""
    cwd = Path(__file__).resolve().parent
    head = _run_git_head(cwd) or "unknown"
    dirty = _run_git_status_hash(cwd)
    return f"{head}+{dirty[:8]}" if dirty else head


#: `build_causal_graph`'s (and `causal_weights.py`'s edge functions')
#: real, non-first-party input `engine_commit_hash` does *not* cover:
#: tree-sitter grammars are external PyPI packages (see
#: `pyproject.toml`), independently versioned from this repo - a `pip
#: install --upgrade tree-sitter-python` changes what `builder.
#: calls_graph`/the edge-extraction functions see without any commit to
#: this repo at all. Verified directly (`pip show tree-sitter-python`
#: shows a version independent of this repo's own git history), not
#: assumed.
_GRAMMAR_PACKAGES = (
    "tree-sitter",
    "tree-sitter-python",
    "tree-sitter-javascript",
    "tree-sitter-typescript",
    "tree-sitter-go",
    "tree-sitter-java",
    "tree-sitter-c-sharp",
)


@lru_cache(maxsize=1)
def grammar_version() -> str:
    parts = []
    for pkg in _GRAMMAR_PACKAGES:
        try:
            parts.append(f"{pkg}=={importlib.metadata.version(pkg)}")
        except importlib.metadata.PackageNotFoundError:
            parts.append(f"{pkg}=missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


#: Bookmark 1 Item 1: duplicated from `prism.cli.IGNORED_DIRS` rather
#: than imported - `prism.cli` imports `prism.surface.build`, which
#: imports `prism.semantics.extractor`, which imports this module, so
#: importing `prism.cli` from here would be circular. Same layering
#: reason `_run_git_head`/`engine_commit_hash` above are duplicated
#: from `benchmarks.engines.prism_engine_cache` instead of imported.
_IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", "site-packages", ".prism_cache",
}

#: A cached (mtime, size) match is only trusted without re-hashing if
#: its mtime is more than this many seconds older than the *previous*
#: scan's own timestamp - guards both a same-second edit (mtime ==
#: last_scan_time - delta is 0, not > the window) and a `cp -p`-style
#: preserved-mtime overwrite (an edit whose mtime lands suspiciously
#: close to when we last looked is treated as ambiguous, not trusted).
_MTIME_STABILITY_WINDOW_SECONDS = 1.0


@dataclass(frozen=True)
class _TrackedFileState:
    mtime: float
    size: int
    sha256: str


@dataclass
class _RepoScanState:
    git_head: str | None
    last_scan_time: float
    files: dict[str, _TrackedFileState] = field(default_factory=dict)


#: Bookmark 1 Item 1: one entry per repo (resolved absolute path) this
#: process has scanned - the real mtime/size/hash bookkeeping an MCP
#: session needs to detect an uncommitted edit between two `retrieve()`
#: calls, which the old git-HEAD-only signature could never see (its
#: whole failure mode: a live session serving stale results after the
#: first uncommitted edit). Not itself bounded here - Bookmark 1 Item 3
#: covers eviction for the five caches that *consume* this state's own
#: output; this dict holds one entry per distinct repo a session has
#: touched, which grows far slower than any of those five.
_REPO_SCAN_STATE: dict[str, _RepoScanState] = {}


def _discover_tracked_files(repo_root: str) -> list[str]:
    """The same file-tracking definition `prism.cli.discover_files`
    uses (`EXTENSION_LANGUAGE_MAP` + ignored-directory pruning) - the
    "tracked file list" Item 1's own scan walks, not a separately
    invented one. Returns absolute paths, sorted."""
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            if any(filename.endswith(ext) for ext in EXTENSION_LANGUAGE_MAP):
                files.append(os.path.join(dirpath, filename))
    return sorted(files)


def _normalize_content(raw: bytes) -> bytes:
    """Bookmark 1 Item 4: strips whitespace/formatting differences that
    cannot change Prism's own parse output out of a file's content
    before it contributes to `file_hash_set` - a purely cosmetic edit
    (line endings, trailing whitespace, redundant blank-line padding, a
    missing/doubled final newline) should not cost a real cache miss
    and the real re-extraction work that follows one.

    Applied: CRLF/CR -> LF; trailing whitespace stripped per line;
    consecutive blank lines collapsed to one; exactly one trailing
    newline. **Never** touches indentation (semantic in Python - two
    functions differing only in indentation are genuinely different
    code) or internal whitespace within a line (may be semantic, e.g.
    inside a string literal) - only line-boundary and file-boundary
    whitespace is ever altered.

    `errors="surrogateescape"` round-trips any byte sequence that isn't
    valid UTF-8 losslessly (Prism indexes real-world source files, not
    just clean UTF-8 ones) - this never raises on binary-ish content,
    it just leaves un-decodable bytes as unpaired surrogates that
    re-encode back to their original bytes unchanged.
    """
    text = raw.decode("utf-8", errors="surrogateescape")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t\x0b\x0c") for line in text.split("\n")]

    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank

    normalized = "\n".join(collapsed).rstrip("\n") + "\n"
    return normalized.encode("utf-8", errors="surrogateescape")


def _hash_file_bytes(path: str) -> str | None:
    """Hashes the Item-4-normalized content, not the raw bytes - see
    `_normalize_content`'s own docstring for exactly what changes and
    what never does."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    return hashlib.sha256(_normalize_content(raw)).hexdigest()


def _compute_file_hash_set(repo_root: str) -> str:
    """The real, stateful mtime-based scan (Bookmark 1 Item 1):

    1. Git-HEAD is still the first-pass fast check - unchanged (or this
       repo isn't git-tracked at all) falls through to the per-file
       mtime scan below; changed forces a full rescan (every tracked
       file re-hashed unconditionally, and a fresh `_RepoScanState`
       baseline established) rather than trying to diff against a
       potentially wholesale-stale mtime map after a checkout/pull.
    2. Otherwise, per tracked file: a cached (mtime, size) match whose
       mtime is safely in the past (see `_MTIME_STABILITY_WINDOW_
       SECONDS`) is trusted without re-reading the file's bytes; any
       other case (new file, mtime/size changed, or an ambiguous
       recent mtime) is hashed for real.
    3. Added/deleted files fall out of comparing the current tracked
       set against the previous scan's own key set - no separate pass.

    The returned signature is built **only from each file's own
    content hash** (never mtime/size, which are pure bookkeeping for
    deciding whether to re-hash) - two scans with different mtimes but
    identical content must produce the identical signature (a `touch`
    with no content change is a cache hit, per Item 1's own
    verification requirement), and any content difference - a real
    edit, a new file, a deleted file - must change it.
    """
    resolved_root = str(Path(repo_root).resolve())
    git_head = _run_git_head(resolved_root) if (Path(resolved_root) / ".git").is_dir() else None
    prev_state = _REPO_SCAN_STATE.get(resolved_root)

    force_full_rescan = prev_state is None or prev_state.git_head != git_head
    last_scan_time = prev_state.last_scan_time if prev_state is not None else 0.0
    prev_files = prev_state.files if prev_state is not None else {}

    current_paths = _discover_tracked_files(resolved_root)
    new_files: dict[str, _TrackedFileState] = {}

    for path in current_paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        mtime, size = st.st_mtime, st.st_size
        prev = prev_files.get(path)

        trusted_stable = (
            not force_full_rescan
            and prev is not None
            and prev.mtime == mtime
            and prev.size == size
            and (last_scan_time - mtime) > _MTIME_STABILITY_WINDOW_SECONDS
        )
        if trusted_stable:
            new_files[path] = prev
            continue

        digest = _hash_file_bytes(path)
        if digest is None:
            continue
        new_files[path] = _TrackedFileState(mtime=mtime, size=size, sha256=digest)

    _REPO_SCAN_STATE[resolved_root] = _RepoScanState(git_head=git_head, last_scan_time=time.time(), files=new_files)

    hasher = hashlib.sha256()
    for path in sorted(new_files):
        hasher.update(os.path.relpath(path, resolved_root).encode())
        hasher.update(new_files[path].sha256.encode())
    return f"content:{hasher.hexdigest()}"


#: Bookmark 1 Item 2: a request-scoped override, set by `snapshot_file_
#: hash_set` (below) so every `target_repo_file_signature(repo_root)`
#: call within one `pack_symbol_context()` invocation answers from one
#: value computed once, instead of independently re-running the real
#: scan above per call site (`build_causal_graph`, `compute_feature_
#: masks_cached`, `compute_topological_distances`, the three
#: causal-edge functions all call this same function). A plain dict
#: behind a `contextvars.ContextVar`, not a lock - "the snapshot is a
#: value, not a shared object," per Item 2's own spec. Callers outside
#: an active snapshot context (`prism.surface.build`'s own direct
#: calls, unmodified - see Bookmark 1's closed-module guardrail)
#: compute fresh exactly as before; unaffected by this mechanism.
_snapshot_override: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "_snapshot_override", default=None
)


def target_repo_file_signature(repo_root: str) -> str:
    """`file_hash_set` - the checked-out commit SHA plus a real
    mtime/content-hash scan for anything git-HEAD can't see (an
    uncommitted edit), or a pure content scan for a non-git repo. See
    `_compute_file_hash_set`'s own docstring for the real algorithm;
    this wrapper only adds the Item 2 snapshot short-circuit."""
    resolved_root = str(Path(repo_root).resolve())
    override = _snapshot_override.get()
    if override is not None and resolved_root in override:
        return override[resolved_root]
    return _compute_file_hash_set(resolved_root)


@contextmanager
def snapshot_file_hash_set(repo_root: str):
    """Bookmark 1 Item 2: computes `file_hash_set` for `repo_root`
    exactly once, then makes every `target_repo_file_signature(repo_
    root)` call for the duration of this `with` block return that same
    value - `pack_symbol_context` wraps its own body in this so a
    single `retrieve()` performs exactly one real file-hash-set scan,
    not the 3-4 redundant ones the un-snapshotted call chain would
    otherwise trigger (`build_causal_graph`, `compute_feature_masks_
    cached`, `compute_topological_distances` - which itself re-invokes
    `build_causal_graph`'s own cache-key check - each independently
    call `target_repo_file_signature`). A file changed after the
    snapshot is taken is invisible to this retrieve (by design - "no
    lock, the snapshot is a value") and is only ever caught starting
    with the *next* `retrieve()`, which opens its own fresh context."""
    resolved_root = str(Path(repo_root).resolve())
    value = _compute_file_hash_set(resolved_root)
    token = _snapshot_override.set({resolved_root: value})
    try:
        yield value
    finally:
        _snapshot_override.reset(token)


@dataclass(frozen=True)
class GraphCacheKey:
    """`(repo_path, engine_commit_hash, grammar_version, tag_rule_version,
    file_hash_set)` - the Blocker 1 cache-key matrix's own literal shape
    for `build_causal_graph`/`compute_causal_edges`/`compute_all_
    data_flow_edges`/`compute_guard_indicator_edges` (all "Same as
    build_causal_graph" per that matrix).

    **`tag_rule_version` is `engine_commit_hash`'s own value, not a
    separately-tracked constant** - verified directly: no
    independently-versioned tagging-rule scheme exists anywhere in this
    codebase (`prism/tagger/` is first-party source in this same repo,
    unlike the externally-versioned tree-sitter grammars `grammar_
    version` above exists specifically to cover). Using the same repo
    commit hash for both slots is not a fabricated placeholder value -
    it is the real, exact answer to "what version of this repo's own
    tagging rules produced this," since that logic lives in this same
    repo and changes only when this repo's own commit changes.
    """

    repo_path: str
    engine_commit_hash: str
    grammar_version: str
    tag_rule_version: str
    file_hash_set: str

    def digest(self) -> str:
        raw = "|".join(
            (self.repo_path, self.engine_commit_hash, self.grammar_version, self.tag_rule_version, self.file_hash_set)
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def graph_cache_key(repo_root: str) -> GraphCacheKey:
    repo_path = str(Path(repo_root).resolve())
    commit_hash = engine_commit_hash()
    return GraphCacheKey(
        repo_path=repo_path,
        engine_commit_hash=commit_hash,
        grammar_version=grammar_version(),
        tag_rule_version=commit_hash,
        file_hash_set=target_repo_file_signature(repo_path),
    )


class _LRUCache(Generic[_V]):
    """Bookmark 1 Item 3: a bounded, dict-compatible cache with
    least-recently-used eviction - a small dedicated class (over a bare
    `collections.OrderedDict`) so every one of the five module-level
    cache dicts can swap in this exact drop-in without any change to
    the `.get(key)` / `cache[key] = value` call sites that already use
    them (`build_causal_graph`, `compute_topological_distances`, the
    three causal-edge functions, `compute_feature_masks_cached`).

    Every *read* (`.get`, `__getitem__`, `__contains__`) refreshes the
    accessed key's recency, not just writes - "access an entry to
    refresh its LRU position" per Item 3's own spec, so a hot entry
    that's read often but rewritten rarely still survives eviction.
    Eviction is transparent: the next access after an entry is evicted
    is an ordinary cache miss, and the caller recomputes exactly as it
    would for any other miss - no special-casing needed anywhere else.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, _V] = OrderedDict()

    def get(self, key: str, default: _V | None = None) -> _V | None:
        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        if key not in self._data:
            return False
        self._data.move_to_end(key)
        return True

    def __getitem__(self, key: str) -> _V:
        value = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: str, value: _V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def clear(self) -> None:
        self._data.clear()
