"""v1.1+ Empirical Benchmarking Harness: diagnostic metrics computed
directly from a `ContextPackage`/its underlying selected-symbol set
against ground truth - Causal Pipeline Integrity (`cpi.py`), Semantic
Redundancy Coefficient (`src.py`), Blast-Radius Caller Capture Rate
(`bccr.py`), IDF-weighted Feature Coverage Density (`fcc.py`), False
Positive Rate (`fpr.py`), and cold/warm latency timers (`latency.py`).
Every metric here is a pure function - no LLM calls, no network.
"""
from __future__ import annotations

from prism.surface.models import NodeEntry


def feature_tokens(node: NodeEntry) -> set[str]:
    """One node's own set of set `FeatureBit` names, parsed back out of
    `NodeFeatures`' comma-joined per-axis strings (`prism.surface.build.
    _axis_labels`' own output format) - `"NONE"` (an axis with nothing
    set) contributes nothing. Shared by `src.py` (redundancy: is this
    node's own feature set a subset of everything else already packed)
    and `fcc.py` (density: which distinct features does the whole packed
    set cover at all).
    """
    tokens: set[str] = set()
    for axis_value in (node.features.substance, node.features.form, node.features.output, node.features.role):
        for token in axis_value.split(","):
            token = token.strip()
            if token and token != "NONE":
                tokens.add(token)
    return tokens
