"""v1.1 Axis 2: Form `F(v)` - syntactic structural primitives (the
function's own control-flow *shape*, independent of what it computes).

Five motifs get the audit's own literal, explicit classification rule
(`_classify_primary`); the remaining five (`FORM_VALIDATOR`,
`FORM_BATCH_LOOP`, `FORM_WRAPPED_TRY`, `FORM_RECURSIVE`,
`FORM_ASYNC_CONCURRENT`) are part of the bitmask this task's own
`bitmask.py` deliverable declares but weren't given an explicit rule in
the spec text - real, structural detection is implemented for each here
(not left as dead bits no code ever sets) and documented as this module's
own reasonable extension, same as every other place in this codebase an
audit's literal text under-specifies something its own deliverable list
still requires working.
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
from prism.semantics._ast_utils import (
    catch_nodes,
    conditional_nodes,
    count_statements,
    loop_nodes,
    returns_in_scope,
    top_level_statements,
    try_nodes,
)
from prism.semantics.bitmask import FeatureBit

#: A Go goroutine launch - the one construct on this axis that needs a
#: node type no shared `lang_config` table already carries (nothing else
#: in this codebase currently needs to recognize `go_statement`
#: specifically).
_GO_STATEMENT_TYPE = "go_statement"


def _statement_returns_or_raises(node: Node, lang: str) -> bool:
    if node.type == "return_statement":
        return True
    raise_type = RAISE_NODE_TYPE.get(lang)
    if raise_type and node.type == raise_type:
        return True
    # A block whose only reachable exit is itself a return/raise (an
    # `if cond: return x` where the guard's own body is a one-line block).
    return any(_statement_returns_or_raises(c, lang) for c in node.children if c.type not in ("{", "}"))


def _first_statement_is_guard(def_node: Node, lang: str) -> bool:
    statements = top_level_statements(def_node, lang)
    if not statements:
        return False
    first = statements[0]
    if first.type != "if_statement":
        return False
    consequence = first.child_by_field_name("consequence")
    if consequence is None:
        return False
    return _statement_returns_or_raises(consequence, lang)


def _language_try_types(lang: str) -> set[str]:
    from prism.parser.lang_config import TRY_NODE_TYPES

    return TRY_NODE_TYPES.get(lang, set())


def _has_retry_shape(def_node: Node, lang: str, parsed: ParsedFile) -> bool:
    """A loop that also contains a try/error-check *and* repeats a call -
    the audit's own literal rule ("loop contains a try block or error
    check and a repeated call"). Go has no try/except at all, so its
    "error check" half is a structural `if <cond mentioning "err">`
    instead."""
    call_type = CALL_NODE_TYPE.get(lang)
    try_types = _language_try_types(lang)
    for loop in loop_nodes(def_node, lang):
        has_guard = bool(try_types and iter_scoped_nodes(loop, try_types, lang))
        if not has_guard:
            # No try/except in this language (Go) - look for an
            # `if <cond involving "err"> ...` shaped error check instead.
            for cond in iter_scoped_nodes(loop, {"if_statement"}, lang):
                condition = cond.child_by_field_name("condition")
                if condition is not None and "err" in node_text(condition, parsed.source).lower():
                    has_guard = True
                    break
        if not has_guard:
            continue
        calls = iter_scoped_nodes(loop, {call_type}, lang) if call_type else []
        call_names = [
            tuple(call_callee_segments(c, parsed.source, lang) or [])
            for c in calls
        ]
        call_names = [c for c in call_names if c]
        if len(call_names) != len(set(call_names)):
            return True
        # Even distinct-looking call *sites* (different argument
        # expressions) repeating the same callee name across >=2 call
        # nodes inside the loop body count as "a repeated call".
        simple_names = [c[-1] for c in call_names]
        if len(simple_names) >= 2 and len(set(simple_names)) < len(simple_names):
            return True
    return False


def _has_pipeline_shape(def_node: Node, lang: str, parsed: ParsedFile) -> bool:
    """Multiple *consecutive* top-level statements where a later call's
    argument textually contains an earlier statement's own assigned
    variable name, in sequence - a chain `a = f(x); b = g(a); c = h(b)`."""
    statements = top_level_statements(def_node, lang)
    produced: list[str] = []
    chain_len = 0
    for stmt in statements:
        text = node_text(stmt, parsed.source)
        used_prior = bool(produced) and any(name in text for name in produced[-1:])
        # Track this statement's own assigned name, if any (`name = ...`).
        assign_target = None
        if stmt.type in ("expression_statement",) and stmt.named_children:
            inner = stmt.named_children[0]
            if inner.type in ("assignment", "assignment_expression"):
                target = inner.child_by_field_name("left") or inner.child_by_field_name("name")
                if target is not None and target.type in ("identifier",):
                    assign_target = node_text(target, parsed.source)
        elif stmt.type == "assignment":
            target = stmt.child_by_field_name("left")
            if target is not None and target.type == "identifier":
                assign_target = node_text(target, parsed.source)
        elif stmt.type == "short_var_declaration":
            left = stmt.child_by_field_name("left")
            if left is not None:
                assign_target = node_text(left, parsed.source).split(",")[0].strip()

        if used_prior:
            chain_len += 1
            if chain_len >= 2:
                return True
        else:
            chain_len = 0
        if assign_target:
            produced.append(assign_target)
    return False


def _has_branch_dispatch_shape(def_node: Node, lang: str, parsed: ParsedFile) -> bool:
    conditionals = conditional_nodes(def_node, lang)
    if len(conditionals) <= 3:
        return False
    call_type = CALL_NODE_TYPE.get(lang)
    if not call_type:
        return False
    invoked: set[str] = set()
    for cond in conditionals:
        consequence = cond.child_by_field_name("consequence")
        if consequence is None:
            continue
        for call in iter_scoped_nodes(consequence, {call_type}, lang, is_root=True):
            segments = call_callee_segments(call, parsed.source, lang)
            if segments:
                invoked.add(segments[-1])
    return len(invoked) >= 2


def _is_recursive(def_node: Node, qualified_name: str, lang: str, parsed: ParsedFile) -> bool:
    simple_name = qualified_name.rsplit(".", 1)[-1]
    call_type = CALL_NODE_TYPE.get(lang)
    if not call_type:
        return False
    for call in iter_scoped_nodes(def_node, {call_type}, lang):
        segments = call_callee_segments(call, parsed.source, lang)
        if segments and segments[-1] == simple_name:
            return True
    return False


def _is_async_concurrent(def_node: Node, lang: str, parsed: ParsedFile) -> bool:
    async_types = ASYNC_KEYWORD_NODE_TYPES.get(lang, set())
    if async_types:
        for candidate in (def_node, def_node.parent):
            if candidate is None:
                continue
            if any(c.type in async_types for c in candidate.children):
                return True
    if lang == LanguageID.GO and iter_scoped_nodes(def_node, {_GO_STATEMENT_TYPE}, lang):
        return True
    call_type = CALL_NODE_TYPE.get(lang)
    if call_type:
        concurrency_names = {"gather", "all", "settled", "thread", "spawn"}
        for call in iter_scoped_nodes(def_node, {call_type}, lang):
            segments = call_callee_segments(call, parsed.source, lang)
            if segments and segments[-1].lower() in concurrency_names:
                return True
    return False


def extract_form(def_node: Node, parsed: ParsedFile, qualified_name: str) -> FeatureBit:
    lang = parsed.language_id
    total = count_statements(def_node, lang)

    n_loops = len(loop_nodes(def_node, lang))
    n_conditionals = len(conditional_nodes(def_node, lang))
    n_try = len(try_nodes(def_node, lang))
    n_returns = len(returns_in_scope(def_node, lang))

    loop_density = n_loops / total
    branch_density = n_conditionals / total
    exception_density = n_try / total
    early_return_density = n_returns / total

    bits = FeatureBit(0)

    # -- The audit's own literal, explicit primary classification ---------- #
    # Priority order is not fully pinned down by the spec text, so it's
    # resolved here by specificity: FORM_BRANCH_DISPATCH's own rule
    # (>3 conditionals *and* >=2 distinct callees across their bodies) is
    # a strictly stronger, rarer structural signal than "the first
    # statement happens to be an early-return if" - a real dispatch
    # function's *first* branch is almost always itself an early-return
    # arm, which would otherwise misfire FORM_GUARD_EARLY_EXIT before
    # FORM_BRANCH_DISPATCH's own, more specific rule ever gets checked
    # (confirmed directly against a synthetic 4-arm dispatch fixture).
    if _has_retry_shape(def_node, lang, parsed):
        bits |= FeatureBit.FORM_RETRY_LOOP
    elif _has_branch_dispatch_shape(def_node, lang, parsed):
        bits |= FeatureBit.FORM_BRANCH_DISPATCH
    elif _first_statement_is_guard(def_node, lang):
        bits |= FeatureBit.FORM_GUARD_EARLY_EXIT
    elif _has_pipeline_shape(def_node, lang, parsed):
        bits |= FeatureBit.FORM_PIPELINE
    else:
        bits |= FeatureBit.FORM_LINEAR

    # -- This module's own reasonable extension for the remaining five ----- #
    if n_loops == 0 and early_return_density >= 0.4 and n_conditionals >= 2 and not (bits & FeatureBit.FORM_BRANCH_DISPATCH):
        bits |= FeatureBit.FORM_VALIDATOR
    if n_loops > 0 and not (bits & FeatureBit.FORM_RETRY_LOOP):
        bits |= FeatureBit.FORM_BATCH_LOOP
    if n_try > 0 and exception_density >= 0.3 and not (bits & FeatureBit.FORM_RETRY_LOOP):
        bits |= FeatureBit.FORM_WRAPPED_TRY
    if _is_recursive(def_node, qualified_name, lang, parsed):
        bits |= FeatureBit.FORM_RECURSIVE
    if _is_async_concurrent(def_node, lang, parsed):
        bits |= FeatureBit.FORM_ASYNC_CONCURRENT

    return bits


def compute_form_bits(builder) -> dict[str, int]:
    result: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        result[symbol.qualified_name] = int(extract_form(def_node, parsed, symbol.qualified_name))
    return result
