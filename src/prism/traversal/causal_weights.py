"""v1.1 Part 2: Pairwise Causal Edge Weights `W(u, v)`.

    W(u, v) = w_base(relation) * (1 + lambda_1 * I_dataflow(u, v) + lambda_2 * I_guard(u, v))

`w_base` is the same kind of structural-relation weight
`prism.slicer.distance.RELATION_STRUCTURAL_WEIGHT` already uses for
`D_hybrid` (this module's own literal constants, not imported from
there, since the spec pins its own exact values); `I_dataflow`/`I_guard`
are the confidence-valued indicators `prism.traversal.data_flow_py`/
`data_flow_go`/`data_flow_ts` and this module's own guard-indicator
extraction produce, each in `[0.0, 1.0]`.

    lambda_1 = 0.25   (data-flow multiplier)
    lambda_2 = 0.15   (guard multiplier)
    max W(u, v) = 1.0 * (1.0 + 0.25 + 0.15) = 1.40
    c(e) = 1 / W(u, v)  in  [0.714, 1.00]   (a real call edge, w_base=1.0)

A weaker structural relation (EXTENDS, IMPLEMENTS, ...) still gets the
same multiplicative causal boost applied on top of its own lower
`w_base` - composing the same way `prism.slicer.distance`'s own
`RELATION_TENTATIVE_CALL_WEIGHT`/`GAMMA_UNOBSERVED` multipliers already
compose with `RELATION_STRUCTURAL_WEIGHT` there, rather than a special
case.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import CALL_NODE_TYPE, iter_scoped_nodes
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text
from prism.semantics._ast_utils import conditional_nodes, top_level_statements, try_nodes
from prism.traversal._cache_keys import graph_cache_key
from prism.traversal._data_flow_common import _node_key, _resolve_call_sites
from prism.traversal.data_flow_go import compute_go_data_flow_edges
from prism.traversal.data_flow_py import compute_python_data_flow_edges
from prism.traversal.data_flow_ts import compute_ts_data_flow_edges

#: Blocker 1 performance work, Step 4: `compute_guard_indicator_edges`/
#: `compute_all_data_flow_edges`/`compute_causal_edges` are each
#: seed/budget-independent (pure functions of `builder` alone, same as
#: `continuous_dijkstra.build_causal_graph`) and, per the cache-key
#: matrix, share that function's exact key shape ("Same as
#: build_causal_graph"). `prism.traversal._cache_keys.graph_cache_key`
#: is imported directly (not `continuous_dijkstra`'s own re-export) -
#: `continuous_dijkstra.py` imports *this* module (`compute_causal_
#: edges`, `edge_cost`), so importing back from it here would be
#: circular.
_DATA_FLOW_EDGES_CACHE: dict[str, dict[tuple[str, str], float]] = {}
_GUARD_INDICATOR_EDGES_CACHE: dict[str, dict[tuple[str, str], float]] = {}
_CAUSAL_EDGES_CACHE: dict[str, tuple[dict[tuple[str, str], float], set[tuple[str, str]]]] = {}

#: The spec's own literal base weights - CALLS/INSTANTIATES treated
#: identically (a construction is causally no weaker a link than an
#: ordinary call), EMBEDS priced the same as EXTENDS (Go's own real
#: composition mechanism - see `prism.slicer.distance`'s own EMBEDS
#: entry for the identical reasoning).
BASE_RELATION_WEIGHT: dict[str, float] = {
    "CALLS": 1.00,
    "INSTANTIATES": 1.00,
    "OVERRIDES": 0.90,
    "EXTENDS": 0.85,
    "IMPLEMENTS": 0.80,
    "EMBEDS": 0.85,
}

LAMBDA_DATA_FLOW = 0.25
LAMBDA_GUARD = 0.15

#: `1.0 * (1.0 + LAMBDA_DATA_FLOW + LAMBDA_GUARD)` - the maximum any
#: `W(u, v)` can ever reach, at the strongest base relation and both
#: indicators saturated at 1.0. Derived, not hand-copied, so a future
#: change to either lambda keeps this bound honest automatically.
MAX_CAUSAL_WEIGHT = max(BASE_RELATION_WEIGHT.values()) * (1.0 + LAMBDA_DATA_FLOW + LAMBDA_GUARD)

#: Guard-indicator confidence tiers, in the audit's own literal notation.
PREDICATE_GATE_CONFIDENCE = 1.0
EXCEPTION_GATE_CONFIDENCE = 0.9
EARLY_RETURN_GATE_CONFIDENCE = 0.9


def causal_edge_weight(relation: str, data_flow_indicator: float, guard_indicator: float) -> float:
    """`W(u, v)` for one edge, given its structural `relation` and the two
    (already-computed) indicator confidences in `[0.0, 1.0]`."""
    base = BASE_RELATION_WEIGHT.get(relation, 1.0)
    return base * (1.0 + LAMBDA_DATA_FLOW * data_flow_indicator + LAMBDA_GUARD * guard_indicator)


def edge_cost(weight: float) -> float:
    """`c(e) = 1 / W(u, v)` - the Dijkstra hop cost `prism.traversal.
    continuous_dijkstra` actually traverses on. Guards against a
    degenerate/corrupted zero or negative weight (never produced by
    `causal_edge_weight` itself, whose `base` is always > 0) by treating
    it as the worst-case (maximum) cost rather than dividing by zero.
    """
    if weight <= 0:
        return 1.0 / min(BASE_RELATION_WEIGHT.values())
    return 1.0 / weight


# --------------------------------------------------------------------- #
# Guard indicator extraction (Section 2.3)
# --------------------------------------------------------------------- #
def _negates_a_call(condition: Node, lang: str) -> Node | None:
    """`if not u(): ...` / `if !u() { ... }` - returns the negated call
    node itself, or `None` if `condition` isn't a bare negated call."""
    if condition.type in ("not_operator",):
        inner = condition.named_children[0] if condition.named_children else None
        return inner
    if condition.type == "unary_expression":
        # Best-effort: a `!`-prefixed unary expression (JS/TS/Go) wrapping
        # a bare call.
        operand = condition.named_children[-1] if condition.named_children else None
        first_child = condition.children[0] if condition.children else None
        if first_child is not None and first_child.type == "!":
            return operand
    return None


