"""Call-site invocation context: what a `CALLS` edge's origin looked like
at the exact place it was made - synchronous vs. awaited, inside a loop or
a try/catch, guarded by a null check, how many arguments and what shape,
and (per the native-synonym-resolution work) what the call's *result* is
bound to and what syntactic role it plays at this particular call site -
computed once per resolved call in `ConcreteGraphBuilder._resolve_calls_in_function`
and stored as edge attributes (see that module) rather than as a second
pass over the same call sites.

**Why call-site synonyms matter**: the same callee (`db.user.get`, say) can
play a completely different operational role depending on where it's
called from - bound to a `credentials` variable that flows into a
`return`, vs. discarded inside a `while` loop's condition as a polling
check. Prism's graph nodes are globally invariant (one node per definition
- `db.user.get` is always the same symbol), so this per-call-site meaning
is deliberately attached to the `CALLS` *edge*, not the node: the same
callee can carry a different `bound_to`/`call_site_role`/`is_return_bound`
triple on every edge pointing to it. No manual alias/synonym YAML file is
needed because none of this is name matching - it's read directly off the
AST shape at each call site.
"""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from prism.parser.lang_config import (
    ASSERT_NODE_TYPE,
    ASSIGNMENT_NODE_TYPE,
    AWAIT_NODE_TYPES,
    CONDITIONAL_NODE_TYPES,
    IDENTIFIER_NODE_TYPES,
    LOOP_LIKE_METHOD_NAMES,
    LOOP_NODE_TYPES,
    RETURN_STATEMENT_NODE_TYPE,
    SHORT_VAR_DECL_NODE_TYPE,
    TERNARY_NODE_TYPES,
    TRY_NODE_TYPES,
    VARIABLE_DECLARATOR_NODE_TYPE,
    call_callee_segments,
    find_all,
    flatten_reference_chain,
)
from prism.parser.tree_sitter_loader import LanguageID, node_text

_STATEMENT_NODE_TYPES = frozenset({"expression_statement"})
_FUNCTION_LITERAL_TYPES = frozenset(
    {"lambda", "arrow_function", "function_expression", "function", "anonymous_function"}
)
_LITERAL_NODE_TYPES = frozenset(
    {
        "string", "integer", "float", "true", "false", "none",  # Python
        "number", "true", "false", "null", "undefined", "template_string",  # JS/TS
        "string_literal", "interpreted_string_literal", "raw_string_literal",  # Go
        "decimal_integer_literal", "character_literal",  # Java
        "real_literal", "boolean_literal",  # C#
    }
)


@dataclass(frozen=True)
class CallSiteContext:
    call_kind: str  # "sync" | "awaited" | "callback" | "fire_and_forget"
    inside_loop: bool
    inside_try_catch: bool
    guarded_by_null_check: bool
    args_passed_count: int
    argument_flow: str  # "literal" | "reference" | "callback"
    #: The LHS name this call's result is bound to (`"credentials"` for
    #: `credentials = db.get(...)` / `const credentials = await db.get(...)`
    #: / `this.credentials = db.get(...)`), or `None` for a bare,
    #: unassigned call. See `_bound_to`.
    bound_to: str | None = None
    #: `"predicate_guard"` | `"assertion_subject"` | the same value as
    #: `bound_to` | `None` (bare, unassigned, not a predicate/assertion
    #: argument). See `_call_site_role`.
    call_site_role: str | None = None
    #: `True` iff `bound_to` is later referenced in this function's own
    #: `return` statement(s) or its terminal (last) statement is an
    #: assertion call referencing it - a downstream-criticality signal
    #: distinct from `call_site_role` itself. See `_is_return_bound`.
    is_return_bound: bool = False

    def to_dict(self) -> dict:
        return {
            "call_kind": self.call_kind,
            "inside_loop": self.inside_loop,
            "inside_try_catch": self.inside_try_catch,
            "guarded_by_null_check": self.guarded_by_null_check,
            "args_passed_count": self.args_passed_count,
            "argument_flow": self.argument_flow,
            "bound_to": self.bound_to,
            "call_site_role": self.call_site_role,
            "is_return_bound": self.is_return_bound,
        }


