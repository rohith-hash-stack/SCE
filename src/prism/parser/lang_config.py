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
    # Item 17 (second post-implementation audit): TypeScript's grammar
    # gives a *type-position* reference its own node type
    # (`type_identifier`), distinct from `identifier` (a value-position
    # reference) - confirmed directly: `class Foo extends Bar` (`Bar` is
    # a value/expression position, plain `identifier`) vs. `class Foo
    # implements Baz` / `interface X extends Y` (both type positions,
    # `type_identifier`). Without this, `flatten_reference_chain` could
    # never resolve an `implements` clause or an interface's own
    # `extends` at all - confirmed directly, both silently produced no
    # edge before this fix. `type_identifier` only ever appears in a
    # type position, never in expression/call-target position, so this
    # is a pure addition with no effect on ordinary call/reference
    # resolution elsewhere.
    LanguageID.TYPESCRIPT: {"identifier", "type_identifier"},
    LanguageID.TSX: {"identifier", "type_identifier"},
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
# Issue C1: Go specifies its receiver explicitly in a method's own
# parameter list (`func (c *Context) JSON(...)`) and has no lexical
# `self`/`this` keyword the way Python/JS/Java/C# do - an empty set here
# is intentional, not a missing entry. `ConcreteGraphBuilder._resolve_
# segments`' self/this-fallback branch (gated on `receiver_segments[0]
# in self_tokens`) is therefore always skipped for Go; a Go method call
# like `c.JSON(...)` instead resolves through the ordinary
# `func_instance_map`/`class_instance_map` path, the same one every
# other language's *non*-self-prefixed local variable uses - populated
# for Go by `_bind_go_typed_parameters` (Issue B1's receiver/parameter
# type-signature binding), not by any self-token mechanism.
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
    # Added for prism.graph.contracts's state-mutation detection (a plain
    # `this.x = ...`/`this.x[i] = ...` walk, language-agnostic once this
    # entry exists) - concrete_builder.py's own instance-binding passes
    # stay unaffected: they gate on `instance_binding_langs`, which does
    # not include JS/TS/TSX, and `_collect_attribute_definitions` is
    # separately gated to Python only.
    LanguageID.JAVASCRIPT: "assignment_expression",
    LanguageID.TYPESCRIPT: "assignment_expression",
    LanguageID.TSX: "assignment_expression",
    LanguageID.JAVA: "assignment_expression",
    LanguageID.CSHARP: "assignment_expression",
}
# `self.x += 1` / `this.count += 1` - a real, distinct grammar node from
# plain `ASSIGNMENT_NODE_TYPE` in every language here (confirmed
# directly), not merely an `assignment` with a compound operator token -
# `prism.graph.contracts._state_mutations` previously missed every
# augmented-assignment mutation entirely (a real, pre-existing gap this
# table closes: `self.count += 1` silently didn't count as a state
# mutation, which could under-report impurity). Same `left`/`right` field
# shape as `ASSIGNMENT_NODE_TYPE`, confirmed directly, so no separate
# unwrapping logic is needed at any call site.
AUGMENTED_ASSIGNMENT_NODE_TYPE = {
    LanguageID.PYTHON: "augmented_assignment",
    LanguageID.JAVASCRIPT: "augmented_assignment_expression",
    LanguageID.TYPESCRIPT: "augmented_assignment_expression",
    LanguageID.TSX: "augmented_assignment_expression",
    LanguageID.JAVA: "assignment_expression",  # Java has no distinct node - `+=` still parses as `assignment_expression`
    LanguageID.CSHARP: "assignment_expression",
}
# `const x = ...` / `let x = ...` (JS/TS/TSX) and `T x = ...;` (Java) both
# wrap a single binding in its own `variable_declarator` node (`name`/`value`
# fields) - distinct from `ASSIGNMENT_NODE_TYPE`'s plain `x = ...`
# re-assignment, since a *declarator* is where a call-site synonym binding
# (`call_site.py`'s `bound_to`) most commonly originates. Python has no
# separate declaration form (`ASSIGNMENT_NODE_TYPE`'s `assignment` node
# already covers `x = ...` whether `x` is new or being rebound), so it has
# no entry here. Go's `x := f()` is structurally a declaration too, but
# shaped as a `short_var_declaration` with `left`/`right` fields instead of
# `name`/`value` - kept as its own entry rather than forced into this table,
# since callers already have to branch on the field names anyway.
VARIABLE_DECLARATOR_NODE_TYPE = {
    LanguageID.JAVASCRIPT: "variable_declarator",
    LanguageID.TYPESCRIPT: "variable_declarator",
    LanguageID.TSX: "variable_declarator",
    LanguageID.JAVA: "variable_declarator",
}
SHORT_VAR_DECL_NODE_TYPE = {
    LanguageID.GO: "short_var_declaration",
}
RETURN_STATEMENT_NODE_TYPE = "return_statement"  # identical across every supported grammar

