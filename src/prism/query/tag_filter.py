"""Phase G, Issue #33 / Invariant 3: boolean tag-expression filtering
(`"#network AND NOT #test"`) over a symbol's real four-axis feature mask
(`prism.semantics.bitmask.FeatureBit`).

**A deliberate choice of which "tag" system to extend, documented since
a second, real one already exists**: `prism.mcp.server.find_symbols_by_
tag` already does single-tag lookup, but against the *older* `tag_matrix`/
`TaggingEngine`/metamodel system tied to the `D_hybrid`/
`ContextKnapsackPacker` engine (`prism query`, not `prism causal-query`) -
a different tag vocabulary (`#route_handler`, `#db_write`, ...) from a
different, independently-registered source. Every phase of this session
(A through F) has built on the *other*, newer v1.1+ four-axis model
(`prism.semantics.bitmask.FeatureBit`, already what `prism.surface.
models.NodeFeatures.substance`/`.role` render into the public XML
envelope), so this module extends *that* system - reusing its own real
bit names as the tag vocabulary - rather than the older `tag_matrix`
`find_symbols_by_tag` already owns. The two are not merged or touched by
each other here.

**A second real reuse, not a reinvention**: the brief's own worked
example (`"#network AND NOT #test"`) names `#test`, which is not a
substance/role/form/output tag at all - no symbol is ever "of substance
test". `#test` is special-cased against `prism.scanner.entrypoint_gate.
is_test_file` (the real, already-tested Phase A test-file convention),
applied to the *candidate symbol's own file path* - the same real
signal `prism.packer.submodular_knapsack._is_never_pipeline_module`
already uses for its own "never part of a causal pipeline" rule,
reused here rather than a third, independent test-file heuristic.

Grammar (standard boolean precedence, NOT > AND > OR, left-associative,
parenthesized sub-expressions supported)::

    expr     := or_expr
    or_expr  := and_expr (OR and_expr)*
    and_expr := not_expr (AND not_expr)*
    not_expr := NOT not_expr | atom
    atom     := TAG | '(' expr ')'

Any tag name this module's own `_TAG_BIT_LOOKUP` doesn't recognize (and
isn't `#test`) evaluates to `False` - an unknown tag never matches
anything, per Invariant 3, rather than raising or silently matching
everything.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from prism.query.errors import QueryValidationError
from prism.scanner.entrypoint_gate import is_test_file
from prism.semantics.bitmask import FeatureBit

_AXIS_PREFIXES = ("SINK_", "FORM_", "OUTPUT_", "ROLE_")


def _strip_axis_prefix(bit_name: str) -> str:
    for prefix in _AXIS_PREFIXES:
        if bit_name.startswith(prefix):
            return bit_name[len(prefix):]
    return bit_name


def _build_tag_bit_lookup() -> dict[str, FeatureBit]:
    """Every real `FeatureBit` member, keyed by its own name lowercased
    with the axis prefix stripped (`SINK_NETWORK_IO` -> `"network_io"`),
    plus a second, `_io`-stripped alias for the SINK axis
    (`"network"` -> the same bit) - matching the brief's own literal
    `#network`/`#database` example wording, which never spells the `_io`
    suffix out."""
    lookup: dict[str, FeatureBit] = {}
    for bit in FeatureBit:
        if not bit.name:
            continue
        stripped = _strip_axis_prefix(bit.name).lower()
        lookup[stripped] = bit
        if stripped.endswith("_io"):
            lookup.setdefault(stripped[: -len("_io")], bit)
    return lookup


_TAG_BIT_LOOKUP = _build_tag_bit_lookup()

TagLookup = Callable[[str], bool]


# --------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Tag:
    name: str


@dataclass(frozen=True)
class _Not:
    expr: "_Node"


@dataclass(frozen=True)
class _And:
    left: "_Node"
    right: "_Node"


@dataclass(frozen=True)
class _Or:
    left: "_Node"
    right: "_Node"


_Node = Union[_Tag, _Not, _And, _Or]


def _evaluate(node: _Node, tag_lookup: TagLookup) -> bool:
    if isinstance(node, _Tag):
        return tag_lookup(node.name)
    if isinstance(node, _Not):
        return not _evaluate(node.expr, tag_lookup)
    if isinstance(node, _And):
        return _evaluate(node.left, tag_lookup) and _evaluate(node.right, tag_lookup)
    if isinstance(node, _Or):
        return _evaluate(node.left, tag_lookup) or _evaluate(node.right, tag_lookup)
    raise TypeError(f"unreachable tag-expression node type: {type(node)!r}")  # pragma: no cover


# --------------------------------------------------------------------- #
# Tokenizer & parser
# --------------------------------------------------------------------- #
_KEYWORDS = {"AND", "OR", "NOT"}


def _tokenize(expression: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(expression)
    while i < n:
        ch = expression[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            tokens.append(ch)
            i += 1
            continue
        if ch == "#":
            j = i + 1
            while j < n and (expression[j].isalnum() or expression[j] == "_"):
                j += 1
            if j == i + 1:
                raise QueryValidationError(f"empty tag name at position {i} in tag_filter {expression!r}")
            tokens.append(expression[i:j])
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (expression[j].isalnum() or expression[j] == "_"):
                j += 1
            tokens.append(expression[i:j])
            i = j
            continue
        raise QueryValidationError(f"unexpected character {ch!r} at position {i} in tag_filter {expression!r}")
    return tokens


class _Parser:
    """A small, hand-rolled recursive-descent parser - deliberately not
    a grammar-generator dependency for a 4-production grammar this
    small."""

    def __init__(self, tokens: list[str], original: str) -> None:
        self._tokens = tokens
        self._pos = 0
        self._original = original

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> str:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self) -> _Node:
        node = self._or_expr()
        if self._peek() is not None:
            raise QueryValidationError(f"unexpected trailing token {self._peek()!r} in tag_filter {self._original!r}")
        return node

    def _or_expr(self) -> _Node:
        node = self._and_expr()
        while self._peek() == "OR":
            self._advance()
            node = _Or(node, self._and_expr())
        return node

    def _and_expr(self) -> _Node:
        node = self._not_expr()
        while self._peek() == "AND":
            self._advance()
            node = _And(node, self._not_expr())
        return node

    def _not_expr(self) -> _Node:
        if self._peek() == "NOT":
            self._advance()
            return _Not(self._not_expr())
        return self._atom()

    def _atom(self) -> _Node:
        tok = self._peek()
        if tok is None:
            raise QueryValidationError(f"unexpected end of tag_filter {self._original!r}")
        if tok == "(":
            self._advance()
            node = self._or_expr()
            if self._peek() != ")":
                raise QueryValidationError(f"unbalanced parentheses in tag_filter {self._original!r}")
            self._advance()
            return node
        if tok in _KEYWORDS or tok == ")":
            raise QueryValidationError(f"unexpected token {tok!r} in tag_filter {self._original!r}")
        self._advance()
        name = tok[1:] if tok.startswith("#") else tok
        return _Tag(name.lower())


def parse_tag_filter(expression: str) -> _Node:
    """Parses `expression` into an AST, raising `QueryValidationError`
    (never a bare stdlib exception - Invariant 4) for anything
    malformed: an empty tag, an unbalanced parenthesis, a trailing or
    missing operand."""
    if not expression or not expression.strip():
        raise QueryValidationError("tag_filter expression must not be empty")
    tokens = _tokenize(expression)
    return _Parser(tokens, expression).parse()


def evaluate_tag_filter(expression: str, tag_lookup: TagLookup) -> bool:
    """Parses and evaluates `expression` in one call against `tag_lookup`
    (a callable a caller supplies - see `feature_mask_tag_lookup` below
    for the real, four-axis-backed one)."""
    return _evaluate(parse_tag_filter(expression), tag_lookup)


# --------------------------------------------------------------------- #
# The real, four-axis-backed tag lookup
# --------------------------------------------------------------------- #
def feature_mask_tag_lookup(mask: int, is_test: bool = False) -> TagLookup:
    """Builds the real `tag_lookup` callable `evaluate_tag_filter` needs,
    backed by a symbol's own four-axis `feature_mask` plus the one
    special-cased structural tag, `#test`/`test` (see this module's own
    top-level docstring for why that one isn't a `FeatureBit` at all).
    An unrecognized tag name always evaluates to `False`."""

    def _lookup(name: str) -> bool:
        if name == "test":
            return is_test
        bit = _TAG_BIT_LOOKUP.get(name)
        if bit is None:
            return False
        return bool(mask & int(bit))

    return _lookup


def matches_tag_filter_for_symbol(expression: str, qname: str, feature_masks: dict[str, int], symbol_info) -> bool:
    """The real, end-to-end convenience form: evaluates `expression`
    against `qname`'s own real feature mask (`feature_masks`, e.g.
    `prism.semantics.extractor.compute_feature_masks_cached`'s own
    output) and its own file path (`symbol_info`, a `SymbolInfo`
    lookup - `builder.symbol_table.get`) for the `#test` special case."""
    info = symbol_info(qname)
    is_test = info is not None and is_test_file(info.file)
    mask = feature_masks.get(qname, 0)
    return evaluate_tag_filter(expression, feature_mask_tag_lookup(mask, is_test=is_test))
