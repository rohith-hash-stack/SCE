"""Regression coverage for B1 (knapsack admission tie-break
determinism): `select_submodular_context`'s admission loop used to
iterate `frontier` (a `set`) directly, so an exact density tie between
two candidates resolved to whichever one Python's set happened to yield
first - hash-order-dependent, not reproducible run to run, and with no
preference for either candidate. Fixed by iterating `sorted(frontier)`
instead, so a tie's winner is always the alphabetically-first candidate.
"""
from __future__ import annotations

import networkx as nx

from prism.packer.submodular_knapsack import select_submodular_context


def _tied_graph() -> nx.DiGraph:
    """Three candidates - "z_candidate", "a_candidate", "m_candidate" -
    all at the identical dist_w, identical feature mask (so delta_feat
    ties too), and identical cost: a genuine, exact three-way density
    tie, not a near-tie."""
    g = nx.DiGraph()
    g.add_node("seed")
    for name in ("z_candidate", "a_candidate", "m_candidate"):
        g.add_edge("seed", name)
    return g


def test_exact_tie_always_admits_the_alphabetically_first_candidate():
    graph = _tied_graph()
    dist_w_map = {"z_candidate": 1.0, "a_candidate": 1.0, "m_candidate": 1.0}
    feature_masks = {"seed": 0, "z_candidate": 0, "a_candidate": 0, "m_candidate": 0}
    costs = {"seed": 0, "z_candidate": 10, "a_candidate": 10, "m_candidate": 10}

    result = select_submodular_context(graph, "seed", target_budget=10, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)

    assert result == ["seed", "a_candidate"]


def test_tie_break_is_deterministic_across_repeated_calls():
    graph = _tied_graph()
    dist_w_map = {"z_candidate": 1.0, "a_candidate": 1.0, "m_candidate": 1.0}
    feature_masks = {"seed": 0, "z_candidate": 0, "a_candidate": 0, "m_candidate": 0}
    costs = {"seed": 0, "z_candidate": 10, "a_candidate": 10, "m_candidate": 10}

    results = [
        select_submodular_context(graph, "seed", target_budget=10, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
        for _ in range(20)
    ]

    assert all(r == results[0] for r in results), "the same exact tie must resolve identically on every call"


def test_a_real_density_win_is_unaffected_by_the_sorted_iteration():
    """Sorting the iteration order must never change the outcome of a
    genuine (non-tied) comparison - only exact ties are affected."""
    graph = _tied_graph()
    dist_w_map = {"z_candidate": 1.0, "a_candidate": 5.0, "m_candidate": 5.0}
    feature_masks = {"seed": 0, "z_candidate": 0, "a_candidate": 0, "m_candidate": 0}
    costs = {"seed": 0, "z_candidate": 10, "a_candidate": 10, "m_candidate": 10}

    result = select_submodular_context(graph, "seed", target_budget=10, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)

    # z_candidate is genuinely closer (dist_w=1.0 vs 5.0) so it has a real,
    # unambiguous higher density - alphabetically last, but must still win.
    assert result == ["seed", "z_candidate"]
