"""Tests for Item 12 (second post-implementation audit): Incremental File
Caching - `prism.runtime.index_cache` and its wiring into
`prism.cli.build_pipeline`. Covers cache hits, invalidation on content
change/add/remove, cross-process rehydration fidelity (graph, symbol
table, tags, Go diagnostics), corruption resilience, and - the one thing
that actually broke during development - that a cache-hydrated builder
still renders real L0-L3 context (needs `ConcreteGraphBuilder.
parsed_file`, which nothing in the cached snapshot itself carries; see
`index_cache.py`'s own docstring for why).
"""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from prism.cli import LANGUAGE_TIER_TIER1_ONLY, build_pipeline, discover_files, main
from prism.runtime.index_cache import index_cache_path, load_pipeline_from_cache, save_pipeline_to_cache
from prism.slicer.compressor import ASTCompressor, CompressionContext


def _order_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text(
        "class OrderValidator:\n"
        "    def validate(self, amount):\n"
        "        return amount > 0\n"
        "\n"
        "\n"
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        self.validator = OrderValidator()\n"
        "\n"
        "    def create_order(self, amount):\n"
        "        if not self.validator.validate(amount):\n"
        "            raise ValueError('bad amount')\n"
        "        return amount\n"
    )
    return repo


def test_cache_file_is_created_at_expected_sqlite_path(tmp_path):
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo))
    db_path = index_cache_path(str(repo))
    assert db_path.exists()
    # A real SQLite file, not just a file at that path.
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"files", "snapshot"} <= tables


def test_second_build_is_a_cache_hit_with_identical_graph_shape(tmp_path):
    repo = _order_repo(tmp_path)
    builder1, tag_matrix1 = build_pipeline(str(repo))
    builder2, tag_matrix2 = build_pipeline(str(repo))

    assert sorted(builder1.symbol_table.all_qualified_names()) == sorted(builder2.symbol_table.all_qualified_names())
    assert sorted(builder1.graph.edges()) == sorted(builder2.graph.edges())
    assert sorted(builder1.graph.nodes()) == sorted(builder2.graph.nodes())
    assert tag_matrix1 == tag_matrix2


def test_cache_hit_round_trips_edge_and_node_attributes(tmp_path):
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo))
    builder, _tag_matrix = build_pipeline(str(repo))

    edge = builder.graph.edges["orders.OrderService.create_order", "orders.OrderValidator.validate"]
    assert edge["relation"] == "CALLS"

    node = builder.graph.nodes["orders.OrderService.create_order"]
    assert node["kind"] == "method"
    assert isinstance(node["line_range"], tuple)
    if "tags" in node:
        assert isinstance(node["tags"], set)


def test_cache_invalidates_when_file_content_changes(tmp_path):
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo))

    (repo / "orders.py").write_text(
        (repo / "orders.py").read_text() + "\n\ndef brand_new_function():\n    return 1\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert "orders.brand_new_function" in builder.symbol_table


def test_cache_invalidates_when_a_file_is_added(tmp_path):
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo))

    (repo / "extra.py").write_text("def extra_function():\n    return 2\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    assert "extra.extra_function" in builder.symbol_table


def test_cache_invalidates_when_a_file_is_removed(tmp_path):
    repo = _order_repo(tmp_path)
    (repo / "extra.py").write_text("def extra_function():\n    return 2\n")
    build_pipeline(str(repo))

    (repo / "extra.py").unlink()
    builder, _tag_matrix = build_pipeline(str(repo))
    assert "extra.extra_function" not in builder.symbol_table


def test_use_cache_false_never_reads_or_writes_the_cache(tmp_path):
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo), use_cache=False)
    assert not index_cache_path(str(repo)).exists()


def test_language_tier_change_does_not_reuse_the_other_tiers_cache(tmp_path):
    repo = _order_repo(tmp_path)
    (repo / "main.go").write_text("package main\n\nfunc Helper() {}\n")

    permissive_builder, _ = build_pipeline(str(repo))
    assert "main.Helper" in permissive_builder.symbol_table

    tier1_builder, _ = build_pipeline(str(repo), language_tier=LANGUAGE_TIER_TIER1_ONLY)
    assert "main.Helper" not in tier1_builder.symbol_table

    # And back to permissive again - must not have been clobbered by the
    # tier1-only run's own cache write.
    permissive_builder2, _ = build_pipeline(str(repo))
    assert "main.Helper" in permissive_builder2.symbol_table


