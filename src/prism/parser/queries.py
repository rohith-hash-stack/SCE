"""Tree-sitter S-expression query definitions, keyed by language id.

Each language exposes the same logical query set so the rest of the
pipeline can stay language-agnostic where structure allows:

  - ``definitions``: class/function/method definitions (Stage 1)
  - ``imports``: import statements, for LocalImportMap construction
  - ``calls``: call expressions, for Pass 2 call linking and Stage 3 sinks
  - ``decorators``: decorator/annotation nodes, for #route_handler /
    #event_consumer tagging
"""
from __future__ import annotations

from tree_sitter import Language, Node, Query, QueryCursor

from prism.parser.tree_sitter_loader import LanguageID, _load_language

PYTHON_QUERIES = {
    "definitions": """
        (class_definition name: (identifier) @def.name) @def.class
        (function_definition name: (identifier) @def.name) @def.function
    """,
    "imports": """
        (import_statement) @import.stmt
        (import_from_statement) @import.stmt
    """,
    "calls": """
        (call function: (identifier) @call.name) @call.expr
        (call function: (attribute) @call.attribute) @call.expr
    """,
    "decorators": """
        (decorator) @decorator
    """,
}

JAVASCRIPT_QUERIES = {
    "definitions": """
        (class_declaration name: (identifier) @def.name) @def.class
        (function_declaration name: (identifier) @def.name) @def.function
        (method_definition name: (property_identifier) @def.name) @def.function
    """,
    "imports": """
        (import_statement) @import.stmt
    """,
    "calls": """
        (call_expression function: (identifier) @call.name) @call.expr
        (call_expression function: (member_expression) @call.attribute) @call.expr
    """,
    "decorators": """
        (decorator) @decorator
    """,
}

TYPESCRIPT_QUERIES = {
    "definitions": """
        (class_declaration name: (type_identifier) @def.name) @def.class
        (function_declaration name: (identifier) @def.name) @def.function
        (method_definition name: (property_identifier) @def.name) @def.function
    """,
    "imports": JAVASCRIPT_QUERIES["imports"],
    "calls": JAVASCRIPT_QUERIES["calls"],
    "decorators": JAVASCRIPT_QUERIES["decorators"],
}

GO_QUERIES = {
    "definitions": """
        (type_declaration (type_spec name: (type_identifier) @def.name (struct_type))) @def.class
        (function_declaration name: (identifier) @def.name) @def.function
        (method_declaration name: (field_identifier) @def.name) @def.function
    """,
    "imports": """
        (import_declaration) @import.stmt
    """,
    "calls": """
        (call_expression function: (identifier) @call.name) @call.expr
        (call_expression function: (selector_expression) @call.attribute) @call.expr
    """,
    "decorators": """
        (_) @decorator
    """,
}

JAVA_QUERIES = {
    "definitions": """
        (class_declaration name: (identifier) @def.name) @def.class
        (interface_declaration name: (identifier) @def.name) @def.class
        (record_declaration name: (identifier) @def.name) @def.class
        (enum_declaration name: (identifier) @def.name) @def.class
        (method_declaration name: (identifier) @def.name) @def.function
        (constructor_declaration name: (identifier) @def.name) @def.function
    """,
    "imports": """
        (import_declaration) @import.stmt
    """,
    "calls": """
        (method_invocation name: (identifier) @call.name) @call.expr
    """,
    "decorators": """
        (marker_annotation name: (identifier) @decorator)
        (annotation name: (identifier) @decorator)
    """,
}

CSHARP_QUERIES = {
    "definitions": """
        (class_declaration name: (identifier) @def.name) @def.class
        (interface_declaration name: (identifier) @def.name) @def.class
        (struct_declaration name: (identifier) @def.name) @def.class
        (record_declaration name: (identifier) @def.name) @def.class
        (method_declaration name: (identifier) @def.name) @def.function
        (constructor_declaration name: (identifier) @def.name) @def.function
    """,
    "imports": """
        (using_directive) @import.stmt
    """,
    "calls": """
        (invocation_expression function: (identifier) @call.name) @call.expr
        (invocation_expression function: (member_access_expression) @call.attribute) @call.expr
    """,
    "decorators": """
        (attribute name: (identifier) @decorator)
    """,
}

# `slicer_defs`: locates a function/method/class definition's structural
# parts (parameters, return type, body) for `prism.slicer.universal_slicer`.
# Deliberately separate from `definitions` above (used by
# `concrete_builder`'s Pass 1) rather than adding captures to it - field
# names below were verified against the actual installed grammars (e.g.
# Go's own signature/return-type fields are named "parameters"/"result",
# not "signature"; a wrong field name fails query compilation outright).
PYTHON_SLICER_QUERIES = {
    "slicer_defs": """
        (function_definition
            name: (identifier) @def.name
            parameters: (parameters) @def.params
            body: (block) @def.body) @def.function
        (class_definition
            name: (identifier) @def.name
            body: (block) @def.body) @def.class
    """,
}

