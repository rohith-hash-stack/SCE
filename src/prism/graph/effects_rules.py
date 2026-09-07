"""Deterministic call-sink -> effect-taxonomy mapping, plus thrown-exception
extraction, for `prism.graph.contracts`.

Same design as `prism.tagger.rules`'s `CallSinkRule` table (a pure,
import-corroborated method-name predicate over one function/method's own
subtree) - deliberately reusing that pattern rather than inventing a
second one, since this is structurally the same kind of deterministic
call-sink classification, just against a different, smaller vocabulary:
the six effect flags `BehavioralContract.effects` declares itself against
(`DOM_MUTATION`, `DOM_READ`, `DISK_IO`, `NETWORK_HTTP`, `ASSERTS`,
`ASYNC_WAIT`).
"""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from prism.parser.lang_config import (
    ASSERT_NODE_TYPE,
    AWAIT_NODE_TYPES,
    CALL_NODE_TYPE,
    RAISE_NODE_TYPE,
    call_callee_segments,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text


@dataclass(frozen=True)
class EffectRule:
    effect: str
    method_patterns: tuple[str, ...]
    # Case-insensitive substring match against the call's full receiver
    # chain (e.g. "page.locator" or "fs.readFileSync"), not just the
    # trailing method name - several effect sinks (DOM vs. disk) share
    # generic trailing names (`read`, `write`) that only mean something
    # once the receiver is known, unlike the tag sinks in tagger/rules.py
    # (which corroborate via a *file-level import*, not the call's own
    # receiver text - effects need the tighter check since a function can
    # freely mix DOM and disk calls a shared import wouldn't disambiguate).
    receiver_patterns: tuple[str, ...] = ()


EFFECT_RULES: tuple[EffectRule, ...] = (
    EffectRule(
        "NETWORK_HTTP",
        method_patterns=("fetch", "get", "post", "put", "delete", "patch", "request", "send"),
        receiver_patterns=("fetch", "axios", "http", "https", "requests", "httpx", "aiohttp", "urllib", "$http"),
    ),
    EffectRule(
        "DISK_IO",
        method_patterns=("open", "read", "write", "readfile", "writefile", "readfilesync", "writefilesync", "mkdir", "unlink", "exists"),
        receiver_patterns=("fs", "os", "path", "pathlib", "io"),
    ),
    EffectRule(
        "DOM_MUTATION",
        method_patterns=(
            "click", "fill", "check", "uncheck", "type", "press", "settext", "setattribute",
            "appendchild", "removechild", "remove", "insertadjacenthtml", "focus", "blur",
            "selectoption", "dragto", "hover", "tap", "scrollintoview", "setitem", "clear",
        ),
        receiver_patterns=("page", "locator", "element", "document", "localstorage", "sessionstorage"),
    ),
    EffectRule(
        "DOM_READ",
        method_patterns=(
            "textcontent", "innertext", "innerhtml", "getattribute", "queryselector",
            "queryselectorall", "getbytestid", "getbyrole", "getbytext", "getbylabel",
            "locator", "isvisible", "isenabled", "ischecked", "getitem", "count",
        ),
        receiver_patterns=("page", "locator", "element", "document", "localstorage", "sessionstorage"),
    ),
)

# `expect(...)` (Playwright/Jest/Chai-style assertion entrypoints) and
# Python's own `assert` statement both mark ASSERTS - handled separately
# below rather than as an EffectRule, since `expect` is a bare call name
# (no receiver) and `assert` is a statement, not a call, in Python.
_ASSERT_CALL_NAMES = frozenset({"expect", "assert_", "assertequal", "asserttrue", "assertfalse"})


def classify_effects(def_node: Node, parsed: ParsedFile) -> tuple[list[str], list[str]]:
    """Returns `(effects, thrown_exceptions)` for one function/method body -
    computed together since both are found by the same single walk over
    the body's call sites and raise/throw statements.
    """
    lang = parsed.language_id
    src = parsed.source
    effects: set[str] = set()
    thrown: set[str] = set()

    call_type = CALL_NODE_TYPE.get(lang)
    if call_type:
        for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
            segments = call_callee_segments(call_node, src, lang)
            if not segments:
                continue
            method = segments[-1].lower()
            receiver = ".".join(segments[:-1]).lower()
            if method in _ASSERT_CALL_NAMES:
                effects.add("ASSERTS")
                continue
            for rule in EFFECT_RULES:
                if method not in rule.method_patterns:
                    continue
                if not rule.receiver_patterns or any(p in receiver for p in rule.receiver_patterns):
                    effects.add(rule.effect)

    assert_type = ASSERT_NODE_TYPE.get(lang)
    if assert_type and iter_scoped_nodes(def_node, {assert_type}, lang):
        effects.add("ASSERTS")
        thrown.add("AssertionError")

    await_types = AWAIT_NODE_TYPES.get(lang, set())
    if await_types and iter_scoped_nodes(def_node, await_types, lang):
        effects.add("ASYNC_WAIT")

    raise_type = RAISE_NODE_TYPE.get(lang)
    if raise_type:
        for raise_node in iter_scoped_nodes(def_node, {raise_type}, lang):
            name = _exception_type_name(raise_node, parsed)
            if name:
                thrown.add(name)

    return sorted(effects), sorted(thrown)


def _exception_type_name(raise_node: Node, parsed: ParsedFile) -> str | None:
    """The exception/error type name a `raise`/`throw` statement names -
    `raise TimeoutError("x")` / `throw new TimeoutError("x")` -> `"TimeoutError"`;
    a bare re-raise (`raise` alone) or a raised non-call expression (rare -
    `raise err` re-raising a caught variable) yields None rather than a
    guess, since neither actually names a type in the AST.
    """
    lang = parsed.language_id
    call_type = CALL_NODE_TYPE.get(lang)
    for child in raise_node.children:
        if child.type in ("raise", "from", "throw", ";"):
            continue
        if child.type == call_type:
            segments = call_callee_segments(child, parsed.source, lang)
            return segments[-1] if segments else None
        if child.type == "object_creation_expression":  # Java/C#: `throw new X(...)`
            type_node = child.child_by_field_name("type")
            return node_text(type_node, parsed.source).split("<")[0].strip() if type_node is not None else None
        if child.type == "new_expression":  # JS/TS: `throw new X(...)`
            ctor = child.child_by_field_name("constructor")
            if ctor is not None:
                return node_text(ctor, parsed.source)
        return None
    return None
