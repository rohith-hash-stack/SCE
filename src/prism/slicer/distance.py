"""Stage 4: the Dual-Graph Distance Metric (HLD section 2.2).

    D_hybrid(s, u) = lambda * d_hat_{G_C}(s, u) + (1 - lambda) * d_hat_{G_T}(sigma(s), sigma(u))

`d_hat_{G_C}` is the topological hop count in the (undirected) concrete
graph, normalized against a fixed horizon so a handful of hops still reads
as "close" while an unreachable node reads as maximally far - except an
edge `prism.runtime.reconciler` has marked `confidence="CONFIRMED_RUNTIME"`
(a real execution actually traversed it, not just static inference) costs
less than a normal hop, per `DistanceConfig.runtime_confidence_weight`, so
a path validated by actual execution reads as closer than an equal-length
unexercised static one. `d_hat_{G_T}` is the metamodel tag distance,
normalized against its own max penalty.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from prism.graph.metamodel import MAX_TAG_DISTANCE, SemanticMetamodel

DEFAULT_LAMBDA = 0.7
DEFAULT_MAX_HOPS = 10.0

# Hop cost for a `confidence="CONFIRMED_RUNTIME"` edge (see
# `prism.runtime.reconciler`) - strictly less than the normal 1.0-per-hop
# cost, so a path an actual execution traversed accumulates a smaller
# d_hat_{G_C} than an equal-length path inferred from static analysis
# alone. 0.5 halves a single confirmed hop's cost; a graph with no
# runtime-confirmed edges at all (every edge weight 1.0) reduces exactly
# to plain unweighted hop counting, so this is a pure extension with no
# behavior change for anything that hasn't been reconciled against a
# runtime trace.
DEFAULT_RUNTIME_CONFIDENCE_WEIGHT = 0.5


@dataclass(frozen=True)
class DistanceConfig:
    lambda_weight: float = DEFAULT_LAMBDA
    max_hops: float = DEFAULT_MAX_HOPS
    runtime_confidence_weight: float = DEFAULT_RUNTIME_CONFIDENCE_WEIGHT


class DistanceEngine:
    """Computes `D_hybrid(s, u)` for every reachable node relative to a seed."""

    def __init__(self, metamodel: SemanticMetamodel, tag_matrix: dict[str, set[str]], config: DistanceConfig | None = None) -> None:
        self.metamodel = metamodel
        self.tag_matrix = tag_matrix
        self.config = config or DistanceConfig()

    def compute_all(self, seed: str, g_c: nx.DiGraph) -> dict[str, float]:
        if seed not in g_c:
            return {}
        undirected = self._weighted_undirected(g_c)
        hop_distances = nx.single_source_dijkstra_path_length(undirected, seed, weight="weight")
        seed_tags = self.tag_matrix.get(seed, set())

        distances: dict[str, float] = {}
        for node, hops in hop_distances.items():
            if node == seed:
                continue
            node_tags = self.tag_matrix.get(node, set())
            distances[node] = self._d_hybrid(hops, seed_tags, node_tags)
        return distances

    def confirmed_runtime_reachable(self, seed: str, g_c: nx.DiGraph) -> set[str]:
        """Every node whose shortest (weighted) path from `seed` traverses
        at least one `confidence="CONFIRMED_RUNTIME"` edge - a real
        execution actually exercised some hop on the way there, not just
        static inference. `compute_all` already folds this into a lower
        distance value for such nodes; this is the explicit, rigorous
        version of that same signal (which specific nodes, not just "the
        numbers are smaller") `ContextKnapsackPacker` uses as an
        additional admission/ranking tie-breaker.
        """
        if seed not in g_c:
            return set()
        undirected = self._weighted_undirected(g_c)
        _lengths, paths = nx.single_source_dijkstra(undirected, seed, weight="weight")
        confirmed: set[str] = set()
        for node, path in paths.items():
            if node == seed:
                continue
            for u, v in zip(path, path[1:]):
                if (undirected.get_edge_data(u, v) or {}).get("confidence") == "CONFIRMED_RUNTIME":
                    confirmed.add(node)
                    break
        return confirmed

    def _weighted_undirected(self, g_c: nx.DiGraph) -> nx.Graph:
        """An undirected copy of `g_c` with every edge's hop cost set to
        `config.runtime_confidence_weight` (< 1.0 by default) if the
        runtime reconciler marked it `confidence="CONFIRMED_RUNTIME"`, or
        the normal 1.0 otherwise - so `single_source_dijkstra[_path_length]`
        below reduces to plain unweighted hop counting whenever a graph
        carries no runtime confidence data at all (every edge then weighs
        exactly 1.0), and only diverges from that once a `prism trace` run
        has actually confirmed some of its edges.
        """
        undirected = g_c.to_undirected()
        for _u, _v, data in undirected.edges(data=True):
            data["weight"] = (
                self.config.runtime_confidence_weight if data.get("confidence") == "CONFIRMED_RUNTIME" else 1.0
            )
        return undirected

    def _d_hybrid(self, hops: float, seed_tags: set[str], node_tags: set[str]) -> float:
        d_hat_gc = min(hops / self.config.max_hops, 1.0)
        tag_distance = self.metamodel.get_tag_distance(seed_tags, node_tags)
        d_hat_gt = min(tag_distance / MAX_TAG_DISTANCE, 1.0)
        return self.config.lambda_weight * d_hat_gc + (1 - self.config.lambda_weight) * d_hat_gt

    def resolution_for_distance(self, distance: float) -> int:
        """Resolution decay policy: closer nodes get more detail."""
        if distance <= 0.25:
            return 1
        if distance <= 0.55:
            return 2
        return 3


def architectural_path(
    seed: str,
    g_c: nx.DiGraph,
    tag_matrix: dict[str, set[str]],
    metamodel: SemanticMetamodel,
    max_direct_calls: int = 6,
) -> list[tuple[int, str | None, str]]:
    """A small, deterministic outline for the "Architectural Path" section:
    the seed, its direct callees, and - for any callee whose tags carry a
    `REQUIRES_BEFORE` obligation in the metamodel - the nearest node in the
    graph that satisfies it (e.g. a `#db_write` callee "requires" a node
    tagged `#auth_guard` somewhere in the same graph).
    """
    from prism.graph.metamodel import TagRelation

    lines: list[tuple[int, str | None, str]] = [(0, None, seed)]
    if seed not in g_c:
        return lines

    direct_callees = sorted(g_c.successors(seed))[:max_direct_calls]
    seen = {seed}
    for callee in direct_callees:
        lines.append((1, "calls", callee))
        seen.add(callee)
        for tag in sorted(tag_matrix.get(callee, set())):
            if tag not in metamodel.graph:
                continue
            for required_tag, edge_data in metamodel.graph[tag].items():
                if edge_data.get("relation") != TagRelation.REQUIRES_BEFORE:
                    continue
                provider = _find_node_with_tag(g_c, tag_matrix, required_tag, exclude=seen)
                if provider:
                    lines.append((2, "requires", provider))
                    seen.add(provider)
    return lines


def _find_node_with_tag(g_c: nx.DiGraph, tag_matrix: dict[str, set[str]], tag: str, exclude: set[str]) -> str | None:
    for node in sorted(g_c.nodes):
        if node in exclude:
            continue
        if tag in tag_matrix.get(node, set()):
            return node
    return None
