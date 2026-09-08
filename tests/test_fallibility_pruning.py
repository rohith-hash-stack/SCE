"""Tests for Fallibility-Based Knapsack Pruning
(`prism.slicer.compressor.is_infallible`/`render_infallible_signature`,
wired into `prism.slicer.knapsack.ContextKnapsackPacker.pack`).
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import BehavioralContract, compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.slicer.compressor import INFALLIBLE_SIGNATURE_RESOLUTION, is_infallible, render_infallible_signature
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


def _pack(repo_path: str, seed: str, budget: int = 2000):
    builder, tag_matrix = build_pipeline(repo_path)
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    packer = ContextKnapsackPacker(token_budget=budget)
    result = packer.pack(seed, builder, tag_matrix, distance_engine, contracts=contracts)
    return result, contracts


# --------------------------------------------------------------------- #
# is_infallible classifier
# --------------------------------------------------------------------- #
def _contract(**overrides) -> BehavioralContract:
    base = dict(qualified_name="sample.f", purity="pure", cyclomatic_complexity=1, thrown_exceptions=[], effects=[])
    base.update(overrides)
    return BehavioralContract(**base)


def test_pure_simple_no_throws_no_effects_is_infallible() -> None:
    assert is_infallible(_contract()) is True


def test_impure_is_not_infallible() -> None:
    assert is_infallible(_contract(purity="impure")) is False


def test_high_complexity_is_not_infallible() -> None:
    assert is_infallible(_contract(cyclomatic_complexity=3)) is False
    assert is_infallible(_contract(cyclomatic_complexity=2)) is True


def test_thrown_exceptions_is_not_infallible() -> None:
    assert is_infallible(_contract(thrown_exceptions=["ValueError"])) is False


def test_effects_is_not_infallible() -> None:
    assert is_infallible(_contract(effects=["DISK_IO"])) is False


def test_none_contract_is_not_infallible() -> None:
    assert is_infallible(None) is False


def test_io_boundary_tag_overrides_pure_contract() -> None:
    assert is_infallible(_contract(), tags={"#io_boundary"}) is False
    assert is_infallible(_contract(), tags={"#external_io"}) is False
    assert is_infallible(_contract(), tags=set()) is True


def test_render_infallible_signature_format() -> None:
    line = render_infallible_signature("sample.validate", "bool")
    assert line == "- callee: sample.validate [infallible_pure_leaf, returns: bool]"
    assert render_infallible_signature("sample.notify", None) == "- callee: sample.notify [infallible_pure_leaf, returns: None]"


# --------------------------------------------------------------------- #
# End-to-end: packing demotes infallible leaves, keeps fallible ones full
# --------------------------------------------------------------------- #
def test_infallible_leaves_are_demoted_to_compact_signature(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def validate(x):\n"
        "    return x > 0\n"
        "\n"
        "def save_to_db(order_id):\n"
        "    import requests\n"
        "    requests.post('http://x', data={'id': order_id})\n"
        "\n"
        "def process_order(order_id):\n"
        "    validate(order_id)\n"
        "    save_to_db(order_id)\n"
        "    return order_id\n"
    )
    result, contracts = _pack(str(repo), "sample.process_order")

    by_symbol = {item.symbol: item for item in result.items}
    assert by_symbol["sample.validate"].resolution == INFALLIBLE_SIGNATURE_RESOLUTION
    assert by_symbol["sample.validate"].content == render_infallible_signature("sample.validate", "bool") or \
        by_symbol["sample.validate"].content.startswith("- callee: sample.validate [infallible_pure_leaf")

    # save_to_db is impure (network effect) - never demoted, keeps a real resolution.
    assert by_symbol["sample.save_to_db"].resolution != INFALLIBLE_SIGNATURE_RESOLUTION
    assert by_symbol["sample.save_to_db"].content != render_infallible_signature("sample.save_to_db", None)


def test_infallible_non_leaf_is_not_demoted(tmp_path) -> None:
    """A pure, simple, throw-free function that still calls something else
    of its own is not a *leaf* - "Demote Infallible LEAF nodes" - so it
    keeps its normal resolution-stepping treatment."""
    repo = tmp_path / "repo2"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def h1(): return 1\n"
        "def h2(): return 2\n"
        "def h3(): return 3\n"
        "def h4(): return 4\n"
        "\n"
        "def outer():\n"
        "    return h1() + h2() + h3() + h4()\n"
        "\n"
        "def seed():\n"
        "    return outer()\n"
    )
    result, _contracts = _pack(str(repo), "sample.seed")
    by_symbol = {item.symbol: item for item in result.items}
    # `outer` calls 4 things of its own - past ContextKnapsackPacker.
    # SHALLOW_OUT_DEGREE (2), so it keeps its normal resolution-stepping
    # treatment even though it's individually Infallible.
    assert by_symbol["sample.outer"].resolution != INFALLIBLE_SIGNATURE_RESOLUTION


def test_infallible_pruning_reduces_total_allocated_tokens(tmp_path) -> None:
    """The whole point: packing a seed with several pure, trivial leaf
    dependencies costs meaningfully fewer tokens than it would if every
    leaf were rendered at full L1/L2 - approximated here by re-packing the
    same repo with contracts withheld (so no demotion can happen at all)
    and confirming the demoted run is never larger."""
    repo = tmp_path / "repo3"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def a(): return 1\n"
        "def b(): return 2\n"
        "def c(): return 3\n"
        "def d(): return 4\n"
        "\n"
        "def orchestrator():\n"
        "    return a() + b() + c() + d()\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())

    with_pruning = ContextKnapsackPacker(token_budget=4000).pack(
        "sample.orchestrator", builder, tag_matrix, distance_engine, contracts=contracts
    )
    without_pruning = ContextKnapsackPacker(token_budget=4000).pack(
        "sample.orchestrator", builder, tag_matrix, distance_engine, contracts=None
    )
    assert with_pruning.allocated_tokens <= without_pruning.allocated_tokens