def _ancestors_up_to(node: Node, root: Node) -> list[Node]:
    """`node`'s ancestor chain, nearest first, stopping at (and excluding)
    `root` itself - the def_node boundary a call site's context is always
    scoped to."""
    chain: list[Node] = []
    current = node.parent
    while current is not None and current.id != root.id:
        chain.append(current)
        current = current.parent
    return chain


def _inside_loop(call_node: Node, def_node: Node, lang: str, source: bytes) -> bool:
    loop_types = LOOP_NODE_TYPES.get(lang, set())
    ancestors = _ancestors_up_to(call_node, def_node)
    for ancestor in ancestors:
        if ancestor.type in loop_types:
            return True
    for ancestor in ancestors:
        if ancestor.type not in _FUNCTION_LITERAL_TYPES:
            continue
        outer_call = ancestor.parent
        if outer_call is None or outer_call.type not in (
            "call", "call_expression", "method_invocation", "invocation_expression"
        ):
            continue
        segments = call_callee_segments(outer_call, source, lang)
        if segments and segments[-1] in LOOP_LIKE_METHOD_NAMES:
            return True
    return False


def _inside_try_catch(call_node: Node, def_node: Node, lang: str) -> bool:
    try_types = TRY_NODE_TYPES.get(lang, set())
    if not try_types:
        return False
    return any(a.type in try_types for a in _ancestors_up_to(call_node, def_node))


def _enclosing_statement(call_node: Node, def_node: Node) -> Node | None:
    for ancestor in _ancestors_up_to(call_node, def_node):
        if ancestor.type.endswith("_statement") or ancestor.type.endswith("_declaration"):
            return ancestor
    return None


def _guarded_by_null_check(call_node: Node, def_node: Node, lang: str, source: bytes) -> bool:
    # Optional chaining (`foo?.bar()`, `foo?.()`) anywhere in the callee
    # expression - a real `?.` token is present in the call's own text up
    # to its argument list.
    func_field = call_node.child_by_field_name("function")
    callee_text = node_text(func_field, source) if func_field is not None else node_text(call_node, source)
    if "?." in callee_text:
        return True

    segments = call_callee_segments(call_node, source, lang)
    if not segments:
        return False
    root_name = segments[0]

    # Case 1: the call is nested INSIDE an `if <cond>:` block whose own
    # condition mentions the same root name (`if self.repo: ... self.repo
    # .save(x)`) - checked via the call's own ancestor chain.
    for ancestor in _ancestors_up_to(call_node, def_node):
        if ancestor.type != "if_statement":
            continue
        condition = ancestor.child_by_field_name("condition")
        condition_text = node_text(condition, source) if condition is not None else node_text(ancestor, source)
        if root_name in condition_text:
            return True

    # Case 2: a preceding sibling statement in the same block is an
    # `if <cond>:` mentioning the same root name (`if not self.repo: return`
    # / `self.repo.save(x)`) - a real type-narrowing/null-check proof is
    # out of scope for a static-only, no-type-inference pass (the same
    # limitation this codebase's other heuristic checks already carry);
    # this, like case 1, is a textual proxy, not verified control-flow
    # dominance.
    stmt = _enclosing_statement(call_node, def_node)
    if stmt is None or stmt.parent is None:
        return False
    siblings = stmt.parent.children
    try:
        idx = siblings.index(stmt)
    except ValueError:
        return False
    for sibling in siblings[:idx]:
        if sibling.type == "if_statement":
            condition = sibling.child_by_field_name("condition")
            condition_text = node_text(condition, source) if condition is not None else node_text(sibling, source)
            if root_name in condition_text:
                return True
    return False


