"""Phase C, Step 2 (`docs/phase_c_architecture_spec.md` Section 3.2):
`prism.packer.submodular_knapsack.split_budget_for_external` - pure,
no-fixture unit tests, since the function itself takes nothing but
plain ints/floats. Also Step 4's `pack_external_context_requested`
(Section 3.4), against synthetic `ExternalSymbolInfo` cache entries -
no real package/tree-sitter parse needed, since the function itself is
pure given an already-resolved cache dict.
"""
from __future__ import annotations

import pytest

from prism.external.index import ExternalSymbolInfo
from prism.packer.submodular_knapsack import (
    DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS,
    DEFAULT_EXTERNAL_BUDGET_FLOOR_TOKENS,
    DEFAULT_EXTERNAL_BUDGET_FRACTION,
    ROLE_EXTERNAL,
    SubmodularPackedItem,
    pack_external_context_requested,
    split_budget_for_external,
)


def _info(qualified_name: str, signature_text: str) -> ExternalSymbolInfo:
    module_origin, _, _ = qualified_name.partition(".")
    return ExternalSymbolInfo(
        qualified_name=qualified_name,
        module_origin=module_origin,
        language="python",
        signature_text=signature_text,
        docstring=None,
        kind="function",
        file=f"/fake/{module_origin}/__init__.pyi",
        line=1,
        end_line=1,
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


# --------------------------------------------------------------------- #
# pack_external_context_requested (Section 3.4, extracted from
# prism.engine.PrismEngine.retrieve_two_or_three_pass in Phase C Step 4)
# --------------------------------------------------------------------- #
def test_admits_a_resolvable_symbol_within_budget():
    cache = {"orjson.dumps": _info("orjson.dumps", "def dumps(obj) -> bytes:")}
    items, skipped = pack_external_context_requested(cache, ["orjson.dumps"], external_budget_tokens=100)

    assert skipped == []
    assert len(items) == 1
    item = items[0]
    assert item.symbol == "orjson.dumps"
    assert item.role == ROLE_EXTERNAL
    assert item.origin == "external"
    assert item.compression == "L2_skeleton"
    assert item.cost > 0


def test_name_absent_from_cache_is_skipped_as_hallucinated():
    """A requested name Turn 2a never actually resolved (never a real
    candidate) must never be admitted, whatever the budget."""
    items, skipped = pack_external_context_requested({}, ["totally.invented.symbol"], external_budget_tokens=10_000)
    assert items == []
    assert skipped == ["totally.invented.symbol"]


def test_stops_admitting_once_budget_is_exhausted():
    """Greedy, in-request-order admission - the second symbol doesn't
    fit alongside the first, so it's skipped, not substituted or
    partially rendered."""
    cache = {
        "pkg.first": _info("pkg.first", "def first(a, b, c, d, e, f, g, h) -> None:"),
        "pkg.second": _info("pkg.second", "def second(a, b, c, d, e, f, g, h) -> None:"),
    }
    first_cost = pack_external_context_requested(cache, ["pkg.first"], external_budget_tokens=10_000)[0][0].cost
    items, skipped = pack_external_context_requested(cache, ["pkg.first", "pkg.second"], external_budget_tokens=first_cost)

    assert [i.symbol for i in items] == ["pkg.first"]
    assert skipped == ["pkg.second"]


def test_a_pathologically_repeated_request_never_exceeds_the_budget():
    """The same real, resolvable name requested many times over must
    never let the running cost exceed external_budget_tokens - only as
    many repeats as actually fit are admitted."""
    cache = {"orjson.dumps": _info("orjson.dumps", "def dumps(obj) -> bytes:")}
    single_cost = pack_external_context_requested(cache, ["orjson.dumps"], external_budget_tokens=10_000)[0][0].cost
    budget = single_cost * 3  # room for exactly 3 repeats

    items, skipped = pack_external_context_requested(cache, ["orjson.dumps"] * 10, external_budget_tokens=budget)

    assert len(items) == 3
    assert len(skipped) == 7
    assert sum(i.cost for i in items) <= budget


def test_empty_request_list_yields_nothing_admitted_or_skipped():
    items, skipped = pack_external_context_requested({"orjson.dumps": _info("orjson.dumps", "def dumps(obj) -> bytes:")}, [], external_budget_tokens=1000)
    assert items == []
    assert skipped == []


def test_admission_cost_matches_what_external_symbol_to_node_entry_would_render():
    """The packer's own admission-time cost must be the exact same
    value the resulting NodeEntry.cost carries once rendered - no
    metering drift between "what fit the sub-budget" and "what the
    final package actually reports"."""
    from prism.external.index import external_symbol_to_node_entry

    info = _info("orjson.dumps", "def dumps(obj: Any, default=..., option=...) -> bytes:")
    items, _skipped = pack_external_context_requested({"orjson.dumps": info}, ["orjson.dumps"], external_budget_tokens=10_000)
    node = external_symbol_to_node_entry(info)

    assert items[0].cost == node.cost
