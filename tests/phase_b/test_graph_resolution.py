"""Phase B: graph resolution layer (G40 inherited-method resolution, G41
attribute-chain disambiguation, G44 tentative fallback, data-flow wiring).

Import-time note: `prism.graph.weights` is a theoretical reference table,
NOT the live traversal cost model - see that module's own docstring. Tests
against it (test_w_tentative_dominance_bound) verify the algebraic
derivation only; tests against real Dijkstra behavior
(test_tentative_call_loses_to_one_confident_hop) exercise the actual,
already-wired `prism.slicer.distance` cost model instead.
"""
from prism.graph.weights import (
    EDGE_WEIGHTS,
    MAX_EDGE_COST,
    MAX_INHERITANCE_DEPTH,
    W_TENTATIVE,
)


# ============================================================
# G44 dominance derivation (reference table only)
# ============================================================

def test_w_tentative_dominance_bound():
    """W_TENTATIVE, as derived in prism.graph.weights, strictly exceeds
    the worst-case structural path cost over every edge weight the
    reference table defines - a pure algebraic property of the constants
    themselves, independent of whether any real edge is currently priced
    this way (see module docstring: it isn't, yet)."""
    worst_case_structural_path = MAX_INHERITANCE_DEPTH * MAX_EDGE_COST
    assert worst_case_structural_path == 12.5
    assert W_TENTATIVE > worst_case_structural_path
    assert W_TENTATIVE == 15.0
    # Re-derive MAX_EDGE_COST independently from every weight in the
    # table, rather than trusting the module's own computation - this is
    # the "against all possible edge weights" check.
    recomputed_max = max(1.0 / w for w in EDGE_WEIGHTS.values())
    assert recomputed_max == MAX_EDGE_COST
