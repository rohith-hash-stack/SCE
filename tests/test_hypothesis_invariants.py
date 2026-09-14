"""Test-practice: property-based (hypothesis) tests for
`prism.semantics.bitmask` - the 64-bit `FeatureBit` encoding every
four-axis feature (Substance/Form/Output/Role) is packed into, and the
two functions (`describe_mask`, and `prism.surface.build._axis_labels`)
that decode it back for a human/LLM-readable envelope. `test_v11_
invariants.py` and `test_invariants_hypothesis.py` already
property-test the knapsack algorithm and six systemic-audit invariants
respectively; this file is the first property coverage of the bitmask
layer itself - every other test exercises it only through hand-picked
real symbols, never through the full input space of possible bit
combinations."""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from prism.semantics.bitmask import ALL_KNOWN_BITS, FORM_BITS, OUTPUT_BITS, ROLE_BITS, SUBSTANCE_BITS, compose_mask, describe_mask
from prism.surface.build import _axis_labels

_AXES = {"SUBSTANCE": SUBSTANCE_BITS, "FORM": FORM_BITS, "OUTPUT": OUTPUT_BITS, "ROLE": ROLE_BITS}
_ALL_BITS_LIST = sorted(ALL_KNOWN_BITS, key=int)


def test_the_four_axes_are_pairwise_disjoint():
    """No single FeatureBit value may belong to two axes - a shared bit
    would make `_axis_labels` attribute the same set signal to more than
    one axis in a rendered envelope."""
    axes = list(_AXES.values())
    for i in range(len(axes)):
        for j in range(i + 1, len(axes)):
            assert axes[i].isdisjoint(axes[j]), (list(_AXES.keys())[i], list(_AXES.keys())[j])


def test_the_four_axes_union_to_exactly_all_known_bits():
    assert SUBSTANCE_BITS | FORM_BITS | OUTPUT_BITS | ROLE_BITS == ALL_KNOWN_BITS


@given(st.sets(st.sampled_from(_ALL_BITS_LIST)))
@settings(max_examples=200)
def test_describe_mask_round_trips_any_subset_of_known_bits(bit_subset):
    """For ANY subset of the known bits (not just real, hand-picked
    combinations a symbol might actually produce), composing them into a
    mask and describing it back must recover exactly that subset's
    names - no bit silently dropped, no phantom bit reported, no name
    duplicated."""
    mask = compose_mask(*bit_subset) if bit_subset else 0
    described = set(describe_mask(mask))
    assert described == {b.name for b in bit_subset}


@given(st.sets(st.sampled_from(_ALL_BITS_LIST)))
@settings(max_examples=200)
def test_axis_labels_never_leaks_a_bit_from_a_different_axis(bit_subset):
    """`_axis_labels(mask, axis_bits)` must report only names from its
    own axis, regardless of which OTHER axes' bits are also set in the
    mask - cross-axis leakage would make one axis's rendered value
    silently reflect a completely different axis's signal."""
    mask = compose_mask(*bit_subset) if bit_subset else 0
    for axis_name, axis_bits in _AXES.items():
        raw = _axis_labels(mask, axis_bits)
        labels = set() if raw == "NONE" else set(raw.split(","))
        expected = {b.name.split("_", 1)[1] for b in bit_subset if b in axis_bits}
        assert labels == expected, (axis_name, bit_subset)


@given(st.sets(st.sampled_from(_ALL_BITS_LIST)))
@settings(max_examples=200)
def test_composing_the_same_subset_twice_is_idempotent(bit_subset):
    """OR-composition of a fixed set of bits must be stable - composing
    the same subset via compose_mask twice (e.g. once for a symbol's own
    mask, once for a reachable-mask union elsewhere) must never itself
    introduce or lose a bit."""
    mask_1 = compose_mask(*bit_subset) if bit_subset else 0
    mask_2 = compose_mask(*bit_subset) if bit_subset else 0
    assert mask_1 == mask_2
    assert mask_1 | mask_2 == mask_1


@given(st.sets(st.sampled_from(_ALL_BITS_LIST)), st.sets(st.sampled_from(_ALL_BITS_LIST)))
@settings(max_examples=200)
def test_mask_union_never_loses_a_bit_present_in_either_operand(subset_a, subset_b):
    """A covered_mask built by OR-ing two symbols' own masks together
    (exactly how build_context_package/select_submodular_context
    accumulate coverage) must contain every bit either symbol alone
    contributed."""
    mask_a = compose_mask(*subset_a) if subset_a else 0
    mask_b = compose_mask(*subset_b) if subset_b else 0
    union = mask_a | mask_b
    for bit in subset_a | subset_b:
        assert union & int(bit), bit.name
