"""Call-graph and architectural-invariant coverage metrics (HLD evaluation
dimension 2): how much of a "ground-truth" k-hop neighborhood around the
target seed actually survives into the packed Prism context.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from prism.tagger.rules import AUTH_GUARD_RULE, CALL_SINK_RULES, DECORATOR_RULES, STATE_MUTATION_TAG

# The 8 tags Prism's deterministic tagger can actually assign (derived from
# the rule tables themselves, not hand-duplicated, so this can't drift out
# of sync with prism.tagger.rules). `#payment_charge` is a metamodel-only
# relationship node - HLD section 4.2's rule table never assigns it - so it
# is intentionally excluded here.
INVARIANT_TAGS: tuple[str, ...] = tuple(
    sorted(
        {rule.tag for rule in CALL_SINK_RULES}
        | {rule.tag for rule in DECORATOR_RULES}
        | {AUTH_GUARD_RULE.tag, STATE_MUTATION_TAG}
    )
)


class CoverageError(Exception):
    """Raised when a ground-truth subgraph cannot be built for a seed."""


@dataclass(frozen=True)
class CoverageResult:
    k_hops: int
    subgraph_node_count: int
    subgraph_edge_count: int
    reached_nodes: int
    reached_node_ratio: float
    preserved_edges: int
    preserved_edge_ratio: float
    tag_totals: dict[str, int] = field(default_factory=dict)
    tag_preserved: dict[str, int] = field(default_factory=dict)
    invariant_coverage_ratio: float = 1.0
    missing_invariant_tags: tuple[str, ...] = ()


def ground_truth_subgraph(g_c: nx.DiGraph, seed: str, k: int) -> nx.DiGraph:
    """`G_sub`: the induced subgraph over every node within `k` directed
    hops of `seed` (i.e. reachable along an actual call path of length <=
    k), plus the seed itself. Directed, matching what "call paths" means
    for the target - the same notion of reachability the raw-dump baseline
    uses for its file closure.
    """
    if seed not in g_c:
        raise CoverageError(f"seed symbol '{seed}' is not a known node in the concrete graph")
    if k < 0:
        raise ValueError("k must be >= 0")
    lengths = nx.single_source_shortest_path_length(g_c, seed, cutoff=k)
    return g_c.subgraph(set(lengths.keys())).copy()


def compute_coverage(
    g_sub: nx.DiGraph,
    tag_matrix: dict[str, set[str]],
    packed_symbols: set[str],
    k_hops: int,
    invariant_tags: tuple[str, ...] = INVARIANT_TAGS,
) -> CoverageResult:
    """Compare `g_sub` (the ground truth) against `packed_symbols` (the set
    of symbols that actually made it into the Prism context package).
    """
    sub_nodes = set(g_sub.nodes)
    reached = sub_nodes & packed_symbols
    reached_ratio = (len(reached) / len(sub_nodes)) if sub_nodes else 1.0

    sub_edges = list(g_sub.edges())
    preserved_edges = [(u, v) for u, v in sub_edges if u in packed_symbols and v in packed_symbols]
    edge_ratio = (len(preserved_edges) / len(sub_edges)) if sub_edges else 1.0

    tag_totals = {tag: 0 for tag in invariant_tags}
    tag_preserved = {tag: 0 for tag in invariant_tags}
    for node in sub_nodes:
        node_tags = tag_matrix.get(node, set())
        for tag in invariant_tags:
            if tag in node_tags:
                tag_totals[tag] += 1
                if node in packed_symbols:
                    tag_preserved[tag] += 1

    total_tag_instances = sum(tag_totals.values())
    preserved_tag_instances = sum(tag_preserved.values())
    invariant_ratio = (preserved_tag_instances / total_tag_instances) if total_tag_instances else 1.0
    missing = tuple(sorted(tag for tag in invariant_tags if tag_totals[tag] > 0 and tag_preserved[tag] == 0))

    return CoverageResult(
        k_hops=k_hops,
        subgraph_node_count=len(sub_nodes),
        subgraph_edge_count=len(sub_edges),
        reached_nodes=len(reached),
        reached_node_ratio=round(reached_ratio, 4),
        preserved_edges=len(preserved_edges),
        preserved_edge_ratio=round(edge_ratio, 4),
        tag_totals=tag_totals,
        tag_preserved=tag_preserved,
        invariant_coverage_ratio=round(invariant_ratio, 4),
        missing_invariant_tags=missing,
    )
