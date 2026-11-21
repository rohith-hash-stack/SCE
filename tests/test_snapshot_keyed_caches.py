"""Derived caches are keyed on the builder's snapshot, never on the disk.

The failure this guards: in a live session the disk moves ahead of a cached
builder (an edit before `reindex_repo`). A derived cache keyed on the disk
then stores a result computed from the *old* builder under the *new* disk
state, and the rebuilt builder reads it back - feature masks missing the
new function, in memory and, through the per-file SQLite cache, across
processes. Keys now come from `snapshot_signature(builder)` (the bytes it
parsed plus merged runtime state) and the per-file cache from
`ParsedFile.source`; `GraphCache.reindex` also evicts the old entries.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from prism.cli import build_pipeline
from prism.semantics import extractor as X
from prism.semantics.capabilities import compute_capability_profiles
from prism.semantics.profile_cache import cached_capability_profiles
from prism.traversal import _cache_keys as K

BEFORE = "def a():\n    return 1\n"
AFTER = BEFORE + "\n\nimport sqlite3\n\n\ndef save(row):\n    sqlite3.connect('x').execute('insert', row)\n"


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.py").write_text(BEFORE)
    K.evict_derived_caches()
    yield root
    K.evict_derived_caches()


def _build(root: Path):
    return build_pipeline(str(root), use_cache=False)[0]


def test_snapshot_follows_the_builder_not_the_disk(repo):
    old = _build(repo)
    sig = K.snapshot_signature(old)
    (repo / "m.py").write_text(AFTER)
    assert K.snapshot_signature(old) == sig  # the old builder's snapshot does not move with the disk
    assert K.graph_cache_key_for(old).file_hash_set == sig
    new = _build(repo)
    assert K.snapshot_signature(new) != sig
    assert K.graph_cache_key_for(new).digest() != K.graph_cache_key_for(old).digest()
    (repo / "m.py").write_text(BEFORE + "   \n")  # cosmetic edit: same snapshot, as for the disk scan
    assert K.snapshot_signature(_build(repo)) == sig


def test_masks_from_a_stale_builder_are_never_served_to_the_rebuilt_one(repo):
    old = _build(repo)
    X.compute_feature_masks_cached(old, str(repo))
    (repo / "m.py").write_text(AFTER)
    stale = X.compute_feature_masks_cached(old, str(repo))  # edit before reindex: old builder, new disk
    assert set(stale) == {"m.a"}
    new = _build(repo)
    assert X.compute_feature_masks_cached(new, str(repo)) == X.compute_feature_masks(new)
    assert "m.save" in X.compute_feature_masks_cached(new, str(repo))


def test_per_file_disk_cache_holds_the_parsed_bytes_entry(repo):
    old = _build(repo)
    (repo / "m.py").write_text(AFTER)
    X.compute_feature_masks_cached(old, str(repo))  # writes per-file entries for the old parse
    K.evict_derived_caches()  # a new process: only the SQLite file cache survives
    new = _build(repo)
    assert X.compute_feature_masks_cached(new, str(repo)) == X.compute_feature_masks(new)


def test_capability_profiles_from_a_stale_builder_are_never_served(repo):
    old = _build(repo)
    (repo / "m.py").write_text(AFTER)
    cached_capability_profiles(old, str(repo))
    new = _build(repo)
    assert cached_capability_profiles(new, str(repo)) == compute_capability_profiles(new)


def test_merged_runtime_state_is_part_of_the_snapshot(repo):
    from prism.runtime.reconciler import apply_runtime_state
    builder = _build(repo)
    static = K.snapshot_signature(builder)
    apply_runtime_state(builder, {}, {"confirmed_edges": [], "discovered_edges": [], "trace_files": ["t1"]})
    with_trace = K.snapshot_signature(builder)
    assert with_trace != static and with_trace.startswith(static)
    apply_runtime_state(builder, {}, {"confirmed_edges": [], "discovered_edges": [], "trace_files": ["t1", "t2"]})
    assert K.snapshot_signature(builder) not in (static, with_trace)


def test_reindex_evicts_and_records_the_snapshot(repo):
    from prism.mcp.cache import GraphCache
    cache = GraphCache()
    ctx = cache.get_or_index(str(repo))
    assert ctx.snapshot_signature == K.snapshot_signature(ctx.builder)
    X.compute_feature_masks_cached(ctx.builder, str(repo))
    assert len(X._FEATURE_MASKS_CACHE) >= 1
    (repo / "m.py").write_text(AFTER)
    fresh = cache.reindex(str(repo))
    assert fresh.snapshot_signature != ctx.snapshot_signature
    assert X.compute_feature_masks_cached(fresh.builder, str(repo)) == X.compute_feature_masks(fresh.builder)
    cache.invalidate(str(repo))
    assert len(X._FEATURE_MASKS_CACHE) == 0


def test_mcp_edit_then_tool_call_then_reindex_serves_the_new_graph(repo):
    """The live-session sequence end to end: a tool call between the edit
    and `reindex_repo` must not poison what the rebuilt index serves."""
    pytest.importorskip("mcp")
    from mcp.client import Client
    from prism.mcp import server as mcp_server

    async def call(name, args):
        async with Client(mcp_server.server) as client:
            r = await client.call_tool(name, args)
            assert not r.is_error, r.content[0].text
            return r

    mcp_server._cache.clear()
    try:
        asyncio.run(call("prism.slice", {"repo_path": str(repo), "seed_symbol": "m.a"}))
        (repo / "m.py").write_text(AFTER)
        asyncio.run(call("prism.slice", {"repo_path": str(repo), "seed_symbol": "m.a"}))  # stale index, new disk
        asyncio.run(call("reindex_repo", {"repo_path": str(repo)}))
        builder = mcp_server._cache.get_or_index(str(repo)).builder
        masks = X.compute_feature_masks_cached(builder, str(repo))
        assert masks == X.compute_feature_masks(builder) and "m.save" in masks
    finally:
        mcp_server._cache.clear()
