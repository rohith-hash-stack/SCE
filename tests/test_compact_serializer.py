"""Tests for Metadata Serialization Compaction on High-Coupling Nodes
(`prism.serializers.markdown`'s fan-out-adaptive Outgoing Dependencies
rendering).
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.serializers.markdown import COMPACT_FANOUT_THRESHOLD, COMPACT_MAX_RENDERED, render_markdown
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


def _query(repo_path: str, seed: str, budget: int = 4000) -> str:
    builder, tag_matrix = build_pipeline(repo_path)
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    result = ContextKnapsackPacker(token_budget=budget).pack(seed, builder, tag_matrix, distance_engine, contracts=contracts)
    return render_markdown(result, tag_matrix, contracts=contracts, graph=builder.graph)


def _low_fanout_repo(tmp_path):
    repo = tmp_path / "low_fanout"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def a(): return 1\n"
        "def b(): return 2\n"
        "\n"
        "def seed():\n"
        "    a()\n"
        "    b()\n"
        "    return None\n"
    )
    return str(repo)


def _high_fanout_repo(tmp_path, n: int = 8):
    repo = tmp_path / "high_fanout"
    repo.mkdir()
    defs = "\n".join(f"def callee_{i}(): return {i}\n" for i in range(n))
    calls = "\n".join(f"    callee_{i}()" for i in range(n))
    (repo / "sample.py").write_text(f"{defs}\ndef seed():\n{calls}\n    return None\n")
    return str(repo)


def test_low_fanout_uses_full_dependency_block(tmp_path) -> None:
    repo = _low_fanout_repo(tmp_path)
    text = _query(repo, "sample.seed")
    assert "Outgoing Dependencies:" in text
    assert "Compact View" not in text
    assert "  - target: sample.a" in text


def test_high_fanout_switches_to_compact_view(tmp_path) -> None:
    assert COMPACT_FANOUT_THRESHOLD == 5  # the test fixture below assumes this
    repo = _high_fanout_repo(tmp_path, n=8)
    text = _query(repo, "sample.seed")
    assert "Outgoing Dependencies (Compact View - 8 total):" in text
    # Compact one-liner shape, not the old multi-line "- target: X" block.
    assert "  - target: sample.callee_0" not in text
    assert "sample.callee_0 [pure]" in text


def test_compact_view_caps_at_max_rendered_with_summary(tmp_path) -> None:
    n = COMPACT_MAX_RENDERED + 4
    repo = _high_fanout_repo(tmp_path, n=n)
    text = _query(repo, "sample.seed")
    assert f"Outgoing Dependencies (Compact View - {n} total):" in text
    rendered_lines = [ln for ln in text.splitlines() if ln.strip().startswith("- sample.callee_") or ln.strip().startswith("- callee_")]
    assert len(rendered_lines) <= COMPACT_MAX_RENDERED
    assert "other pure leaf utilities" in text


def test_critical_return_bound_dependency_stays_expanded_under_compaction(tmp_path) -> None:
    repo = tmp_path / "critical_repo"
    repo.mkdir()
    defs = "\n".join(f"def callee_{i}(): return {i}\n" for i in range(7))
    (repo / "sample.py").write_text(
        f"{defs}\n"
        "def critical(): return 99\n"
        "\n"
        "def seed():\n"
        + "\n".join(f"    callee_{i}()" for i in range(7))
        + "\n    x = critical()\n"
        "    return x\n"
    )
    text = _query(str(repo), "sample.seed")
    assert "Compact View" in text
    # The return-bound callee keeps its "-> bound_to:" critical form, not
    # the bare one-line compact form.
    assert "sample.critical -> bound_to: x" in text
    assert "return_bound" in text


def test_backward_compatible_without_contracts(tmp_path) -> None:
    """render_markdown must keep working when contracts is omitted
    entirely (the pre-compaction call signature) - the compaction logic
    reads `contracts.get(target)`, which must tolerate a missing/None
    contract gracefully."""
    repo = _high_fanout_repo(tmp_path, n=8)
    builder, tag_matrix = build_pipeline(repo)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    result = ContextKnapsackPacker(token_budget=4000).pack("sample.seed", builder, tag_matrix, distance_engine)
    text = render_markdown(result, tag_matrix, graph=builder.graph)
    assert "Outgoing Dependencies" in text
