"""Hermetic, in-process MCP end-to-end tests for `prism.slice`/`prism.
explain` (v1.1+ Agent Surface) - same harness as `tests/test_mcp_server.py`
(`mcp.client.Client(server)`'s in-memory transport, no subprocess/stdio/
network), plus the security boundaries `prism.mcp.auth` adds: path
traversal (-32001), unauthorized (401), rate limit (429).
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from prism.mcp import auth as mcp_auth  # noqa: E402
from prism.mcp import server as mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_mcp_state():
    mcp_server._cache.clear()
    os.environ.pop(mcp_auth.API_KEYS_ENV_VAR, None)
    mcp_auth.rate_limiter.reset()
    yield
    mcp_server._cache.clear()
    os.environ.pop(mcp_auth.API_KEYS_ENV_VAR, None)
    mcp_auth.rate_limiter.reset()


def _run(coro):
    return asyncio.run(coro)


def _order_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def calculate_tax(amount):\n"
        "    return amount * 0.2\n"
        "\n"
        "\n"
        "def invoice_generator(amount):\n"
        "    total = calculate_tax(amount)\n"
        "    return total\n"
    )
    return repo


async def _call(name: str, arguments: dict):
    async with Client(mcp_server.server) as client:
        return await client.call_tool(name, arguments)


async def _call_expect_error(name: str, arguments: dict) -> MCPError:
    async with Client(mcp_server.server) as client:
        try:
            await client.call_tool(name, arguments)
        except MCPError as exc:
            return exc
    raise AssertionError(f"expected {name} to raise MCPError, it returned normally")


# --------------------------------------------------------------------- #
# Tool listing
# --------------------------------------------------------------------- #
def test_prism_slice_and_explain_are_registered_tools():
    async def go():
        async with Client(mcp_server.server) as client:
            return await client.list_tools()

    result = _run(go())
    names = {t.name for t in result.tools}
    assert "prism.slice" in names
    assert "prism.explain" in names


# --------------------------------------------------------------------- #
# prism.slice / prism.explain happy paths
# --------------------------------------------------------------------- #
def test_prism_slice_returns_a_well_formed_envelope(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "budget_tokens": 2000}))
    assert not result.is_error
    payload = result.structured_content
    assert payload["envelope"].startswith("<prism_context")
    assert payload["envelope"].rstrip("\n").endswith("</prism_context>")
    assert isinstance(payload["token_count"], int) and payload["token_count"] > 0
    assert isinstance(payload["truncated"], bool)

    import xml.etree.ElementTree as ET

    ET.fromstring(payload["envelope"])


def test_prism_slice_pulls_in_the_upstream_blast_radius_caller(tmp_path):
    """The spec's own scenario: seeding at `calculate_tax`, the envelope
    includes `invoice_generator` (a caller in the same repo that unpacks
    its return value) as a real, not merely theoretical, packed node."""
    repo = _order_repo(tmp_path)
    result = _run(_call("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "budget_tokens": 4000}))
    assert not result.is_error
    envelope = result.structured_content["envelope"]
    assert 'id="svc.invoice_generator"' in envelope
    assert 'role="caller"' in envelope


def test_prism_slice_json_format_returns_parseable_context_package_json(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "format": "json"}))
    assert not result.is_error
    parsed = json.loads(result.structured_content["envelope"])
    assert parsed["seed"]["symbol"] == "svc.calculate_tax"
    assert "nodes" in parsed and "edges" in parsed


def test_prism_explain_summary_omits_nodes_and_edges(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(
        _call("prism.explain", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "detail_level": "summary"})
    )
    assert not result.is_error
    envelope = result.structured_content["envelope"]
    assert "<nodes/>" in envelope
    assert "<edges/>" in envelope
    assert "<manifest" in envelope  # manifest/coverage/metadata still present


def test_prism_explain_manifest_keeps_nodes_but_blanks_bodies(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(
        _call("prism.explain", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "detail_level": "manifest"})
    )
    assert not result.is_error
    envelope = result.structured_content["envelope"]
    assert 'id="svc.calculate_tax"' in envelope
    assert "<body><![CDATA[]]></body>" in envelope
    assert "return amount" not in envelope


def test_prism_explain_full_matches_prism_slice_shape(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(
        _call("prism.explain", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "detail_level": "full", "budget_tokens": 4000})
    )
    assert not result.is_error
    assert "return amount" in result.structured_content["envelope"]


def test_include_warnings_false_drops_derived_warnings_but_not_a_real_overflow(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(
        _call("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "budget_tokens": 500, "include_warnings": False})
    )
    assert not result.is_error
    envelope = result.structured_content["envelope"]
    assert "TOKENIZER_FALLBACK" not in envelope


# --------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------- #
def test_budget_tokens_below_minimum_is_rejected(tmp_path):
    repo = _order_repo(tmp_path)
    exc = _run(_call_expect_error("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "budget_tokens": 100}))
    assert exc.code == -32602


def test_invalid_format_is_rejected(tmp_path):
    repo = _order_repo(tmp_path)
    exc = _run(_call_expect_error("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "format": "yaml"}))
    assert exc.code == -32602


# --------------------------------------------------------------------- #
# Security boundaries
# --------------------------------------------------------------------- #
def test_path_traversal_in_repo_path_is_rejected(tmp_path):
    exc = _run(_call_expect_error("prism.slice", {"repo_path": "../../etc/passwd", "seed_symbol": "x"}))
    assert exc.code == -32001


def test_missing_symbol_returns_fuzzy_candidates(tmp_path):
    repo = _order_repo(tmp_path)
    exc = _run(_call_expect_error("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_taxx"}))
    assert exc.code == -32002
    assert "svc.calculate_tax" in exc.data["candidates"]


def test_seed_exceeding_budget_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    huge_body = "\n".join(f"    x{i} = {i}" for i in range(400))
    (repo / "svc.py").write_text(f"def huge_seed():\n{huge_body}\n    return 0\n")
    exc = _run(_call_expect_error("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.huge_seed", "budget_tokens": 500}))
    assert exc.code == -32003


def test_unauthorized_without_api_key_returns_401(tmp_path):
    repo = _order_repo(tmp_path)
    os.environ[mcp_auth.API_KEYS_ENV_VAR] = "secret-key"
    exc = _run(_call_expect_error("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax"}))
    assert exc.code == 401


def test_authorized_with_correct_api_key_argument_succeeds(tmp_path):
    repo = _order_repo(tmp_path)
    os.environ[mcp_auth.API_KEYS_ENV_VAR] = "secret-key"
    result = _run(_call("prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "api_key": "secret-key"}))
    assert not result.is_error


def test_rate_limit_exceeded_returns_429(tmp_path):
    repo = _order_repo(tmp_path)
    os.environ[mcp_auth.API_KEYS_ENV_VAR] = "secret-key"

    async def go():
        async with Client(mcp_server.server) as client:
            for _ in range(mcp_auth.RATE_LIMIT_PER_MINUTE):
                r = await client.call_tool(
                    "prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "api_key": "secret-key"}
                )
                assert not r.is_error
            try:
                await client.call_tool(
                    "prism.slice", {"repo_path": str(repo), "seed_symbol": "svc.calculate_tax", "api_key": "secret-key"}
                )
                raise AssertionError("expected the 61st call to be rate-limited")
            except MCPError as exc:
                return exc

    exc = _run(go())
    assert exc.code == 429
