"""v1.1 Axis 3: Output `O(v)` - the return contract a symbol's body
actually implements, inspected purely from its `return` expressions (no
type inference - the same "AST predicate, not type checking" discipline
`prism.graph.contracts`/`prism.tagger.rules` already commit to
elsewhere).

Classification is a priority cascade over the *first substantive*
`return` statement found (a function's dominant shape, not an attempted
per-branch union - most real functions have one dominant return shape;
see `_classify_expression`'s own docstring for the exact order), with two
whole-function checks handled first: no `return` at all with no raise ->
`OUTPUT_COMMAND`; no successful return anywhere but at least one raise/
panic -> `OUTPUT_GUARD`.

**Tuple/error-return handling**: a Python `return val, err` or Go
`return res, err` must not let the trailing error identifier contaminate
the *payload*'s own classification - a function returning `(Order, nil)`
on success is still an `OUTPUT_FACTORY` producing an `Order`, not some
mixed shape "because it also has an error slot". `_strip_error_slot`
filters any tuple/expression-list element whose text is exactly an
error-shaped identifier (`err`, `error`, `_`, case-insensitively for the
first two) before classification ever runs.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.parser.lang_config import (
    ASYNC_KEYWORD_NODE_TYPES,
    CALL_NODE_TYPE,
    RAISE_NODE_TYPE,
    call_callee_segments,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text
from prism.semantics._ast_utils import returns_in_scope
from prism.semantics.bitmask import FeatureBit

_SELF_NAMES = frozenset({"self", "this"})
_ERROR_SLOT_NAMES = frozenset({"err", "error", "_"})
_BOOL_LITERAL_TYPES = frozenset({"true", "false", "boolean_literal"})
_COMPARISON_OPERATORS = frozenset({"==", "!=", "<", "<=", ">", ">=", "in", "not in", "is", "is not"})
_BOOLEAN_LOGIC_OPERATORS = frozenset({"and", "or", "&&", "||"})
_COLLECTION_NODE_TYPES = frozenset({
    "list", "dictionary", "set", "list_comprehension", "dictionary_comprehension",
    "set_comprehension", "generator_expression",  # Python
    "array", "object",  # JS/TS
})
_DEFERRED_TYPE_MARKERS = ("promise", "future", "task", "deferred")
_GO_COMPOSITE_TYPE_KINDS = frozenset({"slice_type", "array_type", "map_type"})


def _param_names(def_node: Node, parsed: ParsedFile) -> set[str]:
    params_node = def_node.child_by_field_name("parameters")
    if params_node is None:
        return set()
    names: set[str] = set()
    for child in params_node.children:
        candidate = child
        if candidate.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            name_node = candidate.child_by_field_name("name")
            if name_node is None:
                inner = next((c for c in candidate.children if c.type == "identifier"), None)
                name_node = inner
        elif candidate.type in ("required_parameter", "optional_parameter", "parameter_declaration", "parameter"):
            name_node = candidate.child_by_field_name("name") or candidate.child_by_field_name("pattern")
        elif candidate.type == "identifier":
            name_node = candidate
        else:
            name_node = None
        if name_node is not None:
            names.add(node_text(name_node, parsed.source))
    return names - _SELF_NAMES


def _is_async_def(def_node: Node, lang: str) -> bool:
    async_types = ASYNC_KEYWORD_NODE_TYPES.get(lang, set())
    if not async_types:
        return False
    for candidate in (def_node, def_node.parent):
        if candidate is None:
            continue
        if any(c.type in async_types for c in candidate.children):
            return True
    return False


def _return_type_text(def_node: Node, parsed: ParsedFile) -> str | None:
    node = def_node.child_by_field_name("return_type") or def_node.child_by_field_name("result")
    if node is None:
        return None
    return node_text(node, parsed.source)


def _return_value_node(return_node: Node) -> Node | None:
    """`return_statement` exposes no `value` field in *any* supported
    grammar (confirmed directly for Python/JS/TS/Go) - the value, if any,
    is just the first named child after the bare `return` keyword token.
    Go always wraps its value(s) in an `expression_list`, even for a
    single-value return - `_strip_error_slot` already expects to see that
    shape (alongside Python's `tuple`) and unwraps it.
    """
    for child in return_node.named_children:
        return child
    return None


def _strip_error_slot(value_node: Node | None, parsed: ParsedFile) -> Node | None:
    if value_node is None:
        return None
    if value_node.type in ("tuple", "expression_list"):
        elements = [c for c in value_node.named_children]
        payload = [e for e in elements if node_text(e, parsed.source).strip().lower() not in _ERROR_SLOT_NAMES]
        if len(payload) == 1:
            return payload[0]
        if payload:
            return payload[0]
        return None
    return value_node


def _is_boolean_expression(node: Node, parsed: ParsedFile) -> bool:
    if node.type in _BOOL_LITERAL_TYPES:
        return True
    if node.type in ("comparison_operator", "binary_expression", "boolean_operator"):
        operator_text = None
        for child in node.children:
            if not child.is_named and node_text(child, parsed.source) in (_COMPARISON_OPERATORS | _BOOLEAN_LOGIC_OPERATORS):
                operator_text = node_text(child, parsed.source)
                break
        if operator_text is not None:
            return True
        # `comparison_operator` (Python) doesn't always expose the operator
        # as a distinct unnamed child accessible this simply - fall back to
        # a direct type check for that node type, which only ever exists to
        # represent a chained comparison.
        if node.type == "comparison_operator":
            return True
    if node.type in ("not_operator", "unary_expression"):
        text = node_text(node, parsed.source)
        if text.startswith("not ") or text.startswith("!"):
            return True
    return False


def _is_class_instantiation(node: Node, lang: str, parsed: ParsedFile) -> bool:
    if lang == LanguageID.GO:
        # `&Order{...}` / bare `Order{...}` - Go's own struct-literal
        # construction shape. Checked *first*, but must fall through
        # (not return False) to the general call-based check below for
        # anything else - Go's other common factory idiom is a plain
        # `NewOrder(...)` constructor *function* (the language has no
        # `new`/constructor-call syntax of its own), which is a
        # `call_expression` indistinguishable in shape from any other
        # Go function call except by name convention, same as Python.
        target = node
        if target.type == "unary_expression":  # &Order{...}
            target = target.children[-1] if target.children else target
        if target.type == "composite_literal":
            type_node = target.child_by_field_name("type")
            if type_node is not None and type_node.type not in _GO_COMPOSITE_TYPE_KINDS:
                return True
    call_type = CALL_NODE_TYPE.get(lang)
    if node.type == "new_expression":  # JS/TS `new Order(...)`
        return True
    if node.type == call_type:
        segments = call_callee_segments(node, parsed.source, lang)
        if not segments:
            return False
        name = segments[-1]
        if not name or not name[0].isupper():
            return False
        if lang == LanguageID.GO:
            # Capitalization alone is a much weaker signal in Go than in
            # Python/JS - it's the exported-ness convention for *every*
            # public function, not specifically a constructor one. Go's
            # own real constructor idiom (the language has no `new Foo()`
            # syntax) is a function literally named `NewXxx`, so that's
            # the additional bar for this language specifically.
            return name.startswith("New") and len(name) > 3 and name[3].isupper()
        return True
    return False


def _is_collection_literal(node: Node) -> bool:
    if node.type in _COLLECTION_NODE_TYPES:
        return True
    if node.type == "composite_literal":  # Go: []int{...} / map[K]V{...}
        type_node = node.child_by_field_name("type")
        return type_node is not None and type_node.type in _GO_COMPOSITE_TYPE_KINDS
    return False


def _is_deferred_expression(node: Node, lang: str, parsed: ParsedFile) -> bool:
    if node.type == "await_expression" or node.type == "await":
        return True
    call_type = CALL_NODE_TYPE.get(lang)
    if node.type == call_type:
        segments = call_callee_segments(node, parsed.source, lang)
        if segments:
            joined = ".".join(segments).lower()
            if any(marker in joined for marker in _DEFERRED_TYPE_MARKERS):
                return True
    return False


def _classify_expression(node: Node, lang: str, parsed: ParsedFile, param_names: set[str]) -> FeatureBit | None:
    """One return expression's own shape, in the audit's own literal
    priority order (predicate, factory, transformer, aggregator, fluent,
    async-deferred, else query) - `OUTPUT_COMMAND`/`OUTPUT_GUARD` are
    whole-function properties handled by the caller, never reached here.
    """
    text = node_text(node, parsed.source).strip()
    if text in _SELF_NAMES:
        return FeatureBit.OUTPUT_FLUENT
    if _is_boolean_expression(node, parsed):
        return FeatureBit.OUTPUT_PREDICATE
    if _is_class_instantiation(node, parsed=parsed, lang=lang):
        return FeatureBit.OUTPUT_FACTORY
    if node.type in ("identifier",) and text in param_names:
        return FeatureBit.OUTPUT_TRANSFORMER
    if _is_collection_literal(node):
        return FeatureBit.OUTPUT_AGGREGATOR
    if _is_deferred_expression(node, lang, parsed):
        return FeatureBit.OUTPUT_ASYNC_DEFERRED
    return None


def extract_output(def_node: Node, parsed: ParsedFile) -> FeatureBit:
    lang = parsed.language_id
    param_names = _param_names(def_node, parsed)
    returns = returns_in_scope(def_node, lang)

    is_async = _is_async_def(def_node, lang)
    return_type_text = (_return_type_text(def_node, parsed) or "").lower()
    if is_async or any(marker in return_type_text for marker in _DEFERRED_TYPE_MARKERS) or "chan " in return_type_text or return_type_text.startswith("chan"):
        async_bit = FeatureBit.OUTPUT_ASYNC_DEFERRED
    else:
        async_bit = FeatureBit(0)

    value_returns = [_return_value_node(r) for r in returns]
    value_returns = [v for v in value_returns if v is not None]

    # Every return either has no value at all, or explicitly returns
    # None/null/nil - "bare return" in spirit even when a literal
    # None/nil token is present in the AST - so nothing here counts as a
    # substantive payload.
    saw_only_empty_payloads = True
    for raw_value in value_returns:
        payload = _strip_error_slot(raw_value, parsed)
        if payload is None:
            continue
        if node_text(payload, parsed.source).strip().lower() in ("none", "null", "nil"):
            continue
        saw_only_empty_payloads = False
        classified = _classify_expression(payload, lang, parsed, param_names)
        if classified is not None:
            return classified | async_bit

    if saw_only_empty_payloads:
        raise_type = RAISE_NODE_TYPE.get(lang)
        has_raise = bool(raise_type and iter_scoped_nodes(def_node, {raise_type}, lang))
        if not has_raise:
            call_type = CALL_NODE_TYPE.get(lang)
            if call_type:
                for call in iter_scoped_nodes(def_node, {call_type}, lang):
                    segments = call_callee_segments(call, parsed.source, lang)
                    if segments and segments[-1] == "panic":
                        has_raise = True
                        break
        if has_raise:
            return FeatureBit.OUTPUT_GUARD | async_bit
        return FeatureBit.OUTPUT_COMMAND | async_bit

    return FeatureBit.OUTPUT_QUERY | async_bit


def compute_output_bits(builder) -> dict[str, int]:
    result: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        result[symbol.qualified_name] = int(extract_output(def_node, parsed))
    return result
