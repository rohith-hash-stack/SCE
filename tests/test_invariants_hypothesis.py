"""Property-based regression tests (hypothesis) proving the six formal
invariants from the systemic audit hold across the input space, not just
on the specific hand-picked examples the other test files exercise.
See docs/design_formalism.md Section 6 for the formal statement of each.
"""
from __future__ import annotations

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.graph.symbol_table import (
    EXPORT_RESOLUTION_MAX_DEPTH,
    ExportRegistry,
    GlobalSymbolTable,
    resolve_export,
)
from prism.runtime.reconciler import OrphanReason, classify_orphan
from prism.runtime.tracer import TraceRecord
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import SymbolInfo
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker

_TAG_NAMES = sorted(SemanticMetamodel().graph.nodes)
_tag_set_strategy = st.sets(st.sampled_from(_TAG_NAMES), max_size=3) if _TAG_NAMES else st.just(set())


# --------------------------------------------------------------------- #
# Invariant #1: Topological Monotonicity
# --------------------------------------------------------------------- #
@given(
    hop_a=st.integers(min_value=1, max_value=9),
    hop_gap=st.integers(min_value=1, max_value=9),
    tags_a=_tag_set_strategy,
    tags_b=_tag_set_strategy,
    seed_tags=_tag_set_strategy,
)
@settings(max_examples=200, deadline=None)
def test_invariant_1_topological_monotonicity(hop_a, hop_gap, tags_a, tags_b, seed_tags):
    """If dist(s,a) < dist(s,b), then D_hybrid(a) < D_hybrid(b), for any
    tag overlap - closer must always win regardless of tags."""
    hop_b = hop_a + hop_gap
    engine = DistanceEngine(SemanticMetamodel(), {}, DistanceConfig())
    d_a = engine._d_hybrid(hop_a, seed_tags, tags_a)
    d_b = engine._d_hybrid(hop_b, seed_tags, tags_b)
    assert d_a < d_b, f"hop_a={hop_a} hop_b={hop_b} tags_a={tags_a} tags_b={tags_b}: D(a)={d_a} !< D(b)={d_b}"


# --------------------------------------------------------------------- #
# Invariant #4: Seed Dominance
# --------------------------------------------------------------------- #
@given(
    hop_v=st.integers(min_value=1, max_value=9),
    seed_tags=_tag_set_strategy,
    node_tags=_tag_set_strategy,
)
@settings(max_examples=100, deadline=None)
def test_invariant_4_seed_dominance(hop_v, seed_tags, node_tags):
    """D_hybrid(s|s) (0 hops, matching tags) < D_hybrid(v|s) for any
    v != s at >= 1 hop, regardless of v's own tags."""
    engine = DistanceEngine(SemanticMetamodel(), {}, DistanceConfig())
    d_seed = engine._d_hybrid(0, seed_tags, seed_tags)  # perfect self-match, 0 hops
    d_v = engine._d_hybrid(hop_v, seed_tags, node_tags)
    assert d_seed < d_v


