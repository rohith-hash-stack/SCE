"""Tests for Item 17 (second post-implementation audit): TypeScript
Interface & Type Regression Suite - `tests/fixtures/ts/
interfaces_and_types.ts` verifies interface registration
(`kind="interface"`, previously not captured at all) and EXTENDS/
IMPLEMENTS edges for interface-to-interface `extends` and class
`implements`, neither of which resolved before this fix (a real,
pre-existing bug this surfaced: TS's grammar gives a type-position
reference its own node type, `type_identifier`, which
`flatten_reference_chain` couldn't recognize at all until this fix).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from prism.cli import build_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "ts" / "interfaces_and_types.ts"


def _build(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(FIXTURE, repo / "interfaces_and_types.ts")
    return build_pipeline(str(repo))


def test_order_service_registers_as_interface(tmp_path) -> None:
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("interfaces_and_types.OrderService")
    assert symbol is not None
    assert symbol.kind == "interface"


def test_base_service_registers_as_interface(tmp_path) -> None:
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("interfaces_and_types.BaseService")
    assert symbol is not None
    assert symbol.kind == "interface"


def test_extends_edge_from_order_service_to_base_service(tmp_path) -> None:
    builder, _tag_matrix = _build(tmp_path)
    assert builder.graph.has_edge(
        "interfaces_and_types.OrderService", "interfaces_and_types.BaseService"
    )
    data = builder.graph.get_edge_data(
        "interfaces_and_types.OrderService", "interfaces_and_types.BaseService"
    )
    assert data.get("relation") == "EXTENDS"


def test_implements_edge_from_order_service_impl_to_order_service(tmp_path) -> None:
    builder, _tag_matrix = _build(tmp_path)
    assert builder.graph.has_edge(
        "interfaces_and_types.OrderServiceImpl", "interfaces_and_types.OrderService"
    )
    data = builder.graph.get_edge_data(
        "interfaces_and_types.OrderServiceImpl", "interfaces_and_types.OrderService"
    )
    assert data.get("relation") == "IMPLEMENTS"


def test_order_service_impl_registers_as_class_not_interface(tmp_path) -> None:
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("interfaces_and_types.OrderServiceImpl")
    assert symbol is not None
    assert symbol.kind == "class"


def test_type_alias_does_not_crash_indexing_or_get_registered_as_a_symbol(tmp_path) -> None:
    """`type Handler = (req: Request) => Response;` isn't a class,
    interface, function, or method - Prism doesn't currently register
    type aliases as symbols at all, and this fixture's own presence
    (parsed without error, real symbols before/after it in the same file
    still register correctly) is itself the regression coverage that
    adding interface support didn't break parsing around a type alias."""
    builder, _tag_matrix = _build(tmp_path)
    assert not any("Handler" in n for n in builder.symbol_table.all_qualified_names())
    # Real symbols on both sides of the type alias in the source file
    # still registered correctly.
    assert builder.symbol_table.get("interfaces_and_types.OrderService") is not None
    assert builder.symbol_table.get("interfaces_and_types.OrderServiceImpl") is not None


def test_interface_methods_are_not_registered_as_standalone_symbols(tmp_path) -> None:
    """Interface members are signatures, not implementations
    (`method_signature`, a different grammar node from `method_definition`
    - confirmed directly) - `execute`/`cancel` inside BaseService/
    OrderService's own interface bodies must not appear as registered
    methods (only OrderServiceImpl's real implementations should)."""
    builder, _tag_matrix = _build(tmp_path)
    assert builder.symbol_table.get("interfaces_and_types.BaseService.execute") is None
    assert builder.symbol_table.get("interfaces_and_types.OrderService.cancel") is None
    assert builder.symbol_table.get("interfaces_and_types.OrderServiceImpl.execute") is not None
    assert builder.symbol_table.get("interfaces_and_types.OrderServiceImpl.cancel") is not None


def test_class_extends_still_resolves_via_plain_identifier(tmp_path) -> None:
    """Regression guard: a class's own `extends` clause names a
    value-position `identifier` (not `type_identifier`) in TS's grammar -
    confirmed directly - so adding `type_identifier` to
    IDENTIFIER_NODE_TYPES[TYPESCRIPT] for Item 17 must not be the only
    path that works; ordinary class-to-class EXTENDS (already working
    before this fix) must keep working unchanged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "chain.ts").write_text(
        "class Base {\n  m(): void {}\n}\n\nclass Derived extends Base {\n}\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("chain.Derived", "chain.Base")
    assert builder.graph.get_edge_data("chain.Derived", "chain.Base")["relation"] == "EXTENDS"


def test_tsx_interface_also_registers(tmp_path) -> None:
    """TSX shares TYPESCRIPT_QUERIES directly - confirm the fix applies
    there too, not just plain .ts files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widget.tsx").write_text(
        "interface WidgetProps {\n  label: string;\n}\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    symbol = builder.symbol_table.get("widget.WidgetProps")
    assert symbol is not None
    assert symbol.kind == "interface"