def test_corrupt_cache_falls_back_to_a_full_rebuild(tmp_path):
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo))

    db_path = index_cache_path(str(repo))
    db_path.write_bytes(b"not a real sqlite database")

    builder, tag_matrix = build_pipeline(str(repo))
    assert "orders.OrderService.create_order" in builder.symbol_table
    assert builder.graph.has_edge("orders.OrderService.create_order", "orders.OrderValidator.validate")


def test_missing_cache_directory_is_created_on_save(tmp_path):
    repo = _order_repo(tmp_path)
    assert not (repo / ".prism").exists()
    build_pipeline(str(repo))
    assert index_cache_path(str(repo)).exists()


def test_cache_hit_preserves_go_receiver_call_resolution_diagnostics(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n"
        "\n"
        "type Context struct{}\n"
        "\n"
        "func (c *Context) JSON(code int) {}\n"
        "\n"
        "func Handler(c *Context) {\n"
        "    c.JSON(200)\n"
        "}\n"
    )
    builder1, _ = build_pipeline(str(repo))
    ratio1 = builder1.go_call_resolution_ratio
    total1 = builder1._go_receiver_call_sites_total

    builder2, _ = build_pipeline(str(repo))
    assert builder2.go_call_resolution_ratio == ratio1
    assert builder2._go_receiver_call_sites_total == total1
    assert total1 > 0


def test_cache_hit_still_renders_real_l0_source_via_parsed_file(tmp_path):
    """The regression this module's development actually hit: a
    cache-hydrated builder must still be able to render real source
    (L0), which needs `builder.parsed_file(...)` - nothing a JSON/SQLite
    snapshot of the graph and symbol table alone provides."""
    repo = _order_repo(tmp_path)
    build_pipeline(str(repo))  # populates the cache
    builder, _tag_matrix = build_pipeline(str(repo))  # cache hit

    symbol = builder.symbol_table.get("orders.OrderService.create_order")
    assert symbol is not None
    parsed = builder.parsed_file(symbol.file)
    assert parsed is not None
    source = parsed.source.decode("utf-8")

    context = CompressionContext(tags=set(), callees=[])
    rendered = ASTCompressor().compress(
        symbol.language_id, source, "create_order", symbol.line_range, 0, context
    )
    assert "def create_order" in rendered


def test_cache_hit_supports_a_full_cli_query_end_to_end(tmp_path):
    repo = _order_repo(tmp_path)
    runner = CliRunner()
    # First invocation populates the cache.
    result1 = runner.invoke(main, ["query", str(repo), "orders.OrderService.create_order", "--budget", "2000"])
    assert result1.exit_code == 0, result1.output
    # Second invocation is a cache hit end to end.
    result2 = runner.invoke(main, ["query", str(repo), "orders.OrderService.create_order", "--budget", "2000"])
    assert result2.exit_code == 0, result2.output
    assert "create_order" in result2.output


def test_index_cache_module_functions_are_directly_usable(tmp_path):
    """`load_pipeline_from_cache`/`save_pipeline_to_cache` are usable
    directly, not only through `build_pipeline` - a miss on an empty repo
    returns None, and a save-then-load round-trips."""
    repo = _order_repo(tmp_path)
    files = discover_files(str(repo))
    assert load_pipeline_from_cache(str(repo), files, "permissive") is None

    builder, tag_matrix = build_pipeline(str(repo), use_cache=False)
    save_pipeline_to_cache(str(repo), files, "permissive", builder, tag_matrix)

    loaded = load_pipeline_from_cache(str(repo), files, "permissive")
    assert loaded is not None
    loaded_builder, loaded_tag_matrix = loaded
    assert sorted(loaded_builder.symbol_table.all_qualified_names()) == sorted(builder.symbol_table.all_qualified_names())
    assert loaded_tag_matrix == tag_matrix
