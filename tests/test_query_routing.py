"""Automatic routing in `prism.ask`: the route classifier, the exact-name
pin, the summary-weight rule over `.prism/summaries.json`, the offline
summarizer (`prism summarize`), and the guardrails - the production summary
field is the harness's FIELD arm, and `prism.blast_radius` never sees
summaries."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from harness.experiments.purpose_search.run import fielded
from prism.cli import build_pipeline, main
from prism.query.nlq import NLQIndex
from prism.query.route import classify, name_candidates
from prism.summaries import index as S
from prism.summaries.pipeline import LOCKED_MODEL, build_index, card_key

ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "shop/__init__.py": "",
    "shop/orders.py": '''
class OrderService:
    def create_order(self, session, item):
        """Create an order row."""
        self.check_stock(item)
        session.add(item)
        session.commit()
        return item

    def check_stock(self, item):
        return item.count > 0

    def cancel_order(self, session, order):
        session.delete(order)


def send_receipt(mailer, order):
    mailer.send(order.email, "thanks")


def format_price(cents):
    return f"{cents / 100:.2f}"
''',
    "shop/views.py": '''
from shop.orders import OrderService, format_price


def checkout(request):
    order = OrderService().create_order(request.session, request.item)
    return format_price(order.total)
''',
}


def _write(root: Path) -> Path:
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text.lstrip("\n"))
    return root


class FakeLLM:
    """Answers the locked prompts deterministically: a plain sentence per
    qualified name; `bad` names get an identifier-laden first answer."""

    def __init__(self, bad=()):
        self.bad, self.calls, self.repairs = set(bad), 0, 0

    def json(self, prompt, max_out, seed=42):
        self.calls += 1
        if prompt.startswith("The sentence below"):
            self.repairs += 1
            return {"summary": "Rewritten as a plain sentence describing what the code achieves for users"}
        names = re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)
        return {q: (f"Calls {q}() directly" if q in self.bad else
                    "Persists the customer purchase and charges the payment card") for q in names}


@pytest.fixture()
def repo(tmp_path):
    root = _write(tmp_path / "repo")
    builder = build_pipeline(str(root), use_cache=False)[0]
    return builder, root


# --------------------------------------------------------------------- #
# route classifier
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("query,route", [
    ("Router.include_router", "identifier"), ("url_for()", "identifier"), ("Flask::send_file", "identifier"),
    ("createApp", "identifier"), ("redirect", "identifier"), ("send file", "identifier"),
    ("where is user_id checked before saving?", "mixed"), ("How does res.json set the content type", "mixed"),
    ("where are notifications saved to the database?", "natural_language"),
    ("validate the order before it is stored", "natural_language"),
])
def test_classify(query, route):
    assert classify(query) == route


def test_name_candidates_normalise_code_tokens():
    assert name_candidates("Flask::send_file()", "identifier") == ["Flask.send_file"]
    assert name_candidates("Model#save", "identifier") == ["Model.save"]
    assert name_candidates("redirect", "identifier") == ["redirect"]
    assert name_candidates("send file", "identifier") == []  # two plain words: no name to look up
    assert name_candidates("is `user_id`, checked?", "mixed") == ["user_id"]
    assert name_candidates("where are orders saved", "natural_language") == []


def test_frozen_questions_route_as_measured():
    """Held-out sets 2-4 + Q2E behavioural questions: never `identifier`, so
    no natural-language question loses its ranking to the pin; set-4 gold
    names asked bare are all `identifier`."""
    qs = [q["question"] for s in ("set2", "set3", "set4")
          for q in json.loads((ROOT / f"harness/experiments/heldout_retrieval/{s}/dataset.json").read_text())["questions"]]
    qs += [q["query"] for q in json.loads((ROOT / "harness/experiments/query_to_evidence/dataset/questions.json")
                                          .read_text())["questions"] if q["category"] == "unseeded_behavioral"]
    routes = [classify(q) for q in qs]
    assert (len(qs), routes.count("natural_language"), routes.count("mixed"), routes.count("identifier")) == (164, 156, 8, 0)
    gold = [g for q in json.loads((ROOT / "harness/experiments/heldout_retrieval/set4/dataset.json").read_text())["questions"]
            for g in q["gold"]]
    lookups = [form for g in gold for form in (g.split(".")[-1], ".".join(g.split(".")[-2:]))]
    assert len(lookups) == 104 and {classify(q) for q in lookups} == {"identifier"}


# --------------------------------------------------------------------- #
# exact-name pin
# --------------------------------------------------------------------- #
def test_identifier_query_pins_exact_names_first(repo):
    builder, root = repo
    res = NLQIndex(builder, str(root)).search("cancel_order", top_k=5)
    assert res["intent"]["route"] == "identifier"
    assert res["hits"][0]["qname"] == "shop.orders.OrderService.cancel_order" and res["hits"][0]["pinned"]
    assert [h["pinned"] for h in res["hits"]].count(True) == 1
    dotted = NLQIndex(builder, str(root)).search("OrderService.check_stock()", top_k=3)
    assert dotted["hits"][0]["qname"] == "shop.orders.OrderService.check_stock"


def test_mixed_query_pins_and_natural_language_does_not(repo):
    builder, root = repo
    nlq = NLQIndex(builder, str(root))
    mixed = nlq.search("where does send_receipt deliver the email?", top_k=3)
    assert mixed["intent"]["route"] == "mixed"
    assert mixed["hits"][0]["qname"] == "shop.orders.send_receipt" and mixed["hits"][0]["pinned"]
    question = "where is the order stored in the session"
    plain = nlq.search(question, top_k=50)
    assert plain["intent"]["route"] == "natural_language" and not any(h["pinned"] for h in plain["hits"])
    assert [h["qname"] for h in plain["hits"]] == nlq.ranked(question)[:50]  # NL: the ranking, untouched


def test_pinned_matches_keep_score_order(repo):
    builder, root = repo
    nlq = NLQIndex(builder, str(root))
    order = ["b", "shop.orders.OrderService.create_order", "a"]
    assert nlq.pinned("create_order", "identifier", order) == ["shop.orders.OrderService.create_order"]
    assert nlq.pinned("no_such_name", "identifier", order) == []


# --------------------------------------------------------------------- #
# the summary field: production == harness FIELD arm
# --------------------------------------------------------------------- #
QUESTIONS = ("where is the purchase charged to the card", "how is the price shown", "cancel an order",
             "which code persists the customer purchase")


def test_production_summary_field_equals_harness_field_arm(repo):
    builder, root = repo
    base = NLQIndex(builder, str(root))
    summaries = {"shop.orders.format_price": "Charges the customer purchase to the payment card",
                 "shop.orders.send_receipt": "Emails a thank-you note after the purchase"}
    for w in (0.5, 1.0):
        prod = NLQIndex(builder, str(root), summaries=summaries, summary_weight=w)
        for q in QUESTIONS:
            assert prod.ranked(q) == fielded(base, summaries, w).ranked(q)
    assert NLQIndex(builder, str(root), summaries=summaries).summary_weight == S.SUMMARY_WEIGHT
    zero = NLQIndex(builder, str(root), summaries=summaries, summary_weight=0.0)
    assert zero.summary_bm25 is None and all(zero.ranked(q) == base.ranked(q) for q in QUESTIONS)
    assert NLQIndex(builder, str(root), summaries="auto").summary_weight == 0.0  # no index on disk
    assert NLQIndex(builder, str(root), summaries="auto").ranked(QUESTIONS[0]) == base.ranked(QUESTIONS[0])


# --------------------------------------------------------------------- #
# the weight rule and the offline index
# --------------------------------------------------------------------- #
def test_locked_index_with_full_coverage_turns_the_field_on(repo):
    builder, root = repo
    llm = FakeLLM()
    counts = build_index(builder, str(root), llm=llm)
    assert counts["coverage"] == 1.0 and counts["locked_pipeline"] and counts["summarized"] == counts["functions"] == 6
    nlq = NLQIndex(builder, str(root), summaries="auto")
    assert nlq.summary_weight == 0.5 and nlq.summary_state.status()["reason"] == "locked pipeline, coverage >= 0.95"
    assert nlq.search("purchase charged", top_k=1)["intent"]["summary_weight"] == 0.5
    again = build_index(builder, str(root), llm=FakeLLM())  # incremental: nothing to redo
    assert again["to_summarize"] == 0 and again["kept"] == 6


def test_stale_summaries_drop_out_and_turn_the_field_off(repo):
    builder, root = repo
    build_index(builder, str(root), llm=FakeLLM())
    (root / "shop/orders.py").write_text((root / "shop/orders.py").read_text().replace("cents / 100", "cents / 1000"))
    edited = build_pipeline(str(root), use_cache=False)[0]
    state = NLQIndex(edited, str(root), summaries="auto").summary_state
    assert state.stale == 1 and "shop.orders.format_price" not in state.summaries
    assert state.coverage == round(5 / 6, 4) and state.weight == 0.0  # 83.3% < 95%


def test_an_index_from_another_model_is_never_used(repo):
    builder, root = repo
    counts = build_index(builder, str(root), llm=FakeLLM(), model="some-other-model:7b")
    assert counts["coverage"] == 1.0 and not counts["locked_pipeline"]
    state = NLQIndex(builder, str(root), summaries="auto").summary_state
    assert state.weight == 0.0 and state.reason == "index not built by the locked pipeline"


def test_lint_failures_are_repaired(repo):
    builder, root = repo
    llm = FakeLLM(bad={"shop.orders.format_price"})
    counts = build_index(builder, str(root), llm=llm)
    entry = S.read_index(S.index_path(str(root)))["functions"]["shop.orders.format_price"]
    assert counts["repaired"] == 1 and llm.repairs == 1 and "()" not in entry["summary"]


def test_from_cache_installs_only_entries_whose_card_key_matches(repo):
    builder, root = repo
    from prism.graph.entry_points import find_entry_points
    from prism.summaries.cards import build_card
    card = build_card(builder, {}, find_entry_points(builder), "shop.orders.format_price", axes=False)["card"]
    cache = {"shop.orders.format_price": {"key": card_key(LOCKED_MODEL, card), "summary": "Shows money as text"},
             "shop.orders.send_receipt": {"key": "stale-key", "summary": "old"}}
    counts = build_index(builder, str(root), from_cache=cache, dry_run=True)
    assert counts["imported"] == 1 and counts["to_summarize"] == 5
    assert not S.index_path(str(root)).exists()  # dry run writes nothing


def test_cli_summarize_dry_run(repo):
    from click.testing import CliRunner
    builder, root = repo
    out = CliRunner().invoke(main, ["summarize", "--repo", str(root), "--dry-run"])
    assert out.exit_code == 0 and json.loads(out.stdout)["to_summarize"] == 6


# --------------------------------------------------------------------- #
# MCP surface and the blast-radius guardrail
# --------------------------------------------------------------------- #
def _mcp(name, arguments):
    pytest.importorskip("mcp")
    from mcp.client import Client
    from prism.mcp import server as mcp_server

    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(go())


@pytest.fixture()
def fresh_server():
    pytest.importorskip("mcp")
    from prism.mcp import server as mcp_server
    mcp_server._cache.clear()
    mcp_server._nlq_indexes.clear()
    yield
    mcp_server._cache.clear()
    mcp_server._nlq_indexes.clear()


def _payload(result):
    assert not result.is_error, result.content[0].text
    return result.structured_content if result.structured_content else json.loads(result.content[0].text)


def test_mcp_ask_reports_route_and_picks_up_a_new_summary_index(repo, fresh_server):
    builder, root = repo
    before = _payload(_mcp("prism.ask", {"question": "OrderService.cancel_order", "repo_path": str(root)}))
    assert before["intent"]["route"] == "identifier" and before["intent"]["summary_weight"] == 0.0
    assert before["hits"][0]["qname"] == "shop.orders.OrderService.cancel_order" and before["hits"][0]["pinned"]
    build_index(builder, str(root), llm=FakeLLM())
    after = _payload(_mcp("prism.ask", {"question": "where is the purchase charged", "repo_path": str(root)}))
    assert after["intent"]["route"] == "natural_language" and after["intent"]["summary_weight"] == 0.5
    status = _payload(_mcp("get_graph_status", {"repo_path": str(root)}))["summary_index"]
    assert status["coverage"] == 1.0 and status["summary_weight"] == 0.5 and status["locked_pipeline"]


@pytest.mark.parametrize("seed", ["shop.orders.OrderService.create_order", "shop.orders.format_price",
                                  "shop.orders.OrderService.check_stock"])
def test_blast_radius_is_byte_identical_with_and_without_summaries(repo, fresh_server, seed):
    """R0: prism.blast_radius requests every caller by rule; summaries are a
    search field only and must never change it."""
    builder, root = repo
    args = {"repo_path": str(root), "seed_symbol": seed}
    without = _mcp("prism.blast_radius", args)
    build_index(builder, str(root), llm=FakeLLM())
    assert _payload(_mcp("prism.ask", {"question": "purchase", "repo_path": str(root)}))["intent"]["summary_weight"] == 0.5
    with_summaries = _mcp("prism.blast_radius", args)
    assert not without.is_error
    assert [c.text for c in with_summaries.content] == [c.text for c in without.content]
    assert with_summaries.structured_content == without.structured_content
