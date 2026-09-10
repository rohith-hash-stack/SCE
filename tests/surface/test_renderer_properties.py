"""Property-based tests (hypothesis) for `prism.surface.renderer.render`
and `prism.surface.parser.parse_context`:

  - **Determinism**: `render(pkg, opts) == render(pkg, opts)` for any
    generated `pkg`/`opts`.
  - **Roundtrip Fidelity**: `parse_context(render(pkg)) == pkg`, for a
    `pkg` already in the *canonical* order the renderer itself sorts
    into (`render` is a canonicalizing serializer, not merely one that
    preserves input order - the hypothesis strategy pre-sorts nodes/
    edges/warnings via the renderer's own sort keys so this is a
    meaningful test of information preservation, not an accidental
    ordering mismatch), with `budget.tokens` fixed large enough that
    `render`'s own `BUDGET_OVERFLOW` auto-injection (a real, intentional,
    one-way transformation - see `renderer.py`'s own docstring) never
    fires and silently adds a warning the original `pkg` never had.
  - **XML Well-Formedness**: `ElementTree.fromstring(render(pkg))` never
    raises, for any generated `pkg`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageGap,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    EnvelopeWarning,
    FeatureCoverage,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeContract,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    NodeSignatureParam,
    NodeSignatureReturn,
    SeedRef,
)
from prism.surface.parser import parse_context
from prism.surface.renderer import _COMPRESSION_LEVELS, RenderOptions, _sorted_edges, _sorted_nodes, _sorted_warnings, render

# --------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------- #
#: Every code point XML 1.0 can legally carry, *and* that `render`
#: preserves byte-for-byte - tab/LF plus everything outside the C0
#: control range, surrogates excluded. `\r` is deliberately excluded too,
#: even though XML 1.0 permits it: `render`'s own final normalization
#: pass (`"Line endings normalized strictly to \n"`, spec-mandated) does
#: a global `\r` -> `\n` replace across the *entire* rendered document,
#: not just body content, so a `\r` anywhere - a warning message, an
#: identifier - is a real, intentional, one-way transformation, the same
#: category as `BUDGET_OVERFLOW` auto-injection or canonical ordering,
#: not something Roundtrip Fidelity should fight.
_xml_safe_char = st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f")
_text = st.text(alphabet=_xml_safe_char, max_size=60)
_nonempty_text = st.text(alphabet=_xml_safe_char, min_size=1, max_size=40)
_ident = st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_."), min_size=1, max_size=30)
_safe_float = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)
_safe_int = st.integers(min_value=-1_000_000, max_value=1_000_000)

_compression_level = st.sampled_from(["L0_full", "L1_pruned", "L2_skeleton", "L3_alias"])
_gap_reason = st.sampled_from(["no_candidate_in_budget", "no_candidate_reachable", "filtered_by_policy", "covered_transitively"])
_warning_code = st.sampled_from(
    ["BUDGET_OVERFLOW", "TOKENIZER_FALLBACK", "ORPHANED_RUNTIME_EVENTS", "GRAPH_INCOMPLETE", "LANGUAGE_TIER_2",
     "LANGUAGE_TIER_3", "RE_EXPORT_UNRESOLVED", "DYNAMIC_ATTRIBUTES_DETECTED", "CACHE_STALE", "SCHEMA_VERSION_MISMATCH"]
)
_severity = st.sampled_from(["low", "medium", "high"])
_node_role = st.sampled_from(["seed", "callee", "caller", "transitive"])
_edge_type = st.sampled_from(["CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"])

_detail_value = st.one_of(_text, _safe_int, st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6))

_engine = st.builds(EngineRef, name=_ident, version=_ident, commit=_ident)
_seed_ref = st.builds(SeedRef, symbol=_ident, file=_ident, line=_safe_int)
_budget_ref_free = st.builds(BudgetRef, tokens=st.integers(min_value=1, max_value=1_000_000), tokenizer=_ident, exact=st.booleans())
_language_ref = st.builds(LanguageRef, tier=st.sampled_from(["1", "2", "3"]), primary=_ident, files=st.integers(min_value=0, max_value=1000))
_manifest_compression = st.builds(ManifestCompression, level=_compression_level, count=st.integers(min_value=0, max_value=10_000))
_distance_metric = st.builds(ManifestDistanceMetric, name=_ident, lambda_data_flow=_safe_float, lambda_guard=_safe_float, dist_max=_safe_float)
_manifest = st.builds(
    Manifest,
    packed_nodes=st.integers(min_value=0, max_value=10_000),
    considered_nodes=st.integers(min_value=0, max_value=10_000),
    reachable_nodes=st.integers(min_value=0, max_value=10_000),
    # unique_by level: `render`'s own `_render_manifest` collapses
    # `compression` into a `{level: count}` dict before rendering (it's a
    # per-level tally, never genuinely repeated for the same level in any
    # real `ContextPackage` - see `prism.surface.build`'s own
    # `compression_counts`), so a generated duplicate-level input isn't a
    # realistic roundtrip case, just an artifact of an unconstrained
    # generator.
    compression=st.lists(_manifest_compression, max_size=4, unique_by=lambda c: c.level),
    distance_metric=_distance_metric,
)
_feature_coverage = st.builds(FeatureCoverage, id=_ident, present=st.booleans(), count=st.integers(min_value=0, max_value=1000))
_coverage_gap = st.builds(CoverageGap, feature=_ident, reason=_gap_reason)
_coverage = st.builds(
    CoverageSummary,
    total_features=st.integers(min_value=0, max_value=1000),
    covered_features=st.integers(min_value=0, max_value=1000),
    omitted_features=st.integers(min_value=0, max_value=1000),
    features=st.lists(_feature_coverage, max_size=5, unique_by=lambda f: f.id),
    gaps=st.lists(_coverage_gap, max_size=5, unique_by=lambda g: g.feature),
)
_warning = st.builds(
    EnvelopeWarning,
    code=_warning_code, severity=_severity, message=_text,
    details=st.dictionaries(_ident, _detail_value, max_size=4),
)
_sig_param = st.builds(NodeSignatureParam, name=_ident, type=st.one_of(st.none(), _ident), optional=st.booleans())
_sig_return = st.builds(NodeSignatureReturn, type=st.one_of(st.none(), _ident), kind=_ident)
_signature = st.builds(NodeSignature, params=st.lists(_sig_param, max_size=4), returns=st.one_of(st.none(), _sig_return))
_features = st.builds(NodeFeatures, substance=_ident, form=_ident, output=_ident, role=_ident)
_contract = st.builds(NodeContract, target_id=_ident, call_line=st.integers(min_value=0, max_value=100_000), unpacks=st.one_of(st.none(), _ident), passes_args=st.one_of(st.none(), _text))
_node = st.builds(
    NodeEntry,
    id=_ident, role=_node_role, distance=st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
    compression=_compression_level, cost=st.integers(min_value=0, max_value=100_000),
    symbol_name=_ident, symbol_kind=_ident, language=_ident, file=_ident,
    line=st.integers(min_value=0, max_value=1_000_000), end_line=st.integers(min_value=0, max_value=1_000_000),
    signature=_signature, features=_features, contract=st.one_of(st.none(), _contract), body=_text,
)
_edge = st.builds(
    EdgeEntry,
    from_node=_ident, to_node=_ident, type=_edge_type,
    weight=st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
    data_flow=st.booleans(), guard=st.booleans(), back_edge=st.booleans(),
)

_options = st.dictionaries(_ident, _text, max_size=5)


@st.composite
def _context_packages(draw, budget: st.SearchStrategy = _budget_ref_free) -> ContextPackage:
    nodes = draw(st.lists(_node, max_size=6, unique_by=lambda n: n.id))
    edges = draw(st.lists(_edge, max_size=6, unique_by=lambda e: (e.from_node, e.to_node, e.type)))
    warnings = draw(st.lists(_warning, max_size=4, unique_by=lambda w: (w.severity, w.code)))
    return ContextPackage(
        engine=draw(_engine), seed=draw(_seed_ref), budget=draw(budget), language=draw(_language_ref),
        options=draw(_options), manifest=draw(_manifest), coverage=draw(_coverage),
        warnings=warnings, nodes=nodes, edges=edges,
        run_id=draw(st.one_of(st.none(), _ident)), generated_at=draw(st.one_of(st.none(), _ident)),
    )


def _canonicalize(pkg: ContextPackage) -> ContextPackage:
    """Reorders `nodes`/`edges`/`warnings`/`manifest.compression` into
    exactly the order `render` itself would sort them into - see this
    module's own docstring for why Roundtrip Fidelity is tested against
    an already-canonical input. `manifest.compression` specifically:
    `_render_manifest` walks its own fixed `_COMPRESSION_LEVELS` order
    (`L0_full`, `L1_pruned`, `L2_skeleton`, `L3_alias`) rather than the
    input list's order - it's conceptually a `{level: count}` map, always
    emitted in one canonical order, not a preserved sequence.
    """
    by_level = {c.level: c for c in pkg.manifest.compression}
    canonical_compression = [by_level[level] for level in _COMPRESSION_LEVELS if level in by_level]
    canonical_manifest = pkg.manifest.model_copy(update={"compression": canonical_compression})
    return pkg.model_copy(
        update={
            "nodes": _sorted_nodes(pkg),
            "edges": _sorted_edges(pkg),
            "warnings": _sorted_warnings(pkg.warnings),
            "manifest": canonical_manifest,
        }
    )


_render_options = st.builds(
    RenderOptions,
    indent=st.integers(min_value=1, max_value=4),
    pretty=st.booleans(),
    include_timestamp=st.booleans(),
    include_run_id=st.booleans(),
    include_bodies=st.booleans(),
    max_body_lines=st.one_of(st.none(), st.integers(min_value=0, max_value=100)),
    schema_version=st.integers(min_value=1, max_value=5),
)


# --------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------- #
@given(pkg=_context_packages(), options=_render_options)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_determinism_render_twice_is_byte_identical(pkg, options):
    assert render(pkg, options) == render(pkg, options)


# --------------------------------------------------------------------- #
# XML Well-Formedness
# --------------------------------------------------------------------- #
@given(pkg=_context_packages(), options=_render_options)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_render_is_always_well_formed_xml(pkg, options):
    ET.fromstring(render(pkg, options))  # raises ParseError on any malformed output


# --------------------------------------------------------------------- #
# Roundtrip Fidelity
# --------------------------------------------------------------------- #
#: Fixed, deliberately huge - see this module's own docstring for why a
#: random/small budget would make `render`'s own `BUDGET_OVERFLOW`
#: auto-injection a false-positive roundtrip failure.
_huge_budget = st.builds(BudgetRef, tokens=st.just(1_000_000_000), tokenizer=_ident, exact=st.booleans())


@given(pkg=_context_packages(budget=_huge_budget))
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_roundtrip_fidelity_parse_context_of_render_equals_original(pkg):
    canonical = _canonicalize(pkg)
    options = RenderOptions(include_timestamp=True, include_run_id=True)
    rendered = render(canonical, options)
    assert parse_context(rendered) == canonical