# --------------------------------------------------------------------- #
# Invariant #5: Compression Monotonicity
# --------------------------------------------------------------------- #
@given(
    d_a=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    d_b=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_invariant_5_compression_monotonicity(d_a, d_b):
    """resolution_for_distance is non-decreasing: a smaller (closer)
    effective distance never earns a *worse* (higher-numbered)
    compression level than a larger one."""
    engine = DistanceEngine(SemanticMetamodel(), {}, DistanceConfig())
    if d_a > d_b:
        d_a, d_b = d_b, d_a
    res_a = engine.resolution_for_distance(d_a)
    res_b = engine.resolution_for_distance(d_b)
    assert res_a <= res_b


# --------------------------------------------------------------------- #
# Invariant #3: Resolution Termination
# --------------------------------------------------------------------- #
@given(
    chain_names=st.lists(
        st.text(alphabet="abcdefghij", min_size=1, max_size=4), min_size=2, max_size=12, unique=True
    ),
    is_circular=st.booleans(),
)
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_invariant_3_resolve_export_terminates(chain_names, is_circular):
    """For any chain of re-export hops - circular or not, of any length -
    resolve_export() must return (never raise RecursionError, never loop
    forever), deterministically, using only symbols actually registered."""
    registry = ExportRegistry()
    table = GlobalSymbolTable()
    modules = [f"m_{name}" for name in chain_names]

    for a, b in zip(modules, modules[1:]):
        registry.add_explicit(a, "X", b, "X")
    if is_circular and len(modules) >= 2:
        registry.add_explicit(modules[-1], "X", modules[0], "X")
    else:
        # Terminate the chain with a real definition so short chains have
        # something to actually resolve to.
        table.add(SymbolInfo(
            qualified_name=f"{modules[-1]}.X", kind="function", file="x.py",
            line_range=(1, 1), language_id="python", module=modules[-1],
        ))

    result = resolve_export(modules[0], "X", registry, table)
    # No exception raised (hypothesis itself proves this by not erroring) -
    # additionally assert the result is either a real registered symbol or
    # None, never a fabricated/unverified string.
    assert result is None or result in table


# --------------------------------------------------------------------- #
# Invariant #6: Zero Silent Orphans
# --------------------------------------------------------------------- #
_qname_strategy = st.one_of(
    st.none(),
    st.text(alphabet="abcdefghijklmnop.<>_", min_size=1, max_size=20),
)


@given(
    caller=_qname_strategy,
    callee=_qname_strategy,
    source=st.sampled_from(["pytest_tracer", "otel"]),
)
@settings(max_examples=200, deadline=None)
def test_invariant_6_every_orphan_gets_a_reason(caller, callee, source):
    """classify_orphan must always return a real OrphanReason member for
    any TraceRecord shape - never None, never raises - for the full space
    of caller/callee text hypothesis can generate."""
    table = GlobalSymbolTable()
    builder = ConcreteGraphBuilder("/tmp/repo", table)
    event = TraceRecord(caller=caller, callee=callee, source=source)
    reason = classify_orphan(event, builder)
    assert isinstance(reason, OrphanReason)


# --------------------------------------------------------------------- #
# Invariant #2: Strict Budget Compliance
# --------------------------------------------------------------------- #
@given(budget=st.integers(min_value=1000, max_value=6000))
@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_invariant_2_strict_budget_compliance(budget, tmp_path_factory):
    """ActualTokens(RenderedContext) <= admission budget for any budget,
    against a real (if small) call-graph fixture with a fan-out shape
    deliberately large enough that packing decisions (not just "everything
    trivially fits") are actually exercised.

    `budget` is bounded below at 1000 tokens - a range this fixture's
    seed (~204 tokens at L0) fits at L0 throughout, so this property
    test's own assertion (checked against the *reduced*
    `_admission_budget`, not the raw `budget`) stays exactly as strict as
    it always was.

    Item 18 (third post-implementation audit), Progressive Seed
    Degradation, means the seed is no longer *always* pinned at L0
    regardless of budget - it degrades L0 -> L1 -> L2 -> L3, stopping at
    the first tier that fits the raw `budget` (not `_admission_budget`;
    checked directly: at `budget=100` against this exact fixture the
    seed degrades to L3, 57 tokens, which fits the 92-token admission
    budget too, `fits_admission=True`) - so a budget below 1000 is no
    longer guaranteed to violate this assertion the way it did before
    Item 18. The seed is still always packed unconditionally in the
    sense that nothing ever excludes it outright (Invariant #4, Seed
    Dominance - a query response must always show its own target); what
    changed is that "unconditionally" no longer means "always at full
    L0 cost". The one residual case this assertion can still legitimately
    fail on is `fatal_seed_overflow` (even the minimal L3 stub doesn't
    fit `budget`) - `min_value=1000` is kept here (rather than lowered
    now that it's no longer strictly required for this specific fixture)
    to keep this property test focused on ordinary candidate-selection
    packing rather than the seed-degradation edge case, which
    `tests/test_budget_exceeded.py` covers directly and explicitly
    instead. See docs/design_formalism.md Section 4.4 for the updated
    formal statement."""
    repo = tmp_path_factory.mktemp("invariant2") / "repo"
    repo.mkdir()
    lines = []
    for i in range(25):
        lines.append(f"def f{i}():")
        lines.append(f"    return {i}")
        lines.append("")
    lines.append("def seed():")
    lines.append("    total = 0")
    for i in range(25):
        lines.append(f"    total += f{i}()")
    lines.append("    return total")
    (repo / "sample.py").write_text("\n".join(lines) + "\n")

    builder, tag_matrix = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    packer = ContextKnapsackPacker(token_budget=budget)
    result = packer.pack("sample.seed", builder, tag_matrix, distance_engine, contracts=contracts)
    assert result.allocated_tokens <= packer._admission_budget + 1e-6, (
        f"budget={budget} allocated={result.allocated_tokens} admission_budget={packer._admission_budget}"
    )
