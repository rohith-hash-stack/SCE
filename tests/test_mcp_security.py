"""Tests for Item 13 (second post-implementation audit): MCP Server
Security Audit & Path Sandboxing - `prism.mcp.security`'s validators, and
their wiring into `prism.mcp.cache.GraphCache.canonical_path`.
"""
from __future__ import annotations

import os

import pytest

from prism.mcp.cache import GraphCache
from prism.mcp.security import (
    MAX_TOKEN_BUDGET,
    SecurityError,
    assert_path_in_repo,
    validate_symbol_name,
    validate_tag,
    validate_token_budget,
)


# --------------------------------------------------------------------- #
# validate_token_budget
# --------------------------------------------------------------------- #
def test_validate_token_budget_accepts_a_normal_value():
    assert validate_token_budget(2000) == 2000


def test_validate_token_budget_accepts_the_maximum():
    assert validate_token_budget(MAX_TOKEN_BUDGET) == MAX_TOKEN_BUDGET


def test_validate_token_budget_rejects_over_the_cap():
    with pytest.raises(SecurityError):
        validate_token_budget(MAX_TOKEN_BUDGET + 1)


def test_validate_token_budget_rejects_zero_and_negative():
    with pytest.raises(SecurityError):
        validate_token_budget(0)
    with pytest.raises(SecurityError):
        validate_token_budget(-100)


# --------------------------------------------------------------------- #
# validate_symbol_name
# --------------------------------------------------------------------- #
def test_validate_symbol_name_accepts_a_real_qualified_name():
    assert validate_symbol_name("src.controllers.orders.OrderService.create_order") == (
        "src.controllers.orders.OrderService.create_order"
    )


def test_validate_symbol_name_accepts_hyphenated_module_segments():
    assert validate_symbol_name("my-utils.helper_fn") == "my-utils.helper_fn"


def test_validate_symbol_name_rejects_path_separators():
    with pytest.raises(SecurityError):
        validate_symbol_name("../../etc/passwd")
    with pytest.raises(SecurityError):
        validate_symbol_name("a/b/c")


def test_validate_symbol_name_rejects_null_byte_and_control_characters():
    with pytest.raises(SecurityError):
        validate_symbol_name("orders.OrderService\x00.create_order")
    with pytest.raises(SecurityError):
        validate_symbol_name("orders.OrderService\n.create_order")


def test_validate_symbol_name_rejects_empty_string():
    with pytest.raises(SecurityError):
        validate_symbol_name("")


def test_validate_symbol_name_rejects_oversized_input():
    with pytest.raises(SecurityError):
        validate_symbol_name("a" * 10_000)


# --------------------------------------------------------------------- #
# validate_tag
# --------------------------------------------------------------------- #
def test_validate_tag_accepts_a_real_registered_tag_shape():
    assert validate_tag("#auth_guard") == "#auth_guard"


def test_validate_tag_rejects_missing_hash_prefix():
    with pytest.raises(SecurityError):
        validate_tag("auth_guard")


def test_validate_tag_rejects_uppercase_and_symbols():
    with pytest.raises(SecurityError):
        validate_tag("#Auth-Guard")
    with pytest.raises(SecurityError):
        validate_tag("#auth; DROP TABLE")


# --------------------------------------------------------------------- #
# assert_path_in_repo
# --------------------------------------------------------------------- #
def test_assert_path_in_repo_accepts_the_root_itself(tmp_path):
    root = str(tmp_path)
    assert assert_path_in_repo(root, root) == os.path.realpath(root)


def test_assert_path_in_repo_accepts_a_real_subdirectory(tmp_path):
    sub = tmp_path / "nested" / "deeper"
    sub.mkdir(parents=True)
    result = assert_path_in_repo(str(sub), str(tmp_path))
    assert result == os.path.realpath(str(sub))


def test_assert_path_in_repo_rejects_a_sibling_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    with pytest.raises(SecurityError):
        assert_path_in_repo(str(sibling), str(root))


def test_assert_path_in_repo_rejects_dotdot_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    escape = str(root / ".." / "sibling")
    with pytest.raises(SecurityError):
        assert_path_in_repo(escape, str(root))


def test_assert_path_in_repo_rejects_a_symlink_that_escapes(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    link = root / "escape_link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")
    with pytest.raises(SecurityError):
        assert_path_in_repo(str(link), str(root))


# --------------------------------------------------------------------- #
# GraphCache.canonical_path / sandbox_root wiring
# --------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_sandbox_env(monkeypatch):
    monkeypatch.delenv("PRISM_MCP_DEFAULT_REPO", raising=False)
    yield


def test_sandbox_root_is_none_when_prism_mcp_default_repo_is_unset():
    assert GraphCache.sandbox_root() is None


def test_canonical_path_allows_any_path_when_unpinned(tmp_path):
    # No PRISM_MCP_DEFAULT_REPO set - the server's original, documented
    # "index whatever repo_path an agent names" design.
    result = GraphCache.canonical_path(str(tmp_path))
    assert result == os.path.abspath(str(tmp_path))


def test_canonical_path_rejects_escape_once_pinned(tmp_path, monkeypatch):
    root = tmp_path / "pinned_repo"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setenv("PRISM_MCP_DEFAULT_REPO", str(root))

    with pytest.raises(SecurityError):
        GraphCache.canonical_path(str(outside))


def test_canonical_path_allows_subdirectory_once_pinned(tmp_path, monkeypatch):
    root = tmp_path / "pinned_repo"
    sub = root / "packages" / "core"
    sub.mkdir(parents=True)
    monkeypatch.setenv("PRISM_MCP_DEFAULT_REPO", str(root))

    result = GraphCache.canonical_path(str(sub))
    assert result == os.path.realpath(str(sub))


def test_canonical_path_default_with_no_override_is_the_pinned_root_itself(tmp_path, monkeypatch):
    root = tmp_path / "pinned_repo"
    root.mkdir()
    monkeypatch.setenv("PRISM_MCP_DEFAULT_REPO", str(root))

    assert GraphCache.canonical_path(None) == os.path.realpath(str(root))


def test_canonical_path_rejects_traversal_sequence_once_pinned(tmp_path, monkeypatch):
    root = tmp_path / "pinned_repo"
    root.mkdir()
    (tmp_path / "secret").mkdir()
    monkeypatch.setenv("PRISM_MCP_DEFAULT_REPO", str(root))

    with pytest.raises(SecurityError):
        GraphCache.canonical_path(str(root / ".." / "secret"))
