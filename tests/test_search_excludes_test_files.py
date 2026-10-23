"""Natural-language search leaves test files out; the graph does not.

`candidate_pool` drops every symbol defined in a test file (`tests/`,
`*.test.ts`, `test_*.py`, type tests `*.test-d.ts`) from `prism.ask`'s
pool. The graph, `prism.slice` and `prism.blast_radius` still index and
traverse them - a test that calls the seed is still a caller.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from prism.cli import build_pipeline
from prism.query.nlq import NLQIndex, candidate_pool
from prism.summaries import index as S
from prism.summaries.pipeline import build_index

FILES = {
    "app/__init__.py": "",
    "app/orders.py": "def save_order(order):\n    \"\"\"Persist an order row.\"\"\"\n    return order\n",
    "tests/test_orders.py": "from app.orders import save_order\n\n\ndef make_saved_order():\n"
                            "    return save_order({'id': 1})\n",
    "web/src/cart.ts": "export const addToCart = (item: string) => item\n",
    "web/src/cart.test.ts": "import { addToCart } from './cart'\nconst cartFixture = () => addToCart('order')\n",
    "web/src/cart.test-d.ts": "import { addToCart } from './cart'\nconst cartTypeCheck = () => addToCart('order')\n",
}


@pytest.fixture()
def repo(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return build_pipeline(str(tmp_path), use_cache=False)[0], tmp_path


def test_the_pool_drops_test_files_and_the_graph_keeps_them(repo):
    builder, root = repo
    in_graph = {s.qualified_name for s in builder.symbol_table}
    assert {q for q in in_graph if q.rsplit(".", 1)[-1] in {"make_saved_order", "cartFixture", "cartTypeCheck"}}, in_graph
    pool = {s.qualified_name for s in candidate_pool(builder)}
    assert {"app.orders.save_order"} <= pool and not {q for q in pool if re.search(r"Fixture|TypeCheck|make_saved", q)}
    full = {s.qualified_name for s in candidate_pool(builder, exclude_test_files=False)}
    assert {q for q in full if re.search(r"Fixture|TypeCheck|make_saved", q)}  # the comparison arm keeps them
    callers = {u for u, _v in builder.graph.in_edges("app.orders.save_order")}
    assert any("make_saved_order" in u for u in callers)  # the test caller is still in the graph


def test_search_never_returns_a_test_file_symbol(repo):
    builder, root = repo
    hits = NLQIndex(builder, str(root)).search("where is an order saved", top_k=10)["hits"]
    assert hits and hits[0]["qname"] == "app.orders.save_order"
    assert not any(h["location"].startswith(("tests/", "web/src/cart.test")) for h in hits)
    with_tests = NLQIndex(builder, str(root), exclude_test_files=False).search("where is an order saved", top_k=10)["hits"]
    assert any(h["location"].startswith("tests/") for h in with_tests)


def test_summary_coverage_counts_library_functions_only(repo):
    builder, root = repo

    class LLM:
        def json(self, prompt, max_out, seed=42):
            return {q: "Stores the record and returns it to the caller unchanged"
                    for q in re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)}

    counts = build_index(builder, str(root), llm=LLM())
    assert counts["functions"] == 2 and counts["coverage"] == 1.0  # save_order + addToCart, no test helpers
    assert set(S.read_index(S.index_path(str(root)))["functions"]) == {"app.orders.save_order", "web.src.cart.addToCart"}
    assert S.state_for(builder, str(root)).weight == S.SUMMARY_WEIGHT


def test_blast_radius_still_reports_the_test_caller(repo):
    pytest.importorskip("mcp")
    from mcp.client import Client
    from prism.mcp import server as mcp_server
    builder, root = repo

    async def call():
        async with Client(mcp_server.server) as client:
            return await client.call_tool("prism.blast_radius", {"repo_path": str(root), "seed_symbol": "app.orders.save_order"})

    mcp_server._cache.clear()
    try:
        result = asyncio.run(call())
        assert not result.is_error, result.content[0].text
        text = json.dumps(result.structured_content) if result.structured_content else result.content[0].text
        assert "make_saved_order" in text
    finally:
        mcp_server._cache.clear()
