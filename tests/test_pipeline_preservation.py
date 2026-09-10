"""Integration test for Prism v1.1's Causal Coupling: a synthetic
3-stage pipeline (`parse_order -> store_order -> emit_audit`) plus a
structurally-unrelated logger, proving the submodular knapsack
(`prism.packer.submodular_knapsack`) preserves the real causal pipe under
a tight budget rather than admitting the disconnected logger.

The orchestrator function (`process_order`) is what actually establishes
the causal *coupling*: `data = parse_order(raw); result = store_order(data)`
is exactly the "variable pass" data-flow shape
(`prism.traversal.data_flow_py`) that produces a synthetic causal edge
`parse_order -> store_order` (`prism.traversal.continuous_dijkstra`) -
without that edge, `store_order` and `parse_order` are mere siblings
under a shared caller with no direct connection to each other at all,
and there would be nothing for "causally coupled edges shorten graph
distance" to actually demonstrate.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import pack_symbol_context
from prism.traversal.continuous_dijkstra import compute_topological_distances


def _pipeline_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pipeline.py").write_text(
        "def parse_order(raw):\n"
        "    return clean(raw)\n"
        "\n"
        "\n"
        "def clean(raw):\n"
        "    return raw.strip()\n"
        "\n"
        "\n"
        "def store_order(data):\n"
        "    return data\n"
        "\n"
        "\n"
        "def emit_audit(result):\n"
        "    return result\n"
        "\n"
        "\n"
        "def log_message(msg):\n"
        "    return msg\n"
        "\n"
        "\n"
        "def process_order(raw):\n"
        "    data = parse_order(raw)\n"
        "    result = store_order(data)\n"
        "    emit_audit(result)\n"
        "    log_message('done')\n"
    )
    return repo


def test_store_order_is_causally_reachable_from_parse_order(tmp_path):
    """The precondition the rest of this test depends on: a real
    synthetic causal edge connects parse_order directly to store_order -
    without it there would be nothing to "preserve"."""
    repo = _pipeline_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    distances = compute_topological_distances(builder, "pipeline.parse_order")
    assert "pipeline.store_order" in distances
    assert distances["pipeline.store_order"] < float("inf")


def test_log_message_is_not_causally_reachable_from_parse_order(tmp_path):
    """The disconnected logger has no data-flow or guard relationship to
    parse_order at all - it is structurally *impossible* for the packer
    to admit it as a candidate when seeded at parse_order, the strongest
    possible form of "preserving the causal pipe rather than picking a
    disconnected logger"."""
    repo = _pipeline_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    distances = compute_topological_distances(builder, "pipeline.parse_order")
    assert "pipeline.log_message" not in distances


def test_store_order_is_closer_than_the_real_direct_callee_clean(tmp_path):
    """The causal coupling doesn't just make store_order *reachable* - it
    makes it *closer* than parse_order's own real, direct callee `clean`
    (a plain 1.0-cost structural hop), because the data-flow-backed
    synthetic edge is priced below 1.0 (W > 1.0 -> cost = 1/W < 1.0)."""
    repo = _pipeline_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    distances = compute_topological_distances(builder, "pipeline.parse_order")
    assert distances["pipeline.store_order"] < distances["pipeline.clean"]


def test_tight_budget_admits_store_order_over_the_disconnected_logger(tmp_path):
    """Set a budget that fits the seed plus exactly two more items -
    under causal coupling, store_order (the real next pipeline stage) is
    packed; log_message (the disconnected logger) never can be, no
    matter how the budget is sized, since it is never even a candidate.
    """
    repo = _pipeline_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    # Each function's own source is a couple of tokens - a budget of
    # ~35 tokens comfortably fits the seed plus two more real items
    # without accidentally fitting every reachable node.
    result = pack_symbol_context(builder, "pipeline.parse_order", target_budget=35)

    assert "pipeline.store_order" in result.selected
    assert "pipeline.log_message" not in result.selected
    assert len(result.selected) <= 3  # seed + at most two more


def test_full_pipeline_chain_is_reachable_in_causal_order(tmp_path):
    """The whole chain - parse_order -> store_order -> emit_audit -
    is reachable in strictly increasing causal distance, confirming the
    pipeline's own real sequence is what the causal graph actually
    encodes, not an accident of one single edge."""
    repo = _pipeline_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    distances = compute_topological_distances(builder, "pipeline.parse_order")
    assert distances["pipeline.store_order"] < distances["pipeline.emit_audit"]


def test_generous_budget_packs_the_entire_causal_pipe(tmp_path):
    repo = _pipeline_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    result = pack_symbol_context(builder, "pipeline.parse_order", target_budget=1000)
    assert "pipeline.store_order" in result.selected
    assert "pipeline.emit_audit" in result.selected
    assert "pipeline.log_message" not in result.selected
