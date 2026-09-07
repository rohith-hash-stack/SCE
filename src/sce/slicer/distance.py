"""Stage 4: the Dual-Graph Distance Metric (HLD section 2.2).

    D_hybrid(s, u) = lambda * d_hat_{G_C}(s, u) + (1 - lambda) * d_hat_{G_T}(sigma(s), sigma(u))

`d_hat_{G_C}` is the topological hop count in the (undirected) concrete
graph, normalized against a fixed horizon so a handful of hops still reads
as "close" while an unreachable node reads as maximally far. `d_hat_{G_T}`
is the metamodel tag distance, normalized against its own max penalty.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from sce.graph.metamodel import MAX_TAG_DISTANCE, SemanticMetamodel

DEFAULT_LAMBDA = 0.7
DEFAULT_MAX_HOPS = 10.0


@dataclass(frozen=True)
class DistanceConfig:
    lambda_weight: float = DEFAULT_LAMBDA
    max_hops: float = DEFAULT_MAX_HOPS


class DistanceEngine:
    """Computes `D_hybrid(s, u)` for every reachable node relative to a seed."""

    def __init__(self, metamodel: SemanticMetamodel, tag_matrix: dict[str, set[str]], config: DistanceConfig | None = None) -> None:
        self.metamodel = metamodel
        self.tag_matrix = tag_matrix
        self.config = config or DistanceConfig()

    def compute_all(self, seed: str, g_c: nx.DiGraph) -> dict[str, float]:
        if seed not in g_c:
            return {}
        undirected = g_c.to_undirected()
        hop_distances = nx.single_source_shortest_path_length(undirected, seed)
        seed_tags = self.tag_matrix.get(seed, set())

        distances: dict[str, float] = {}
        for node, hops in hop_distances.items():
            if node == seed:
                continue
            node_tags = self.tag_matrix.get(node, set())
            distances[node] = self._d_hybrid(hops, seed_tags, node_tags)
        return distances

    def _d_hybrid(self, hops: int, seed_tags: set[str], node_tags: set[str]) -> float:
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
    from sce.graph.metamodel import TagRelation

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
