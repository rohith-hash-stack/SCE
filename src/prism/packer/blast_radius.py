"""v1.1+ Part 3.2: Bidirectional Blast-Radius Slicing - upstream caller
weighting.

**The problem this solves**: `prism.packer.submodular_knapsack` (Part 3)
only ever expands *forward* from a seed - callees, data-flow targets,
guard-coupled siblings. An agent editing `calculate_tax()` never sees
`invoice_generator()` in another file that does `total =
calculate_tax(...)` and destructures the result - so a return-shape
change silently breaks a consumer the agent never had in context at all.
This module computes a real, weighted set of *direct* (1-hop) callers of
a seed, so the packer can pull the most causally-coupled one in
alongside the downstream slice.

    W_upstream(u, s) = w_base(relation) * (1 + mu_1 * I_return_unpack(u, s) + mu_2 * I_nontrivial_args(u, s))
    dist_w_upstream(s, u) = 1 / W_upstream(u, s)
    mu_1 = 0.30   (caller actively unpacks/binds s's return value)
    mu_2 = 0.20   (caller supplies at least one non-trivial - i.e. not a
                   bare literal - argument to s)

Same multiplicative-boost shape `prism.traversal.causal_weights.
causal_edge_weight` already uses for `W(u, v)` (`w_base` reused directly
from `BASE_RELATION_WEIGHT` there, rather than a second, possibly-
drifting copy), just with the two upstream-specific contract-protection
indicators in place of data-flow/guard evidence - a caller who actually
*consumes* `s`'s return value, or hands it real (non-constant) arguments,
has a stronger behavioral dependency on `s`'s own signature than one
that merely happens to call it and discard the result.

Deliberately restricted to **direct callers only** (`builder.graph.
in_edges(seed_id)`, never a caller-of-a-caller) - the spec's own "strictly
1 hop" framing: a transitive blast radius belongs to a different, much
noisier question ("what could this change affect anywhere") than the one
this module answers ("who is relying on this exact signature right now").
"""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import CALL_NODE_TYPE, iter_scoped_nodes
from prism.traversal._data_flow_common import _bindings, _decl_node_types, _node_key, _resolve_call_sites
from prism.traversal.causal_weights import BASE_RELATION_WEIGHT, edge_cost

#: Caller actively unpacks/binds the return value of `s`
#: (`total = calculate_tax(...)`, `val, err := f()`).
MU_RETURN_UNPACK = 0.30
#: Caller supplies at least one non-trivial (not a bare literal) argument
#: to `s`.
MU_NONTRIVIAL_ARGS = 0.20

#: Applied multiplicatively to an upstream candidate's own submodular
#: value (`prism.packer.submodular_knapsack.compute_candidate_value`)
#: when it unpacks `s`'s return value - the "Consumer Contract Scoring
#: Boost" the spec asks for. Reuses `1 + MU_RETURN_UNPACK` rather than an
#: independent constant, so the same "how much does a real return-value
#: consumer matter" judgment isn't expressed as two different numbers in
#: two different places.
CONTRACT_PRESERVATION_MULTIPLIER = 1.0 + MU_RETURN_UNPACK

#: Node types this module treats as "a bare literal, not a real
#: expression" for `_call_supplies_nontrivial_args` - a permissive,
#: structural definition (same spirit as `_ast_utils._is_statement_node`)
#: covering every supported grammar's own literal-token shapes, not an
#: exhaustive one: an argument this table doesn't recognize is treated as
#: non-trivial (the safer default - a caller doing something unusual with
#: its arguments is a real dependency, not a false positive worth hiding).
_TRIVIAL_ARG_NODE_TYPES = frozenset(
    {
        "integer",
        "float",
        "string",
        "concatenated_string",
        "true",
        "false",
        "none",
        "null",
        "number",
        "template_string",
        "interpreted_string_literal",
        "raw_string_literal",
        "nil",
        "undefined",
    }
)


def _is_trivial_arg(node: Node) -> bool:
    return node.type in _TRIVIAL_ARG_NODE_TYPES


def _call_supplies_nontrivial_args(call_node: Node) -> bool:
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return False
    return any(not _is_trivial_arg(child) for child in args_node.named_children)


@dataclass
class UpstreamCaller:
    """One direct caller `u` of a seed `s`, with its own upstream weight
    and the two contract-protection indicators that produced it."""

    symbol: str
    weight: float  # W_upstream(u, s)
    dist_w_upstream: float  # 1 / weight - the Dijkstra-style hop cost this caller is admitted at
    unpacks_return: bool
    supplies_nontrivial_args: bool


def compute_upstream_callers(builder: ConcreteGraphBuilder, seed_id: str) -> dict[str, UpstreamCaller]:
    """`{caller_qualified_name: UpstreamCaller}` for every direct
    (`CALLS`/`INSTANTIATES`) predecessor of `seed_id` in `builder.graph` -
    the candidate set `prism.packer.submodular_knapsack.
    select_submodular_context`'s upstream frontier is seeded from.

    For each caller `u`, finds the actual call site(s) in `u`'s own body
    that Pass 2 already resolved to `seed_id` (`_resolve_call_sites`,
    reused directly rather than re-deriving resolution a second way),
    then checks whether any such call site's return value is bound to a
    local name (Phase 2a-style provenance, same `_bindings` helper
    `prism.semantics.substance` uses) and whether any supplies a
    non-trivial argument.
    """
    result: dict[str, UpstreamCaller] = {}
    if seed_id not in builder.graph:
        return result

    for caller, _seed, data in builder.graph.in_edges(seed_id, data=True):
        relation = data.get("relation", "CALLS")
        if relation not in ("CALLS", "INSTANTIATES"):
            continue

        def_node = builder.def_node(caller)
        info = builder.symbol_table.get(caller)
        if def_node is None or info is None:
            continue
        parsed = builder.parsed_file(info.file)
        if parsed is None:
            continue

        lang = parsed.language_id
        call_type = CALL_NODE_TYPE.get(lang)
        if not call_type:
            continue

        resolved = _resolve_call_sites(def_node, parsed, caller, builder)
        matching_keys = {key for key, target in resolved.items() if target == seed_id}
        if not matching_keys:
            continue

        bound_value_keys: set[tuple[int, int]] = set()
        decl_types = _decl_node_types(lang)
        if decl_types:
            for decl_node in iter_scoped_nodes(def_node, decl_types, lang):
                for _name, value in _bindings(decl_node, lang, parsed.source):
                    if value is not None and value.type == call_type:
                        bound_value_keys.add(_node_key(value))
        unpacks_return = bool(matching_keys & bound_value_keys)

        nontrivial_args = False
        for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
            if _node_key(call_node) in matching_keys and _call_supplies_nontrivial_args(call_node):
                nontrivial_args = True
                break

        base = BASE_RELATION_WEIGHT.get(relation, 1.0)
        weight = base * (
            1.0
            + MU_RETURN_UNPACK * (1.0 if unpacks_return else 0.0)
            + MU_NONTRIVIAL_ARGS * (1.0 if nontrivial_args else 0.0)
        )
        result[caller] = UpstreamCaller(
            symbol=caller,
            weight=weight,
            dist_w_upstream=edge_cost(weight),
            unpacks_return=unpacks_return,
            supplies_nontrivial_args=nontrivial_args,
        )

    return result