def extract_guard_indicators(
    def_node: Node, parsed: ParsedFile, qualified_name: str, builder: ConcreteGraphBuilder
) -> list[tuple[str, str, float]]:
    """`[(u_symbol_id, v_symbol_id, confidence)]` for the three guard
    shapes the spec names: Predicate Gate (1.0), Exception Gate (0.9),
    Early-Return Gate (0.9) - see this module's own docstring section.
    """
    lang = parsed.language_id
    resolved = _resolve_call_sites(def_node, parsed, qualified_name, builder)
    if not resolved:
        return []

    call_type = CALL_NODE_TYPE.get(lang)

    def _resolved_calls_in(node: Node) -> list[str]:
        if not call_type:
            return []
        out = []
        for call in iter_scoped_nodes(node, {call_type}, lang, is_root=True):
            target = resolved.get(_node_key(call))
            if target:
                out.append(target)
        return out

    edges: list[tuple[str, str, float]] = []

    # -- Predicate Gate: if <cond calls u>: <body calls v> --------------- #
    for cond_node in conditional_nodes(def_node, lang):
        condition = cond_node.child_by_field_name("condition")
        consequence = cond_node.child_by_field_name("consequence")
        if condition is None or consequence is None:
            continue
        us = _resolved_calls_in(condition)
        vs = _resolved_calls_in(consequence)
        for u in us:
            for v in vs:
                if u != v:
                    edges.append((u, v, PREDICATE_GATE_CONFIDENCE))

    # -- Exception Gate: try { ...u()... ...v()... } ---------------------- #
    for try_node in try_nodes(def_node, lang):
        body = try_node.child_by_field_name("body") or try_node
        calls_in_order = _resolved_calls_in(body)
        for i in range(len(calls_in_order) - 1):
            u, v = calls_in_order[i], calls_in_order[i + 1]
            if u != v:
                edges.append((u, v, EXCEPTION_GATE_CONFIDENCE))

    # -- Early-Return Gate: if not u(): return \n v() --------------------- #
    statements = top_level_statements(def_node, lang)
    for i, stmt in enumerate(statements):
        if stmt.type != "if_statement":
            continue
        condition = stmt.child_by_field_name("condition")
        consequence = stmt.child_by_field_name("consequence")
        if condition is None or consequence is None:
            continue
        negated_call = _negates_a_call(condition, lang)
        if negated_call is None:
            continue
        if not any(c.type == "return_statement" for c in iter_scoped_nodes(consequence, {"return_statement"}, lang, is_root=True)):
            continue
        u_target = resolved.get(_node_key(negated_call))
        if not u_target:
            continue
        for later in statements[i + 1:]:
            vs = _resolved_calls_in(later)
            if vs:
                for v in vs:
                    if v != u_target:
                        edges.append((u_target, v, EARLY_RETURN_GATE_CONFIDENCE))
                break

    return edges


