"""Shared core for `prism.traversal.data_flow_py`/`data_flow_go`/
`data_flow_ts` - one real implementation, parametrized by the same
per-language `lang_config` tables every other cross-language walker in
this codebase already shares, rather than three independently drifting
copies of the same algorithm. Each of the three thin per-language modules
exists as its own file (the deliverable list names them separately) and
simply calls `extract_data_flow` with its own `LanguageID`.

**Critical requirement this module honors** (from the v1.1 spec): data
flow must not match raw variable strings against bare function names - it
runs *after* symbol resolution, so provenance stores concrete resolved
symbol IDs, not lexical guesses. `_resolve_call_sites` builds that
`{node_position: resolved_symbol_id}` map itself (see `_node_key` for why
a byte-position tuple, not Python's own `id(node)`, is the right key for
a tree-sitter node), from the *already-linked* `G_C` graph Pass 2
produced (`builder.graph.out_edges(qualified_name)`) -
matching each call site's own callee simple name against the (small, per-
function) set of real resolved out-edges rather than re-deriving
resolution from scratch a second, potentially-inconsistent way. A call
site whose simple name matches more than one distinct resolved out-edge
target is left unresolved rather than guessed - the same "don't guess"
principle Go Stage 2 receiver resolution and TS heritage-target
resolution already apply elsewhere in this codebase.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    ATTR_OBJECT_FIELD,
    ATTRIBUTE_NODE_TYPE,
    AUGMENTED_ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    IDENTIFIER_NODE_TYPES,
    SHORT_VAR_DECL_NODE_TYPE,
    VARIABLE_DECLARATOR_NODE_TYPE,
    call_callee_segments,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import ParsedFile, node_text

#: Never bound into data-flow provenance, in any language - an error/
#: context placeholder, not a real payload value (Go's own
#: `val, err := u()` idiom is the primary motivation, but the same names
#: are just as meaningless as "the thing a downstream call cares about"
#: in Python/TS).
_NEVER_PROVENANCE_NAMES = frozenset({"err", "_", "ctx"})


def _node_key(node: Node) -> tuple[int, int]:
    """A stable identity key for a tree-sitter node - `(start_byte,
    end_byte)`, not Python's own `id(node)`. Confirmed directly: this
    codebase's tree-sitter bindings construct a *new* Python wrapper
    object (a different `id()`) each time the "same" underlying node is
    reached via a different access path (`.children` traversal vs
    `.child_by_field_name(...).named_children`, e.g.) - `id()` equality
    only ever held reliably for CPython's own native `ast` module (stable
    per-node Python objects), which the spec's own reference
    implementation was written against; tree-sitter needs a position-
    based key instead. `(start_byte, end_byte)` is unique within one
    parsed file (two distinct nodes can't share the exact same byte
    span), so it composes safely with the per-function dicts this module
    builds (`resolved_call_sites` is always scoped to one function body
    at a time, never merged across files).
    """
    return (node.start_byte, node.end_byte)


def _decl_node_types(lang: str) -> set[str]:
    types: set[str] = set()
    for table in (ASSIGNMENT_NODE_TYPE, AUGMENTED_ASSIGNMENT_NODE_TYPE, VARIABLE_DECLARATOR_NODE_TYPE, SHORT_VAR_DECL_NODE_TYPE):
        value = table.get(lang)
        if value:
            types.add(value)
    return types


def _resolve_call_sites(def_node: Node, parsed: ParsedFile, qualified_name: str, builder: ConcreteGraphBuilder) -> dict[tuple[int, int], str]:
    lang = parsed.language_id
    call_type = CALL_NODE_TYPE.get(lang)
    if not call_type or qualified_name not in builder.graph:
        return {}

    # Real out-edges Pass 2 already resolved for this exact caller,
    # indexed by the callee's own trailing simple name - the join key a
    # call site's own (unresolved-at-the-AST-level) callee segments can
    # match against.
    by_simple_name: dict[str, list[str]] = {}
    for _u, callee, data in builder.graph.out_edges(qualified_name, data=True):
        if data.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
            continue
        simple = callee.rsplit(".", 1)[-1]
        by_simple_name.setdefault(simple, []).append(callee)

    resolved: dict[tuple[int, int], str] = {}
    for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
        segments = call_callee_segments(call_node, parsed.source, lang)
        if not segments:
            continue
        candidates = by_simple_name.get(segments[-1])
        if candidates and len(set(candidates)) == 1:
            resolved[_node_key(call_node)] = candidates[0]
    return resolved


def _bindings(node: Node, lang: str, source: bytes) -> list[tuple[str, Node | None]]:
    """`(bound_name, value_expr)` pairs for one declaration/assignment
    node - a single pair for `y = u(x)`-shaped assignment; potentially
    several for a Go multi-value `val, err := g()` (each left identifier
    paired with the *same* right-hand value expression when the right
    side is a single call, since that's the only shape Go's grammar
    allows there - a multi-call right side, `a, b := f(), g()`, is rare
    enough and unambiguous enough to fall out naturally the same way:
    positionally zipped).
    """
    if node.type in ASSIGNMENT_NODE_TYPE.values() or node.type in AUGMENTED_ASSIGNMENT_NODE_TYPE.values():
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return []
        if left.type in IDENTIFIER_NODE_TYPES.get(lang, set()):
            return [(node_text(left, source), right)]
        return []
    if node.type in VARIABLE_DECLARATOR_NODE_TYPE.values():
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")
        if name_node is None or name_node.type not in IDENTIFIER_NODE_TYPES.get(lang, set()):
            return []
        return [(node_text(name_node, source), value_node)]
    if node.type in SHORT_VAR_DECL_NODE_TYPE.values():
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return []
        names = [c for c in left.named_children if c.type in IDENTIFIER_NODE_TYPES.get(lang, set())]
        values = [c for c in right.named_children]
        if not names or not values:
            return []
        if len(values) == 1:
            return [(node_text(n, source), values[0]) for n in names]
        return list(zip((node_text(n, source) for n in names), values))
    return []


def extract_data_flow(
    def_node: Node, parsed: ParsedFile, qualified_name: str, builder: ConcreteGraphBuilder
) -> list[tuple[str, str, float]]:
    """Returns `[(producer_symbol_id, consumer_symbol_id, confidence)]` -
    every local data-flow edge this function's own body establishes
    between two of its *already-resolved* call sites, in the exact
    confidence tiers the spec's own Python reference implementation
    defines: 1.0 for a directly-nested call argument (`v(u(x))`), 0.9 for
    a variable pass with one level of provenance chaining
    (`y = u(x); v(y)`, and `clean = raw.strip()` re-binding an existing
    provenance chain), 0.7 for an attribute-access pass (`v(y.data)`).
    """
    lang = parsed.language_id
    src = parsed.source
    resolved_call_sites = _resolve_call_sites(def_node, parsed, qualified_name, builder)
    if not resolved_call_sites:
        return []

    call_type = CALL_NODE_TYPE.get(lang)
    attr_type = ATTRIBUTE_NODE_TYPE.get(lang)
    obj_field = ATTR_OBJECT_FIELD.get(lang)
    ident_types = IDENTIFIER_NODE_TYPES.get(lang, set())
    decl_types = _decl_node_types(lang)
    if not call_type:
        return []

    provenance: dict[str, str] = {}
    edges: list[tuple[str, str, float]] = []

    relevant_types = decl_types | {call_type}
    for node in iter_scoped_nodes(def_node, relevant_types, lang):
        if node.type in decl_types:
            for name, value in _bindings(node, lang, src):
                if name in _NEVER_PROVENANCE_NAMES:
                    continue
                if value is None:
                    continue
                if value.type == call_type:
                    producer_id = resolved_call_sites.get(_node_key(value))
                    if producer_id:
                        provenance[name] = producer_id
                        continue
                    # An unresolved call (a stdlib/builtin method, not a
                    # symbol Pass 2 linked) falls through to the
                    # provenance-chaining check below rather than
                    # stopping here - `clean = raw.strip().lower()`'s own
                    # value *is* a call node (the outer `.lower()`), but
                    # it's a method chain off `raw`, not a resolved call
                    # site in its own right.
                # Provenance chaining: `clean = raw.strip().lower()` -
                # `raw` (or whatever the base of this method-chain
                # expression is) already has a known producer, so `clean`
                # inherits it too.
                if attr_type and obj_field:
                    base = value
                    # Walk down through nested attribute/call wrapping to
                    # find the root identifier this expression chains off.
                    seen_call = False
                    while base is not None and base.type not in ident_types:
                        if base.type == call_type:
                            seen_call = True
                            func = base.child_by_field_name("function")
                            base = func
                        elif base.type == attr_type:
                            base = base.child_by_field_name(obj_field)
                        else:
                            base = None
                    if seen_call and base is not None:
                        base_name = node_text(base, src)
                        if base_name in provenance:
                            provenance[name] = provenance[base_name]
            continue

        # node.type == call_type: this call site is a *consumer* of
        # whatever its own arguments provide.
        consumer_id = resolved_call_sites.get(_node_key(node))
        if not consumer_id:
            continue
        for arg in _call_arguments(node, lang):
            if arg.type == call_type:
                producer_id = resolved_call_sites.get(_node_key(arg))
                if producer_id:
                    edges.append((producer_id, consumer_id, 1.0))
            elif arg.type in ident_types:
                name = node_text(arg, src)
                if name in provenance:
                    edges.append((provenance[name], consumer_id, 0.9))
            elif attr_type and arg.type == attr_type and obj_field:
                base = arg.child_by_field_name(obj_field)
                if base is not None and base.type in ident_types:
                    name = node_text(base, src)
                    if name in provenance:
                        edges.append((provenance[name], consumer_id, 0.7))

    return edges


def _call_arguments(call_node: Node, lang: str) -> list[Node]:
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return []
    return [c for c in args_node.named_children]


def compute_data_flow_edges(builder: ConcreteGraphBuilder, lang_filter: str | None = None) -> dict[tuple[str, str], float]:
    """`{(producer, consumer): confidence}` across every function/method
    `builder` indexed (optionally restricted to one `LanguageID`, which
    is how `data_flow_py.py`/`data_flow_go.py`/`data_flow_ts.py` each
    expose their own single-language entry point) - the highest
    confidence observed wins when more than one call site inside the same
    function produces the same `(producer, consumer)` pair.
    """
    result: dict[tuple[str, str], float] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        if lang_filter is not None and parsed.language_id != lang_filter:
            continue
        for producer, consumer, confidence in extract_data_flow(def_node, parsed, symbol.qualified_name, builder):
            key = (producer, consumer)
            if confidence > result.get(key, 0.0):
                result[key] = confidence
    return result
