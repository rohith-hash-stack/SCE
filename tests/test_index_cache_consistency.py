"""Reproduction test for the `prism.runtime.index_cache` rehydration bug
found during the noise-reduction spike: `build_pipeline(repo, use_cache=True)`
returned a smaller, inconsistent graph state across process-boundary-like
cache read/write cycles (candidate counts drifting, e.g. observed as 409
vs 402 on a downstream seed), while `use_cache=False` was always stable.

**Diagnosis, corrected from the task brief's own literal framing**: this
is *not* a graph/edge serialization bug. Direct set comparison of
`builder.graph` (a `networkx.DiGraph`) between a fresh build
(`use_cache=False`) and a cache-hit rebuild (`use_cache=True`, second
call) on the real pinned Django corpus shows the node and edge sets are
byte-identical (76790 nodes, 106890 edges, zero set-difference either
direction) - `_node_link_data_compat`/`_node_link_graph_compat` round-trip
`builder.graph` correctly, and `GlobalSymbolTable`/`SymbolInfo` (including
`role`) round-trip correctly too (52075 symbols both ways).

The real, confirmed defect is in `_rehydrate_def_nodes`
(`prism.runtime.index_cache`), which repopulates `ConcreteGraphBuilder.
_def_nodes` - a *separate*, uncached, tree-sitter-node-keyed dict that
`prism.traversal.causal_weights.compute_guard_indicator_edges` and
`prism.semantics.extractor`'s feature-bitmask extraction both call
`def_node()` against for every symbol, silently skipping (`continue`)
any symbol it returns `None` for. Before the fix, `_rehydrate_def_nodes`
joined tree-sitter capture nodes back to cached `SymbolInfo` via a bare
`{start_line: symbol}` dict with no collision handling: any file with two
or more symbols sharing an exact start line (real and reproducible on
the pinned Django corpus's own vendored, minified
`django/contrib/admin/static/admin/js/vendor/jquery/jquery.min.js`, which
packs dozens of one-liner function definitions onto shared lines) silently
dropped every same-line symbol but the last from `_def_nodes` on a cache
hit - 86 missing entries confirmed directly (37033 fresh vs 36947 on a
cache hit, exclusively in that one minified file; zero non-JS symbols
affected). A `def_node() is None` symbol contributes zero feature bits
and is excluded from guard-indicator/data-flow edge computation, which
starves `compute_causal_edges`'s synthetic-edge computation and can
shrink the reachable-candidate universe for any seed whose candidates
depend on those coupling edges - the actual mechanism behind the spike's
observed 409-vs-402 drift, not raw graph/edge loss.

This test therefore asserts `_def_nodes` completeness directly (the
actual site of the bug) in addition to the graph/edge/symbol-table
equality the task brief asked for (which already passed before this fix,
and stays here as a real regression guard against a *future* graph-
serialization defect, even though it wasn't the bug this time).
"""
from __future__ import annotations

import shutil

import pytest

from benchmarks.corpora.resolver import resolve
from prism.cli import build_pipeline
from prism.runtime.index_cache import index_cache_path

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def django_repo_path() -> str:
    return str(resolve("django"))


@pytest.fixture
def clean_cache(django_repo_path):
    """Every test in this file needs a real cold cache-write, so an
    index.db left over from another test run (or another test in this
    module) can't turn a cold write into a spurious cache hit."""
    cache_path = index_cache_path(django_repo_path)
    if cache_path.exists():
        cache_path.unlink()
    yield
    if cache_path.exists():
        cache_path.unlink()


def _call_graph_edges(builder) -> set[tuple[str, str, str]]:
    """(source, target, relation) triples for every `CALLS`/`INSTANTIATES`
    edge - the outgoing edge-target check the task brief asked for.
    `relation` (not `kind`) is the edge-type attribute
    `ConcreteGraphBuilder` actually sets (concrete_builder.py:1664 etc)."""
    edges = set()
    for u, v, data in builder.graph.edges(data=True):
        relation = data.get("relation")
        if relation in ("CALLS", "INSTANTIATES"):
            edges.add((u, v, relation))
    return edges


def test_cache_write_then_cache_hit_matches_fresh_build(django_repo_path, clean_cache):
    """Ground truth: `use_cache=False`, always a fresh parse. Then a cold
    cache write (`use_cache=True`, first call, no cache yet - simulates
    one process indexing and populating the cache) and a warm cache hit
    (`use_cache=True`, second call, cache now present - simulates a
    *second* process, across a process boundary, reading back what the
    first one wrote) must both reproduce the ground truth exactly."""
    builder_fresh, _tags_fresh = build_pipeline(django_repo_path, use_cache=False)

    builder_write, _tags_write = build_pipeline(django_repo_path, use_cache=True)
    assert index_cache_path(django_repo_path).exists(), (
        "expected build_pipeline(use_cache=True) to populate the cache on a cold miss"
    )

    builder_hit, _tags_hit = build_pipeline(django_repo_path, use_cache=True)

    fresh_nodes = builder_fresh.graph.number_of_nodes()
    write_nodes = builder_write.graph.number_of_nodes()
    hit_nodes = builder_hit.graph.number_of_nodes()
    assert write_nodes == fresh_nodes, f"cold cache-write node count {write_nodes} != fresh {fresh_nodes}"
    assert hit_nodes == fresh_nodes, f"warm cache-hit node count {hit_nodes} != fresh {fresh_nodes}"

    fresh_edge_count = builder_fresh.graph.number_of_edges()
    assert builder_write.graph.number_of_edges() == fresh_edge_count
    assert builder_hit.graph.number_of_edges() == fresh_edge_count

    fresh_edges = _call_graph_edges(builder_fresh)
    assert _call_graph_edges(builder_write) == fresh_edges
    assert _call_graph_edges(builder_hit) == fresh_edges, (
        "CALLS/INSTANTIATES edge targets drifted between a fresh build and a warm cache hit"
    )


def test_cache_hit_rehydrates_every_def_node(django_repo_path, clean_cache):
    """The actual, confirmed defect site: `_def_nodes` must be fully
    repopulated on a cache hit, including files (like a minified/bundled
    vendor JS file) where several symbols share an exact start line.
    Before the fix, this failed with exactly 86 missing entries, all in
    `django/contrib/admin/static/admin/js/vendor/jquery/jquery.min.js`."""
    builder_fresh, _ = build_pipeline(django_repo_path, use_cache=False)
    build_pipeline(django_repo_path, use_cache=True)  # cold write
    builder_hit, _ = build_pipeline(django_repo_path, use_cache=True)  # warm hit

    fresh_defnodes = set(builder_fresh._def_nodes.keys())
    hit_defnodes = set(builder_hit._def_nodes.keys())
    missing = fresh_defnodes - hit_defnodes
    assert not missing, f"{len(missing)} symbols lost their _def_nodes entry on a cache hit: {sorted(missing)[:10]}"
    assert hit_defnodes == fresh_defnodes