RAISE_NODE_TYPE = {
    LanguageID.PYTHON: "raise_statement",
    LanguageID.JAVASCRIPT: "throw_statement",
    LanguageID.TYPESCRIPT: "throw_statement",
    LanguageID.TSX: "throw_statement",
    LanguageID.JAVA: "throw_statement",
    LanguageID.CSHARP: "throw_statement",
}

# --------------------------------------------------------------------- #
# Behavioral-contract node-type tables (src/prism/graph/contracts.py,
# src/prism/graph/call_site.py). Python and JS/TS/TSX get real, verified
# grammar coverage (see contracts.py's module docstring); Go/Java/C#
# entries are included where the construct genuinely exists in that
# grammar, left as an empty set where it doesn't apply (Go has no
# try/catch or ternary) or hasn't been implemented yet - an empty set
# degrades a check to "never observed" rather than crashing, the same
# fail-open convention every other per-language table here already uses.
# --------------------------------------------------------------------- #
LOOP_NODE_TYPES = {
    LanguageID.PYTHON: {"for_statement", "while_statement"},
    LanguageID.JAVASCRIPT: {"for_statement", "for_in_statement", "while_statement", "do_statement"},
    LanguageID.TYPESCRIPT: {"for_statement", "for_in_statement", "while_statement", "do_statement"},
    LanguageID.TSX: {"for_statement", "for_in_statement", "while_statement", "do_statement"},
    LanguageID.GO: {"for_statement"},
    LanguageID.JAVA: {"for_statement", "while_statement", "do_statement", "enhanced_for_statement"},
    LanguageID.CSHARP: {"for_statement", "while_statement", "do_statement", "foreach_statement"},
}
# Higher-order iteration methods (`.map()`, `.forEach()`, `.filter()`, ...)
# also put a call site "inside a loop" in the behavioral sense the task
# asks for, even though no loop-statement node is on its ancestor chain -
# detected by callee simple name at the call site, language-agnostically,
# in call_site.py rather than here (it's a name pattern, not a grammar
# shape), documented here for discoverability.
LOOP_LIKE_METHOD_NAMES = frozenset({"map", "forEach", "filter", "reduce", "flatMap", "each"})
TRY_NODE_TYPES = {
    LanguageID.PYTHON: {"try_statement"},
    LanguageID.JAVASCRIPT: {"try_statement"},
    LanguageID.TYPESCRIPT: {"try_statement"},
    LanguageID.TSX: {"try_statement"},
    LanguageID.GO: set(),  # Go has no try/catch construct
    LanguageID.JAVA: {"try_statement"},
    LanguageID.CSHARP: {"try_statement"},
}
CATCH_NODE_TYPES = {
    LanguageID.PYTHON: {"except_clause"},
    LanguageID.JAVASCRIPT: {"catch_clause"},
    LanguageID.TYPESCRIPT: {"catch_clause"},
    LanguageID.TSX: {"catch_clause"},
    LanguageID.GO: set(),
    LanguageID.JAVA: {"catch_clause"},
    LanguageID.CSHARP: {"catch_clause"},
}
CONDITIONAL_NODE_TYPES = {
    LanguageID.PYTHON: {"if_statement"},
    LanguageID.JAVASCRIPT: {"if_statement"},
    LanguageID.TYPESCRIPT: {"if_statement"},
    LanguageID.TSX: {"if_statement"},
    LanguageID.GO: {"if_statement"},
    LanguageID.JAVA: {"if_statement"},
    LanguageID.CSHARP: {"if_statement"},
}
TERNARY_NODE_TYPES = {
    LanguageID.PYTHON: {"conditional_expression"},
    LanguageID.JAVASCRIPT: {"ternary_expression"},
    LanguageID.TYPESCRIPT: {"ternary_expression"},
    LanguageID.TSX: {"ternary_expression"},
    LanguageID.GO: set(),  # Go has no ternary operator
    LanguageID.JAVA: {"ternary_expression"},
    LanguageID.CSHARP: {"conditional_expression"},
}
AWAIT_NODE_TYPES = {
    LanguageID.PYTHON: {"await"},
    LanguageID.JAVASCRIPT: {"await_expression"},
    LanguageID.TYPESCRIPT: {"await_expression"},
    LanguageID.TSX: {"await_expression"},
    LanguageID.GO: set(),  # no async/await; goroutines are a different model
    LanguageID.JAVA: set(),
    LanguageID.CSHARP: {"await_expression"},
}
ASYNC_KEYWORD_NODE_TYPES = {
    # Presence of a bare "async" token among a definition's own children -
    # `is_async` detection.
    LanguageID.PYTHON: {"async"},
    LanguageID.JAVASCRIPT: {"async"},
    LanguageID.TYPESCRIPT: {"async"},
    LanguageID.TSX: {"async"},
    LanguageID.GO: set(),
    LanguageID.JAVA: set(),
    LanguageID.CSHARP: {"async"},  # a modifier keyword inside `modifiers`
}
GLOBAL_NONLOCAL_STATEMENT_TYPES = {
    # Only Python has an explicit "I'm rebinding an enclosing/module
    # scope name" statement; every other supported language mutates
    # outer scope purely through assignment shape, already covered by
    # ASSIGNMENT_NODE_TYPE/`this.*` detection.
    LanguageID.PYTHON: {"global_statement", "nonlocal_statement"},
}
ASSERT_NODE_TYPE = {
    LanguageID.PYTHON: "assert_statement",
}
EXPORT_WRAPPER_TYPES = {
    # A definition whose *parent* is one of these is publicly exported -
    # JS/TS/TSX's `export function foo() {}` / `export class Foo {}`
    # (`export default ...` is also an `export_statement`).
    LanguageID.JAVASCRIPT: {"export_statement"},
    LanguageID.TYPESCRIPT: {"export_statement"},
    LanguageID.TSX: {"export_statement"},
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


#: Issue C2 (security audit): `iter_scoped_nodes` recurses once per CST
#: nesting level, pure Python - confirmed directly to raise a real,
#: uncaught `RecursionError` on an adversarial ~600-deep nested-`if`
#: source file well before tree-sitter's own C-level parser rejects
#: anything (unlike CPython's `ast.parse`, tree-sitter's grammar has no
#: comparable nesting-depth guard of its own for this shape). Prism
#: indexes arbitrary, potentially adversarial repositories, so this
#: needs its own explicit floor rather than relying on
#: `sys.getrecursionlimit()` (1000 by default) to fail safely - by the
#: time that limit is hit, the caller's own stack frames (this function
#: is invoked from deep inside pass-2 call resolution) have already
#: consumed an unknown, non-portable amount of the budget. 300 leaves
#: comfortable headroom below that default limit for every real-world
#: nesting depth this has been measured against (single digits to low
#: tens for ordinary code) while still failing well short of a crash.
MAX_SCOPED_NODE_DEPTH = 300


def iter_scoped_nodes(
    node: Node, target_types: set[str], lang: str, is_root: bool = True, _depth: int = 0
) -> list[Node]:
    """Descendants of `node` matching `target_types`, without crossing into
    nested function/class definitions (those are their own symbols/scopes).
    Stops descending (rather than raising) past `MAX_SCOPED_NODE_DEPTH` -
    see that constant's own comment.
    """
    if _depth >= MAX_SCOPED_NODE_DEPTH:
        return []
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
        results.extend(iter_scoped_nodes(child, target_types, lang, is_root=False, _depth=_depth + 1))
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