def _call_kind(call_node: Node, def_node: Node, lang: str) -> str:
    await_types = AWAIT_NODE_TYPES.get(lang, set())
    if call_node.parent is not None and call_node.parent.type in await_types:
        return "awaited"
    # Also catches Python's `x = await foo()` / `return await foo()` shape
    # where the await node is not the immediate parent expression wrapper
    # tree-sitter builds for a bare `await foo()` statement, but *is* an
    # ancestor between the call and its enclosing statement.
    for ancestor in _ancestors_up_to(call_node, def_node):
        if ancestor.type in await_types:
            return "awaited"
        if ancestor.type.endswith("_statement") or ancestor.type.endswith("_declaration"):
            break

    # This call happens inside a function-literal that is itself passed as
    # an argument to another call (`.then(() => foo())`, `setTimeout(() =>
    # foo(), 0)`) - the call executes as part of a callback, not the
    # caller's own direct control flow.
    for ancestor in _ancestors_up_to(call_node, def_node):
        if ancestor.type not in _FUNCTION_LITERAL_TYPES:
            continue
        parent = ancestor.parent
        if parent is not None and parent.type in ("arguments", "argument_list"):
            return "callback"

    stmt = _enclosing_statement(call_node, def_node)
    if stmt is not None and stmt.type == "expression_statement":
        # The call's own result is discarded outright (never assigned,
        # returned, or used in a larger expression) - true whether or not
        # `stmt`'s expression *is* exactly this call (`foo()` alone) or
        # this call is a sub-expression of a larger discarded one
        # (`foo() and bar()`), since either way nothing captures the value.
        return "fire_and_forget"
    return "sync"


def _args_node(call_node: Node) -> Node | None:
    return (
        call_node.child_by_field_name("arguments")
        or call_node.child_by_field_name("argument_list")
    )


def _argument_flow(call_node: Node, source: bytes) -> str:
    args_node = _args_node(call_node)
    if args_node is None:
        return "literal"
    arg_children = [c for c in args_node.named_children]
    if not arg_children:
        return "literal"
    if any(c.type in _FUNCTION_LITERAL_TYPES for c in arg_children):
        return "callback"
    if all(c.type in _LITERAL_NODE_TYPES for c in arg_children):
        return "literal"
    return "reference"


# --------------------------------------------------------------------- #
# Native call-site synonym resolution: bound_to / call_site_role /
# is_return_bound (no manual alias/synonym configuration - read directly
# off each call site's own AST shape).
# --------------------------------------------------------------------- #
_ASSERTION_CALLEE_NAMES = frozenset({
    "expect", "assert", "assert_", "assertEqual", "assertTrue", "assertFalse",
    "assertRaises", "assertIn", "assertIsNone", "assertIsNotNone", "raises", "fail", "ok",
})
_PREDICATE_EXTRA_TYPES = frozenset({"while_statement"})


def _lhs_bound_name(lhs: Node | None, source: bytes, lang: str) -> str | None:
    if lhs is None:
        return None
    # Go's `short_var_declaration.left` (and a JS/TS destructuring pattern
    # that degrades the same way) is an `expression_list`/list-shaped node
    # wrapping one or more identifiers, not a bare identifier itself - take
    # the first bound name rather than failing to resolve at all.
    if lhs.type == "expression_list" and lhs.named_children:
        lhs = lhs.named_children[0]
    if lhs.type in IDENTIFIER_NODE_TYPES.get(lang, set()):
        return node_text(lhs, source)
    segments = flatten_reference_chain(lhs, source, lang)
    if segments:
        return segments[-1]
    return None


def _direct_parent_skipping_await(call_node: Node, lang: str) -> tuple[Node | None, Node]:
    """`call_node`'s immediate parent, transparently hopping over exactly
    one `await`-expression wrapper - `const x = await f()` must bind to the
    `variable_declarator`, not to the intervening `await_expression`, per
    spec section 1.2.1 ("traversing through await_expression if present")."""
    parent = call_node.parent
    if parent is not None and parent.type in AWAIT_NODE_TYPES.get(lang, set()):
        return parent.parent, parent
    return parent, call_node


