"""Tests for barrel-file / re-export resolution (Issues #6/#7):
`prism.graph.symbol_table.ExportRegistry`/`resolve_export`, wired into
`ConcreteGraphBuilder._register_exports`/`_resolve_reference_chain`.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.symbol_table import (
    EXPORT_RESOLUTION_MAX_DEPTH,
    ExportRegistry,
    GlobalSymbolTable,
    SymbolInfo,
    resolve_export,
)


def _symbol(qname: str, module: str, kind: str = "function") -> SymbolInfo:
    return SymbolInfo(qualified_name=qname, kind=kind, file=f"{module}.py", line_range=(1, 1), language_id="python", module=module)


# --------------------------------------------------------------------- #
# Unit: resolve_export against a synthetic registry/symbol table
# --------------------------------------------------------------------- #
def test_resolve_export_direct_definition_base_case() -> None:
    table = GlobalSymbolTable()
    table.add(_symbol("pkg.mod.Foo", "pkg.mod", kind="class"))
    registry = ExportRegistry()
    assert resolve_export("pkg.mod", "Foo", registry, table) == "pkg.mod.Foo"


def test_resolve_export_single_hop_barrel() -> None:
    table = GlobalSymbolTable()
    table.add(_symbol("app.order_service.OrderService", "app.order_service", kind="class"))
    registry = ExportRegistry()
    registry.add_explicit("app", "OrderService", "app.order_service", "OrderService")
    assert resolve_export("app", "OrderService", registry, table) == "app.order_service.OrderService"


def test_resolve_export_chases_multi_hop_barrel_chain() -> None:
    """`a` re-exports from `b`, which re-exports from `c`, where the real
    definition lives - the recursive chase must reach all the way through."""
    table = GlobalSymbolTable()
    table.add(_symbol("c.Thing", "c", kind="class"))
    registry = ExportRegistry()
    registry.add_explicit("a", "Thing", "b", "Thing")
    registry.add_explicit("b", "Thing", "c", "Thing")
    assert resolve_export("a", "Thing", registry, table) == "c.Thing"


def test_resolve_export_wildcard_honors_all_whitelist() -> None:
    table = GlobalSymbolTable()
    table.add(_symbol("pkg.impl.Public", "pkg.impl", kind="class"))
    table.add(_symbol("pkg.impl._Private", "pkg.impl", kind="class"))
    registry = ExportRegistry()
    registry.add_wildcard("pkg", "pkg.impl")
    registry.set_all_whitelist("pkg.impl", frozenset({"Public"}))
    assert resolve_export("pkg", "Public", registry, table) == "pkg.impl.Public"
    assert resolve_export("pkg", "_Private", registry, table) is None


def test_resolve_export_wildcard_without_all_uses_public_convention() -> None:
    table = GlobalSymbolTable()
    table.add(_symbol("pkg.impl.Public", "pkg.impl", kind="class"))
    table.add(_symbol("pkg.impl._Private", "pkg.impl", kind="class"))
    registry = ExportRegistry()
    registry.add_wildcard("pkg", "pkg.impl")
    assert resolve_export("pkg", "Public", registry, table) == "pkg.impl.Public"
    assert resolve_export("pkg", "_Private", registry, table) is None


def test_resolve_export_partial_export_map_falls_back_to_public_convention() -> None:
    """__all__.extend(...)-style dynamic modification: PARTIAL_EXPORT_MAP
    means the static whitelist can't be trusted, so a public name still
    resolves through the wildcard even if it's absent from the (partial)
    recorded whitelist."""
    table = GlobalSymbolTable()
    table.add(_symbol("pkg.impl.Dynamic", "pkg.impl", kind="class"))
    registry = ExportRegistry()
    registry.add_wildcard("pkg", "pkg.impl")
    registry.set_all_whitelist("pkg.impl", frozenset({"SomethingElse"}))
    registry.mark_partial_export("pkg.impl")
    assert resolve_export("pkg", "Dynamic", registry, table) == "pkg.impl.Dynamic"


# --------------------------------------------------------------------- #
# Invariant #3: circular import chains terminate deterministically <= 5 hops
# --------------------------------------------------------------------- #
def test_resolve_export_circular_chain_terminates() -> None:
    table = GlobalSymbolTable()
    registry = ExportRegistry()
    registry.add_explicit("a", "X", "b", "X")
    registry.add_explicit("b", "X", "a", "X")
    # Must terminate (return None - X is genuinely never defined anywhere)
    # rather than recursing forever or raising RecursionError.
    assert resolve_export("a", "X", registry, table) is None


def test_resolve_export_respects_max_depth() -> None:
    # A chain far longer than EXPORT_RESOLUTION_MAX_DEPTH, with no real
    # definition anywhere along it - must terminate with None (not a
    # RecursionError, not an infinite loop) well before reaching the end.
    table = GlobalSymbolTable()
    registry = ExportRegistry()
    chain_length = EXPORT_RESOLUTION_MAX_DEPTH * 4
    chain = [f"m{i}" for i in range(chain_length)]
    for a, b in zip(chain, chain[1:]):
        registry.add_explicit(a, "X", b, "X")
    assert resolve_export(chain[0], "X", registry, table) is None

    # A short chain, well within the bound, still resolves.
    table2 = GlobalSymbolTable()
    table2.add(_symbol("s2.X", "s2", kind="class"))
    registry2 = ExportRegistry()
    registry2.add_explicit("s0", "X", "s1", "X")
    registry2.add_explicit("s1", "X", "s2", "X")
    assert resolve_export("s0", "X", registry2, table2) == "s2.X"


def test_resolve_export_unknown_symbol_returns_none() -> None:
    table = GlobalSymbolTable()
    registry = ExportRegistry()
    assert resolve_export("nowhere", "Nothing", registry, table) is None


# --------------------------------------------------------------------- #
# End-to-end: real Python/TS barrel files through build_pipeline
# --------------------------------------------------------------------- #
def test_python_package_init_barrel_resolves_to_real_definition(tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("from .order_service import OrderService\n")
    (app / "order_service.py").write_text(
        "class OrderService:\n"
        "    def create(self, order_id):\n"
        "        return order_id\n"
    )
    (tmp_path / "consumer.py").write_text(
        "from app import OrderService\n"
        "\n"
        "def handle(order_id):\n"
        "    svc = OrderService()\n"
        "    return svc.create(order_id)\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert "app.OrderService" not in builder.symbol_table
    assert builder.graph.has_edge("consumer.handle", "app.order_service.OrderService")
    assert builder.graph.edges["consumer.handle", "app.order_service.OrderService"]["relation"] == "INSTANTIATES"
    assert builder.graph.has_edge("consumer.handle", "app.order_service.OrderService.create")


def test_python_multi_hop_barrel_chain_resolves(tmp_path) -> None:
    """`app/__init__.py` re-exports from `app.services` (also a package
    barrel), which re-exports from `app.services.order`, where the real
    class lives - two barrel hops deep."""
    app = tmp_path / "app"
    services = app / "services"
    services.mkdir(parents=True)
    (app / "__init__.py").write_text("from .services import OrderService\n")
    (services / "__init__.py").write_text("from .order import OrderService\n")
    (services / "order.py").write_text(
        "class OrderService:\n"
        "    def create(self):\n"
        "        return 1\n"
    )
    (tmp_path / "consumer.py").write_text(
        "from app import OrderService\n"
        "\n"
        "def handle():\n"
        "    return OrderService()\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("consumer.handle", "app.services.order.OrderService")


def test_python_wildcard_barrel_import_resolves(tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("from .order_service import *\n")
    (app / "order_service.py").write_text(
        "def create_order(order_id):\n"
        "    return order_id\n"
    )
    (tmp_path / "consumer.py").write_text(
        "from app import create_order\n"
        "\n"
        "def handle(order_id):\n"
        "    return create_order(order_id)\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("consumer.handle", "app.order_service.create_order")


def test_python_all_whitelist_restricts_star_import(tmp_path) -> None:
    impl = tmp_path / "impl.py"
    impl.write_text(
        "__all__ = ['Public']\n"
        "\n"
        "def Public():\n"
        "    return 1\n"
        "\n"
        "def _internal():\n"
        "    return 2\n"
    )
    (tmp_path / "consumer.py").write_text(
        "from impl import *\n"
        "\n"
        "def handle():\n"
        "    return Public()\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("consumer.handle", "impl.Public")


def test_ts_barrel_named_reexport_resolves(tmp_path) -> None:
    (tmp_path / "index.ts").write_text("export { OrderService } from './order_service';\n")
    (tmp_path / "order_service.ts").write_text(
        "export class OrderService {\n"
        "    create(orderId: string) {\n"
        "        return orderId;\n"
        "    }\n"
        "}\n"
    )
    (tmp_path / "consumer.ts").write_text(
        "import { OrderService } from './index';\n"
        "\n"
        "function handle(orderId: string) {\n"
        "    return new OrderService();\n"
        "}\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("consumer.handle", "order_service.OrderService")


def test_ts_barrel_wildcard_reexport_resolves(tmp_path) -> None:
    (tmp_path / "index.ts").write_text("export * from './widgets';\n")
    (tmp_path / "widgets.ts").write_text(
        "export class Widget {\n"
        "    render() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    (tmp_path / "consumer.ts").write_text(
        "import { Widget } from './index';\n"
        "\n"
        "function handle() {\n"
        "    return new Widget();\n"
        "}\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("consumer.handle", "widgets.Widget")


def test_ts_bare_directory_import_resolves_through_index_barrel(tmp_path) -> None:
    """The real, confirmed bug this guards: `import { x } from './core'`
    (a *directory* reference, resolved by every real JS/TS toolchain to
    `core/index.ts` implicitly - no literal `index` in the specifier at
    all, unlike `test_ts_barrel_named_reexport_resolves`'s explicit
    `'./index'` above) previously computed a *different* module string
    for `core/index.ts`'s own registration (`"core.index"`, since nothing
    stripped the trailing `index` segment there) than for the importing
    side (`"core"`, correctly - the specifier never mentions `index` to
    strip in the first place). The two never matched, so this exact
    shape - confirmed live against tRPC's own `packages/server/src`,
    which imports across 15 such `index.ts` barrels - silently resolved
    to a phantom, unregistered node instead of the real re-exported
    symbol."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "initTRPC.ts").write_text(
        "export function initTRPC() {\n"
        "    return 1;\n"
        "}\n"
    )
    (core_dir / "index.ts").write_text("export { initTRPC } from './initTRPC';\n")
    (tmp_path / "main.ts").write_text(
        "import { initTRPC } from './core';\n"
        "\n"
        "function useIt() {\n"
        "    return initTRPC();\n"
        "}\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("main.useIt", "core.initTRPC.initTRPC")


def test_ts_root_level_index_barrel_resolves(tmp_path) -> None:
    """The edge case `test_ts_bare_directory_import_resolves_through_
    index_barrel` doesn't cover: `index.ts` sitting at the *repo root*
    itself strips to an *empty* module string (the same convention a
    root-level Python `__init__.py` already gets), which must not
    corrupt the barrel-fallback's own `from_import.rsplit(".", 1)` split
    into a bare, unsplittable name."""
    (tmp_path / "widgets.ts").write_text(
        "export class Widget {\n"
        "    render() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    (tmp_path / "index.ts").write_text("export { Widget } from './widgets';\n")
    (tmp_path / "consumer.ts").write_text(
        "import { Widget } from '.';\n"
        "\n"
        "function handle() {\n"
        "    return new Widget();\n"
        "}\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("consumer.handle", "widgets.Widget")


def test_ts_direct_file_import_unaffected_by_index_stripping(tmp_path) -> None:
    """Regression guard: a plain, non-directory relative import (`./utils`
    resolving directly to `utils.ts`, not a directory's `index.ts`) must
    keep resolving exactly as it always has - the index-barrel fix must
    not touch this, more common shape at all."""
    (tmp_path / "utils.ts").write_text(
        "export function helper() {\n"
        "    return 1;\n"
        "}\n"
    )
    (tmp_path / "main.ts").write_text(
        "import { helper } from './utils';\n"
        "\n"
        "function useIt() {\n"
        "    return helper();\n"
        "}\n"
    )
    builder, _tag_matrix = build_pipeline(str(tmp_path))
    assert builder.graph.has_edge("main.useIt", "utils.helper")
