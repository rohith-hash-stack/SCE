"""Tests for Higher-Order Function (HOF) & Callback Signature Contracts
(`prism.graph.call_site.detect_hof_callback_params`, wired into
`prism.graph.contracts.BehavioralContract.hof_callbacks`, rendered by
`prism.serializers.markdown`).
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.serializers.markdown import render_markdown
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


def test_go_named_callback_typedef_produces_hof_contract(tmp_path) -> None:
    repo = tmp_path / "go_repo"
    repo.mkdir()
    (repo / "recovery.go").write_text(
        "package recovery\n"
        "\n"
        "type RecoveryFunc func(c *Context, err any)\n"
        "\n"
        "func CustomRecovery(handle RecoveryFunc) HandlerFunc {\n"
        "\treturn nil\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    contract = contracts["recovery.CustomRecovery"]
    assert len(contract.hof_callbacks) == 1
    cb = contract.hof_callbacks[0]
    assert cb.param == "handle"
    assert cb.type == "RecoveryFunc"
    assert cb.invoked_dynamically is True
    assert cb.expected_signature == "func(c *Context, err any)"


def test_go_inline_callback_type_produces_hof_contract(tmp_path) -> None:
    repo = tmp_path / "go_repo2"
    repo.mkdir()
    (repo / "sample.go").write_text(
        "package sample\n"
        "\n"
        "func Process(handle func(x int, y string) error) error {\n"
        "\treturn nil\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    contract = contracts["sample.Process"]
    assert len(contract.hof_callbacks) == 1
    cb = contract.hof_callbacks[0]
    assert cb.param == "handle"
    assert cb.expected_signature == "func(x int, y string) error"


def test_python_callable_annotation_produces_hof_contract(tmp_path) -> None:
    repo = tmp_path / "py_repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "from typing import Callable\n"
        "\n"
        "def process(callback: Callable[[int, str], None]) -> None:\n"
        "    pass\n"
    )
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    contract = contracts["sample.process"]
    assert len(contract.hof_callbacks) == 1
    cb = contract.hof_callbacks[0]
    assert cb.param == "callback"
    assert "Callable" in cb.expected_signature


def test_typescript_inline_arrow_type_produces_hof_contract(tmp_path) -> None:
    repo = tmp_path / "ts_repo"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "function process(callback: (x: number, y: string) => void): void {\n"
        "    callback(1, \"a\");\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    contract = contracts["sample.process"]
    assert len(contract.hof_callbacks) == 1
    cb = contract.hof_callbacks[0]
    assert cb.param == "callback"
    assert "=>" in cb.expected_signature


def test_no_callback_params_yields_empty_hof_callbacks(tmp_path) -> None:
    repo = tmp_path / "plain_repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    assert contracts["sample.add"].hof_callbacks == []


def test_hof_callback_round_trips_through_to_dict_from_dict(tmp_path) -> None:
    repo = tmp_path / "go_repo3"
    repo.mkdir()
    (repo / "recovery.go").write_text(
        "package recovery\n"
        "\n"
        "type RecoveryFunc func(c *Context, err any)\n"
        "\n"
        "func CustomRecovery(handle RecoveryFunc) HandlerFunc {\n"
        "\treturn nil\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    contract = contracts["recovery.CustomRecovery"]

    from prism.graph.contracts import BehavioralContract

    roundtripped = BehavioralContract.from_dict(contract.to_dict())
    assert roundtripped.hof_callbacks == contract.hof_callbacks


def test_serializer_renders_higher_order_callbacks_section(tmp_path) -> None:
    repo = tmp_path / "go_repo4"
    repo.mkdir()
    (repo / "recovery.go").write_text(
        "package recovery\n"
        "\n"
        "type RecoveryFunc func(c *Context, err any)\n"
        "\n"
        "func CustomRecovery(handle RecoveryFunc) HandlerFunc {\n"
        "\treturn nil\n"
        "}\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    result = ContextKnapsackPacker(token_budget=2000).pack(
        "recovery.CustomRecovery", builder, tag_matrix, distance_engine, contracts=contracts
    )
    text = render_markdown(result, tag_matrix, contracts=contracts, graph=builder.graph)
    assert "Higher-Order Callbacks:" in text
    assert "param: handle" in text
    assert "type: RecoveryFunc" in text
    assert "invoked_dynamically: true" in text
    assert 'expected_signature: "func(c *Context, err any)"' in text
