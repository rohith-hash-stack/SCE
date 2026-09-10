"""Tests for Item 18 (third post-implementation audit): Progressive Seed
Degradation - `ContextKnapsackPacker._degrade_seed_to_fit`,
`PackResult.seed_compression_level`/`fatal_seed_overflow`.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


def _pack(repo_path: str, seed: str, budget: int):
    builder, tag_matrix = build_pipeline(repo_path)
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    packer = ContextKnapsackPacker(token_budget=budget)
    return packer.pack(seed, builder, tag_matrix, distance_engine, contracts=contracts)


def _make_repo(tmp_path, n_statements: int = 80):
    """A fixture with no real function calls - L1 (ArgPreservingSkeletonizer)
    strips its bare math-only assignments almost entirely, so L1 is
    dramatically smaller than L0 here (used for the L0/L1 boundary)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["def seed():"]
    for i in range(n_statements):
        lines.append(f"    value_{i} = {i} * 2 + 1")
    lines.append("    return value_0")
    (repo / "sample.py").write_text("\n".join(lines) + "\n")
    return repo


def _make_repo_with_all_four_tiers_distinct(tmp_path):
    """A fixture engineered so L0 > L1 > L2 > L3 are all strictly
    decreasing and individually reachable - a docstring (L1 strips it,
    L0 doesn't) plus many real calls with long argument expressions (L1
    preserves them in full, L2 collapses them to `...`), confirmed
    directly: L0=1380, L1=1091, L2=328, L3=57 (+ real wrapper overhead)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["def helper(x):", "    return x + 1", "", "def seed():"]
    lines.append('    """' + ("A very long docstring line. " * 30) + '"""')
    for i in range(40):
        lines.append(f"    v{i} = helper({i}*111111 + {i}*222222 + {i}*333333 + {i}*444444)")
    lines.append("    return v0")
    (repo / "sample.py").write_text("\n".join(lines) + "\n")
    return repo


def test_seed_packed_at_l0_when_it_fits(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result = _pack(str(repo), "sample.seed", budget=10_000)
    assert result.seed_compression_level == 0
    assert result.fatal_seed_overflow is False
    assert result.budget_exceeded is False
    assert "Warning" not in result.items[0].content


def test_seed_degrades_to_l1_under_moderate_constraint(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result = _pack(str(repo), "sample.seed", budget=100)
    assert result.seed_compression_level == 1
    assert "/* Warning: Seed compressed to L1" in result.items[0].content
    assert result.fatal_seed_overflow is False
    assert result.seed_cost <= result.budget


def test_seed_degrades_to_l2_under_severe_constraint(tmp_path) -> None:
    repo = _make_repo_with_all_four_tiers_distinct(tmp_path)
    result = _pack(str(repo), "sample.seed", budget=350)
    assert result.seed_compression_level == 2
    assert "/* Warning: Seed compressed to L2 skeleton" in result.items[0].content
    assert "/* Warning: Seed compressed to L1" not in result.items[0].content
    assert result.fatal_seed_overflow is False


def test_all_four_compression_tiers_are_individually_reachable(tmp_path) -> None:
    """Directly demonstrates every tier (L0/L1/L2/L3) is reachable on
    one fixture engineered to keep them strictly size-ordered, at
    budgets chosen to land exactly on each."""
    repo = _make_repo_with_all_four_tiers_distinct(tmp_path)
    expectations = {10_000: 0, 1200: 1, 350: 2, 10: 3}
    for budget, expected_level in expectations.items():
        result = _pack(str(repo), "sample.seed", budget=budget)
        assert result.seed_compression_level == expected_level, (
            f"budget={budget}: expected level {expected_level}, got {result.seed_compression_level} "
            f"(seed_cost={result.seed_cost})"
        )


def test_seed_degrades_progressively_as_budget_shrinks(tmp_path) -> None:
    """Compression level must be non-decreasing as budget shrinks."""
    repo = _make_repo_with_all_four_tiers_distinct(tmp_path)
    levels = []
    for budget in (10_000, 1200, 350, 100, 10):
        result = _pack(str(repo), "sample.seed", budget=budget)
        levels.append(result.seed_compression_level)
    assert levels == sorted(levels)  # non-decreasing as budget shrinks
    assert levels[0] == 0
    assert levels[-1] == 3


def test_fatal_seed_overflow_only_when_even_l3_stub_does_not_fit(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result = _pack(str(repo), "sample.seed", budget=10)
    assert result.seed_compression_level == 3
    assert result.fatal_seed_overflow is True
    assert result.budget_exceeded is True
    assert result.fatal_seed_overflow == result.budget_exceeded
    assert "/* Warning: Seed compressed to minimal stub" in result.items[0].content


def test_seed_is_always_present_regardless_of_degradation_tier(tmp_path) -> None:
    """Invariant #4 (Seed Dominance) must hold at every tier - the seed
    is never excluded outright, even at the most severe budget."""
    repo = _make_repo(tmp_path)
    for budget in (10_000, 100, 50, 10, 1):
        result = _pack(str(repo), "sample.seed", budget=budget)
        assert result.items[0].symbol == "sample.seed"
        assert result.items[0].content  # never empty


def test_seed_compression_level_matches_item_resolution(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result = _pack(str(repo), "sample.seed", budget=100)
    assert result.items[0].resolution == result.seed_compression_level
