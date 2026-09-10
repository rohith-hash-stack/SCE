"""Semantic Redundancy Coefficient (SRC) - what fraction of a packed set
`S_M` is genuinely redundant: a node whose removal (a) doesn't break any
required causal pipeline and (b) loses no feature coverage at all, since
everything it contributes is already covered by the rest of the packed
set. High SRC means the engine burned budget on near-duplicate siblings
(five identical loggers) instead of genuinely novel context - the same
"submodular diminishing returns" property `prism.packer.submodular_
knapsack` is designed to prevent, measured here from the outside, on
whatever `ContextPackage` any engine (including the baselines, which
never compute real feature masks at all - see `benchmarks.metrics.
feature_tokens`) actually produced.
"""
from __future__ import annotations

from prism.surface.models import ContextPackage

from benchmarks.metrics import feature_tokens


def redundant_nodes(pkg: ContextPackage, pipeline: set[str] | None = None) -> set[str]:
    """Every non-seed, non-pipeline node in `pkg.nodes` whose own feature
    token set is a subset of the union of every *other* packed node's
    feature tokens - "removal preserves all pipelines" (the seed and any
    `pipeline` member are always protected, never counted as redundant -
    removing either could break a required causal chain regardless of
    its own feature overlap) "and whose feature bitmask is a subset of
    the remaining packed set" (the spec's own two conditions, applied
    literally).
    """
    pipeline = pipeline or set()
    protected = {n.id for n in pkg.nodes if n.role == "seed"} | pipeline

    tokens_by_id = {n.id: feature_tokens(n) for n in pkg.nodes}
    redundant: set[str] = set()
    for node in pkg.nodes:
        if node.id in protected:
            continue
        own = tokens_by_id[node.id]
        remaining = set()
        for other_id, other_tokens in tokens_by_id.items():
            if other_id != node.id:
                remaining |= other_tokens
        if own <= remaining:
            redundant.add(node.id)
    return redundant


def src(pkg: ContextPackage, pipeline: set[str] | None = None) -> float:
    """`SRC(S_M) = |RedundantNodes(S_M, G*)| / |S_M|` - `0.0` for an
    empty package (no nodes to be redundant)."""
    if not pkg.nodes:
        return 0.0
    return len(redundant_nodes(pkg, pipeline)) / len(pkg.nodes)
