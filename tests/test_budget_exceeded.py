"""Tests for Issue A3: `PackResult.budget_exceeded`/`seed_cost`/
`truncation_occurred` - explicit diagnostic metadata for the seed-at-L0
budget invariant exception documented in docs/design_formalism.md SS4.4.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.serializers.markdown import render_markdown
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


def _pack(repo_path: str, seed: str, budget: int):
    builder, tag_matrix = build_pipeline(repo_path)
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    packer = ContextKnapsackPacker(token_budget=budget)
    result = packer.pack(seed, builder, tag_matrix, distance_engine, contracts=contracts)
    return result, tag_matrix


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["def seed():"]
    # A body large enough that its own L0 render alone is well over a
    # tiny budget - many distinct statements, not one long line, so real
    # tokenization (or the fallback) counts it as genuinely many tokens.
    for i in range(80):
        lines.append(f"    value_{i} = {i} * 2 + 1")
    lines.append("    return value_0")
    (repo / "sample.py").write_text("\n".join(lines) + "\n")
    return repo


def test_budget_exceeded_true_when_seed_alone_overflows(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result, _ = _pack(str(repo), "sample.seed", budget=50)
    assert result.budget_exceeded is True
    assert result.truncation_occurred is True
    assert result.seed_cost > 50
    assert result.allocated_tokens == result.seed_cost
    assert [i.symbol for i in result.items] == ["sample.seed"]


def test_budget_exceeded_false_under_normal_operation(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result, _ = _pack(str(repo), "sample.seed", budget=10_000)
    assert result.budget_exceeded is False
    assert result.truncation_occurred is False
    assert result.seed_cost <= result.budget
    assert result.allocated_tokens <= result.budget


def test_budget_exceeded_and_truncation_occurred_agree_in_this_architecture(tmp_path) -> None:
    """Documented in docs/design_formalism.md SS4.4: every non-seed
    candidate is strictly admission-gated, so the seed is the only
    possible overflow source today - the two independently-computed
    flags must always agree, across both the over- and under-budget
    cases."""
    repo = _make_repo(tmp_path)
    for budget in (50, 200, 2000, 10_000):
        result, _ = _pack(str(repo), "sample.seed", budget=budget)
        assert result.budget_exceeded == result.truncation_occurred


def test_markdown_renders_explicit_overflow_notice_when_budget_exceeded(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result, tag_matrix = _pack(str(repo), "sample.seed", budget=50)
    text = render_markdown(result, tag_matrix)
    assert "Budget exceeded" in text
    assert str(round(result.seed_cost)) in text


def test_markdown_omits_overflow_notice_under_normal_operation(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    result, tag_matrix = _pack(str(repo), "sample.seed", budget=10_000)
    text = render_markdown(result, tag_matrix)
    assert "Budget exceeded" not in text
