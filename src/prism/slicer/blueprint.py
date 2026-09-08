"""Canonical Structural Blueprint Injection.

Semantic facts (contracts, call-site synonyms, flow-engine effect
distributions) tell an LLM *what* a symbol does and *what it's connected
to* - none of that says anything about the concrete syntactic shape a
correct addition to this specific package should take. Syntactic
Conformance plateaued at ~3.9/5.0 in this benchmark's own comparison
report for exactly that reason: Prism was handing over semantics without
a structural skeleton to match local convention against.

This module mines that skeleton directly from real, already-indexed
sibling code - every other function/method Prism's own AST parser already
processed in the target symbol's own directory - rather than asking a
model to infer "the local idiom" from a handful of full function bodies
it happens to have been shown. Two structural patterns are mined, both
common, load-bearing idioms across the languages this project supports:

  - **Guard**: a function's own *first* statement is a conditional whose
    body returns/raises/panics early (`if err := f(); err != nil {
    return err }`, `if not x: raise ValueError(...)`) - an early-exit
    validation/error-check idiom.
  - **Delegate**: a function's own *last* statement is a bare `return
    <call>(...)` - a thin pass-through to another function.

Each is reported as whichever concrete rendering occurred most often
across the sibling set (a real majority-vote mode, not a synthesized
"average") - ties broken by encounter order for determinism. A directory
with no siblings of the target's own kind, or none exhibiting either
pattern, yields no blueprint at all (`None`) rather than fabricating one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import (
    CALL_NODE_TYPE,
    CONDITIONAL_NODE_TYPES,
    RAISE_NODE_TYPE,
    RETURN_STATEMENT_NODE_TYPE,
    find_all,
)
from prism.parser.tree_sitter_loader import node_text

#: How many sibling definitions (in the target's own directory, of the
#: same kind - function or method) get inspected before mining stops -
#: bounded so this stays a cheap, single-query-time pass even in a large
#: directory, not an unbounded scan of the whole repository.
MAX_SIBLINGS = 20
MAX_LINE_LENGTH = 90


@dataclass(frozen=True)
class BlueprintPattern:
    label: str  # "Guard" | "Delegate"
    text: str
    occurrence_count: int


@dataclass
class StructuralBlueprint:
    sibling_count: int
    patterns: list[BlueprintPattern] = field(default_factory=list)


def _first_line(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    line = stripped.splitlines()[0]
    return line if len(line) <= MAX_LINE_LENGTH else line[: MAX_LINE_LENGTH - 3] + "..."


def _body_statements(def_node) -> list:
    body = def_node.child_by_field_name("body")
    if body is None:
        return []
    # Go's `block` wraps its real statements one level deeper, inside a
    # `statement_list` child - every other supported language's body node
    # holds its statements directly.
    container = body
    for child in body.children:
        if child.type == "statement_list":
            container = child
            break
    return list(container.named_children)


def _contains_early_exit(node, lang: str) -> bool:
    exit_types = {RETURN_STATEMENT_NODE_TYPE}
    raise_type = RAISE_NODE_TYPE.get(lang)
    if raise_type:
        exit_types.add(raise_type)
    return bool(find_all(node, exit_types))


def _guard_candidate(def_node, source: bytes, lang: str) -> str | None:
    statements = _body_statements(def_node)
    if not statements:
        return None
    first = statements[0]
    if first.type not in CONDITIONAL_NODE_TYPES.get(lang, set()):
        return None
    consequence = first.child_by_field_name("consequence")
    if consequence is None or not _contains_early_exit(consequence, lang):
        return None
    return _first_line(node_text(first, source))


def _delegate_candidate(def_node, source: bytes, lang: str) -> str | None:
    statements = _body_statements(def_node)
    if not statements:
        return None
    last = statements[-1]
    if last.type != RETURN_STATEMENT_NODE_TYPE:
        return None
    call_type = CALL_NODE_TYPE.get(lang)
    if call_type and find_all(last, {call_type}):
        return _first_line(node_text(last, source))
    return None


def mine_sibling_blueprint(builder: ConcreteGraphBuilder, target_symbol: str) -> StructuralBlueprint | None:
    """Mines the Guard/Delegate canonical patterns from every sibling
    definition (same directory, same kind, excluding the target itself)
    Prism already indexed - no extra parsing beyond what indexing already
    did. Returns `None` when there are no eligible siblings, or none of
    them exhibit either pattern - a caller (the serializer) treats that
    as "nothing to inject", not an error.
    """
    target = builder.symbol_table.get(target_symbol)
    if target is None or target.kind not in ("function", "method"):
        return None
    target_dir = os.path.dirname(target.file)

    guard_candidates: dict[str, int] = {}
    delegate_candidates: dict[str, int] = {}
    sibling_count = 0

    for symbol in builder.symbol_table:
        if sibling_count >= MAX_SIBLINGS:
            break
        if symbol.qualified_name == target_symbol or symbol.kind != target.kind:
            continue
        if os.path.dirname(symbol.file) != target_dir:
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        sibling_count += 1

        guard = _guard_candidate(def_node, parsed.source, symbol.language_id)
        if guard:
            guard_candidates[guard] = guard_candidates.get(guard, 0) + 1
        delegate = _delegate_candidate(def_node, parsed.source, symbol.language_id)
        if delegate:
            delegate_candidates[delegate] = delegate_candidates.get(delegate, 0) + 1

    if sibling_count == 0:
        return None

    patterns: list[BlueprintPattern] = []
    if guard_candidates:
        text, count = max(guard_candidates.items(), key=lambda kv: kv[1])
        patterns.append(BlueprintPattern("Guard", text, count))
    if delegate_candidates:
        text, count = max(delegate_candidates.items(), key=lambda kv: kv[1])
        patterns.append(BlueprintPattern("Delegate", text, count))
    if not patterns:
        return None
    return StructuralBlueprint(sibling_count=sibling_count, patterns=patterns)