def compute_guard_indicator_edges(builder: ConcreteGraphBuilder) -> dict[tuple[str, str], float]:
    """`{(u, v): confidence}` across every function/method `builder`
    indexed, max-confidence-wins per pair - same aggregation shape
    `prism.traversal._data_flow_common.compute_data_flow_edges` uses, for
    the same reason (both indicators ultimately price one `G_C` graph
    edge, not a per-caller-context fact).

    Cached, repo-content-addressed (`_GraphCacheKey`'s own key shape,
    "Same as build_causal_graph" per the cache-key matrix) - seed/
    budget-independent, safe to share across every call site (this
    function's own direct callers, and `compute_causal_edges`'s
    internal call) and across retrieves on the same or a different
    engine instance.
    """
    key = graph_cache_key(builder.repo_root).digest()
    cached = _GUARD_INDICATOR_EDGES_CACHE.get(key)
    if cached is not None:
        return cached
    result: dict[tuple[str, str], float] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        for u, v, confidence in extract_guard_indicators(def_node, parsed, symbol.qualified_name, builder):
            pair = (u, v)
            if confidence > result.get(pair, 0.0):
                result[pair] = confidence
    _GUARD_INDICATOR_EDGES_CACHE[key] = result
    return result


def compute_all_data_flow_edges(builder: ConcreteGraphBuilder) -> dict[tuple[str, str], float]:
    """`{(u, v): confidence}` merging Python/Go/TS data-flow extraction
    across the whole repository, max-confidence-wins per pair.

    Cached, repo-content-addressed - see `compute_guard_indicator_
    edges`'s own docstring for the exact same rationale (this function
    is the other half of "Same as build_causal_graph" in the cache-key
    matrix).
    """
    key = graph_cache_key(builder.repo_root).digest()
    cached = _DATA_FLOW_EDGES_CACHE.get(key)
    if cached is not None:
        return cached
    merged: dict[tuple[str, str], float] = {}
    for edges in (
        compute_python_data_flow_edges(builder),
        compute_go_data_flow_edges(builder),
        compute_ts_data_flow_edges(builder),
    ):
        for pair, confidence in edges.items():
            if confidence > merged.get(pair, 0.0):
                merged[pair] = confidence
    _DATA_FLOW_EDGES_CACHE[key] = merged
    return merged


#: Base weight a *synthetic* causal edge is priced at - a data-flow/guard
#: pair whose producer and consumer are both real symbols but aren't
#: directly connected by any structural `G_C` edge at all (the far more
#: common case in practice: `parse_order`/`store_order` are usually
#: *siblings* called by a shared orchestrator, not caller and callee of
#: each other). Priced as an ordinary `CALLS`-strength link (1.0) -
#: there's no other structural relation to draw a base weight from, and a
#: real data-flow/guard coupling is exactly as causally significant as an
#: explicit call would have been.
SYNTHETIC_EDGE_BASE_RELATION = "CALLS"


