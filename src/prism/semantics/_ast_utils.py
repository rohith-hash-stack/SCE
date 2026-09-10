"""Shared AST-walking helpers for the four-axis extractors
(`prism.semantics.substance`/`form`/`output`/`role`) - kept in one place
so "what counts as a statement" and "how do I get this function's own
body block" stay consistent across axes rather than drifting per-module.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.parser.lang_config import (
    CATCH_NODE_TYPES,
    CLASS_NODE_TYPES,
    CONDITIONAL_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    FUNCTION_NODE_TYPES,
    LOOP_NODE_TYPES,
    MAX_SCOPED_NODE_DEPTH,
    RETURN_STATEMENT_NODE_TYPE,
    TRY_NODE_TYPES,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import ParsedFile, node_text

#: Every node-type suffix/shape this module treats as "one statement" for
#: density-normalization purposes - deliberately a permissive, structural
#: definition ("anything ending in _statement, plus a bare expression as
#: a statement") rather than a pedantically-exact per-grammar statement
#: list: the four-axis model only ever uses this as a *denominator* for a
#: ratio, so an approximately-right count that's consistent across
#: languages matters far more than an exactly-right one for any single
#: grammar.
def _is_statement_node(node: Node) -> bool:
    return node.type.endswith("_statement") or node.type == "expression_statement"


def count_statements(def_node: Node, lang: str) -> int:
    """Total statement-shaped descendants inside `def_node`'s own body,
    not crossing into a nested function/class definition (same scoping
    rule `iter_scoped_nodes` already applies everywhere else in this
    codebase) - the shared denominator every Form-axis density ratio
    divides by.
    """
    body = get_body_block(def_node)
    root = body if body is not None else def_node
    count = 0
    # A direct, depth-tracked walk (not `iter_scoped_nodes`, which only
    # collects a specific target-type set) - mirrors `iter_scoped_nodes`'s
    # own nested-scope skip and depth guard for consistency, but counts
    # every statement-shaped node in one pass instead of one query per
    # target type.
    skip_types = CLASS_NODE_TYPES.get(lang, set()) | FUNCTION_NODE_TYPES.get(lang, set()) | DECORATED_WRAPPER_TYPES.get(lang, set())
    depths = {id(root): 0}
    stack = [root]
    while stack:
        current = stack.pop()
        depth = depths.get(id(current), 0)
        if depth >= MAX_SCOPED_NODE_DEPTH:
            continue
        if current is not root and _is_statement_node(current):
            count += 1
        for child in current.children:
            if current is not root and child.type in skip_types:
                continue
            depths[id(child)] = depth + 1
            stack.append(child)
    return max(count, 1)


def get_body_block(def_node: Node) -> Node | None:
    """The `block`/`statement_block`/`block` body node of a function
    definition - `child_by_field_name("body")` covers every supported
    grammar here (Python `function_definition`, JS/TS `function_
    declaration`/`method_definition`, Go `function_declaration`/`method_
    declaration` all expose a `body` field for exactly this node).
    """
    return def_node.child_by_field_name("body")


def top_level_statements(def_node: Node, lang: str) -> list[Node]:
    """The *direct* (non-nested) statement children of `def_node`'s own
    body block - used for "is the first statement a guard" /
    "1-2-statement wrapper" shape checks that care about position, not
    just density.
    """
    body = get_body_block(def_node)
    if body is None:
        return []
    return [c for c in body.named_children if _is_statement_node(c) or c.type not in ("{", "}")]


def returns_in_scope(def_node: Node, lang: str) -> list[Node]:
    return iter_scoped_nodes(def_node, {RETURN_STATEMENT_NODE_TYPE}, lang)


def loop_nodes(def_node: Node, lang: str) -> list[Node]:
    return iter_scoped_nodes(def_node, LOOP_NODE_TYPES.get(lang, set()), lang)


def conditional_nodes(def_node: Node, lang: str) -> list[Node]:
    return iter_scoped_nodes(def_node, CONDITIONAL_NODE_TYPES.get(lang, set()), lang)


def try_nodes(def_node: Node, lang: str) -> list[Node]:
    return iter_scoped_nodes(def_node, TRY_NODE_TYPES.get(lang, set()), lang)


def catch_nodes(def_node: Node, lang: str) -> list[Node]:
    return iter_scoped_nodes(def_node, CATCH_NODE_TYPES.get(lang, set()), lang)


def node_source(node: Node, parsed: ParsedFile) -> str:
    return node_text(node, parsed.source)
