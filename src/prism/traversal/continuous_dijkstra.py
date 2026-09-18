"""v1.1 Part 2.4: Continuous Dijkstra Topological Distance.

**Critical architectural invariant** (the spec's own framing): to prevent
a distant node "leapfrogging" a genuinely closer one mid-knapsack-
traversal, every pairwise `W(u, v)` and every shortest path from the seed
is computed *once*, up front, before `prism.packer.submodular_knapsack`'s
selection loop ever starts - not recomputed incrementally as the frontier
expands. This module is that one precomputation step.

The traversal graph this runs Dijkstra over is **directed**, and built
from the *union* of `builder.graph`'s real structural edges and
`prism.traversal.causal_weights`'s synthetic causal-coupling edges (see
that module's own docstring for why a synthetic edge is often the only
connection between two causally-coupled sibling calls at all) - forward-
only reachability from the seed, matching `submodular_knapsack`'s own
frontier-expansion contract (`graph.successors(node)`), not the
bidirectional caller+callee view `prism.slicer.distance.DistanceEngine`
uses for `D_hybrid`. The two distance models serve different consumers
with different needs and are not meant to produce the same numbers.

Edge cost is `c(e) = 1 / W(u, v)` (`prism.traversal.causal_weights.
edge_cost`) - a stronger causal coupling (higher `W`) costs *less* to
traverse, so Dijkstra naturally prefers a causally-coupled path over an
equal-hop-count uncoupled one, without ever needing a separate tie-break
rule the way `D_hybrid`'s `TagBonus` does.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.traversal._cache_keys import GraphCacheKey, _LRUCache, engine_commit_hash, graph_cache_key
from prism.traversal.causal_weights import LAMBDA_DATA_FLOW, LAMBDA_GUARD, compute_causal_edges, edge_cost

#: Backward-compatible aliases - this module's own cache-key logic
#: moved to `prism.traversal._cache_keys` (Step 4) so `causal_weights.
#: py` could share it without a circular import (see that module's own
#: docstring for why). Re-exported under their original names here
#: since tests and this module's own docstrings already reference them
#: this way.
_GraphCacheKey = GraphCacheKey
_engine_commit_hash = engine_commit_hash


def _graph_cache_key(builder: ConcreteGraphBuilder) -> GraphCacheKey:
    return graph_cache_key(builder.repo_root)


#: Blocker 1 performance work, Step 2/4: a correctly-keyed cache for
#: `build_causal_graph` - seed/budget-independent (a pure function of
#: `builder` alone), so a repo-content-addressed key is safe: the same
#: (repo, engine build, target-repo content) combination always
#: produces the same graph, on this instance or a different one, this
#: retrieve() call or a later one on the same engine.
#:
#: **Never** used for `compute_topological_distances` - that result is
#: seed-dependent and lives in its own, separately-keyed cache
#: (`_DISTANCE_CACHE`, Step 3, below) specifically to prevent the
#: silent-corruption bug a distance lookup under a seed-less key would
#: cause: a query for seed B would silently receive seed A's distances
#: - plausible-looking, wrong output that a same-seed bit-identical
#: test would never catch. `_DistanceCacheKey.digest()` always includes
#: `seed` - see that class's own docstring.
#:
#: Bookmark 1 Item 3: bounded at 10 entries (LRU-evicted) - one repo's
#: graph is a real, potentially large object; a long-running MCP
#: session touching many repos should not grow this without limit.
_GRAPH_CACHE: _LRUCache[nx.DiGraph] = _LRUCache(maxsize=10)

#: Blocker 1 Step 3/4: the seed-keyed distance cache - see
#: `_DistanceCacheKey` for its own key shape and why it is a distinct
#: dataclass from `_GraphCacheKey` rather than that key plus a seed
#: tacked on (the seed-separation guarantee is structural: there is no
#: code path in this module that can compute a `_DISTANCE_CACHE` key
#: without a `seed` argument, by construction, not by convention).
#:
#: Bookmark 1 Item 3: bounded at 100 entries (LRU-evicted) - the
#: fastest-growing of the five caches, since it holds one entry per
#: distinct seed queried against a repo, not one per repo.
_DISTANCE_CACHE: _LRUCache[dict[str, float]] = _LRUCache(maxsize=100)


@dataclass(frozen=True)
class _DistanceCacheKey:
    """`(repo_path, engine_commit_hash, file_hash_set, seed_symbol,
    distance_metric_params, d_max)` - the cache-key matrix's own literal
    shape for `compute_topological_distances`, plus `d_max` (Phase C).
    `distance_metric_params` here is `(LAMBDA_DATA_FLOW, LAMBDA_GUARD)` -
    the two real inputs to `edge_cost`/`causal_edge_weight` this
    function's own Dijkstra run actually depends on.

    The matrix's third named parameter, `DIST_MAX`, previously had no
    corresponding input to this function at all - `compute_topological_
    distances` took no hop-limit argument and always returned the full
    unfiltered reachable set. Phase C added a real `d_max` parameter
    (Issue #110), so this key now includes it for real: a `d_max=5.0`
    call and an unbounded (`d_max=None`) call against the identical
    `(repo, seed)` must never share a cache entry - the same "never
    silently return a mismatched result" reasoning `seed` itself is
    already held to below. `None` (the default, matching every existing
    caller) hashes to a fixed, stable sentinel string distinct from any
    real float value, so a pre-Phase-C cache entry is simply never a hit
    against the new key shape (a clean miss and recompute, not a
    collision) rather than needing an explicit migration.

    **Deliberately excludes `grammar_version`/`tag_rule_version`**,
    matching the matrix's own row for this function exactly - flagged
    directly, not silently accepted: since this function calls
    `build_causal_graph` internally, and that graph *does* depend on
    grammar/tag-rule version (see `_GraphCacheKey`), a grammar or
    tag-rule change with an unchanged `(repo_path, engine_commit_hash,
    file_hash_set, seed)` would leave a stale distance cached here even
    though the underlying graph would rebuild fresh via `_GRAPH_CACHE`'s
    own (correctly-keyed) miss. Implemented exactly as the matrix
    specifies per "no deviations" - not silently corrected - so this is
    a known, reported gap in the matrix's own design for this one row,
    not an oversight in this implementation.

    `seed` is always a required field - see `_DISTANCE_CACHE`'s own
    module-level docstring for why that is structural, not a
    convention that could be forgotten at a call site.
    """

    repo_path: str
    engine_commit_hash: str
    file_hash_set: str
    seed: str
    lambda_data_flow: float
    lambda_guard: float
    d_max: float | None = None
    direction: str = "forward"

    def digest(self) -> str:
        raw = "|".join(
            (
                self.repo_path,
                self.engine_commit_hash,
                self.file_hash_set,
                self.seed,
                repr(self.lambda_data_flow),
                repr(self.lambda_guard),
                repr(self.d_max),
                self.direction,
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def _distance_cache_key(
    builder: ConcreteGraphBuilder, seed: str, d_max: float | None = None, direction: str = "forward"
) -> _DistanceCacheKey:
    graph_key = _graph_cache_key(builder)
    return _DistanceCacheKey(
        repo_path=graph_key.repo_path,
        engine_commit_hash=graph_key.engine_commit_hash,
        file_hash_set=graph_key.file_hash_set,
        seed=seed,
        lambda_data_flow=LAMBDA_DATA_FLOW,
        lambda_guard=LAMBDA_GUARD,
        d_max=d_max,
        direction=direction,
    )


def build_causal_graph(builder: ConcreteGraphBuilder) -> nx.DiGraph:
    """The directed traversal graph `compute_topological_distances` runs
    Dijkstra over - every function/method `builder` indexed as a node,
    every real structural edge plus every synthetic causal-coupling edge
    as a weighted directed edge (`weight` = `edge_cost(W)`, the Dijkstra
    hop cost; `causal_weight` = the raw `W(u, v)` itself, kept on the
    edge for inspection/testing; `synthetic` = `True` for a
    causal-coupling-only edge with no real structural counterpart).

    Cached by `_graph_cache_key(builder)` (repo/engine/grammar/tag-rule/
    file-content addressed - see `_GraphCacheKey`'s own docstring) -
    the same (repo, engine build, target content) combination always
    returns the identical graph object, whether this is the 1st, 2nd,
    or 3rd call within one `retrieve()`, or a call from a later
    `retrieve()` on the same or a different engine instance.
    """
    key = _graph_cache_key(builder).digest()
    cached = _GRAPH_CACHE.get(key)
    if cached is not None:
        return cached
    weights, synthetic_edges = compute_causal_edges(builder)
    graph = nx.DiGraph()
    graph.add_nodes_from(builder.calls_graph.nodes())
    for (u, v), w in weights.items():
        graph.add_node(u)
        graph.add_node(v)
        graph.add_edge(u, v, weight=edge_cost(w), causal_weight=w, synthetic=(u, v) in synthetic_edges)
    _GRAPH_CACHE[key] = graph
    return graph


def compute_topological_distances(
    builder: ConcreteGraphBuilder, seed: str, d_max: float | None = None, direction: str = "forward"
) -> dict[str, float]:
    """`{node: dist_w(seed, node)}` - every node forward-reachable from
    `seed` in the causal graph, via Dijkstra over `c(e) = 1/W(u, v)`
    edge costs. `seed` itself is never included (distance 0 to itself is
    implicit - every consumer of this map already treats "not present"
    as "not reachable/not the seed", the same convention `DistanceEngine.
    compute_all` uses). Empty dict if `seed` isn't in the graph at all.

    `d_max` (Phase C, Issue #110): `None` (the default) preserves the
    exact prior behavior - the full, unfiltered reachable set, with no
    Dijkstra-internal bound - so every existing caller (`prism.surface.
    build.build_context_package`, `prism.packer.submodular_knapsack.
    pack_symbol_context`, `benchmarks.runner`) is completely unaffected.
    A caller that passes a real `d_max` gets the search itself stopped
    once the frontier's minimum distance exceeds it (`cutoff=d_max` on
    `nx.single_source_dijkstra_path_length`, which already implements
    exactly this early-termination semantics natively - no hand-rolled
    priority-queue loop needed), avoiding wasted exploration of a large
    repo's distant, irrelevant majority when only a small neighborhood
    around the seed is ever going to matter.

    Empirically verified safe against every real ground-truth pipeline
    symbol in the one corpus this repo currently has ground truth for
    (Django, 24 accepted tasks): the maximum distance from any task's
    seed to any of its own adjudicated `pipeline_symbols` is exactly
    3.0, none unreachable - see tests/phase_c/test_traversal_layer.py's
    own corpus-safety test, which re-verifies this directly against the
    live corpus rather than trusting this docstring's claim. `d_max=5.0`
    (this module's own recommended default for a caller that wants the
    bound) is not itself hard-coded here - callers that want it opt in
    explicitly - this function makes no claim about corpora it has never
    been run against (gin/trpc/express have no ground-truth tasks yet to
    verify against at all).

    `direction` (Phase C, Query Reach 3.4): `"forward"` (the default,
    byte-identical to every prior call - the causal graph's own outgoing
    edges, i.e. callees/instantiated classes/etc.) - `"reverse"`
    (incoming edges - callers/instantiators, via `graph.reverse(copy=
    False)`, a networkx view, not a rebuilt graph) - or `"both"` (the
    point-wise minimum of the forward and reverse distance maps, per
    node - a node reachable both 2 hops forward and 1 hop reverse gets
    the 1-hop distance). Raises `ValueError` for anything else, rather
    than silently falling back to forward.

    Simplification, stated honestly: `build_causal_graph`'s own edges
    already carry no `relation` label (only a pre-baked scalar `weight`
    - see that function's own docstring), so "reverse only inverts
    traversable functional relations, not metadata/non-directional
    edges" (the general concern that distinction exists for elsewhere in
    this codebase) is not separately re-checked here - every edge this
    causal graph contains is already a structural-or-synthetic-causal
    edge by construction (never e.g. a raw `READS_STATE` edge filtered
    in some other way), so reversing the whole graph reverses exactly
    the same edge set forward traversal already uses, nothing more. A
    future audit of exactly which relations `compute_causal_edges` folds
    in (it does not currently filter by `TRAVERSABLE_RELATIONS` before
    weighting - confirmed by reading it directly) is a real, separate
    question this phase does not attempt to resolve.

    Cached, seed-and-d_max-and-direction-keyed (`_DistanceCacheKey` -
    see its own docstring for the exact key shape). **The cache is never
    consulted or written without `seed` as part of the key** - this is
    the one constraint stated with the most force in the whole Blocker 1
    plan (a seed-less key would silently return a *different* seed's
    distances), so it is structural here: `_distance_cache_key` takes
    `seed` as a required positional argument, and there is no other code
    path into `_DISTANCE_CACHE`. `d_max`/`direction` are included in the
    same key for the identical reason - two different call shapes
    against the identical `(repo, seed)` must never share a cache entry.
    """
    if direction not in ("forward", "reverse", "both"):
        raise ValueError(f"direction must be 'forward', 'reverse', or 'both', got {direction!r}")

    dist_key = _distance_cache_key(builder, seed, d_max=d_max, direction=direction).digest()
    cached = _DISTANCE_CACHE.get(dist_key)
    if cached is not None:
        return cached
    graph = build_causal_graph(builder)
    if seed not in graph:
        return {}

    if direction == "both":
        forward = nx.single_source_dijkstra_path_length(graph, seed, cutoff=d_max, weight="weight")
        reverse = nx.single_source_dijkstra_path_length(graph.reverse(copy=False), seed, cutoff=d_max, weight="weight")
        distances = dict(forward)
        for node, dist in reverse.items():
            if node not in distances or dist < distances[node]:
                distances[node] = dist
    else:
        traversal_graph = graph if direction == "forward" else graph.reverse(copy=False)
        distances = nx.single_source_dijkstra_path_length(traversal_graph, seed, cutoff=d_max, weight="weight")
    distances.pop(seed, None)
    _DISTANCE_CACHE[dist_key] = distances
    return distances