def _bound_to(call_node: Node, def_node: Node, lang: str, source: bytes) -> str | None:
    """The LHS this call's own result is directly bound to - deliberately
    only the *immediate* parent (after the one allowed `await` hop), not an
    arbitrary ancestor search: `x = f() + g()` binds neither `f()` nor
    `g()` to `x` on its own, since neither call's value alone is what `x`
    holds."""
    parent, current = _direct_parent_skipping_await(call_node, lang)
    if parent is None:
        return None

    assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
    if assign_type and parent.type == assign_type:
        rhs = parent.child_by_field_name("right")
        if rhs is not None and rhs.id == current.id:
            return _lhs_bound_name(parent.child_by_field_name("left"), source, lang)

    decl_type = VARIABLE_DECLARATOR_NODE_TYPE.get(lang)
    if decl_type and parent.type == decl_type:
        rhs = parent.child_by_field_name("value")
        if rhs is not None and rhs.id == current.id:
            return _lhs_bound_name(parent.child_by_field_name("name"), source, lang)

    short_var_type = SHORT_VAR_DECL_NODE_TYPE.get(lang)
    if short_var_type and parent.type == short_var_type:
        rhs = parent.child_by_field_name("right")
        if rhs is not None and rhs.id == current.id:
            return _lhs_bound_name(parent.child_by_field_name("left"), source, lang)

    return None


def _is_assertion_call(node: Node, source: bytes, lang: str) -> bool:
    segments = call_callee_segments(node, source, lang)
    if not segments:
        return False
    return segments[-1] in _ASSERTION_CALLEE_NAMES or segments[0] in _ASSERTION_CALLEE_NAMES


def _is_assertion_subject(call_node: Node, def_node: Node, lang: str, source: bytes) -> bool:
    # Case 1: a bare Python-style `assert <expr>` (or `assert_(x)`) whose
    # test expression is, or contains, this call - checked before hitting
    # any intervening statement boundary.
    assert_type = ASSERT_NODE_TYPE.get(lang)
    if assert_type:
        for ancestor in _ancestors_up_to(call_node, def_node):
            if ancestor.type == assert_type:
                return True
            if ancestor.type.endswith("_statement"):
                break

    # Case 2: this call is passed as an argument to a call whose own callee
    # reads as an assertion function (`expect(getMetric())`, `assertEqual
    # (compute(), 10)`).
    parent = call_node.parent
    if parent is not None and parent.type in ("arguments", "argument_list"):
        outer_call = parent.parent
        if outer_call is not None and _is_assertion_call(outer_call, source, lang):
            return True
    return False


def _call_site_role(call_node: Node, def_node: Node, lang: str, source: bytes, bound_to: str | None) -> str | None:
    if _is_assertion_subject(call_node, def_node, lang, source):
        return "assertion_subject"

    predicate_types = set(CONDITIONAL_NODE_TYPES.get(lang, set())) | _PREDICATE_EXTRA_TYPES | set(TERNARY_NODE_TYPES.get(lang, set()))
    current = call_node
    for ancestor in _ancestors_up_to(call_node, def_node):
        if ancestor.type in predicate_types:
            condition = ancestor.child_by_field_name("condition")
            if condition is not None and condition.id == current.id:
                return "predicate_guard"
        if ancestor.type.endswith("_statement") or ancestor.type.endswith("_declaration"):
            break
        current = ancestor

    if bound_to is not None:
        return bound_to
    return None


def _identifier_referenced(node: Node, source: bytes, lang: str, name: str) -> bool:
    ident_types = IDENTIFIER_NODE_TYPES.get(lang, set())
    return any(node_text(n, source) == name for n in find_all(node, ident_types))


def _is_return_bound(def_node: Node, lang: str, source: bytes, bound_to: str | None) -> bool:
    if not bound_to:
        return False
    for ret in find_all(def_node, {RETURN_STATEMENT_NODE_TYPE}):
        if _identifier_referenced(ret, source, lang, bound_to):
            return True
    body = def_node.child_by_field_name("body")
    if body is not None:
        statements = body.named_children
        if statements and _identifier_referenced(statements[-1], source, lang, bound_to):
            return True
    return False


