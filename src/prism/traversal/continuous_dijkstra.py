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

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.traversal.causal_weights import compute_causal_edges, edge_cost


def build_causal_graph(builder: ConcreteGraphBuilder) -> nx.DiGraph:
    """The directed traversal graph `compute_topological_distances` runs
    Dijkstra over - every function/method `builder` indexed as a node,
    every real structural edge plus every synthetic causal-coupling edge
    as a weighted directed edge (`weight` = `edge_cost(W)`, the Dijkstra
    hop cost; `causal_weight` = the raw `W(u, v)` itself, kept on the
    edge for inspection/testing; `synthetic` = `True` for a
    causal-coupling-only edge with no real structural counterpart).
    """
    weights, synthetic_edges = compute_causal_edges(builder)
    graph = nx.DiGraph()
    graph.add_nodes_from(builder.calls_graph.nodes())
    for (u, v), w in weights.items():
        graph.add_node(u)
        graph.add_node(v)
        graph.add_edge(u, v, weight=edge_cost(w), causal_weight=w, synthetic=(u, v) in synthetic_edges)
    return graph


def compute_topological_distances(builder: ConcreteGraphBuilder, seed: str) -> dict[str, float]:
    """`{node: dist_w(seed, node)}` - every node forward-reachable from
    `seed` in the causal graph, via Dijkstra over `c(e) = 1/W(u, v)`
    edge costs. `seed` itself is never included (distance 0 to itself is
    implicit - every consumer of this map already treats "not present"
    as "not reachable/not the seed", the same convention `DistanceEngine.
    compute_all` uses). Empty dict if `seed` isn't in the graph at all.
    """
    graph = build_causal_graph(builder)
    if seed not in graph:
        return {}
    distances = nx.single_source_dijkstra_path_length(graph, seed, weight="weight")
    distances.pop(seed, None)
    return distances
