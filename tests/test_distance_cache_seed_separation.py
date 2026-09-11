"""Blocker 1 performance work, Step 3: `prism.traversal.continuous_
dijkstra.compute_topological_distances` is now cached, keyed by
`_DistanceCacheKey` - critically, the key always includes `seed`. This
is the mandatory test the research review flagged: a session-level
distance cache with a seed-less (or seed-insensitive) key would
silently hand seed B a set of distances computed for seed A -
plausible-looking, wrong output no same-seed bit-identical test could
ever catch. These tests prove that specific failure mode cannot occur,
not just that the cache "generally works".
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.traversal import continuous_dijkstra
from prism.traversal.continuous_dijkstra import compute_topological_distances

_SOURCE = (
    "def parse_order(raw):\n    return validate_order(raw)\n\n\n"
    "def validate_order(data):\n    return price_order(data)\n\n\n"
    "def price_order(data):\n    return store_order(data)\n\n\n"
    "def store_order(data):\n    notify_order(data)\n    return data\n\n\n"
    "def notify_order(data):\n    return data\n"
)

_SEEDS = ["x.parse_order", "x.validate_order", "x.price_order", "x.store_order", "x.notify_order"]


def _counting_dijkstra(monkeypatch):
    """External counter around the real `nx.single_source_dijkstra_
    path_length` call - the expensive step `compute_topological_
    distances` is meant to skip on a cache hit - not `compute_
    topological_distances`'s own call count."""
    calls = {"n": 0}
    real_fn = continuous_dijkstra.nx.single_source_dijkstra_path_length

    def counting_wrapper(*args, **kwargs):
        calls["n"] += 1
        return real_fn(*args, **kwargs)

    monkeypatch.setattr(continuous_dijkstra.nx, "single_source_dijkstra_path_length", counting_wrapper)
    return calls


def test_compute_topological_distances_called_once_across_two_calls_in_one_retrieve(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    continuous_dijkstra._GRAPH_CACHE.clear()
    continuous_dijkstra._DISTANCE_CACHE.clear()
    calls = _counting_dijkstra(monkeypatch)

    d1 = compute_topological_distances(builder, "x.parse_order")
    d2 = compute_topological_distances(builder, "x.parse_order")

    assert calls["n"] == 1, "2 calls, same seed, inside one retrieve must do the real Dijkstra run exactly once"
    assert d1 == d2
    assert d1 is d2, "a cache hit must return the same dict object"


def test_seed_separation_mandatory(tmp_path, monkeypatch):
    """The exact test the research review required, verbatim in intent:
    retrieve(seed_a) populates the cache for seed_a; retrieve(seed_b)
    must be a real cache MISS (a different key, because the key
    includes the seed) and must return seed_b's own real distances,
    never seed_a's."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    continuous_dijkstra._GRAPH_CACHE.clear()
    continuous_dijkstra._DISTANCE_CACHE.clear()
    calls = _counting_dijkstra(monkeypatch)

    seed_a, seed_b = "x.parse_order", "x.validate_order"

    distances_a = compute_topological_distances(builder, seed_a)
    assert calls["n"] == 1  # cache stores distances for seed_a

    distances_b = compute_topological_distances(builder, seed_b)
    assert calls["n"] == 2, "a different seed must be a real cache MISS, not a hit on seed_a's entry"

    # The actual bug this test exists to catch: if the cache key omitted
    # (or mis-derived) the seed, this call would silently return
    # distances_a's own dict instead of computing seed_b's real ones.
    assert distances_b is not distances_a
    assert distances_a != distances_b, "seed_a and seed_b have different topology - their distance maps must differ"

    # And the seed_a entry itself must be exactly what a direct,
    # independent recomputation for seed_a produces - not corrupted by
    # the seed_b call that came after it.
    continuous_dijkstra._GRAPH_CACHE.clear()
    continuous_dijkstra._DISTANCE_CACHE.clear()
    calls["n"] = 0
    fresh_distances_a = compute_topological_distances(builder, seed_a)
    assert fresh_distances_a == distances_a


def test_distance_cache_output_equivalent_cached_vs_uncached(tmp_path):
    """5 seeds: distances computed with the cache warm (shared across
    all 5 seeds) must be identical to distances computed with the
    cache force-cleared before every single call."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    continuous_dijkstra._GRAPH_CACHE.clear()
    continuous_dijkstra._DISTANCE_CACHE.clear()
    warm = {seed: compute_topological_distances(builder, seed) for seed in _SEEDS}

    cold = {}
    for seed in _SEEDS:
        continuous_dijkstra._GRAPH_CACHE.clear()
        continuous_dijkstra._DISTANCE_CACHE.clear()
        cold[seed] = compute_topological_distances(builder, seed)

    for seed in _SEEDS:
        assert warm[seed] == cold[seed], seed


def test_distance_cache_key_changes_when_seed_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    key_a = continuous_dijkstra._distance_cache_key(builder, "x.parse_order")
    key_b = continuous_dijkstra._distance_cache_key(builder, "x.validate_order")
    assert key_a.digest() != key_b.digest()
    assert key_a.seed == "x.parse_order"
    assert key_b.seed == "x.validate_order"
