"""Blocker 1 performance work, Step 4: `prism.traversal.causal_weights.
compute_causal_edges`/`compute_all_data_flow_edges`/`compute_guard_
indicator_edges` are now cached, repo-content-addressed (the exact
same key shape `continuous_dijkstra.build_causal_graph` already uses -
"Same as build_causal_graph" per the cache-key matrix), extending the
Step 2/3 caches into a real session-level cache covering all five
functions the Blocker 1 plan named. These tests exercise the real
`PrismEngine.retrieve()` entry point (not the traversal functions in
isolation), since "session-level" is a claim about behavior across
retrieve() calls on one engine instance, not about any single
function.
"""
from __future__ import annotations

from prism.traversal import causal_weights, continuous_dijkstra
from prism.traversal._cache_keys import graph_cache_key

from benchmarks.engines.prism_engine import PrismEngine

_SOURCE = (
    "def parse_order(raw):\n    return validate_order(raw)\n\n\n"
    "def validate_order(data):\n    return price_order(data)\n\n\n"
    "def price_order(data):\n    return store_order(data)\n\n\n"
    "def store_order(data):\n    notify_order(data)\n    return data\n\n\n"
    "def notify_order(data):\n    return data\n"
)

_SEEDS = ["x.parse_order", "x.validate_order", "x.price_order", "x.store_order", "x.notify_order"]


def _clear_all_traversal_caches():
    continuous_dijkstra._GRAPH_CACHE.clear()
    continuous_dijkstra._DISTANCE_CACHE.clear()
    causal_weights._DATA_FLOW_EDGES_CACHE.clear()
    causal_weights._GUARD_INDICATOR_EDGES_CACHE.clear()
    causal_weights._CAUSAL_EDGES_CACHE.clear()


def _repo_keyed_cache_state(repo_root: str) -> dict[str, bool]:
    """Whether the current repo-content-addressed key is already
    present in each of the four repo-keyed caches - checked via the
    exact same key-computation the production code itself uses, not a
    mock or a proxy."""
    key = graph_cache_key(repo_root).digest()
    return {
        "build_causal_graph": key in continuous_dijkstra._GRAPH_CACHE,
        "compute_causal_edges": key in causal_weights._CAUSAL_EDGES_CACHE,
        "compute_all_data_flow_edges": key in causal_weights._DATA_FLOW_EDGES_CACHE,
        "compute_guard_indicator_edges": key in causal_weights._GUARD_INDICATOR_EDGES_CACHE,
    }


def test_session_cache_across_three_retrieves_two_seeds_two_budgets(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)

    _clear_all_traversal_caches()
    engine = PrismEngine()
    engine.index(str(repo))

    seed_a, seed_b = "x.parse_order", "x.validate_order"

    # retrieve #1: everything must be a real miss beforehand.
    state_before_1 = _repo_keyed_cache_state(str(repo))
    assert not any(state_before_1.values()), state_before_1
    dist_key_a = continuous_dijkstra._distance_cache_key(engine._builder, seed_a).digest()
    assert dist_key_a not in continuous_dijkstra._DISTANCE_CACHE

    pkg1 = engine.retrieve(seed_a, 4000)
    assert pkg1.nodes

    state_after_1 = _repo_keyed_cache_state(str(repo))
    assert all(state_after_1.values()), state_after_1  # retrieve #1 populated all four repo-keyed caches
    assert dist_key_a in continuous_dijkstra._DISTANCE_CACHE  # and seed_a's own distances

    # retrieve #2: same seed, different budget - all four repo-keyed
    # caches hit (already true from retrieve #1); distances also hit -
    # budget does not affect distance computation, and the key is
    # unchanged (same repo, same seed).
    pkg2 = engine.retrieve(seed_a, 8000)
    assert pkg2.nodes
    state_after_2 = _repo_keyed_cache_state(str(repo))
    assert all(state_after_2.values())
    assert dist_key_a in continuous_dijkstra._DISTANCE_CACHE

    # retrieve #3: a DIFFERENT seed. The four repo-keyed caches still
    # hit (seed-independent). Distances must be a real MISS for seed_b
    # - this is the critical assertion: distances must never bleed
    # from seed_a into seed_b.
    dist_key_b = continuous_dijkstra._distance_cache_key(engine._builder, seed_b).digest()
    assert dist_key_b not in continuous_dijkstra._DISTANCE_CACHE

    pkg3 = engine.retrieve(seed_b, 4000)
    assert pkg3.nodes
    state_after_3 = _repo_keyed_cache_state(str(repo))
    assert all(state_after_3.values())  # repo-keyed caches: still hits
    assert dist_key_b in continuous_dijkstra._DISTANCE_CACHE  # seed_b now has its OWN entry
    assert dist_key_a in continuous_dijkstra._DISTANCE_CACHE  # seed_a's own entry is untouched

    distances_a = continuous_dijkstra._DISTANCE_CACHE[dist_key_a]
    distances_b = continuous_dijkstra._DISTANCE_CACHE[dist_key_b]
    assert distances_a != distances_b  # never aliased/bled into each other


