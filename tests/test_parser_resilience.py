"""Tests for Item 4 (second post-implementation audit): per-file error
boundaries in `ConcreteGraphBuilder.pass1_collect_definitions`/
`pass2_resolve_calls` - a single malformed or pathological file must
never crash the whole `index`/`query` run.
"""
from __future__ import annotations

import prism.graph.concrete_builder as cb
from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder


def _write_two_files(tmp_path, bad_content: str, bad_name: str = "bad.py"):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / bad_name).write_text(bad_content)
    (repo / "good.py").write_text("def g():\n    return 1\n")
    return repo


# --------------------------------------------------------------------- #
# Real adversarial fixtures - proving the *engine* stays up end to end
# --------------------------------------------------------------------- #
def test_deeply_nested_not_chain_does_not_crash_indexing(tmp_path) -> None:
    """500+-deep nesting was confirmed (earlier in this same audit) to
    crash `ast.parse` well past this depth and `NodeTransformer.visit`
    well *before* it - this proves the whole `index` pipeline (not just
    the isolated compressor call already fixed) stays up end to end on
    exactly that adversarial shape, and the other file in the repo is
    still indexed normally regardless."""
    depth = 3000
    src = "def f():\n    x = " + "not " * depth + "True\n    return x\n"
    repo = _write_two_files(tmp_path, src)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert "good.g" in builder.symbol_table.all_qualified_names()


def test_500_nested_parentheses_does_not_crash_indexing(tmp_path) -> None:
    depth = 500
    src = "def f():\n    x = " + "(" * depth + "1" + ")" * depth + "\n    return x\n"
    repo = _write_two_files(tmp_path, src)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert "good.g" in builder.symbol_table.all_qualified_names()


def test_500_nested_brackets_does_not_crash_indexing(tmp_path) -> None:
    depth = 500
    src = "def f():\n    x = " + "[" * depth + "1" + "]" * depth + "\n    return x\n"
    repo = _write_two_files(tmp_path, src)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert "good.g" in builder.symbol_table.all_qualified_names()


def test_deeply_nested_go_blocks_do_not_crash_indexing(tmp_path) -> None:
    depth = 600
    src = (
        "package main\nfunc f() {\n"
        + "if true {\n" * depth
        + "x := 1\n_ = x\n"
        + "}\n" * depth
        + "}\n"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.go").write_text(src)
    (repo / "good.go").write_text("package main\nfunc g() int { return 1 }\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    assert any(n.endswith(".g") for n in builder.symbol_table.all_qualified_names())


# --------------------------------------------------------------------- #
# Direct fault injection - deterministic proof the error boundary itself
# catches, records, and continues, independent of whether any known
# real-world input shape happens to still reach it (several were closed
# by earlier depth-guard fixes in this same audit, confirmed above - a
# genuine improvement, but it means those specific inputs no longer
# exercise this particular boundary).
# --------------------------------------------------------------------- #
def test_pass1_error_boundary_catches_and_continues(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def f():\n    return 1\n")
    (repo / "good.py").write_text("def g():\n    return 1\n")

    real = ConcreteGraphBuilder._collect_definitions_in_file

    def fake(self, parsed, module):
        if module == "bad":
            raise RecursionError("synthetic failure for test")
        return real(self, parsed, module)

    monkeypatch.setattr(ConcreteGraphBuilder, "_collect_definitions_in_file", fake)

    from prism.cli import discover_files

    files = discover_files(str(repo))
    builder = ConcreteGraphBuilder(str(repo))
    builder.pass1_collect_definitions(files)

    assert "good.g" in builder.symbol_table.all_qualified_names()
    assert "bad.f" not in builder.symbol_table.all_qualified_names()
    assert len(builder.index_errors) == 1
    assert builder.index_errors[0]["category"] == "RecursionError"
    assert builder.index_errors[0]["stage"] == "pass1"
    assert "bad.py" in builder.index_errors[0]["file"]


def test_pass1_error_boundary_catches_unicode_decode_error(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def f():\n    return 1\n")
    (repo / "good.py").write_text("def g():\n    return 1\n")

    real = ConcreteGraphBuilder._collect_definitions_in_file

    def fake(self, parsed, module):
        if module == "bad":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic failure for test")
        return real(self, parsed, module)

    monkeypatch.setattr(ConcreteGraphBuilder, "_collect_definitions_in_file", fake)

    from prism.cli import discover_files

    files = discover_files(str(repo))
    builder = ConcreteGraphBuilder(str(repo))
    builder.pass1_collect_definitions(files)

    assert "good.g" in builder.symbol_table.all_qualified_names()
    assert len(builder.index_errors) == 1
    assert builder.index_errors[0]["category"] == "UnicodeDecodeError"


def test_pass2a_error_boundary_catches_and_continues(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def f():\n    return g()\n")
    (repo / "good.py").write_text("def g():\n    return 1\n")

    from prism.cli import discover_files

    files = discover_files(str(repo))
    builder = ConcreteGraphBuilder(str(repo))
    builder.pass1_collect_definitions(files)

    real = ConcreteGraphBuilder._build_import_map

    def fake(self, parsed, module):
        if module == "bad":
            raise RecursionError("synthetic failure for test")
        return real(self, parsed, module)

    monkeypatch.setattr(ConcreteGraphBuilder, "_build_import_map", fake)
    builder.pass2_resolve_calls(files)

    assert len(builder.index_errors) == 1
    assert builder.index_errors[0]["stage"] == "pass2a"
    # good.py's own symbols are still fully registered (Pass 1 already
    # completed for it before the Pass 2a fault was injected).
    assert "good.g" in builder.symbol_table.all_qualified_names()


def test_pass2b_error_boundary_catches_and_continues(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def f():\n    return 1\n")
    (repo / "good.py").write_text("def g():\n    return 1\n")

    from prism.cli import discover_files

    files = discover_files(str(repo))
    builder = ConcreteGraphBuilder(str(repo))
    builder.pass1_collect_definitions(files)

    real = ConcreteGraphBuilder._resolve_calls_for_file_symbols

    def fake(self, file_symbols, parsed, module, import_map):
        if module == "bad":
            raise RecursionError("synthetic failure for test")
        return real(self, file_symbols, parsed, module, import_map)

    monkeypatch.setattr(ConcreteGraphBuilder, "_resolve_calls_for_file_symbols", fake)
    builder.pass2_resolve_calls(files)

    assert len(builder.index_errors) == 1
    assert builder.index_errors[0]["stage"] == "pass2b"
    # bad.py's own Pass 1 definitions survive (only call resolution for
    # that file failed) - a real, intentional partial-degradation
    # behavior, not a full rollback.
    assert "bad.f" in builder.symbol_table.all_qualified_names()


def test_unexpected_exception_type_is_not_swallowed(tmp_path, monkeypatch) -> None:
    """The error boundary is deliberately narrow (RecursionError,
    UnicodeDecodeError only) - an unrelated bug must still surface, not
    be silently absorbed as if it were an adversarial-input case."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def f():\n    return 1\n")

    def fake(self, parsed, module):
        raise ValueError("a real bug, not an adversarial-input case")

    monkeypatch.setattr(ConcreteGraphBuilder, "_collect_definitions_in_file", fake)

    from prism.cli import discover_files

    files = discover_files(str(repo))
    builder = ConcreteGraphBuilder(str(repo))
    try:
        builder.pass1_collect_definitions(files)
        raised = False
    except ValueError:
        raised = True
    assert raised
