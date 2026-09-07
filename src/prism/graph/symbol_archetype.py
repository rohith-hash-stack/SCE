"""Layer 2 of the Semantic Knowledge Engine: per-symbol Behavioral
Archetype classification, applying the decision matrix given verbatim in
the hierarchical-intent-profiling spec against a `FlowEngineResult`
(`prism.analysis.flow_engine`) plus each symbol's own `BehavioralContract`
and `calls_graph` out-degree.

Purely a lookup/threshold pass over already-computed numbers - no new
graph traversal, no new AST work - so classifying every symbol in a
repository costs microseconds once `FlowEngineResult` exists (and is free
entirely on a `flow_cache` hit).
"""
from __future__ import annotations

from prism.analysis.flow_engine import FlowEngineResult
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract

VERIFICATION_ORACLE = "VERIFICATION_ORACLE"
UI_STATE_MUTATOR = "UI_STATE_MUTATOR"
PURE_TRANSFORMER_LEAF = "PURE_TRANSFORMER_LEAF"
ARCHITECTURAL_CHOKEPOINT = "ARCHITECTURAL_CHOKEPOINT"
COMPOSITE_ORCHESTRATOR = "COMPOSITE_ORCHESTRATOR"
TERMINAL_BOUNDARY_SINK = "TERMINAL_BOUNDARY_SINK"
GENERAL_LOGIC_UNIT = "GENERAL_LOGIC_UNIT"

#: `C_B(u) >= 0.85` is checked ahead of the composite-orchestrator /
#: verification-oracle rules below (matches the spec's own ordering: a
#: chokepoint that also happens to assert is still, first and foremost, a
#: chokepoint) - see `classify_symbol`'s docstring for the full order.
_CHOKE_THRESHOLD = 0.85


def classify_symbol(
    symbol: str,
    result: FlowEngineResult,
    contract: BehavioralContract | None,
    out_degree: int,
) -> str:
    """Applies the Layer 2 decision matrix, in the exact priority order the
    spec lists it (verification/UI/purity checks first since they read the
    most specific signal - a dominant effect dimension - then the two
    purely structural rules, chokepoint before orchestrator since a node
    can satisfy both and "chokepoint" is the stronger claim):

      1. `f_down_ASSERT > 0.5 AND delta_u > 0.6` -> VERIFICATION_ORACLE
      2. `f_down_UI > 0.5 AND f_down_ASSERT < 0.2` -> UI_STATE_MUTATOR
      3. `f_down_PURE = 1.0 AND purity == pure` -> PURE_TRANSFORMER_LEAF
      4. `C_B(u) >= 0.85` -> ARCHITECTURAL_CHOKEPOINT
      5. `out_degree(u) > 2 AND delta_u in [0.2, 0.5]` -> COMPOSITE_ORCHESTRATOR

    Falls back to GENERAL_LOGIC_UNIT when none of the above match - a
    symbol doing normal, unremarkable internal work is a real, common
    category the spec doesn't name but every classifier needs a default
    for (never silently mislabeled as one of the five specific archetypes).
    """
    egress = result.egress(symbol)
    delta = result.depth(symbol)

    if egress["ASSERT_SIGNAL"] > 0.5 and delta > 0.6:
        return VERIFICATION_ORACLE
    if egress["UI_RENDER"] > 0.5 and egress["ASSERT_SIGNAL"] < 0.2:
        return UI_STATE_MUTATOR
    if egress["PURE_LEAF"] == 1.0 and contract is not None and contract.purity == "pure":
        return PURE_TRANSFORMER_LEAF
    if result.choke(symbol) >= _CHOKE_THRESHOLD:
        return ARCHITECTURAL_CHOKEPOINT
    if out_degree > 2 and 0.2 <= delta <= 0.5:
        return COMPOSITE_ORCHESTRATOR
    return GENERAL_LOGIC_UNIT


def classify_all(
    builder: ConcreteGraphBuilder,
    result: FlowEngineResult,
    contracts: dict[str, BehavioralContract],
) -> dict[str, str]:
    """Classifies every internal symbol `FlowEngineResult` covers, plus
    every external sink node as TERMINAL_BOUNDARY_SINK (a sink was never a
    candidate for the internal decision matrix above - it has no
    `delta_u`/out-degree of its own within `calls_graph`, it terminates
    flow by definition)."""
    g = builder.calls_graph
    archetypes: dict[str, str] = {}
    for symbol in result.downstream_egress:
        out_degree = g.out_degree(symbol) if symbol in g else 0
        archetypes[symbol] = classify_symbol(symbol, result, contracts.get(symbol), out_degree)
    for sink in result.sink_nodes:
        archetypes[sink] = TERMINAL_BOUNDARY_SINK
    return archetypes