def test_repo_keyed_caches_invalidate_on_file_change(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)

    _clear_all_traversal_caches()
    engine = PrismEngine()
    engine.index(str(repo))
    engine.retrieve("x.parse_order", 4000)

    state_before_touch = _repo_keyed_cache_state(str(repo))
    assert all(state_before_touch.values())
    dist_key_before = continuous_dijkstra._distance_cache_key(engine._builder, "x.parse_order").digest()
    assert dist_key_before in continuous_dijkstra._DISTANCE_CACHE

    # Touch the source file (real content change) and re-index.
    (repo / "x.py").write_text(_SOURCE + "\n\ndef unrelated_new_function():\n    return 1\n")
    engine_after_touch = PrismEngine()
    engine_after_touch.index(str(repo))

    # A file change means a new file_hash_set, hence a new key - the
    # OLD key's cache rows are simply never looked up again (not
    # explicitly evicted); the NEW key must be a real miss.
    state_after_touch = _repo_keyed_cache_state(str(repo))
    assert not any(state_after_touch.values()), "a changed file must produce a new key, not reuse the stale one"
    dist_key_after = continuous_dijkstra._distance_cache_key(engine_after_touch._builder, "x.parse_order").digest()
    assert dist_key_after not in continuous_dijkstra._DISTANCE_CACHE
    assert dist_key_after != dist_key_before, "distances must also invalidate - a file change can alter reachability"

    pkg = engine_after_touch.retrieve("x.parse_order", 4000)
    assert pkg.nodes


def test_fresh_engine_after_clearing_caches_recomputes_correctly(tmp_path):
    """The established convention in this codebase (see tests/
    benchmarks/test_prism_engine_cache.py's own
    test_prism_engine_cache_reloads_from_disk_in_a_fresh_instance) for
    "a fresh instance" is: clear the shared cache (simulating a new
    process/session), then confirm a new instance correctly misses and
    recomputes - not silently serving something wrong. Repo-content-
    addressed caches are *intended* to be shared across instances on
    the same unchanged repo (that is the entire point of Step 4); this
    test proves clearing them actually forces a real, correct
    recompute rather than leaving a stale reference somewhere."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)

    engine_1 = PrismEngine()
    engine_1.index(str(repo))
    pkg_1 = engine_1.retrieve("x.parse_order", 4000)

    _clear_all_traversal_caches()  # simulate a new process/session

    engine_2 = PrismEngine()
    engine_2.index(str(repo))
    state = _repo_keyed_cache_state(str(repo))
    assert not any(state.values()), "caches were cleared - a fresh engine must start from a real miss"

    pkg_2 = engine_2.retrieve("x.parse_order", 4000)
    assert pkg_2.nodes
    assert [n.id for n in pkg_1.nodes] == [n.id for n in pkg_2.nodes]


def test_output_equivalent_across_all_five_caches_for_five_seeds(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)

    warm_results = {}
    _clear_all_traversal_caches()
    engine = PrismEngine()
    engine.index(str(repo))
    for seed in _SEEDS:
        warm_results[seed] = engine.retrieve(seed, 4000)

    cold_results = {}
    for seed in _SEEDS:
        _clear_all_traversal_caches()
        cold_engine = PrismEngine()
        cold_engine.index(str(repo))
        cold_results[seed] = cold_engine.retrieve(seed, 4000)

    for seed in _SEEDS:
        warm_ids = [(n.id, n.role, n.cost, n.distance) for n in warm_results[seed].nodes]
        cold_ids = [(n.id, n.role, n.cost, n.distance) for n in cold_results[seed].nodes]
        assert warm_ids == cold_ids, seed