def compute_call_site_context(call_node: Node, def_node: Node, lang: str, source: bytes) -> CallSiteContext:
    args_node = _args_node(call_node)
    args_count = len(args_node.named_children) if args_node is not None else 0
    bound_to = _bound_to(call_node, def_node, lang, source)
    return CallSiteContext(
        call_kind=_call_kind(call_node, def_node, lang),
        inside_loop=_inside_loop(call_node, def_node, lang, source),
        inside_try_catch=_inside_try_catch(call_node, def_node, lang),
        guarded_by_null_check=_guarded_by_null_check(call_node, def_node, lang, source),
        args_passed_count=args_count,
        argument_flow=_argument_flow(call_node, source),
        bound_to=bound_to,
        call_site_role=_call_site_role(call_node, def_node, lang, source, bound_to),
        is_return_bound=_is_return_bound(def_node, lang, source, bound_to),
    )


# --------------------------------------------------------------------- #
# Higher-Order Function (HOF) & Callback Signature Contracts.
#
# A parameter typed as a callback (Go's `handle RecoveryFunc` where
# `RecoveryFunc` is a `type RecoveryFunc func(c *Context, err any)`
# typedef, a bare inline Go `func(...)` parameter type, a TypeScript
# arrow-function type annotation, or Python's `typing.Callable[[...],
# R]`) never produces a `CALLS` edge at all - the callee is a value
# passed in by the *caller*, not a symbol this function's own body names
# - so without this, a reader (or an LLM) sees the parameter's bare type
# name and has no way to know what arguments it will actually be invoked
# with. Confirmed as a real, not hypothetical, gap: gin's
# `recovery.CustomRecovery(handle RecoveryFunc)` produced zero visibility
# into `RecoveryFunc`'s own `(c *Context, err any)` shape, which measured
# 100% hallucination on every identifier a generated implementation of
# `handle` needed to reference.
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class HofCallbackContract:
    param: str
    type: str
    #: Always `True` for every entry this module produces - invoking a
    #: value held in a callback-typed parameter is, by construction,
    #: never a statically resolvable named call (the concrete function
    #: passed in is chosen by the caller, not this definition), so this
    #: field records that structural fact rather than something that
    #: could ever come out `False` here.
    invoked_dynamically: bool
    expected_signature: str

    def to_dict(self) -> dict:
        return {
            "param": self.param,
            "type": self.type,
            "invoked_dynamically": self.invoked_dynamically,
            "expected_signature": self.expected_signature,
        }


_GO_PARAM_TYPE = "parameter_declaration"
_TS_PARAM_TYPES = frozenset({"required_parameter", "optional_parameter"})
_TS_FUNCTION_TYPE = "function_type"  # same node-type name Go's grammar also happens to use


def _find_go_func_typedef(root_node: Node, source: bytes, type_name: str) -> Node | None:
    """A Go `type X func(...)` declaration's own `function_type` node, by
    name - `type_declaration` is always a direct child of the file's root
    (or of a `block` inside one, for a grouped `type ( ... )` form), so a
    flat scan via `find_all` is sufficient without needing real scope
    resolution."""
    for type_spec in find_all(root_node, {"type_spec"}):
        name_node = type_spec.child_by_field_name("name")
        type_node = type_spec.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        if node_text(name_node, source) == type_name and type_node.type == "function_type":
            return type_node
    return None


