"""Per-language structural node-type tables and generic tree-walking helpers.

Shared by `sce.graph.concrete_builder` (Pass 2 call linking) and
`sce.tagger.engine` (Stage 3 deterministic tagging) so both operate on the
same notion of "call expression", "attribute chain", "self token", and
"scoped subtree" without duplicating tree-sitter plumbing.
"""
from __future__ import annotations

from tree_sitter import Node

from sce.parser.tree_sitter_loader import LanguageID, node_text

CALL_NODE_TYPE = {
    LanguageID.PYTHON: "call",
    LanguageID.JAVASCRIPT: "call_expression",
    LanguageID.TYPESCRIPT: "call_expression",
    LanguageID.TSX: "call_expression",
    LanguageID.GO: "call_expression",
}
ATTRIBUTE_NODE_TYPE = {
    LanguageID.PYTHON: "attribute",
    LanguageID.JAVASCRIPT: "member_expression",
    LanguageID.TYPESCRIPT: "member_expression",
    LanguageID.TSX: "member_expression",
    LanguageID.GO: "selector_expression",
}
ATTR_OBJECT_FIELD = {
    LanguageID.PYTHON: "object",
    LanguageID.JAVASCRIPT: "object",
    LanguageID.TYPESCRIPT: "object",
    LanguageID.TSX: "object",
    LanguageID.GO: "operand",
}
ATTR_PROPERTY_FIELD = {
    LanguageID.PYTHON: "attribute",
    LanguageID.JAVASCRIPT: "property",
    LanguageID.TYPESCRIPT: "property",
    LanguageID.TSX: "property",
    LanguageID.GO: "field",
}
IDENTIFIER_NODE_TYPES = {
    LanguageID.PYTHON: {"identifier"},
    LanguageID.JAVASCRIPT: {"identifier"},
    LanguageID.TYPESCRIPT: {"identifier"},
    LanguageID.TSX: {"identifier"},
    LanguageID.GO: {"identifier"},
}
SELF_NODE_EXTRA_TYPES = {
    # Languages where the "self" receiver is its own node type rather than a
    # plain identifier (JS/TS `this`). Python's `self` is just an identifier.
    LanguageID.JAVASCRIPT: {"this"},
    LanguageID.TYPESCRIPT: {"this"},
    LanguageID.TSX: {"this"},
    LanguageID.GO: set(),
    LanguageID.PYTHON: set(),
}
SELF_TOKEN_TEXT = {
    LanguageID.PYTHON: {"self"},
    LanguageID.JAVASCRIPT: {"this"},
    LanguageID.TYPESCRIPT: {"this"},
    LanguageID.TSX: {"this"},
    LanguageID.GO: set(),
}
CLASS_NODE_TYPES = {
    LanguageID.PYTHON: {"class_definition"},
    LanguageID.JAVASCRIPT: {"class_declaration"},
    LanguageID.TYPESCRIPT: {"class_declaration"},
    LanguageID.TSX: {"class_declaration"},
    LanguageID.GO: {"type_declaration"},
}
FUNCTION_NODE_TYPES = {
    LanguageID.PYTHON: {"function_definition"},
    LanguageID.JAVASCRIPT: {"function_declaration", "method_definition"},
    LanguageID.TYPESCRIPT: {"function_declaration", "method_definition"},
    LanguageID.TSX: {"function_declaration", "method_definition"},
    LanguageID.GO: {"function_declaration", "method_declaration"},
}
DECORATED_WRAPPER_TYPES = {
    LanguageID.PYTHON: {"decorated_definition"},
    LanguageID.JAVASCRIPT: set(),
    LanguageID.TYPESCRIPT: set(),
    LanguageID.TSX: set(),
    LanguageID.GO: set(),
}
ASSIGNMENT_NODE_TYPE = {
    LanguageID.PYTHON: "assignment",
}
RAISE_NODE_TYPE = {
    LanguageID.PYTHON: "raise_statement",
}


def find_all(node: Node, types: set[str]) -> list[Node]:
    """All descendants of `node` (inclusive) whose type is in `types`."""
    found: list[Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in types:
            found.append(current)
        stack.extend(current.children)
    found.sort(key=lambda n: n.start_byte)
    return found


def iter_scoped_nodes(node: Node, target_types: set[str], lang: str, is_root: bool = True) -> list[Node]:
    """Descendants of `node` matching `target_types`, without crossing into
    nested function/class definitions (those are their own symbols/scopes).
    """
    skip_types = (
        CLASS_NODE_TYPES.get(lang, set())
        | FUNCTION_NODE_TYPES.get(lang, set())
        | DECORATED_WRAPPER_TYPES.get(lang, set())
    )
    results: list[Node] = []
    for child in node.children:
        if not is_root and child.type in skip_types:
            continue
        if child.type in target_types:
            results.append(child)
        results.extend(iter_scoped_nodes(child, target_types, lang, is_root=False))
    return results


def flatten_reference_chain(node: Node, source: bytes, lang: str) -> list[str] | None:
    """Flatten an identifier/attribute chain (`a.b.c`, `self.svc`, `this.x`)
    into its textual segments, root first. Returns None for a dynamic root
    (e.g. `foo().bar` - the root is a call, not a name).
    """
    attr_type = ATTRIBUTE_NODE_TYPE[lang]
    obj_field = ATTR_OBJECT_FIELD[lang]
    prop_field = ATTR_PROPERTY_FIELD[lang]
    ident_types = IDENTIFIER_NODE_TYPES[lang] | SELF_NODE_EXTRA_TYPES.get(lang, set())
    segments: list[str] = []
    current: Node | None = node
    while current is not None:
        if current.type in ident_types:
            segments.insert(0, node_text(current, source))
            return segments
        if current.type == attr_type:
            prop = current.child_by_field_name(prop_field)
            if prop is None:
                return None
            segments.insert(0, node_text(prop, source))
            current = current.child_by_field_name(obj_field)
            continue
        return None
    return None
