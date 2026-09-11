"""Blocker 1 performance work, Step 2: `prism.traversal.continuous_
dijkstra.build_causal_graph` is now cached, keyed by `_GraphCacheKey`
(repo/engine/grammar/tag-rule/file-content addressed - seed/budget-
independent, safe to share across calls, seeds, and engine instances
on the same repo). These tests verify the cache actually engages (via
an external counter around the real, expensive `compute_causal_edges`
work, not `build_causal_graph`'s own call count) and that caching
never changes the graph or any downstream selection built from it.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import pack_symbol_context
from prism.traversal import continuous_dijkstra
from prism.traversal.continuous_dijkstra import build_causal_graph

_SOURCE = (
    "def parse_order(raw):\n    return raw\n\n\n"
    "def validate_order(data):\n    return data\n\n\n"
    "def price_order(data):\n    return data\n\n\n"
    "def store_order(data):\n    return data\n\n\n"
    "def notify_order(data):\n    return data\n\n\n"
    "def process(raw):\n"
    "    data = parse_order(raw)\n"
    "    data = validate_order(data)\n"
    "    data = price_order(data)\n"
    "    store_order(data)\n"
    "    notify_order(data)\n"
    "    return data\n"
)

_SEEDS = ["x.parse_order", "x.validate_order", "x.price_order", "x.store_order", "x.notify_order"]


def _counting_compute_causal_edges(monkeypatch):
    """External counter around the real `compute_causal_edges` -
    `build_causal_graph`'s own expensive step - not `build_causal_
    graph`'s self-reported call count, matching the pattern already
    established for Gap 5's `extraction_count`."""
    calls = {"n": 0}
    real_fn = continuous_dijkstra.compute_causal_edges

    def counting_wrapper(*args, **kwargs):
        calls["n"] += 1
        return real_fn(*args, **kwargs)

    monkeypatch.setattr(continuous_dijkstra, "compute_causal_edges", counting_wrapper)
    return calls


def test_build_causal_graph_called_once_across_three_calls_in_one_retrieve(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    continuous_dijkstra._GRAPH_CACHE.clear()
    calls = _counting_compute_causal_edges(monkeypatch)

    g1 = build_causal_graph(builder)
    g2 = build_causal_graph(builder)
    g3 = build_causal_graph(builder)

    assert calls["n"] == 1, "3 calls inside one retrieve must do the real work exactly once"
    assert g1 is g2 is g3, "a cache hit must return the same graph object, not an equal-but-rebuilt one"


def test_build_causal_graph_output_equivalent_cached_vs_uncached(tmp_path, monkeypatch):
    """5 seeds: the SubmodularPackResult produced with the causal-graph
    cache warm (shared across all 5 seeds on one builder) must be
    identical to the result produced with the cache cleared before
    every single call (forcing a fresh, uncached build each time)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    warm_results = {}
    continuous_dijkstra._GRAPH_CACHE.clear()
    for seed in _SEEDS:
        warm_results[seed] = pack_symbol_context(builder, seed, 2000)

    cold_results = {}
    for seed in _SEEDS:
        continuous_dijkstra._GRAPH_CACHE.clear()  # force an uncached rebuild for every seed
        cold_results[seed] = pack_symbol_context(builder, seed, 2000)

    for seed in _SEEDS:
        assert warm_results[seed].selected == cold_results[seed].selected, seed
        assert warm_results[seed].total_cost == cold_results[seed].total_cost, seed
        assert warm_results[seed].covered_mask == cold_results[seed].covered_mask, seed
        warm_items = [(i.symbol, i.cost, i.feature_mask, i.dist_w, i.role) for i in warm_results[seed].items]
        cold_items = [(i.symbol, i.cost, i.feature_mask, i.dist_w, i.role) for i in cold_results[seed].items]
        assert warm_items == cold_items, seed


def test_new_builder_on_same_repo_still_hits_the_repo_content_addressed_cache(tmp_path, monkeypatch):
    """The cache is intentionally repo-content-addressed, not
    id(builder)-scoped - a second, independently-built `builder` over
    the identical unchanged repo is expected to share the same cache
    entry (this is what makes the cache useful across a multi-task
    sweep, not just within one retrieve())."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)

    continuous_dijkstra._GRAPH_CACHE.clear()
    calls = _counting_compute_causal_edges(monkeypatch)

    builder_1, _ = build_pipeline(str(repo))
    build_causal_graph(builder_1)
    assert calls["n"] == 1

    builder_2, _ = build_pipeline(str(repo))  # a genuinely different builder object
    assert builder_2 is not builder_1
    build_causal_graph(builder_2)
    assert calls["n"] == 1, "same repo, same content, different builder object -> still a real cache hit"
