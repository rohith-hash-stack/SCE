"""IDF-weighted Feature Coverage Density (FCC) - how much genuinely rare,
information-dense semantic content a packed set delivers per 1,000
tokens spent, weighting each distinct feature by its corpus-wide rarity
(`IDF(f) = log(N / (1 + df(f)))` - a feature every symbol in the repo
carries contributes almost nothing; one only a handful of symbols carry
contributes a lot).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.bitmask import ALL_KNOWN_BITS
from prism.semantics.extractor import compute_feature_masks
from prism.surface.models import ContextPackage

from benchmarks.metrics import feature_tokens


@dataclass
class CorpusFeatureStats:
    """`N` and `df(f)` computed once per indexed repository (every
    symbol, not just what any one engine packed) - reused across every
    task/engine/budget combination run against that same corpus, so this
    is deliberately its own object rather than recomputed per `fcc` call.
    """

    total_symbols: int
    document_frequency: dict[str, int]


def compute_corpus_feature_stats(builder: ConcreteGraphBuilder) -> CorpusFeatureStats:
    masks = compute_feature_masks(builder)
    document_frequency: dict[str, int] = {}
    for mask in masks.values():
        for bit in ALL_KNOWN_BITS:
            if mask & int(bit):
                document_frequency[bit.name] = document_frequency.get(bit.name, 0) + 1
    return CorpusFeatureStats(total_symbols=len(masks), document_frequency=document_frequency)


def idf(feature: str, stats: CorpusFeatureStats) -> float:
    if stats.total_symbols <= 0:
        return 0.0
    df = stats.document_frequency.get(feature, 0)
    return math.log(stats.total_symbols / (1 + df))


def has_coordinate_space_features(pkg: ContextPackage) -> bool:
    """Whether `pkg` carries any real (non-`"NONE"`) four-axis feature
    label on any node, on any axis - FCC's own coordinate space. A
    retrieval engine that never computes per-node feature vectors at
    all (the lexical/topological baselines - BM25, BFS forward and
    bidirectional - hardcode every axis to `"NONE"` on every node,
    verifiably, in their own source) always produces `False` here; a
    real four-axis engine (Prism, Oracle) produces `True` for any
    package with at least one classifiable node - which in practice is
    every package that isn't literally empty of any indexed symbol."""
    return any(feature_tokens(node) for node in pkg.nodes)


def fcc(pkg: ContextPackage, stats: CorpusFeatureStats) -> float | None:
    """`FCC(S_M) = sum(IDF(f) for f in union(Phi(v))) / (Total Tokens(S_M) / 1000)`.

    `None` (never a fabricated `0.0`) when `pkg` carries no real
    four-axis feature data at all (`has_coordinate_space_features` is
    `False`) - FCC is an internal packing-density diagnostic defined
    over that coordinate space; it is structurally inapplicable to an
    engine that never operates over it, not "measured and found to be
    zero". `0.0` for a package that spent zero tokens (nothing to
    divide by, and nothing was covered either) despite otherwise
    carrying real feature data.
    """
    if not has_coordinate_space_features(pkg):
        return None
    total_tokens = sum(node.cost for node in pkg.nodes)
    if total_tokens <= 0:
        return 0.0
    covered_features: set[str] = set()
    for node in pkg.nodes:
        covered_features |= feature_tokens(node)
    weighted_sum = sum(idf(f, stats) for f in covered_features)
    return weighted_sum / (total_tokens / 1000)
