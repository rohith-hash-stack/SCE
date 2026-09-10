"""Stage 4: the Dual-Graph Distance Metric (HLD section 2.2).

    D_hybrid(s, u) = lambda * d_hat_{G_C}(s, u) + TagBonus(s, u)

`d_hat_{G_C}` is the topological hop count in the (undirected) concrete
graph, normalized against a fixed horizon so a handful of hops still reads
as "close" while an unreachable node reads as maximally far - except an
edge `prism.runtime.reconciler` has marked `confidence="CONFIRMED_RUNTIME"`
(a real execution actually traversed it, not just static inference) costs
less than a normal hop, per `DistanceConfig.runtime_confidence_weight`, so
a path validated by actual execution reads as closer than an equal-length
unexercised static one.

`TagBonus(s, u)` is a *tie-breaker*, not a peer term, over `d_hat_{G_T}`
(the metamodel tag distance, normalized against its own max penalty):
its entire possible range is strictly clamped below the smallest possible
increment a single extra topological hop can ever contribute
(`lambda / max_hops`, halved again for margin - see
`DistanceConfig.tag_bonus_safety_margin`), so no amount of tag overlap can
ever make a `(k+1)`-hop node outrank a `k`-hop one (Issue #8 / Invariant
#1: Topological Monotonicity). Before this bound existed, the tag term
was blended in at full peer weight (`(1 - lambda) * d_hat_{G_T}`, up to
0.3 of the total score at the `lambda=0.7` default) against a per-hop
step of only `lambda / max_hops` (0.07 at the same default) - a real,
reproducible regression where a 1-hop direct callee with zero tag overlap
scored *worse* (a `D_hybrid` value further from the seed) than a 3-hop
sibling that merely happened to share a tag with the seed.
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

# Extra discount multiplier applied on top of `runtime_confidence_weight`
# for an edge `prism.runtime.reconciler` marked `high_trust_runtime=True` -
# only ever set when that reconciliation's own RuntimeTrust (Matched
# Events / Total Events) was >= `RUNTIME_TRUST_HIGH_THRESHOLD` (Issue
# #15.3: "If RuntimeTrust >= 0.9, boost confidence weights on
# runtime-confirmed edges"). A normal confirmed edge already costs
# `runtime_confidence_weight` (0.5 by default); a high-trust one costs
# `runtime_confidence_weight * HIGH_TRUST_EXTRA_DISCOUNT` (0.25 by
# default) - strictly less, so a trace whose reconciliation was almost
# entirely clean earns its edges an even lower hop cost than one with
# many orphans, without ever letting an untrusted trace's edges masquerade
# at the same confidence.
DEFAULT_HIGH_TRUST_EXTRA_DISCOUNT = 0.5


#: `TagBonus`'s maximum possible swing (best-possible-tag-match vs.
#: worst-possible-tag-match) is `tag_bonus_safety_margin * (lambda_weight /
#: max_hops)` - strictly less than one topological hop-step's own
#: contribution (`lambda_weight / max_hops`) by this fraction, guaranteeing
#: Topological Monotonicity (Issue #8) with real, provable headroom rather
#: than a value (like the audit's own illustrative "15%") that isn't
#: actually safe at this module's `max_hops=10` default - 0.5 means the
#: full tag-bonus range never exceeds half of one hop-step, giving a full
#: 2x safety factor.
DEFAULT_TAG_BONUS_SAFETY_MARGIN = 0.5

# Structural-causality edge weights (Issue #9) - how strongly each
# relation type implies "this node is really part of the seed's own
# behavioral neighborhood", 1.0 being a normal CALLS/INSTANTIATES hop.
# The Dijkstra hop *cost* used below is `1 / weight`, so a weaker
# relation (a lower number here) costs strictly more than one hop
# (EXTENDS: 1/0.85 ~= 1.18 hops, IMPLEMENTS: 1/0.80 = 1.25 hops) - real
# and reachable (see `ConcreteGraphBuilder.TRAVERSABLE_RELATIONS`'s own
# docstring for the "phantom method" failure this closes), but never as
# cheap as an actual call, so an inherited method never outranks a
# same-or-fewer-hop behavioral neighbor under a tight token budget.
RELATION_STRUCTURAL_WEIGHT: dict[str, float] = {
    "CALLS": 1.0,
    "INSTANTIATES": 1.0,
    "OVERRIDES": 0.90,
    "EXTENDS": 0.85,
    "IMPLEMENTS": 0.80,
}


@dataclass(frozen=True)
class DistanceConfig:
    lambda_weight: float = DEFAULT_LAMBDA
    max_hops: float = DEFAULT_MAX_HOPS
    runtime_confidence_weight: float = DEFAULT_RUNTIME_CONFIDENCE_WEIGHT
    high_trust_extra_discount: float = DEFAULT_HIGH_TRUST_EXTRA_DISCOUNT
    tag_bonus_safety_margin: float = DEFAULT_TAG_BONUS_SAFETY_MARGIN


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
            structural_weight = RELATION_STRUCTURAL_WEIGHT.get(data.get("relation", "CALLS"), 1.0)
            base_cost = 1.0 / structural_weight if structural_weight else 1.0
            if data.get("confidence") != "CONFIRMED_RUNTIME":
                data["weight"] = base_cost
            elif data.get("high_trust_runtime"):
                data["weight"] = base_cost * self.config.runtime_confidence_weight * self.config.high_trust_extra_discount
            else:
                data["weight"] = base_cost * self.config.runtime_confidence_weight
        return undirected

    def _d_hybrid(self, hops: float, seed_tags: set[str], node_tags: set[str]) -> float:
        # Deliberately *unclamped* here (no `min(..., 1.0)`) - the
        # Topological Monotonicity proof below depends on the raw
        # hop-count term growing without bound, so a real hop always adds
        # its full `lambda_weight / max_hops` no matter how far out it is;
        # only the final combined value is clamped to [0, 1] for the
        # knapsack packer/resolution-tier consumers that expect a bounded
        # score.
        raw_gc = hops / self.config.max_hops
        tag_distance = self.metamodel.get_tag_distance(seed_tags, node_tags)
        d_hat_gt = min(tag_distance / MAX_TAG_DISTANCE, 1.0)
        # TagBonus(s, u): a *mismatch penalty*, not a peer term - 0 for a
        # perfect tag match (d_hat_gt=0, no penalty at all) up to
        # `tag_bonus_cap` for the worst possible mismatch (d_hat_gt=1),
        # bounded well below one topological hop-step (`min_hop_gap`) by
        # `tag_bonus_safety_margin` (< 1.0). Proof this can never flip hop
        # ordering: take any u closer than v (hop(v) >= hop(u) + 1). The
        # worst case for monotonicity is u at maximum penalty and v at
        # zero penalty:
        #   D(u) = lambda*raw_gc(u) + tag_bonus_cap
        #   D(v) = lambda*raw_gc(v) + 0
        # D(u) < D(v) requires tag_bonus_cap < lambda*(raw_gc(v)-raw_gc(u))
        # <= lambda*(hop(v)-hop(u))/max_hops, and since hop(v)-hop(u) >= 1,
        # it's sufficient that tag_bonus_cap < lambda/max_hops = min_hop_gap
        # - exactly what `tag_bonus_safety_margin < 1.0` guarantees, with
        # margin to spare at the default 0.5.
        min_hop_gap = self.config.lambda_weight / self.config.max_hops
        tag_bonus_cap = self.config.tag_bonus_safety_margin * min_hop_gap
        tag_bonus = tag_bonus_cap * d_hat_gt
        d_hybrid = self.config.lambda_weight * raw_gc + tag_bonus
        return min(d_hybrid, 1.0)

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
