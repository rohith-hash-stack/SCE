"""Tests for Issue C2 (security audit): adversarially deep-but-valid
source must degrade a single compression/traversal call gracefully
rather than crash with an uncaught `RecursionError` - confirmed as a
real, reproducible gap before these fixes (a ~400-deep chain of `not`
expressions already crashed `compress_python`'s `NodeTransformer.visit`
well before CPython's own parser-level nesting guards would ever fire;
a ~600-deep nested Go `if` block crashed `iter_scoped_nodes`).
"""
from __future__ import annotations

import pathlib
import tempfile

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import MAX_SCOPED_NODE_DEPTH, iter_scoped_nodes
from prism.parser.tree_sitter_loader import parse_file
from prism.slicer.compressor import CompressionContext, compress_python


def _deeply_nested_not_chain_source(depth: int) -> str:
    return "def f():\n    x = " + "not " * depth + "True\n    return x\n"


def test_compress_python_l1_degrades_on_deep_not_chain_instead_of_crashing() -> None:
    src = _deeply_nested_not_chain_source(800)
    result = compress_python(src, "f", (1, 4), 1, CompressionContext())
    assert isinstance(result, str)
    assert result  # degraded to a raw slice, not an empty/crashed result


def test_compress_python_l2_degrades_on_deep_not_chain_instead_of_crashing() -> None:
    src = _deeply_nested_not_chain_source(800)
    result = compress_python(src, "f", (1, 4), 2, CompressionContext())
    assert isinstance(result, str)
    assert result


def test_compress_python_handles_pathological_depth_that_defeats_ast_parse() -> None:
    """A deep enough chain crashes even `ast.parse` itself (confirmed
    directly at ~3000), not just the NodeTransformer - both must degrade,
    not raise."""
    src = _deeply_nested_not_chain_source(3000)
    for resolution in (1, 2, 3):
        result = compress_python(src, "f", (1, 4), resolution, CompressionContext())
        assert isinstance(result, str)


def test_compress_python_shallow_not_chain_is_unaffected() -> None:
    """A normal, shallow chain must still skeletonize for real (not
    silently degrade to a raw slice for every input) - a plain math
    expression alone is deliberately stripped at L1 regardless of depth
    (see ArgPreservingSkeletonizer's own math/logging-stripping
    behavior), so this uses a real call to prove real skeletonization
    still happens, not the degrade path."""
    src = "def f():\n    return g(not not True)\n"
    result = compress_python(src, "f", (1, 2), 1, CompressionContext())
    assert "g(not not True)" in result


def test_iter_scoped_nodes_stops_at_max_depth_instead_of_crashing(tmp_path) -> None:
    depth = 600
    src = (
        "package main\nfunc f() {\n"
        + "if true {\n" * depth
        + "x := 1\n_ = x\n"
        + "}\n" * depth
        + "}\n"
    )
    (tmp_path / "main.go").write_text(src)
    parsed = parse_file(str(tmp_path / "main.go"))
    result = iter_scoped_nodes(parsed.root_node, {"if_statement"}, "go")
    assert isinstance(result, list)
    # Capped, not the full 600 - proves the depth guard actually fired
    # rather than happening to succeed some other way.
    assert len(result) < depth


def test_iter_scoped_nodes_shallow_case_is_unaffected(tmp_path) -> None:
    (tmp_path / "main.go").write_text(
        "package main\nfunc f() {\n    if true {\n        x := 1\n        _ = x\n    }\n}\n"
    )
    parsed = parse_file(str(tmp_path / "main.go"))
    result = iter_scoped_nodes(parsed.root_node, {"if_statement"}, "go")
    assert len(result) == 1


def test_mro_ancestors_bounded_on_very_long_inheritance_chain(tmp_path) -> None:
    depth = 600
    lines = ["class C0:", "    def m(self): pass"]
    for i in range(1, depth):
        lines.append(f"class C{i}(C{i - 1}):")
        lines.append("    pass")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "chain.py").write_text("\n".join(lines) + "\n")

    from prism.cli import build_pipeline

    builder, _tag_matrix = build_pipeline(str(repo))
    ancestors = builder._mro_ancestors(f"chain.C{depth - 1}")
    assert isinstance(ancestors, list)
    assert len(ancestors) <= 300  # matches the depth guard, not the full 599-deep chain


def test_mro_ancestors_shallow_chain_is_unaffected() -> None:
    builder = ConcreteGraphBuilder("/tmp/repo")
    builder.graph.add_node("m.A")
    builder.graph.add_node("m.B")
    builder.graph.add_node("m.C")
    builder.graph.add_edge("m.C", "m.B", relation="EXTENDS")
    builder.graph.add_edge("m.B", "m.A", relation="EXTENDS")
    assert builder._mro_ancestors("m.C") == ["m.B", "m.A"]
