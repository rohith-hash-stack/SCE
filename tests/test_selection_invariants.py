"""Test-practice: end-to-end invariants of
`prism.packer.submodular_knapsack.pack_symbol_context` (the real, wired
entry point - causal graph, four-axis feature masks, Continuous
Dijkstra distances, upstream blast radius, real BPE costs, then
`select_submodular_context`). `test_v11_invariants.py` already property-
tests the algorithm's internal pieces (dominance, submodularity,
data-flow weighting) with hypothesis-generated synthetic graphs; this
file checks black-box invariants a caller actually depends on, against
a real built repo: budget respected, seed always present, no
duplicates, non-decreasing coverage as budget grows, and determinism."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import pack_symbol_context

_SOURCE = (
    "def leaf_a():\n    return 1\n\n\n"
    "def leaf_b():\n    return 2\n\n\n"
    "def mid_a():\n    return leaf_a() + leaf_b()\n\n\n"
    "def mid_b():\n    return leaf_b()\n\n\n"
    "def mid_c():\n    return leaf_a()\n\n\n"
    "def seed():\n    return mid_a() + mid_b() + mid_c()\n\n\n"
    "def unrelated():\n    return 42\n"
)


def _builder(tmp_path, subdir="repo"):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    return builder


def test_total_cost_never_exceeds_budget(tmp_path):
    builder = _builder(tmp_path)
    for budget in (50, 100, 500, 2000, 10000):
        result = pack_symbol_context(builder, "svc.seed", budget)
        assert result.total_cost <= budget, budget


def test_item_costs_sum_to_total_cost(tmp_path):
    builder = _builder(tmp_path)
    result = pack_symbol_context(builder, "svc.seed", 2000)
    assert sum(item.cost for item in result.items) == result.total_cost


def test_seed_is_always_selected_when_it_alone_fits_budget(tmp_path):
    builder = _builder(tmp_path)
    for budget in (100, 500, 2000, 10000):
        result = pack_symbol_context(builder, "svc.seed", budget)
        assert "svc.seed" in result.selected, budget


def test_selected_has_no_duplicate_symbols(tmp_path):
    builder = _builder(tmp_path)
    result = pack_symbol_context(builder, "svc.seed", 2000)
    assert len(result.selected) == len(set(result.selected))
    assert len(result.items) == len(set(item.symbol for item in result.items))


def test_selected_matches_items_exactly(tmp_path):
    builder = _builder(tmp_path)
    result = pack_symbol_context(builder, "svc.seed", 2000)
    assert set(result.selected) == {item.symbol for item in result.items}


def test_larger_budget_never_shrinks_the_selection(tmp_path):
    """More budget should never leave the caller with a strictly smaller
    (or less-covering) context than a tighter budget would have - the
    submodular admission loop is monotone in this sense."""
    builder = _builder(tmp_path)
    small = pack_symbol_context(builder, "svc.seed", 60)
    large = pack_symbol_context(builder, "svc.seed", 5000)
    assert len(large.selected) >= len(small.selected)
    assert bin(large.covered_mask).count("1") >= bin(small.covered_mask).count("1")
    assert set(small.selected) <= set(large.selected)


def test_unrelated_unreachable_symbol_is_never_selected(tmp_path):
    """`unrelated()` has no causal edge to/from `seed()` - it must never
    appear in the pack regardless of how generous the budget is."""
    builder = _builder(tmp_path)
    result = pack_symbol_context(builder, "svc.seed", 10000)
    assert "svc.unrelated" not in result.selected


def test_pack_symbol_context_is_deterministic_across_repeated_calls(tmp_path):
    builder = _builder(tmp_path)
    first = pack_symbol_context(builder, "svc.seed", 2000)
    second = pack_symbol_context(builder, "svc.seed", 2000)
    assert first.selected == second.selected
    assert first.total_cost == second.total_cost
    assert first.covered_mask == second.covered_mask
    first_items = [(i.symbol, i.cost, i.feature_mask, i.dist_w, i.role) for i in first.items]
    second_items = [(i.symbol, i.cost, i.feature_mask, i.dist_w, i.role) for i in second.items]
    assert first_items == second_items


def test_pack_symbol_context_is_deterministic_across_independent_builds(tmp_path):
    builder_a = _builder(tmp_path, subdir="repo_a")
    builder_b = _builder(tmp_path, subdir="repo_b")
    result_a = pack_symbol_context(builder_a, "svc.seed", 2000)
    result_b = pack_symbol_context(builder_b, "svc.seed", 2000)
    assert result_a.selected == result_b.selected
    assert result_a.total_cost == result_b.total_cost


def test_zero_budget_still_force_includes_only_the_seed(tmp_path):
    """The seed is unconditionally force-included at initialization
    (`s_pack = [seed_id]`, `submodular_knapsack.py:404`) regardless of
    `target_budget` - a budget too small even for the seed's own cost
    must still select exactly the seed, nothing more, with total_cost
    equal to the seed's own cost (a real, intentional exception to
    "never exceeds budget": the seed is the one item always admitted)."""
    builder = _builder(tmp_path)
    result = pack_symbol_context(builder, "svc.seed", 0)
    assert result.selected == ["svc.seed"]
    seed_cost = next(item.cost for item in result.items if item.symbol == "svc.seed")
    assert result.total_cost == seed_cost
    assert result.total_cost > 0  # the budget itself is 0, so this is the seed-exception in action
