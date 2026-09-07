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

from sce.parser.tree_sitter_loader import LanguageID, _load_language

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

_QUERY_SETS: dict[str, dict[str, str]] = {
    LanguageID.PYTHON: PYTHON_QUERIES,
    LanguageID.JAVASCRIPT: JAVASCRIPT_QUERIES,
    LanguageID.TYPESCRIPT: TYPESCRIPT_QUERIES,
    LanguageID.TSX: TYPESCRIPT_QUERIES,
    LanguageID.GO: GO_QUERIES,
}

_compiled_cache: dict[tuple[str, str], Query] = {}


def get_query(language_id: str, query_name: str) -> Query | None:
    """Compile (and cache) the named query for a language, if it exists."""
    key = (language_id, query_name)
    if key in _compiled_cache:
        return _compiled_cache[key]
    query_set = _QUERY_SETS.get(language_id, {})
    query_src = query_set.get(query_name)
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
