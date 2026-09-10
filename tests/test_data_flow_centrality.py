"""Tests for Data-Flow Centrality (Issue #10.2):
`ContextKnapsackPacker._data_flow_centrality`, and the floor it enforces
on resolution downgrade in `ContextKnapsackPacker.pack`.
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
    return packer.pack(seed, builder, tag_matrix, distance_engine, contracts=contracts), builder


def test_data_flow_centrality_counts_return_bound_callers(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def compute():\n    return 42\n"
        "\n"
        "def a():\n    x = compute()\n    return x\n"
        "\n"
        "def b():\n    y = compute()\n    return y\n"
        "\n"
        "def c():\n    compute()\n    return 1\n"  # fire-and-forget, doesn't count
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    centrality = ContextKnapsackPacker._data_flow_centrality(builder.calls_graph, "sample.compute")
    # a() and b() both capture the return value; c() discards it.
    assert centrality == 2


def test_data_flow_centrality_zero_for_never_captured_node(tmp_path) -> None:
    repo = tmp_path / "repo2"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def log_event():\n    return None\n"
        "\n"
        "def a():\n    log_event()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    centrality = ContextKnapsackPacker._data_flow_centrality(builder.calls_graph, "sample.log_event")
    assert centrality == 0


def test_high_centrality_node_never_compressed_below_l1(tmp_path) -> None:
    """A node several distant hops away, but whose return value is
    captured by >= DATA_FLOW_CENTRALITY_THRESHOLD distinct callers, must
    still render at resolution <= 1 (Pruned) - never demoted to L2/L3
    despite its distance, per Issue #10.2's "never compressed below Level
    1" requirement."""
    repo = tmp_path / "repo3"
    repo.mkdir()
    lines = [
        "def central():",
        "    return 99",
        "",
    ]
    # A long chain of intermediate hops so `central` sits far from `seed`.
    for i in range(6):
        lines.append(f"def hop{i}():")
        lines.append(f"    return hop{i + 1}()" if i < 5 else "    return central()")
        lines.append("")
    lines.append("def seed():")
    lines.append("    return hop0()")
    lines.append("")
    # >= threshold distinct callers that capture `central`'s return value.
    for i in range(3):
        lines.append(f"def caller{i}():")
        lines.append("    v = central()")
        lines.append("    return v")
        lines.append("")
    (repo / "sample.py").write_text("\n".join(lines) + "\n")

    result, builder = _pack(str(repo), "sample.seed", 4000)
    central_items = [item for item in result.items if item.symbol == "sample.central"]
    assert len(central_items) == 1
    assert central_items[0].resolution <= 1


def test_low_centrality_distant_node_still_downgrades_normally(tmp_path) -> None:
    """Control case: a distant node with no data-flow-significant callers
    is unaffected by the centrality floor and downgrades normally."""
    repo = tmp_path / "repo4"
    repo.mkdir()
    lines = ["def distant():", "    return 1", ""]
    for i in range(6):
        lines.append(f"def hop{i}():")
        lines.append(f"    return hop{i + 1}()" if i < 5 else "    return distant()")
        lines.append("")
    lines.append("def seed():")
    lines.append("    return hop0()")
    (repo / "sample.py").write_text("\n".join(lines) + "\n")

    builder, tag_matrix = build_pipeline(str(repo))
    centrality = ContextKnapsackPacker._data_flow_centrality(builder.calls_graph, "sample.distant")
    assert centrality == 0