def compute_causal_weights(builder: ConcreteGraphBuilder) -> dict[tuple[str, str], float]:
    """`{(u, v): W(u, v)}` for the *union* of two edge sets - the one
    entry point `prism.traversal.continuous_dijkstra` calls:

      1. Every real `G_C` edge `builder.graph` already carries, priced
         from its own structural `relation` plus whatever data-flow/guard
         evidence happens to also exist between that exact `(u, v)` pair
         (rare in practice - a caller directly invoking its own callee is
         a different relationship from "this call's result feeds that
         one", so this mostly reduces to the plain `w_base` case, `I_dataflow
         = I_guard = 0`, which is a pure extension: a graph with no
         indicator evidence anywhere behaves identically to the base
         relation-weight table alone).
      2. **Synthetic causal edges**: every `(producer, consumer)` pair
         `prism.traversal.data_flow_py`/`data_flow_go`/`data_flow_ts`/
         this module's own guard-indicator extraction found that *isn't*
         already a real structural edge - almost always two sibling calls
         under a shared caller/orchestrator (`process(): data =
         parse_order(x); store_order(data)` - `parse_order` never calls
         `store_order` directly, so there is no edge to boost without
         this synthetic-edge mechanism, and the whole point of "causally
         coupled edges shorten graph distance" (Part 3's own Pipeline
         Preservation test) requires `parse_order` and `store_order` to
         become directly reachable from each other, not merely each
         reachable from their shared caller two hops apart).

    A synthetic edge is marked `synthetic=True` in the returned weight
    dict's companion `synthetic_edges` set (see `compute_causal_edges`,
    which returns both) so a consumer building an actual traversal graph
    knows which pairs need a *new* edge added versus which already exist
    on `builder.graph`.
    """
    return compute_causal_edges(builder)[0]


def compute_causal_edges(
    builder: ConcreteGraphBuilder,
) -> tuple[dict[tuple[str, str], float], set[tuple[str, str]]]:
    """Returns `(weights, synthetic_edges)` - see `compute_causal_weights`'s
    own docstring for the full rationale. `synthetic_edges` is the subset
    of `weights`' keys that are *not* already real edges on `builder.graph`.

    Cached, repo-content-addressed - see `compute_guard_indicator_
    edges`'s own docstring for the rationale (this is the third of the
    three functions the cache-key matrix marks "Same as build_causal_
    graph"). Its own two internal calls (`compute_all_data_flow_edges`/
    `compute_guard_indicator_edges`) benefit from their own caches too,
    so a cold call here still only pays the real cost once even though
    it fans out into two more cached functions.
    """
    key = graph_cache_key(builder.repo_root).digest()
    cached = _CAUSAL_EDGES_CACHE.get(key)
    if cached is not None:
        return cached

    data_flow = compute_all_data_flow_edges(builder)
    guards = compute_guard_indicator_edges(builder)

    weights: dict[tuple[str, str], float] = {}
    for u, v, data in builder.graph.edges(data=True):
        relation = data.get("relation", "CALLS")
        i_dataflow = data_flow.get((u, v), 0.0)
        i_guard = guards.get((u, v), 0.0)
        weights[(u, v)] = causal_edge_weight(relation, i_dataflow, i_guard)

    synthetic_edges: set[tuple[str, str]] = set()
    all_indicator_pairs = set(data_flow) | set(guards)
    for u, v in all_indicator_pairs:
        if builder.graph.has_edge(u, v):
            continue
        if u not in builder.symbol_table or v not in builder.symbol_table:
            continue
        i_dataflow = data_flow.get((u, v), 0.0)
        i_guard = guards.get((u, v), 0.0)
        weights[(u, v)] = causal_edge_weight(SYNTHETIC_EDGE_BASE_RELATION, i_dataflow, i_guard)
        synthetic_edges.add((u, v))

    result = (weights, synthetic_edges)
    _CAUSAL_EDGES_CACHE[key] = result
    return result
