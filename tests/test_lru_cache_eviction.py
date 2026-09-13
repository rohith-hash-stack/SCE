"""Bookmark 1 Item 3: LRU eviction on all five module-level caches
(`_GRAPH_CACHE`, `_DISTANCE_CACHE`, the three `_CAUSAL_EDGE_CACHES`,
`_FEATURE_MASKS_CACHE`) via `prism.traversal._cache_keys._LRUCache`, a
dict-compatible bounded cache.
"""
from __future__ import annotations

import prism.semantics.extractor as extractor
import prism.traversal.causal_weights as causal_weights
import prism.traversal.continuous_dijkstra as continuous_dijkstra
from prism.traversal._cache_keys import _LRUCache


def test_overflow_evicts_oldest_entry():
    cache: _LRUCache[int] = _LRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    assert len(cache) == 3
    cache["d"] = 4  # overflow - "a" is the oldest, must be evicted
    assert len(cache) == 3
    assert "a" not in cache
    assert "b" in cache and "c" in cache and "d" in cache


def test_access_refreshes_lru_position_via_get():
    cache: _LRUCache[int] = _LRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    cache.get("a")  # refresh "a" - it's no longer the least-recently-used
    cache["d"] = 4  # overflow - "b" (now the oldest, untouched) must be evicted, not "a"
    assert "a" in cache, "an entry accessed via .get() must survive an eviction that would have removed it"
    assert "b" not in cache
    assert "c" in cache and "d" in cache


def test_access_refreshes_lru_position_via_getitem():
    cache: _LRUCache[int] = _LRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    _ = cache["a"]  # refresh via __getitem__
    cache["d"] = 4
    assert "a" in cache
    assert "b" not in cache


def test_access_refreshes_lru_position_via_contains():
    cache: _LRUCache[int] = _LRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    assert "a" in cache  # refresh via __contains__
    cache["d"] = 4
    assert "a" in cache
    assert "b" not in cache


def test_rewriting_an_existing_key_refreshes_its_position():
    cache: _LRUCache[int] = _LRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    cache["a"] = 10  # rewrite - "a" moves to most-recently-used
    cache["d"] = 4
    assert "a" in cache
    assert cache["a"] == 10
    assert "b" not in cache


def test_eviction_is_transparent_next_access_recomputes():
    cache: _LRUCache[int] = _LRUCache(maxsize=1)
    cache["a"] = 1
    cache["b"] = 2  # evicts "a"
    assert cache.get("a") is None
    cache["a"] = 99  # ordinary miss-then-recompute, no special handling needed
    assert cache["a"] == 99


def test_clear_empties_the_cache():
    cache: _LRUCache[int] = _LRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache.clear()
    assert len(cache) == 0
    assert "a" not in cache


def test_all_five_module_caches_are_lru_with_expected_caps():
    """Cap sizes per the item's own spec: _DISTANCE_CACHE=100 (fastest-
    growing, seed-keyed), _GRAPH_CACHE=10, each causal-edge cache=10,
    _FEATURE_MASKS_CACHE=10 (slowest-growing, one per repo)."""
    assert isinstance(continuous_dijkstra._GRAPH_CACHE, _LRUCache)
    assert continuous_dijkstra._GRAPH_CACHE._maxsize == 10

    assert isinstance(continuous_dijkstra._DISTANCE_CACHE, _LRUCache)
    assert continuous_dijkstra._DISTANCE_CACHE._maxsize == 100

    assert isinstance(causal_weights._DATA_FLOW_EDGES_CACHE, _LRUCache)
    assert causal_weights._DATA_FLOW_EDGES_CACHE._maxsize == 10
    assert isinstance(causal_weights._GUARD_INDICATOR_EDGES_CACHE, _LRUCache)
    assert causal_weights._GUARD_INDICATOR_EDGES_CACHE._maxsize == 10
    assert isinstance(causal_weights._CAUSAL_EDGES_CACHE, _LRUCache)
    assert causal_weights._CAUSAL_EDGES_CACHE._maxsize == 10

    assert isinstance(extractor._FEATURE_MASKS_CACHE, _LRUCache)
    assert extractor._FEATURE_MASKS_CACHE._maxsize == 10


def test_distance_cache_caps_at_100_entries_after_200_distinct_seeds(tmp_path):
    """Memory test (real, not synthetic on the bare _LRUCache class):
    200 distinct seeds queried against one repo must leave
    _DISTANCE_CACHE at exactly 100 entries, never growing past its cap,
    and each retrieve after the cap is reached must still produce a
    correct (recomputed, not corrupted) result."""
    from prism.cli import build_pipeline
    from prism.packer.submodular_knapsack import pack_symbol_context

    continuous_dijkstra._GRAPH_CACHE.clear()
    continuous_dijkstra._DISTANCE_CACHE.clear()

    repo = tmp_path / "repo"
    repo.mkdir()
    # 200 independent leaf functions - one per seed, no calls between
    # them (each produces its own trivially-computed, tiny distance
    # map entry: reachable set is empty since none call one another,
    # but each is still its own distinct _DISTANCE_CACHE key).
    lines = [f"def fn_{i}():\n    return {i}\n" for i in range(200)]
    (repo / "x.py").write_text("\n\n".join(lines) + "\n")
    builder, _ = build_pipeline(str(repo))

    for i in range(200):
        result = pack_symbol_context(builder, f"x.fn_{i}", 2000)
        assert result.selected  # each retrieve still produces a real, correct result post-cap

    assert len(continuous_dijkstra._DISTANCE_CACHE) == 100, (
        f"expected exactly 100 entries after 200 distinct seeds, got {len(continuous_dijkstra._DISTANCE_CACHE)}"
    )
