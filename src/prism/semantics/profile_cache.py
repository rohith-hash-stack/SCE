"""Index-time capability profiles, computed once and persisted.

`compute_capability_profiles` walks every function of a repository (18 s on
Django), so retrieval never calls it per query: `cached_capability_profiles`
returns the profiles for a built repository from an in-process cache, then
from `.prism/cache/capability_profiles.db` (next to the index cache,
`prism.runtime.index_cache`), and computes and stores them only on a miss.

The key covers everything the profiles depend on: the engine's own code
(`engine_commit_hash`), the repository's files (`target_repo_file_signature`),
the profile parameters, and - for a build given dependency summaries - every
boundary node's package version and evidence plus the stub shadow, so a build
with summaries never reads a build without them (or with other versions).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.capabilities import MAX_HOPS, CapabilityProfile, compute_capability_profiles
from prism.traversal._cache_keys import _LRUCache, engine_commit_hash, target_repo_file_signature

#: Bump when `CapabilityProfile` or its derivation changes shape.
PROFILE_CACHE_VERSION = 3
_MEMORY: _LRUCache[dict[str, CapabilityProfile]] = _LRUCache(maxsize=4)
_SCHEMA = "CREATE TABLE IF NOT EXISTS profiles (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"


def profile_cache_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "cache" / "capability_profiles.db"


def _dependency_digest(builder: ConcreteGraphBuilder) -> list:
    boundary = sorted(
        (n, a.get("boundary_package"), a.get("boundary_version"), int(a.get("boundary_bits", 0)),
         int(a.get("boundary_tentative_bits", 0)), bool(a.get("boundary_opaque")), a.get("boundary_proxy"))
        for n, a in builder.graph.nodes(data=True) if a.get("boundary")
    )
    return [boundary, sorted(getattr(builder, "stub_shadow", {}).items())]


def profile_cache_key(builder: ConcreteGraphBuilder, repo_root: str, max_hops: int, include_l3_stubs: bool) -> str:
    raw = json.dumps([
        PROFILE_CACHE_VERSION, engine_commit_hash(), str(Path(repo_root).resolve()),
        target_repo_file_signature(repo_root), max_hops, include_l3_stubs, _dependency_digest(builder),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _dump(profiles: dict[str, CapabilityProfile]) -> str:
    return json.dumps({
        q: [list(p.by_hop), p.own_l3, [list(s) for s in p.sources] if p.sources is not None else None,
            [sorted(e) for e in p.established] if p.established is not None else None, p.tentative, list(p.opaque),
            list(p.proxy), list(p.internal) if p.internal is not None else None]
        for q, p in profiles.items()
    })


def _load(payload: str) -> dict[str, CapabilityProfile]:
    return {
        q: CapabilityProfile(
            tuple(by_hop), own_l3, tuple(tuple(s) for s in sources) if sources is not None else None,
            tuple(frozenset(e) for e in established) if established is not None else None, tentative, tuple(opaque),
            tuple(proxy), tuple(internal) if internal is not None else None,
        )
        for q, (by_hop, own_l3, sources, established, tentative, opaque, proxy, internal) in json.loads(payload).items()
    }


def cached_capability_profiles(
    builder: ConcreteGraphBuilder, repo_root: str, max_hops: int = MAX_HOPS, include_l3_stubs: bool = True,
    persist: bool = True,
) -> dict[str, CapabilityProfile]:
    """The capability profiles of `builder`'s repository at `repo_root`,
    from cache when this exact index state was profiled before."""
    key = profile_cache_key(builder, repo_root, max_hops, include_l3_stubs)
    hit = _MEMORY.get(key)
    if hit is not None:
        return hit
    path = profile_cache_path(repo_root)
    if persist and path.exists():
        try:
            with closing(sqlite3.connect(path)) as conn:
                row = conn.execute("SELECT payload FROM profiles WHERE key = ?", (key,)).fetchone()
            if row is not None:
                profiles = _load(row[0])
                _MEMORY[key] = profiles
                return profiles
        except (sqlite3.Error, ValueError, TypeError):
            pass  # unreadable cache: recompute below
    profiles = compute_capability_profiles(builder, max_hops=max_hops, include_l3_stubs=include_l3_stubs)
    _MEMORY[key] = profiles
    if persist:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(_SCHEMA)
                conn.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?)", (key, _dump(profiles)))
        except (OSError, sqlite3.Error):
            pass  # a read-only checkout still gets the profiles, just uncached
    return profiles
