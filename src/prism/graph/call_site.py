"""Call-site invocation context: what a `CALLS` edge's origin looked like
at the exact place it was made - synchronous vs. awaited, inside a loop or
a try/catch, guarded by a null check, how many arguments and what shape -
computed once per resolved call in `ConcreteGraphBuilder._resolve_calls_in_function`
and stored as edge attributes (see that module) rather than as a second
pass over the same call sites.
"""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from prism.parser.lang_config import (
    AWAIT_NODE_TYPES,
    LOOP_LIKE_METHOD_NAMES,
    LOOP_NODE_TYPES,
    TRY_NODE_TYPES,
    call_callee_segments,
)
from prism.parser.tree_sitter_loader import node_text

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

    def to_dict(self) -> dict:
        return {
            "call_kind": self.call_kind,
            "inside_loop": self.inside_loop,
            "inside_try_catch": self.inside_try_catch,
            "guarded_by_null_check": self.guarded_by_null_check,
            "args_passed_count": self.args_passed_count,
            "argument_flow": self.argument_flow,
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


def compute_call_site_context(call_node: Node, def_node: Node, lang: str, source: bytes) -> CallSiteContext:
    args_node = _args_node(call_node)
    args_count = len(args_node.named_children) if args_node is not None else 0
    return CallSiteContext(
        call_kind=_call_kind(call_node, def_node, lang),
        inside_loop=_inside_loop(call_node, def_node, lang, source),
        inside_try_catch=_inside_try_catch(call_node, def_node, lang),
        guarded_by_null_check=_guarded_by_null_check(call_node, def_node, lang, source),
        args_passed_count=args_count,
        argument_flow=_argument_flow(call_node, source),
    )
