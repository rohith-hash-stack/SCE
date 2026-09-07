"""Per-language structural node-type tables and generic tree-walking helpers.

Shared by `prism.graph.concrete_builder` (Pass 2 call linking) and
`prism.tagger.engine` (Stage 3 deterministic tagging) so both operate on the
same notion of "call expression", "attribute chain", "self token", and
"scoped subtree" without duplicating tree-sitter plumbing.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.parser.tree_sitter_loader import LanguageID, node_text

CALL_NODE_TYPE = {
    LanguageID.PYTHON: "call",
    LanguageID.JAVASCRIPT: "call_expression",
    LanguageID.TYPESCRIPT: "call_expression",
    LanguageID.TSX: "call_expression",
    LanguageID.GO: "call_expression",
    LanguageID.JAVA: "method_invocation",
    LanguageID.CSHARP: "invocation_expression",
}
ATTRIBUTE_NODE_TYPE = {
    LanguageID.PYTHON: "attribute",
    LanguageID.JAVASCRIPT: "member_expression",
    LanguageID.TYPESCRIPT: "member_expression",
    LanguageID.TSX: "member_expression",
    LanguageID.GO: "selector_expression",
    LanguageID.JAVA: "field_access",
    LanguageID.CSHARP: "member_access_expression",
}
ATTR_OBJECT_FIELD = {
    LanguageID.PYTHON: "object",
    LanguageID.JAVASCRIPT: "object",
    LanguageID.TYPESCRIPT: "object",
    LanguageID.TSX: "object",
    LanguageID.GO: "operand",
    LanguageID.JAVA: "object",
    LanguageID.CSHARP: "expression",
}
ATTR_PROPERTY_FIELD = {
    LanguageID.PYTHON: "attribute",
    LanguageID.JAVASCRIPT: "property",
    LanguageID.TYPESCRIPT: "property",
    LanguageID.TSX: "property",
    LanguageID.GO: "field",
    LanguageID.JAVA: "field",
    LanguageID.CSHARP: "name",
}
IDENTIFIER_NODE_TYPES = {
    LanguageID.PYTHON: {"identifier"},
    LanguageID.JAVASCRIPT: {"identifier"},
    LanguageID.TYPESCRIPT: {"identifier"},
    LanguageID.TSX: {"identifier"},
    LanguageID.GO: {"identifier"},
    LanguageID.JAVA: {"identifier"},
    LanguageID.CSHARP: {"identifier"},
}
SELF_NODE_EXTRA_TYPES = {
    # Languages where the "self" receiver is its own node type rather than a
    # plain identifier (JS/TS/Java/C# `this`). Python's `self` is just an
    # identifier, by convention rather than grammar.
    LanguageID.JAVASCRIPT: {"this"},
    LanguageID.TYPESCRIPT: {"this"},
    LanguageID.TSX: {"this"},
    LanguageID.GO: set(),
    LanguageID.PYTHON: set(),
    LanguageID.JAVA: {"this"},
    LanguageID.CSHARP: {"this_expression"},
}
SELF_TOKEN_TEXT = {
    LanguageID.PYTHON: {"self"},
    LanguageID.JAVASCRIPT: {"this"},
    LanguageID.TYPESCRIPT: {"this"},
    LanguageID.TSX: {"this"},
    LanguageID.GO: set(),
    LanguageID.JAVA: {"this"},
    LanguageID.CSHARP: {"this"},
}
CLASS_NODE_TYPES = {
    LanguageID.PYTHON: {"class_definition"},
    LanguageID.JAVASCRIPT: {"class_declaration"},
    LanguageID.TYPESCRIPT: {"class_declaration"},
    LanguageID.TSX: {"class_declaration"},
    LanguageID.GO: {"type_declaration"},
    LanguageID.JAVA: {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"},
    LanguageID.CSHARP: {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"},
}
FUNCTION_NODE_TYPES = {
    LanguageID.PYTHON: {"function_definition"},
    LanguageID.JAVASCRIPT: {"function_declaration", "method_definition"},
    LanguageID.TYPESCRIPT: {"function_declaration", "method_definition"},
    LanguageID.TSX: {"function_declaration", "method_definition"},
    LanguageID.GO: {"function_declaration", "method_declaration"},
    LanguageID.JAVA: {"method_declaration", "constructor_declaration"},
    LanguageID.CSHARP: {"method_declaration", "constructor_declaration"},
}
DECORATED_WRAPPER_TYPES = {
    LanguageID.PYTHON: {"decorated_definition"},
    LanguageID.JAVASCRIPT: set(),
    LanguageID.TYPESCRIPT: set(),
    LanguageID.TSX: set(),
    LanguageID.GO: set(),
    # Java annotations / C# attributes are inline "modifiers"/"attribute_list"
    # children of the definition itself, not a separate wrapper node around
    # it the way Python's `@decorator\ndef f()` is - nothing to unwrap here.
    LanguageID.JAVA: set(),
    LanguageID.CSHARP: set(),
}
ASSIGNMENT_NODE_TYPE = {
    LanguageID.PYTHON: "assignment",
    LanguageID.JAVA: "assignment_expression",
    LanguageID.CSHARP: "assignment_expression",
}
RAISE_NODE_TYPE = {
    LanguageID.PYTHON: "raise_statement",
    LanguageID.JAVA: "throw_statement",
    LanguageID.CSHARP: "throw_statement",
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


def call_callee_segments(call_node: Node, source: bytes, lang: str) -> list[str] | None:
    """The dotted reference-chain segments naming what `call_node` invokes
    (e.g. `["self", "repo", "save"]` for `self.repo.save(x)`) - the same
    result `flatten_reference_chain(call_node.child_by_field_name("function"),
    ...)` gives for every other supported language, generalized to cover
    Java's structurally different `method_invocation`, which has no single
    "function" field at all: the receiver and method name are two separate
    fields ("object", optional, and "name", always present) rather than one
    combined callee expression.
    """
    if lang == LanguageID.JAVA:
        name_node = call_node.child_by_field_name("name")
        if name_node is None:
            return None
        name = node_text(name_node, source)
        object_node = call_node.child_by_field_name("object")
        if object_node is None:
            return [name]
        object_segments = flatten_reference_chain(object_node, source, lang)
        if object_segments is None:
            return None
        return [*object_segments, name]

    func_node = call_node.child_by_field_name("function")
    if func_node is None:
        return None
    return flatten_reference_chain(func_node, source, lang)
