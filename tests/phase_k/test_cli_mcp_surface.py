"""Phase K: CLI & MCP Surface Polish - verification suite.

Investigated before implementing (this project's own established
discipline): `prism.query.schema.PrismQuery`/`prism.query.errors.
QueryValidationError` already existed (Phase G) but were only wired into
`causal-query`, not `query` or the MCP server at all; `prism.mcp.server`'s
five original tools already degrade cleanly into `isError=True` results
via `ToolError` (the MCP SDK's own dispatch layer), and `prism.slice`/
`prism.explain` already raise a real protocol-level `MCPError` for every
anticipated failure - neither needed new error-handling machinery, just
richer input schemas. Confirmed via real subprocess/in-process repro
(not assumed from reading argparse/click setup alone) that `query`'s
budget was never validated at all (silently accepted 0/negative,
exit 0) and both `query`/`causal-query` mapped every domain error to
exit code 1 regardless of its real class.

Real gaps closed:
1. `query` command: budget now validated via `PrismQuery` (exit 2 on
   failure); "not found" now exits 3 (was 1).
2. `causal-query` command: `QueryValidationError`/not-found now map to
   exit 2/3 respectively (were both 1 via a bare `SystemExit(str)`,
   which always exits 1 regardless of the string's content); gained a
   real `--format xml/json/summary` option, wired directly through
   `prism.engine.PrismEngine` + `build_context_package` (Phase H) -
   the same pipeline `prism.slice`'s MCP tool already uses - rather
   than rendering `pack_symbol_context`'s raw result three
   independently-shaped ways.
3. `prism.slice`/`prism.explain` MCP tools: gained `seeds: list[str]`
   (mutually exclusive with `seed_symbol`, validated via `PrismQuery`),
   `task_type`, and `d_max` (default 5.0, matching the production
   default `compute_topological_distances` itself uses).

Deliberately NOT done: `query`'s own Markdown/`ContextKnapsackPacker`
engine did not gain `--format xml` - it and `causal-query`'s v1.1+
causal engine are a documented, deliberate architectural split (see
`causal-query`'s own docstring: "see docs/design_formalism.md Section 8
for why both exist side by side") that this phase respects rather than
merges. A `seeds` list with more than one element is honestly rejected
(both CLI... no, only MCP exposes `seeds` today) rather than silently
guessing how multiple seeds would combine - the underlying packing
engine only ever packs from one seed; genuine multi-seed packing is a
real engine feature, out of scope for a surface-polish phase.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from click.testing import CliRunner

from prism.cli import EXIT_INVALID_ARGUMENT, EXIT_SYMBOL_NOT_FOUND, main

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from prism.mcp import server as mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_mcp_cache():
    mcp_server._cache.clear()
    yield
    mcp_server._cache.clear()


def _write_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def helper():\n    return 1\n\n\ndef seed():\n    return helper()\n")
    return repo


async def _call(name: str, arguments: dict):
    async with Client(mcp_server.server) as client:
        return await client.call_tool(name, arguments)


# ============================================================
# CLI: --format output modes
# ============================================================

def test_cli_stdout_is_clean_xml_when_piped(tmp_path):
    """Running the CLI in XML mode (the new causal-query default)
    produces a string that parses cleanly with xml.etree.ElementTree -
    a real subprocess, exercising actual OS-level stdout piping, not
    just CliRunner's in-process buffer."""
    repo = _write_repo(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "prism.cli", "causal-query", str(repo), "mod.seed", "--budget", "4000"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stderr == ""
    root = ET.fromstring(proc.stdout)
    assert root.tag == "prism_context"


def test_cli_format_json_output(tmp_path):
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--budget", "4000", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["seed"] == "mod.seed"
    assert "budget" in payload and "tokens" in payload["budget"]
    assert "symbols" in payload
    assert any(s["id"] == "mod.helper" for s in payload["symbols"])


def test_cli_format_summary_output(tmp_path):
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--budget", "4000", "--format", "summary"])
    assert result.exit_code == 0
    assert "Symbols packed:" in result.output
    assert "Causal steps" in result.output
    assert "mod.helper" in result.output


def test_cli_json_flag_is_deprecated_alias_for_format_json(tmp_path):
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    via_json_flag = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--budget", "4000", "--json"])
    via_format = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--budget", "4000", "--format", "json"])
    assert via_json_flag.exit_code == via_format.exit_code == 0
    assert via_json_flag.output == via_format.output


# ============================================================
# CLI: deterministic exit codes
# ============================================================

def test_cli_exit_code_on_invalid_budget(tmp_path):
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    for budget in ("0", "-5"):
        result = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--budget", budget])
        assert result.exit_code == EXIT_INVALID_ARGUMENT == 2, (budget, result.output)
        assert "error:" in result.output

        result_query = runner.invoke(main, ["query", str(repo), "mod.seed", "--budget", budget])
        assert result_query.exit_code == EXIT_INVALID_ARGUMENT == 2, (budget, result_query.output)


def test_cli_exit_code_on_missing_symbol(tmp_path):
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["causal-query", str(repo), "mod.nonexistent"])
    assert result.exit_code == EXIT_SYMBOL_NOT_FOUND == 3

    result_query = runner.invoke(main, ["query", str(repo), "mod.nonexistent"])
    assert result_query.exit_code == EXIT_SYMBOL_NOT_FOUND == 3


