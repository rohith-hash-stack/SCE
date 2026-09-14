"""Regression coverage for B2 (unresolved call-site placeholder
exclusion): `select_submodular_context` used to admit any graph node
within max_hops, including unresolved polymorphic call-site markers
(`prism.graph.symbol_table.unresolved_polymorphic_node_id`, shaped
`<ambiguous:...>`) and other nodes with no real, resolvable symbol-table
entry (an external/stdlib reference the indexer couldn't find a real
source snippet for). Such a node always gets `cost == 0` from
`_default_costs` (its own `max(count_tokens(...), 1)` floor guarantees a
*real* symbol's cost is never 0), so `cost == 0` is an exact, reliable
"not a real, renderable symbol" signal. Fixed by excluding any candidate
with `costs.get(candidate, 0) == 0` from ever entering the frontier.
"""
from __future__ import annotations

import networkx as nx

from prism.packer.submodular_knapsack import select_submodular_context


def test_ambiguous_placeholder_is_never_admitted():
    graph = nx.DiGraph()
    graph.add_edge("seed", "real_callee")
    graph.add_edge("seed", "<ambiguous:foo@src/x.py:10>")

    dist_w_map = {"real_callee": 1.0, "<ambiguous:foo@src/x.py:10>": 1.0}
    feature_masks = {"seed": 0, "real_callee": 0, "<ambiguous:foo@src/x.py:10>": 0}
    # The placeholder gets cost 0 - exactly what _default_costs would
    # produce for it (builder.symbol_table.get(...) is None for a
    # sentinel node, so `_default_costs` never gets past its own
    # `if info is None: costs[qname] = 0` branch).
    costs = {"seed": 0, "real_callee": 5, "<ambiguous:foo@src/x.py:10>": 0}

    result = select_submodular_context(
        graph, "seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs
    )

    assert result == ["seed", "real_callee"]
    assert "<ambiguous:foo@src/x.py:10>" not in result


def test_any_zero_cost_unresolved_node_is_excluded_not_just_ambiguous_shaped_ones():
    """The exclusion is a real cost==0 signal, not a string-prefix check
    on `<ambiguous:` - an external/stdlib reference with no resolvable
    body (also cost 0) is excluded the same way."""
    graph = nx.DiGraph()
    graph.add_edge("seed", "real_callee")
    graph.add_edge("seed", "itertools.groupby")

    dist_w_map = {"real_callee": 1.0, "itertools.groupby": 1.0}
    feature_masks = {"seed": 0, "real_callee": 0, "itertools.groupby": 0}
    costs = {"seed": 0, "real_callee": 5, "itertools.groupby": 0}

    result = select_submodular_context(
        graph, "seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs
    )

    assert result == ["seed", "real_callee"]
    assert "itertools.groupby" not in result


def test_a_real_symbol_with_the_minimum_real_cost_of_one_is_still_admitted():
    """Never over-exclude: a real symbol's cost floor is 1 (never 0), so
    a genuinely tiny real symbol must still be a normal candidate."""
    graph = nx.DiGraph()
    graph.add_edge("seed", "tiny_real_symbol")

    dist_w_map = {"tiny_real_symbol": 1.0}
    feature_masks = {"seed": 0, "tiny_real_symbol": 0}
    costs = {"seed": 0, "tiny_real_symbol": 1}

    result = select_submodular_context(
        graph, "seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs
    )

    assert result == ["seed", "tiny_real_symbol"]


def test_placeholder_successors_are_never_explored_either():
    """An excluded node's own successors must not enter the frontier via
    expansion from it - since it's never admitted in the first place,
    graph.successors(best_node) is never called on it, but this pins
    that a placeholder cannot smuggle its own successors in some other
    way (e.g. if it also happened to be within max_hops of the seed
    directly)."""
    graph = nx.DiGraph()
    graph.add_edge("seed", "<ambiguous:foo@src/x.py:10>")
    graph.add_edge("<ambiguous:foo@src/x.py:10>", "downstream_of_placeholder")

    dist_w_map = {"<ambiguous:foo@src/x.py:10>": 1.0}  # downstream_of_placeholder NOT in dist_w_map at all
    feature_masks = {"seed": 0, "<ambiguous:foo@src/x.py:10>": 0, "downstream_of_placeholder": 0}
    costs = {"seed": 0, "<ambiguous:foo@src/x.py:10>": 0, "downstream_of_placeholder": 5}

    result = select_submodular_context(
        graph, "seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs
    )

    assert result == ["seed"]
