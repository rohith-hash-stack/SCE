"""Phase G, Issue #32 / Invariant 2: symbol location - resolving either
a `(file_path, line_no)` coordinate or a bare (unqualified) name into a
real, canonical qualified name from `builder.symbol_table`.

No prior coordinate-to-symbol resolver existed anywhere in this codebase
before this module (confirmed by search: `prism.runtime.tracer.
resolve_qualified_name` solves an adjacent but different problem - a
*live* Python interpreter frame's own `co_firstlineno`/`co_qualname`,
not a static `symbol_table` lookup a CLI/MCP caller with a bare file:line
string can use). Bare-name lookup reuses `prism.packer.submodular_
knapsack.suggest_similar_seeds` (the same fuzzy "did you mean" this
codebase's own `SeedNotFoundError` already uses) rather than a second,
independent fuzzy-matching implementation.
"""
from __future__ import annotations

import os

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.submodular_knapsack import suggest_similar_seeds
from prism.query.errors import SymbolNotFoundError

#: `SeedNotFoundError`'s own default - reused here for the same reason:
#: a weak "did you mean" match is worse than no suggestion at all.
DEFAULT_CANDIDATE_TOP_K = 5


def locate_symbol_at(builder: ConcreteGraphBuilder, file_path: str, line_no: int) -> str | None:
    """The most specific (smallest line-span) real symbol in `builder.
    symbol_table` whose own `line_range` encloses `line_no` in
    `file_path` - a nested `def inner(): ...` inside `class Outer:`
    resolves to `inner`, never `Outer`, whenever `line_no` falls inside
    both (every real symbol's own line_range is a real AST span, so a
    smaller span is definitionally the more specific, more deeply
    nested one - no separate parent/child walk needed).

    Returns `None`, never raises, when nothing encloses `line_no` (a
    blank line, an import, a comment, a line past the end of the file) -
    a real, common, non-exceptional outcome for a real coordinate a
    caller might supply (an editor cursor position, a stack frame from
    an untraced line), not a bug to except around every time.
    """
    target = os.path.realpath(os.path.abspath(file_path))
    best: str | None = None
    best_span: int | None = None
    for info in builder.symbol_table:
        if os.path.realpath(os.path.abspath(info.file)) != target:
            continue
        start, end = info.line_range
        if not (start <= line_no <= end):
            continue
        span = end - start
        if best_span is None or span < best_span or (span == best_span and info.qualified_name < best):
            best = info.qualified_name
            best_span = span
    return best


def locate_symbol_by_name(
    builder: ConcreteGraphBuilder, name: str, top_k: int = DEFAULT_CANDIDATE_TOP_K
) -> str:
    """Resolves a bare (unqualified, e.g. `"execute"`) or already-fully-
    qualified name to exactly one real qualified name.

    A name already in `builder.symbol_table` (a fully-qualified name, or
    a bare name that also happens to be a real top-level qualified name)
    resolves directly. Otherwise, every symbol whose own unqualified
    name (`qualified_name.rsplit(".", 1)[-1]`) matches `name` exactly is
    a candidate: exactly one resolves directly; anything else (zero, or
    more than one - a real, genuine ambiguity, e.g. two different
    classes each defining their own `execute`) raises
    `SymbolNotFoundError` carrying `.candidates` - up to `top_k` ranked
    qualified names for the caller to disambiguate with, fuzzy-matched
    (`suggest_similar_seeds`) when there are zero exact unqualified
    matches, or the real ambiguous matches themselves (sorted,
    deterministic) when there are more than one - "ranked candidates"
    either way, never a silent guess.
    """
    if name in builder.symbol_table:
        return name

    exact_unqualified_matches = sorted(
        info.qualified_name for info in builder.symbol_table if info.qualified_name.rsplit(".", 1)[-1] == name
    )
    if len(exact_unqualified_matches) == 1:
        return exact_unqualified_matches[0]
    if len(exact_unqualified_matches) > 1:
        raise SymbolNotFoundError(name, candidates=exact_unqualified_matches[:top_k])

    candidates = suggest_similar_seeds(builder, name, top_k=top_k)
    raise SymbolNotFoundError(name, candidates=candidates)