def test_cli_exit_code_zero_on_success(tmp_path):
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--budget", "4000"])
    assert result.exit_code == 0
    result_query = runner.invoke(main, ["query", str(repo), "mod.seed", "--budget", "4000", "--json"])
    assert result_query.exit_code == 0


def test_cli_invalid_task_type_is_still_a_usage_error(tmp_path):
    """click's own Choice validator rejects an unrecognized --task-type
    before PrismQuery ever runs - a pre-existing, still-correct
    behavior, exit code 2 either way (click's own usage-error code
    happens to already match EXIT_INVALID_ARGUMENT)."""
    repo = _write_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["causal-query", str(repo), "mod.seed", "--task-type", "bogus"])
    assert result.exit_code == 2


# ============================================================
# MCP: seeds list + task_type + d_max schema alignment
# ============================================================

def test_mcp_tool_accepts_seeds_list(tmp_path):
    """The MCP handler accepts `seeds=[...]` (not just `seed_symbol`)
    and executes retrieval - verified with a single-element list
    (the only shape the underlying single-seed packing engine can
    actually satisfy; see test_mcp_tool_rejects_multiple_seeds below
    for the deliberate, honest rejection of a genuinely multi-element
    request rather than silently guessing how it would combine)."""
    repo = _write_repo(tmp_path)

    async def go():
        async with Client(mcp_server.server) as client:
            result = await client.call_tool(
                "prism.slice", {"repo_path": str(repo), "seeds": ["mod.seed"], "budget_tokens": 4000}
            )
            assert not result.is_error
            envelope = result.content[0].text
            assert "prism_context" in envelope
            assert "mod.seed" in envelope

    asyncio.run(go())


def test_mcp_tool_rejects_multiple_seeds(tmp_path):
    repo = _write_repo(tmp_path)

    async def go():
        async with Client(mcp_server.server) as client:
            with pytest.raises(MCPError) as excinfo:
                await client.call_tool(
                    "prism.slice", {"repo_path": str(repo), "seeds": ["mod.seed", "mod.helper"], "budget_tokens": 4000}
                )
            assert "multi-seed" in str(excinfo.value) or "not yet supported" in str(excinfo.value)

    asyncio.run(go())


def test_mcp_tool_rejects_both_seed_and_seeds(tmp_path):
    repo = _write_repo(tmp_path)

    async def go():
        async with Client(mcp_server.server) as client:
            with pytest.raises(MCPError):
                await client.call_tool(
                    "prism.slice",
                    {"repo_path": str(repo), "seed_symbol": "mod.seed", "seeds": ["mod.helper"], "budget_tokens": 4000},
                )

    asyncio.run(go())


def test_mcp_tool_accepts_task_type_and_d_max(tmp_path):
    repo = _write_repo(tmp_path)

    async def go():
        async with Client(mcp_server.server) as client:
            result = await client.call_tool(
                "prism.slice",
                {"repo_path": str(repo), "seed_symbol": "mod.seed", "budget_tokens": 4000, "task_type": "debug", "d_max": 3.0},
            )
            assert not result.is_error

            with pytest.raises(MCPError):
                await client.call_tool(
                    "prism.slice",
                    {"repo_path": str(repo), "seed_symbol": "mod.seed", "budget_tokens": 4000, "task_type": "bogus"},
                )

    asyncio.run(go())


# ============================================================
# MCP: clean error surfaces (never a raw traceback/crashed transport)
# ============================================================

def test_mcp_tool_returns_is_error_on_invalid_query(tmp_path):
    """One of the five original tools (ToolError-raising, per this
    file's own module docstring) - an unknown target returns a clean
    `CallToolResult(isError=True)`, and the server process/session
    keeps working for a subsequent, valid call afterward (never
    terminates the transport)."""
    repo = _write_repo(tmp_path)

    async def go():
        async with Client(mcp_server.server) as client:
            bad = await client.call_tool("get_symbol_context", {"target_symbol": "mod.nonexistent", "repo_path": str(repo)})
            assert bad.is_error

            good = await client.call_tool("get_symbol_context", {"target_symbol": "mod.seed", "repo_path": str(repo)})
            assert not good.is_error

    asyncio.run(go())


def test_mcp_slice_error_does_not_crash_subsequent_calls(tmp_path):
    """prism.slice/prism.explain raise a real MCPError (protocol-level,
    not isError=True - see this file's own module docstring for why
    that's the deliberate, already-correct SDK behavior for these two
    tools) - confirms that error still leaves the session usable for a
    following successful call, i.e. it's a clean, catchable error, not
    a transport-terminating crash."""
    repo = _write_repo(tmp_path)

    async def go():
        async with Client(mcp_server.server) as client:
            with pytest.raises(MCPError):
                await client.call_tool("prism.slice", {"repo_path": str(repo), "seed_symbol": "mod.nonexistent", "budget_tokens": 4000})

            result = await client.call_tool("prism.slice", {"repo_path": str(repo), "seed_symbol": "mod.seed", "budget_tokens": 4000})
            assert not result.is_error

    asyncio.run(go())