JAVASCRIPT_SLICER_QUERIES = {
    "slicer_defs": """
        (function_declaration
            name: (identifier) @def.name
            parameters: (formal_parameters) @def.params
            body: (statement_block) @def.body) @def.function
        (method_definition
            name: (property_identifier) @def.name
            parameters: (formal_parameters) @def.params
            body: (statement_block) @def.body) @def.function
        (class_declaration
            name: (identifier) @def.name
            body: (class_body) @def.body) @def.class
    """,
}

TYPESCRIPT_SLICER_QUERIES = {
    "slicer_defs": """
        (function_declaration
            name: (identifier) @def.name
            parameters: (formal_parameters) @def.params
            body: (statement_block) @def.body) @def.function
        (method_definition
            name: (property_identifier) @def.name
            parameters: (formal_parameters) @def.params
            body: (statement_block) @def.body) @def.function
        (class_declaration
            name: (type_identifier) @def.name
            body: (class_body) @def.body) @def.class
        (interface_declaration
            name: (type_identifier) @def.name
            body: (interface_body) @def.body) @def.class
    """,
}

GO_SLICER_QUERIES = {
    "slicer_defs": """
        (function_declaration
            name: (identifier) @def.name
            parameters: (parameter_list) @def.params
            body: (block) @def.body) @def.function
        (method_declaration
            receiver: (parameter_list) @def.receiver
            name: (field_identifier) @def.name
            parameters: (parameter_list) @def.params
            body: (block) @def.body) @def.function
        (type_declaration
            (type_spec name: (type_identifier) @def.name
                type: (struct_type) @def.body)) @def.class
    """,
}

JAVA_SLICER_QUERIES = {
    "slicer_defs": """
        (method_declaration
            name: (identifier) @def.name
            parameters: (formal_parameters) @def.params
            body: (block) @def.body) @def.function
        (constructor_declaration
            name: (identifier) @def.name
            parameters: (formal_parameters) @def.params
            body: (constructor_body) @def.body) @def.function
        (class_declaration
            name: (identifier) @def.name
            body: (class_body) @def.body) @def.class
        (interface_declaration
            name: (identifier) @def.name
            body: (interface_body) @def.body) @def.class
    """,
}

CSHARP_SLICER_QUERIES = {
    "slicer_defs": """
        (method_declaration
            name: (identifier) @def.name
            parameters: (parameter_list) @def.params
            body: (block) @def.body) @def.function
        (constructor_declaration
            name: (identifier) @def.name
            parameters: (parameter_list) @def.params
            body: (block) @def.body) @def.function
        (class_declaration
            name: (identifier) @def.name
            body: (declaration_list) @def.body) @def.class
        (interface_declaration
            name: (identifier) @def.name
            body: (declaration_list) @def.body) @def.class
    """,
}

_SLICER_QUERY_SETS: dict[str, dict[str, str]] = {
    LanguageID.PYTHON: PYTHON_SLICER_QUERIES,
    LanguageID.JAVASCRIPT: JAVASCRIPT_SLICER_QUERIES,
    LanguageID.TYPESCRIPT: TYPESCRIPT_SLICER_QUERIES,
    LanguageID.TSX: TYPESCRIPT_SLICER_QUERIES,
    LanguageID.GO: GO_SLICER_QUERIES,
    LanguageID.JAVA: JAVA_SLICER_QUERIES,
    LanguageID.CSHARP: CSHARP_SLICER_QUERIES,
}

_QUERY_SETS: dict[str, dict[str, str]] = {
    LanguageID.PYTHON: PYTHON_QUERIES,
    LanguageID.JAVASCRIPT: JAVASCRIPT_QUERIES,
    LanguageID.TYPESCRIPT: TYPESCRIPT_QUERIES,
    LanguageID.TSX: TYPESCRIPT_QUERIES,
    LanguageID.GO: GO_QUERIES,
    LanguageID.JAVA: JAVA_QUERIES,
    LanguageID.CSHARP: CSHARP_QUERIES,
}

_compiled_cache: dict[tuple[str, str], Query] = {}


def get_query(language_id: str, query_name: str) -> Query | None:
    """Compile (and cache) the named query for a language, if it exists."""
    key = (language_id, query_name)
    if key in _compiled_cache:
        return _compiled_cache[key]
    query_src = _QUERY_SETS.get(language_id, {}).get(query_name) or _SLICER_QUERY_SETS.get(language_id, {}).get(query_name)
    if not query_src:
        return None
    lang: Language = _load_language(language_id)
    compiled = Query(lang, query_src)
    _compiled_cache[key] = compiled
    return compiled


def run_query(language_id: str, query_name: str, node: Node) -> dict[str, list[Node]]:
    """Execute a named query against `node`, returning capture-name -> nodes."""
    query = get_query(language_id, query_name)
    if query is None:
        return {}
    cursor = QueryCursor(query)
    return cursor.captures(node)
