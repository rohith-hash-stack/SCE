"""Hermetic tests for `prism.mcp.server` - all in-process via the official
MCP SDK's `Client(server)` in-memory transport (see
`mcp.client._memory.InMemoryTransport`): no subprocess, no stdio, no
network, no persistent daemon. Requires `mcp[cli]` (a core dependency -
`pip install prism-context`, or `pip install 'mcp[cli]>=2.0,<3.0'`
directly), same as the server module itself.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402

from prism.mcp import server as mcp_server  # noqa: E402
from prism.runtime.reconciler import GraphReconciler, load_runtime_state, merge_result_into_state, save_runtime_state  # noqa: E402
from prism.runtime.tracer import TraceRecord  # noqa: E402
from prism.cli import build_pipeline  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_mcp_cache():
    """The server's `GraphCache` is a module-level singleton (by design -
    it's meant to persist across tool calls within one long-lived server
    process) - clear it before and after every test so no test's index
    leaks into another's, even though each test's own `tmp_path` repo
    path is already unique.
    """
    mcp_server._cache.clear()
    yield
    mcp_server._cache.clear()


def _run(coro):
    return asyncio.run(coro)


def _order_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text(
        "import sqlalchemy\n"
        "\n"
        "\n"
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
        "        self.db.commit()\n"
        "        return amount\n"
        "\n"
        "    def secure_create_order(self, amount):\n"
        "        self.auth.verify(amount)\n"
        "        self.db.commit()\n"
        "        return amount\n"
    )
    return repo


async def _call(name: str, arguments: dict):
    async with Client(mcp_server.server) as client:
        return await client.call_tool(name, arguments)


# --------------------------------------------------------------------- #
# Server startup and tool listing
# --------------------------------------------------------------------- #
def test_server_lists_all_five_tools():
    async def go():
        async with Client(mcp_server.server) as client:
            return await client.list_tools()

    result = _run(go())
    names = {t.name for t in result.tools}
    assert names == {
        "get_symbol_context",
        "get_architectural_invariants",
        "find_symbols_by_tag",
        "get_graph_status",
        "reindex_repo",
    }


def test_get_symbol_context_tool_declares_required_and_optional_parameters():
    async def go():
        async with Client(mcp_server.server) as client:
            return await client.list_tools()

    result = _run(go())
    tool = next(t for t in result.tools if t.name == "get_symbol_context")
    schema = tool.input_schema
    assert "target_symbol" in schema["properties"]
    assert schema.get("required") == ["target_symbol"]
    assert "repo_path" in schema["properties"]
    assert "token_budget" in schema["properties"]


# --------------------------------------------------------------------- #
# get_symbol_context
# --------------------------------------------------------------------- #
def test_get_symbol_context_returns_markdown_with_zoom_levels(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("get_symbol_context", {
        "target_symbol": "orders.OrderService.create_order", "repo_path": str(repo), "token_budget": 2000,
    }))
    assert not result.is_error
    text = result.content[0].text
    assert "# SEMANTIC REPOSITORY CONTEXT" in text
    assert "Target Symbol: `orders.OrderService.create_order`" in text
    assert "[TARGET] orders.OrderService.create_order (L0)" in text
    assert "```python" in text
    # A neighbor (validate) is reachable and should surface at some
    # non-zero resolution level.
    assert "orders.OrderValidator.validate" in text


def test_get_symbol_context_respects_token_budget_parameter(tmp_path):
    repo = _order_repo(tmp_path)
    small = _run(_call("get_symbol_context", {
        "target_symbol": "orders.OrderService.create_order", "repo_path": str(repo), "token_budget": 50,
    }))
    large = _run(_call("get_symbol_context", {
        "target_symbol": "orders.OrderService.create_order", "repo_path": str(repo), "token_budget": 8000,
    }))
    assert not small.is_error and not large.is_error
    assert "Context Budget: 50 tokens" in small.content[0].text
    assert "Context Budget: 8000 tokens" in large.content[0].text


def test_get_symbol_context_unknown_symbol_is_a_graceful_tool_error(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("get_symbol_context", {"target_symbol": "not.a.real.symbol", "repo_path": str(repo)}))
    assert result.is_error
    assert "not.a.real.symbol" in result.content[0].text
    assert "was not found" in result.content[0].text


def test_get_symbol_context_nonexistent_repo_path_is_a_graceful_tool_error(tmp_path):
    missing = tmp_path / "does_not_exist"
    result = _run(_call("get_symbol_context", {"target_symbol": "x.y", "repo_path": str(missing)}))
    assert result.is_error
    assert "does not exist" in result.content[0].text


# --------------------------------------------------------------------- #
# find_symbols_by_tag
# --------------------------------------------------------------------- #
def test_find_symbols_by_tag_returns_matches_with_file_and_line_range(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("find_symbols_by_tag", {"tag": "#db_write", "repo_path": str(repo)}))
    assert not result.is_error
    payload = result.structured_content
    symbols = {m["symbol"] for m in payload["symbols"]}
    assert "orders.OrderService.create_order" in symbols
    assert "orders.OrderService.secure_create_order" in symbols
    entry = next(m for m in payload["symbols"] if m["symbol"] == "orders.OrderService.create_order")
    assert entry["file"] == "orders.py"
    assert len(entry["line_range"]) == 2
    assert entry["line_range"][0] <= entry["line_range"][1]
    assert entry["language"] == "python"
    assert entry["runtime_invocation_count"] == 0


def test_find_symbols_by_tag_rejects_unknown_tag(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("find_symbols_by_tag", {"tag": "#not_a_real_tag", "repo_path": str(repo)}))
    assert result.is_error
    assert "unknown tag" in result.content[0].text


def test_find_symbols_by_tag_returns_empty_list_for_tag_with_no_matches(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("find_symbols_by_tag", {"tag": "#payment_charge", "repo_path": str(repo)}))
    assert not result.is_error
    assert result.structured_content["count"] == 0
    assert result.structured_content["symbols"] == []


# --------------------------------------------------------------------- #
# get_architectural_invariants
# --------------------------------------------------------------------- #
def test_get_architectural_invariants_flags_db_write_without_upstream_auth_guard(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("get_architectural_invariants", {"target_symbol": "orders.OrderService.create_order", "repo_path": str(repo)}))
    assert not result.is_error
    payload = result.structured_content
    assert "#db_write" in payload["active_tags"]
    assert {"tag": "#db_write", "requires_before": "#auth_guard"} in payload["unfulfilled_invariants"]


def test_get_architectural_invariants_no_unfulfilled_when_auth_guard_present(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("get_architectural_invariants", {"target_symbol": "orders.OrderService.secure_create_order", "repo_path": str(repo)}))
    assert not result.is_error
    payload = result.structured_content
    assert "#auth_guard" in payload["active_tags"]
    assert "#db_write" in payload["active_tags"]
    assert payload["unfulfilled_invariants"] == []


def test_get_architectural_invariants_unknown_symbol_is_a_graceful_tool_error(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("get_architectural_invariants", {"target_symbol": "nope.nope", "repo_path": str(repo)}))
    assert result.is_error


# --------------------------------------------------------------------- #
# get_graph_status: static vs. runtime-promoted edge counts
# --------------------------------------------------------------------- #
def test_get_graph_status_reports_zero_runtime_edges_before_any_trace(tmp_path):
    repo = _order_repo(tmp_path)
    result = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    assert not result.is_error
    payload = result.structured_content
    assert payload["total_symbols"] > 0
    assert payload["total_edges"] > 0
    assert payload["confirmed_runtime_edges"] == 0
    assert payload["runtime_discovered_edges"] == 0
    assert payload["trace_files_ingested"] == 0


def test_get_graph_status_reflects_confirmed_and_discovered_runtime_edges_after_reindex(tmp_path):
    repo = _order_repo(tmp_path)

    # Prime the cache with a purely-static view first.
    before = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    assert before.structured_content["confirmed_runtime_edges"] == 0

    # Record a runtime trace independently of the MCP server (as `prism
    # trace` would) - one edge the static resolver already found
    # (create_order -> validate) plus one it didn't (create_order ->
    # OrderService.__init__, a constructor call the static resolver
    # only ever resolves to the class itself).
    builder, tag_matrix = build_pipeline(str(repo))
    events = [
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderValidator.validate", source="pytest_tracer"),
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderService.__init__", source="pytest_tracer"),
    ]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    state = merge_result_into_state(load_runtime_state(str(repo)), result, "run_test.jsonl")
    save_runtime_state(str(repo), state)

    # The cached (stale) entry doesn't see this until reindexed.
    stale = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    assert stale.structured_content["confirmed_runtime_edges"] == 0

    reindexed = _run(_call("reindex_repo", {"repo_path": str(repo)}))
    assert not reindexed.is_error
    assert reindexed.structured_content["trace_files_ingested"] == 1

    after = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    payload = after.structured_content
    # confirmed_runtime_edges counts every CONFIRMED_RUNTIME edge - both
    # the promoted static one (create_order -> validate) AND the newly
    # synthesized one (create_order -> __init__, which is also, by
    # definition, runtime-confirmed); runtime_discovered_edges is the
    # strict subset that didn't exist in the static graph at all.
    assert payload["confirmed_runtime_edges"] == 2
    assert payload["runtime_discovered_edges"] == 1  # create_order -> __init__, synthesized
    assert payload["trace_files_ingested"] == 1


def test_get_graph_status_nonexistent_repo_path_is_a_graceful_tool_error(tmp_path):
    missing = tmp_path / "does_not_exist"
    result = _run(_call("get_graph_status", {"repo_path": str(missing)}))
    assert result.is_error


# --------------------------------------------------------------------- #
# Caching behavior
# --------------------------------------------------------------------- #
def test_repeated_calls_against_the_same_repo_reuse_the_cached_index(tmp_path):
    repo = _order_repo(tmp_path)
    first = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    second = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    assert first.structured_content["indexed_at"] == second.structured_content["indexed_at"]

    reindexed = _run(_call("reindex_repo", {"repo_path": str(repo)}))
    assert reindexed.structured_content["indexed_at"] != first.structured_content["indexed_at"]


def test_relative_and_absolute_repo_path_share_one_cache_entry(tmp_path, monkeypatch):
    repo = _order_repo(tmp_path)
    monkeypatch.chdir(repo)
    absolute = _run(_call("get_graph_status", {"repo_path": str(repo)}))
    relative = _run(_call("get_graph_status", {"repo_path": "."}))
    assert absolute.structured_content["indexed_at"] == relative.structured_content["indexed_at"]
