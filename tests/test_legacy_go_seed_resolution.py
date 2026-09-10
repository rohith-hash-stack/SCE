"""Tests for Item 16 (second post-implementation audit): backward-
compatible seed resolution for a bare Go method name against Issue B1's
receiver-qualified registration - `prism.cli._resolve_legacy_go_bare_seed`
and its wiring into the `query` command.
"""
from __future__ import annotations

from click.testing import CliRunner

from prism.cli import _resolve_legacy_go_bare_seed, build_pipeline, main


def _write_repo(tmp_path, source: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(source)
    return repo


_SOURCE = """package main

type Context struct{}

func (c *Context) JSON(code int, data string) {
}
"""


def test_bare_name_resolves_to_unique_receiver_method(tmp_path) -> None:
    repo = _write_repo(tmp_path, _SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert _resolve_legacy_go_bare_seed(builder, "JSON") == "main.Context.JSON"


def test_qualified_name_is_never_touched(tmp_path) -> None:
    repo = _write_repo(tmp_path, _SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert _resolve_legacy_go_bare_seed(builder, "main.Context.JSON") is None


def test_ambiguous_bare_name_is_not_guessed(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        """package main

type A struct{}
func (a *A) JSON() {}

type B struct{}
func (b *B) JSON() {}
"""
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert _resolve_legacy_go_bare_seed(builder, "JSON") is None


def test_unknown_bare_name_returns_none(tmp_path) -> None:
    repo = _write_repo(tmp_path, _SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert _resolve_legacy_go_bare_seed(builder, "NoSuchMethod") is None


def test_query_command_resolves_legacy_bare_seed_with_warning(tmp_path) -> None:
    repo = _write_repo(tmp_path, _SOURCE)
    runner = CliRunner()
    result = runner.invoke(main, ["query", str(repo), "JSON", "--json"])
    assert result.exit_code == 0
    assert "resolved to receiver method 'main.Context.JSON'" in result.output
    assert '"seed": "main.Context.JSON"' in result.output


def test_query_command_still_fails_cleanly_for_truly_unknown_seed(tmp_path) -> None:
    repo = _write_repo(tmp_path, _SOURCE)
    runner = CliRunner()
    result = runner.invoke(main, ["query", str(repo), "TotallyUnknown", "--json"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_query_command_fully_qualified_seed_works_normally(tmp_path) -> None:
    repo = _write_repo(tmp_path, _SOURCE)
    runner = CliRunner()
    result = runner.invoke(main, ["query", str(repo), "main.Context.JSON", "--json"])
    assert result.exit_code == 0
    assert "resolved to receiver method" not in result.output
