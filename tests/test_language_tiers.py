"""Tests for formal Precision Tiers (Issues #1/#2/#3):
`prism.language_tiers`, the `--language-tier` CLI flag, and the
`CompressionProvider` interface.
"""
from __future__ import annotations

from click.testing import CliRunner

from prism.cli import (
    LANGUAGE_TIER_PERMISSIVE,
    LANGUAGE_TIER_TIER1_ONLY,
    build_pipeline,
    discover_files,
    main,
)
from prism.language_tiers import (
    LANGUAGE_PRECISION_TIER,
    PRECISION_TIER_LABELS,
    TIER_1_ONLY_LANGUAGES,
    PrecisionTier,
    precision_tier_for,
)
from prism.parser.tree_sitter_loader import EXTENSION_LANGUAGE_MAP, LanguageID
from prism.slicer.compressor import ASTCompressor, CompressionProvider


def test_every_supported_language_is_classified() -> None:
    unclassified = set(EXTENSION_LANGUAGE_MAP.values()) - set(LANGUAGE_PRECISION_TIER)
    assert not unclassified


def test_python_is_the_only_tier1_language() -> None:
    assert TIER_1_ONLY_LANGUAGES == frozenset({LanguageID.PYTHON})
    assert precision_tier_for(LanguageID.PYTHON) is PrecisionTier.TIER_1_SEMANTIC


def test_go_is_tier3_lexical() -> None:
    assert precision_tier_for(LanguageID.GO) is PrecisionTier.TIER_3_LEXICAL


def test_every_tier_has_a_label() -> None:
    for tier in PrecisionTier:
        assert tier in PRECISION_TIER_LABELS
        assert PRECISION_TIER_LABELS[tier]


def test_compression_provider_protocol_satisfied_by_ast_compressor() -> None:
    assert isinstance(ASTCompressor(), CompressionProvider)


# --------------------------------------------------------------------- #
# discover_files / build_pipeline language-tier filtering
# --------------------------------------------------------------------- #
def test_discover_files_permissive_includes_all_languages(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    (tmp_path / "b.go").write_text("package main\nfunc F() {}\n")
    files = discover_files(str(tmp_path), LANGUAGE_TIER_PERMISSIVE)
    assert any(f.endswith("a.py") for f in files)
    assert any(f.endswith("b.go") for f in files)


def test_discover_files_tier1_only_excludes_non_python(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    (tmp_path / "b.go").write_text("package main\nfunc F() {}\n")
    files = discover_files(str(tmp_path), LANGUAGE_TIER_TIER1_ONLY)
    assert any(f.endswith("a.py") for f in files)
    assert not any(f.endswith("b.go") for f in files)


def test_build_pipeline_tier1_only_indexes_only_python(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    (tmp_path / "b.go").write_text("package main\nfunc Hello() string { return \"hi\" }\n")
    builder, _tag_matrix = build_pipeline(str(tmp_path), LANGUAGE_TIER_TIER1_ONLY)
    names = builder.symbol_table.all_qualified_names()
    assert any("f" in n for n in names)
    assert not any("Hello" in n for n in names)


# --------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------- #
def test_cli_index_accepts_language_tier_flag(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    (tmp_path / "b.go").write_text("package main\nfunc Hello() string { return \"hi\" }\n")
    runner = CliRunner()

    permissive = runner.invoke(main, ["index", str(tmp_path)])
    assert permissive.exit_code == 0
    assert "Symbols indexed: 2" in permissive.output

    tier1 = runner.invoke(main, ["index", str(tmp_path), "--language-tier", "tier1-only"])
    assert tier1.exit_code == 0
    assert "Symbols indexed: 1" in tier1.output


def test_cli_rejects_invalid_language_tier_value(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f(): return 1\n")
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(tmp_path), "--language-tier", "bogus"])
    assert result.exit_code != 0
