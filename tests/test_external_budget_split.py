"""Phase C, Step 2 (`docs/phase_c_architecture_spec.md` Section 3.2):
`prism.packer.submodular_knapsack.split_budget_for_external` - pure,
no-fixture unit tests, since the function itself takes nothing but
plain ints/floats.
"""
from __future__ import annotations

import pytest

from prism.packer.submodular_knapsack import (
    DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS,
    DEFAULT_EXTERNAL_BUDGET_FLOOR_TOKENS,
    DEFAULT_EXTERNAL_BUDGET_FRACTION,
    SubmodularPackedItem,
    split_budget_for_external,
)


# --------------------------------------------------------------------- #
# The three established pilot budgets (2000/4000/8000) - Section 3.2's
# own worked commentary.
# --------------------------------------------------------------------- #
def test_split_at_2000_hits_the_floor():
    """0.125 * 2000 = 250, below the 256 floor - the floor is the one
    that actually binds here, per the spec's own worked example."""
    internal, external = split_budget_for_external(2000)
    assert external == DEFAULT_EXTERNAL_BUDGET_FLOOR_TOKENS
    assert internal == 2000 - DEFAULT_EXTERNAL_BUDGET_FLOOR_TOKENS


def test_split_at_4000_uses_the_flat_fraction():
    """0.125 * 4000 = 500 - between the floor and ceiling, neither
    clamp binds."""
    internal, external = split_budget_for_external(4000)
    assert external == 500
    assert internal == 4000 - 500


def test_split_at_8000_stays_under_the_ceiling():
    """0.125 * 8000 = 1000, below the 1024 ceiling - the ceiling never
    binds at any of the three established pilot budgets."""
    internal, external = split_budget_for_external(8000)
    assert external == 1000
    assert internal == 8000 - 1000


@pytest.mark.parametrize("budget", [2000, 4000, 8000])
def test_internal_plus_external_always_equals_total(budget):
    internal, external = split_budget_for_external(budget)
    assert internal + external == budget
    assert internal >= 0
    assert external >= 0


# --------------------------------------------------------------------- #
# Edge cases beyond the three pilot budgets
# --------------------------------------------------------------------- #
def test_ceiling_binds_at_a_large_budget():
    """0.125 * 16000 = 2000, above the 1024 ceiling - the ceiling now
    binds, unlike at any of the pilot budgets."""
    internal, external = split_budget_for_external(16000)
    assert external == DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS
    assert internal == 16000 - DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS


def test_pathologically_small_budget_gives_everything_to_external():
    """A budget under the floor degrades to external_budget=budget,
    internal_budget=0 - correct, not a bug: there is nothing else to
    spend a budget this tight on either."""
    internal, external = split_budget_for_external(100)
    assert external == 100
    assert internal == 0


def test_zero_budget_splits_to_zero_and_zero():
    internal, external = split_budget_for_external(0)
    assert (internal, external) == (0, 0)


def test_custom_fraction_floor_ceiling_are_honored():
    internal, external = split_budget_for_external(10_000, fraction=0.5, floor_tokens=10, ceiling_tokens=100)
    assert external == 100  # 0.5 * 10_000 = 5000, clamped down to the 100 ceiling
    assert internal == 10_000 - 100


def test_default_fraction_matches_the_resolved_spec_value():
    assert DEFAULT_EXTERNAL_BUDGET_FRACTION == 0.125


# --------------------------------------------------------------------- #
# SubmodularPackedItem.origin
# --------------------------------------------------------------------- #
def test_submodular_packed_item_defaults_to_internal_origin():
    """Additive default - every existing construction site (which never
    passes `origin`) keeps behaving exactly as it did before this field
    existed."""
    item = SubmodularPackedItem(symbol="pkg.mod.func", cost=10, feature_mask=0, dist_w=0.0)
    assert item.origin == "internal"


def test_submodular_packed_item_accepts_external_origin():
    item = SubmodularPackedItem(symbol="starlette.routing.Router.add_route", cost=10, feature_mask=0, dist_w=1.0, origin="external")
    assert item.origin == "external"
