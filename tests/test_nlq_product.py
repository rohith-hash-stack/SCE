"""Natural-language search as a product surface: `prism.query.nlq`, the
`prism ask` CLI command and the `prism.ask` MCP tool."""
from __future__ import annotations

import json
import textwrap

import pytest
from click.testing import CliRunner

from prism.cli import build_pipeline, main
from prism.query.nlq import NLQIndex, apply_priors, rule_intent, tokenize

ROUTES = """
from app.repo import UserRepository


def route(path, methods=None):
    def wrap(fn):
        return fn
    return wrap


@route("/users", methods=["POST"])
def register_user(payload):
    return create_account(payload)


def create_account(payload):
    return UserRepository().insert_user(payload)
"""

REPO = """
import sqlite3


class UserRepository:
    \"\"\"Stores user rows: insert user, delete user, find user.\"\"\"

    def insert_user(self, payload):
        conn = sqlite3.connect("app.db")
        conn.execute("insert into user values (?)", payload)
        conn.commit()

    def delete_user(self, user_id):
        conn = sqlite3.connect("app.db")
        conn.execute("delete from user where id = ?", user_id)
"""

MIGRATION = """
def upgrade():
    op.execute("insert user rows into the database")
"""


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("nlq")
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "routes.py").write_text(textwrap.dedent(ROUTES))
    (root / "app" / "repo.py").write_text(textwrap.dedent(REPO))
    (root / "migrations").mkdir()
    (root / "migrations" / "v1_users.py").write_text(textwrap.dedent(MIGRATION))
    return root


@pytest.fixture(scope="module")
def index(repo):
    builder, _ = build_pipeline(str(repo), use_cache=False)
    return NLQIndex(builder, str(repo))


QUESTION = "Which code inserts a new user row into the database?"


def test_rule_intent_is_deterministic_and_minimal():
    assert rule_intent(QUESTION) == {"action": "MUTATE", "capability": "DATABASE_IO"}
    assert rule_intent("How is the slug built?") is None
    assert tokenize("insertUserRows") == ["insert", "user", "row"]


def test_search_payload(index):
    result = index.search(QUESTION, top_k=3)
    assert result["intent"] == {"action": "MUTATE", "capability": "DATABASE_IO", "source": "rule"}
    top = result["hits"][0]
    assert top["qname"] == "app.repo.UserRepository.insert_user"
    assert set(top) == {"rank", "qname", "kind", "location", "score", "legs", "entry_context"}
    assert top["location"] == "app/repo.py:8" and top["kind"] == "method"
    assert top["legs"]["capability_source"] == "capability" and top["legs"]["capability"] > 0  # sqlite3 is L2 DB I/O
    assert top["entry_context"] == ["[Entry: POST /users] -> register_user() -> create_account() -> UserRepository.insert_user()"]


def test_priors_are_on_in_the_product(index):
    ranked = index.ranked(QUESTION)
    assert ranked.index("app.repo.UserRepository.insert_user") < ranked.index("app.repo.UserRepository")
    assert ranked[-1].startswith("migrations.")  # demoted: the question is not about migrations
    assert index.ranked("Which migration upgrade inserts user rows?")[0].startswith("migrations.")


def test_caller_supplied_intent_and_stems_only(index):
    result = index.search(QUESTION, top_k=2, intent={"action": "MUTATE"})
    assert result["intent"] == {"action": "MUTATE", "source": "caller"}
    plain = index.search(QUESTION, top_k=2, intent=None)
    assert plain["intent"] == {"source": None} and all(h["legs"]["action"] == 0.0 for h in plain["hits"])
    with pytest.raises(ValueError):
        index.search(QUESTION, top_k=0)


def test_apply_priors_is_stable_without_priors():
    rows = [("a", 2.0), ("b", 2.0), ("c", 1.0)]
    assert apply_priors(rows, "q", {}, set()) == ["a", "b", "c"]


def test_product_matches_the_benchmark_arm(repo, index):
    from harness.experiments.query_to_evidence.nlq.lexical import LexicalIndex, TriadContext
    builder = index.builder
    harness = LexicalIndex(builder, str(repo), relative_paths=True, triad=TriadContext(builder, str(repo)),
                           member_over_class=True, demote_migrations=True)
    for question in (QUESTION, "delete a user", "Which migration upgrade inserts user rows?"):
        assert index.ranked(question) == harness.ranked(question, "NB", rule_intent(question))


def test_cli_ask(repo):
    runner = CliRunner()
    out = runner.invoke(main, ["ask", QUESTION, "--repo", str(repo), "-k", "2"])
    assert out.exit_code == 0, out.output
    assert out.output.splitlines()[0] == "Intent: action=MUTATE, capability=DATABASE_IO"
    assert "1. app.repo.UserRepository.insert_user  (app/repo.py:8)" in out.output
    assert "[Entry: POST /users] -> register_user()" in out.output
    data = json.loads(runner.invoke(main, ["ask", QUESTION, "--repo", str(repo), "--json", "-k", "1"]).output)
    assert data["hits"][0]["qname"] == "app.repo.UserRepository.insert_user"


def test_mcp_prism_ask(repo):
    from mcp.shared.exceptions import MCPError

    from prism.mcp import server as mcp_server
    mcp_server._cache.clear()
    result = mcp_server.prism_ask(QUESTION, repo_path=str(repo), top_k=2)
    assert result["hits"][0]["qname"] == "app.repo.UserRepository.insert_user"
    override = mcp_server.prism_ask(QUESTION, repo_path=str(repo), top_k=1, action="READ")
    assert override["intent"] == {"action": "READ", "source": "caller"}
    for bad in ({"top_k": 0}, {"action": "FLY"}, {"capability": "SOMETHING"}):
        with pytest.raises(MCPError):
            mcp_server.prism_ask(QUESTION, repo_path=str(repo), **bad)
    with pytest.raises(MCPError):
        mcp_server.prism_ask("", repo_path=str(repo))
    mcp_server._cache.clear()
