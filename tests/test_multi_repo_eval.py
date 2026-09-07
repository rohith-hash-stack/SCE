"""Hermetic tests for `benchmarks/multi_repo_eval.py`. The pure/static
pieces (graph metrics, hallucination checking, CLI argument validation)
run here against already-local fixtures - no cloning, no network. Actually
running the full httpx/flask/marshmallow suite is documented in
benchmarks/README.md and gated behind SCE_LIVE_NETWORK_TESTS=1 below, since
it needs outbound network access this sandbox may not always have.
"""
from __future__ import annotations

import os

import pytest

from benchmarks.multi_repo_eval import (
    REPOS,
    SCENARIOS,
    GraphMetrics,
    check_no_hallucinated_symbols,
    compute_graph_metrics,
    main,
    run_repo,
)
from sce.cli import build_pipeline

STRESS_FIXTURE = "benchmarks/fixtures/stress_repo"
PYTHON_FIXTURE = "tests/fixtures/python_repo"


def test_scenarios_defined_for_every_repo_with_all_three_query_types():
    assert set(SCENARIOS) == set(REPOS)
    for repo_key, scenarios in SCENARIOS.items():
        assert len(scenarios) >= 3, f"{repo_key} needs at least 3 scenarios"
        assert {s.query_type for s in scenarios} == {"A", "B", "C"}, repo_key
        assert len({s.scenario_id for s in scenarios}) == len(scenarios), f"{repo_key} scenario ids must be unique"


def test_compute_graph_metrics_on_known_fixture():
    builder, _ = build_pipeline(STRESS_FIXTURE)
    metrics = compute_graph_metrics(builder)
    assert isinstance(metrics, GraphMetrics)
    assert metrics.total_symbols == len(builder.symbol_table)
    assert metrics.total_edges == builder.graph.number_of_edges()
    assert 0.0 <= metrics.isolated_node_ratio <= 1.0
    assert metrics.max_traversal_depth >= 0


def test_compute_graph_metrics_handles_empty_graph(tmp_path):
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()
    # A bare comment, not `X = 1`: since concrete_builder now indexes
    # module-level assignments as "attribute" symbols too, `X = 1` would no
    # longer produce a truly empty graph - this keeps that a separate,
    # dedicated case (see test_compute_graph_metrics_on_attribute_only_graph).
    (empty_repo / "constants.py").write_text("# no definitions here\n")
    builder, _ = build_pipeline(str(empty_repo))
    metrics = compute_graph_metrics(builder)
    assert metrics.total_symbols == 0
    assert metrics.total_edges == 0
    assert metrics.isolated_node_ratio == 0.0
    assert metrics.max_traversal_depth == 0


def test_compute_graph_metrics_on_attribute_only_graph(tmp_path):
    """A module with nothing but a top-level constant still indexes one
    "attribute" symbol (see concrete_builder's Pass 1) - as an isolated
    graph node, since nothing calls or is called by it."""
    repo = tmp_path / "attrs_only"
    repo.mkdir()
    (repo / "constants.py").write_text("X = 1\n")
    builder, _ = build_pipeline(str(repo))
    metrics = compute_graph_metrics(builder)
    assert metrics.total_symbols == 1
    assert builder.symbol_table.get("constants.X").kind == "attribute"
    assert metrics.total_edges == 0
    assert metrics.isolated_node_ratio == 1.0


def test_hallucination_check_passes_on_real_sce_output():
    from sce.graph.metamodel import SemanticMetamodel
    from sce.serializers.markdown import render_markdown
    from sce.slicer.distance import DistanceConfig, DistanceEngine
    from sce.slicer.knapsack import ContextKnapsackPacker

    builder, tag_matrix = build_pipeline(STRESS_FIXTURE)
    target = "app.controllers.orders.OrderController.process_order"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=4000).pack(target, builder, tag_matrix, distance_engine)
    markdown_text = render_markdown(pack_result, tag_matrix)

    result = check_no_hallucinated_symbols(markdown_text, builder)
    assert result.passed
    assert not result.unknown_symbols
    assert target in result.referenced_symbols


def test_hallucination_check_flags_a_fabricated_heading():
    fake_markdown = "### totally.made.up.Symbol (Full Implementation - L0)\n```python\ndef f(): ...\n```\n"
    builder, _ = build_pipeline(STRESS_FIXTURE)
    result = check_no_hallucinated_symbols(fake_markdown, builder)
    assert not result.passed
    assert "totally.made.up.Symbol" in result.unknown_symbols


def test_hallucination_check_accepts_legitimate_external_calls():
    """A real (if externally-unresolved) node the linker recorded, e.g.
    `requests.post`, is not a hallucination - it's a genuine call target
    the pipeline saw, just not one it could fully index."""
    builder, _ = build_pipeline(PYTHON_FIXTURE)
    assert "requests.post" in builder.graph  # sanity: this node really exists
    result = check_no_hallucinated_symbols("# Calls: requests.post\n", builder)
    assert result.passed


def test_hallucination_check_flags_a_fabricated_calls_line_entry():
    builder, _ = build_pipeline(PYTHON_FIXTURE)
    result = check_no_hallucinated_symbols("# Calls: some.totally.fake.helper\n", builder)
    assert not result.passed
    assert "some.totally.fake.helper" in result.unknown_symbols


def test_main_requires_suite_or_repo():
    with pytest.raises(SystemExit):
        main([])


def test_main_rejects_unknown_repo():
    with pytest.raises(SystemExit):
        main(["--repo", "not-a-real-repo"])


# --------------------------------------------------------------------- #
# Real-network smoke test (opt-in only)
# --------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("SCE_LIVE_NETWORK_TESTS") != "1",
    reason="set SCE_LIVE_NETWORK_TESTS=1 to clone and evaluate real repositories over the network",
)
@pytest.mark.parametrize("repo_key", sorted(REPOS))
def test_run_repo_against_real_clone(repo_key, tmp_path):
    result = run_repo(repo_key, [2000, 4000], tmp_path / "cache", force_clone=False, k_hops=3)
    assert result.graph_metrics.total_symbols > 100
    for scenario in result.scenarios:
        assert scenario.passed, (scenario.scenario_id, scenario.budget, scenario.checks)