def _go_hof_params(def_node: Node, parsed_root: Node, source: bytes) -> list[HofCallbackContract]:
    params_node = def_node.child_by_field_name("parameters")
    if params_node is None:
        return []
    results: list[HofCallbackContract] = []
    for param in params_node.named_children:
        if param.type != _GO_PARAM_TYPE:
            continue
        name_node = param.child_by_field_name("name")
        type_node = param.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        param_name = node_text(name_node, source)
        if type_node.type == "function_type":
            # An inline `handle func(c *Context, err any)` parameter -
            # the signature is right there, no typedef lookup needed.
            results.append(HofCallbackContract(
                param=param_name, type=node_text(type_node, source), invoked_dynamically=True,
                expected_signature=node_text(type_node, source),
            ))
        elif type_node.type == "type_identifier":
            # A named callback typedef (`handle RecoveryFunc`) - resolve
            # it against the file's own `type X func(...)` declarations.
            type_name = node_text(type_node, source)
            func_type = _find_go_func_typedef(parsed_root, source, type_name)
            if func_type is not None:
                results.append(HofCallbackContract(
                    param=param_name, type=type_name, invoked_dynamically=True,
                    expected_signature=node_text(func_type, source),
                ))
    return results


def _ts_hof_params(def_node: Node, source: bytes) -> list[HofCallbackContract]:
    params_node = def_node.child_by_field_name("parameters")
    if params_node is None:
        return []
    results: list[HofCallbackContract] = []
    for param in params_node.named_children:
        if param.type not in _TS_PARAM_TYPES:
            continue
        name_node = param.child_by_field_name("pattern")
        type_annotation = param.child_by_field_name("type")
        if name_node is None or type_annotation is None:
            continue
        # `type_annotation`'s own child is the real type node (a
        # `function_type` for `(x: number) => void`, something else
        # otherwise) - the annotation node itself is just the `: `
        # wrapper.
        function_type = next((c for c in type_annotation.children if c.type == _TS_FUNCTION_TYPE), None)
        if function_type is None:
            continue
        results.append(HofCallbackContract(
            param=node_text(name_node, source), type=node_text(function_type, source), invoked_dynamically=True,
            expected_signature=node_text(function_type, source),
        ))
    return results


_PY_CALLABLE_RE_PARAM_TYPES = frozenset({"typed_parameter", "typed_default_parameter"})


def _python_hof_params(def_node: Node, source: bytes) -> list[HofCallbackContract]:
    params_node = def_node.child_by_field_name("parameters")
    if params_node is None:
        return []
    results: list[HofCallbackContract] = []
    for param in params_node.named_children:
        if param.type not in _PY_CALLABLE_RE_PARAM_TYPES:
            continue
        name_node = next((c for c in param.children if c.type == "identifier"), None)
        type_node = param.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        type_text = node_text(type_node, source)
        # `typing.Callable[[ArgT1, ArgT2], ReturnT]` (or the bare
        # `Callable[...]` form after `from typing import Callable`) - a
        # simple prefix check on the type's own text, not a full type-
        # checker: precise enough for a syntactic annotation match, which
        # is all this needs.
        simple_type = type_text.rsplit(".", 1)[-1]
        if simple_type.startswith("Callable"):
            results.append(HofCallbackContract(
                param=node_text(name_node, source), type=type_text, invoked_dynamically=True,
                expected_signature=type_text,
            ))
    return results


def detect_hof_callback_params(def_node: Node, parsed) -> list[HofCallbackContract]:
    """Every callback-typed parameter `def_node` declares, across every
    language this detection currently covers (Go: named typedef + inline
    `func(...)`; TypeScript/TSX: inline arrow-function type annotation;
    Python: `typing.Callable[...]`). JavaScript has no static type
    annotations at all, and Java/C# functional-interface parameters
    (`Runnable`, `Function<T,R>`, `Action<T>`) aren't covered - out of
    scope for this pass, not silently claimed, matching this codebase's
    established per-language-coverage-gap disclosure convention.
    """
    lang = parsed.language_id
    source = parsed.source
    if lang == LanguageID.GO:
        return _go_hof_params(def_node, parsed.root_node, source)
    if lang in (LanguageID.TYPESCRIPT, LanguageID.TSX):
        return _ts_hof_params(def_node, source)
    if lang == LanguageID.PYTHON:
        return _python_hof_params(def_node, source)
    return []
