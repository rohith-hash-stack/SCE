"""Formal Precision Tiers (Issues #1/#2/#3): explicit, documented feature
parity levels across the languages Prism indexes, replacing the informal
README claim of uniform support with an accurate, checkable classification.

Issue B2 (post-implementation audit): a single linear Tier 1/2/3 ranking
implies one monotonic degradation axis, which is not literally true per
feature - see README.md's Language Capability Matrix for the precise,
per-feature picture this module's three tiers are a coarse summary of
(useful as a quick `--language-tier` filter, not a substitute for that
table). In particular, Go gained a real receiver/parameter-typed call-
resolution mechanism of its own with Issue B1 (narrower than Python's
constructor-based instance binding, but real) while still correctly
sitting in Tier 3 here - it still has zero inheritance-relation edges
and no barrel/re-export resolution (Go has no re-export syntax at all).

Python gets full AST instance binding, Rule A-D reference-chain
resolution, and relative-import/barrel-file resolution
(`prism.graph.concrete_builder`/`prism.graph.symbol_table`) - a
qualitatively different, more precise pass than every other language gets.
JavaScript/TypeScript/TSX get CST-based structural linking (ES module
import/export parsing including barrel re-exports, `EXTENDS`/
`IMPLEMENTS` class-relation edges, JSDoc/decorator parsing) without
instance-based binding. Go gets CST-based package-level linking (own
import/package resolution, `//go:...` compiler-directive and struct-tag
capture - see `prism.parser.queries.GO_QUERIES["decorators"]`) without
class-relation edges (Go has no classes) or constructor-based instance
binding. Java/C# sit between Tier 2 and Tier 1 in practice (they do get
instance-based constructor binding - see `concrete_builder.py`'s own
`instance_binding_langs` - but not `EXTENDS`/`IMPLEMENTS` linking or
barrel-file resolution) - classified as Tier 2 here since that gap is
real and worth surfacing, not because their support is identical to
JS/TS's own.
"""
from __future__ import annotations

import enum

from prism.parser.tree_sitter_loader import LanguageID


class PrecisionTier(str, enum.Enum):
    #: Semantic & Instance Precision - full AST instance binding, Rule
    #: A-D reference-chain resolution, relative-import/barrel-file
    #: resolution.
    TIER_1_SEMANTIC = "tier1"
    #: Structural & Lexical - CST-based linking (imports/exports, class
    #: relations where applicable), no instance-based binding precision.
    TIER_2_STRUCTURAL = "tier2"
    #: Lexical & Package-level - CST-based, package/import resolution and
    #: language-specific annotation capture only; no class-relation edges,
    #: no instance binding.
    TIER_3_LEXICAL = "tier3"


LANGUAGE_PRECISION_TIER: dict[str, PrecisionTier] = {
    LanguageID.PYTHON: PrecisionTier.TIER_1_SEMANTIC,
    LanguageID.JAVASCRIPT: PrecisionTier.TIER_2_STRUCTURAL,
    LanguageID.TYPESCRIPT: PrecisionTier.TIER_2_STRUCTURAL,
    LanguageID.TSX: PrecisionTier.TIER_2_STRUCTURAL,
    LanguageID.JAVA: PrecisionTier.TIER_2_STRUCTURAL,
    LanguageID.CSHARP: PrecisionTier.TIER_2_STRUCTURAL,
    LanguageID.GO: PrecisionTier.TIER_3_LEXICAL,
}

#: Human-readable one-line description per tier, for `prism index
#: --language-tier` help text and any status/reporting output.
PRECISION_TIER_LABELS: dict[PrecisionTier, str] = {
    PrecisionTier.TIER_1_SEMANTIC: "Tier 1 (Semantic & Instance Precision)",
    PrecisionTier.TIER_2_STRUCTURAL: "Tier 2 (Structural & Lexical)",
    PrecisionTier.TIER_3_LEXICAL: "Tier 3 (Lexical & Package-level)",
}

#: `--language-tier tier1-only` accepts only languages at this precision
#: level - the only tier this codebase currently has, since Tier 1 has
#: exactly one member (Python). Kept as a set (not a bare equality check)
#: so a future second Tier-1 language needs no call-site changes.
TIER_1_ONLY_LANGUAGES: frozenset[str] = frozenset(
    lang for lang, tier in LANGUAGE_PRECISION_TIER.items() if tier == PrecisionTier.TIER_1_SEMANTIC
)


def precision_tier_for(language_id: str) -> PrecisionTier | None:
    """The `PrecisionTier` for a known `LanguageID`, or `None` for a
    language this mapping hasn't classified (should not happen for any
    language `EXTENSION_LANGUAGE_MAP` maps to - see the module's own
    `test_language_tiers.py` coverage check)."""
    return LANGUAGE_PRECISION_TIER.get(language_id)
