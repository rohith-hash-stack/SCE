"""Purpose-enriched search harness (`harness.experiments.purpose_search`)."""
from __future__ import annotations

import pytest

from harness.experiments.purpose_search.cards import CAP, build_card, resources
from harness.experiments.purpose_search.run import augmented, fielded
from harness.experiments.purpose_search.summarize import render
from prism.cli import build_pipeline
from prism.graph.entry_points import find_entry_points
from prism.query.nlq import NLQIndex, tokenize

FILES = {
    "app/__init__.py": "",
    "app/conf.py": "class Settings:\n    ALLOWED_HOSTS = []\n\n\nsettings = Settings()\n",
    "app/crud.py": '''
from app.conf import settings


def validate(item):
    """Check an item before it is stored."""
    return bool(item)


def create_item(*, session, item):
    """Create new item."""
    validate(item)
    session.add(item)
    session.commit()
    return item


def allowed(request):
    host = request.META["HTTP_HOST"]
    return host in settings.ALLOWED_HOSTS
''',
    "app/hub.py": "".join(f"def leaf{i}():\n    return {i}\n\n\n" for i in range(CAP + 3))
    + "def hub():\n" + "".join(f"    leaf{i}()\n" for i in range(CAP + 3)),
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text.lstrip("\n"))
    builder = build_pipeline(str(root), use_cache=False)[0]
    return builder, find_entry_points(builder), str(root)


def test_card_merges_unresolved_calls_on_parameters(repo):
    builder, entries, _ = repo
    card = build_card(builder, {}, entries, "app.crud.create_item")
    assert card["unresolved"] == ["session.add", "session.commit"]
    line = next(l for l in card["card"].splitlines() if l.startswith("Direct Callees:"))
    assert "app.crud.validate" in line and "unresolved calls on parameters/self: session.add, session.commit" in line
    assert "Docstring: Create new item." in card["card"]


def test_card_caps_hub_edges(repo):
    builder, entries, _ = repo
    line = next(l for l in build_card(builder, {}, entries, "app.hub.hub")["card"].splitlines()
                if l.startswith("Direct Callees:"))
    assert line.count("app.hub.leaf") == CAP and "(+3 more)" in line


def test_resources_name_settings_and_header_keys(repo):
    builder, _, _ = repo
    assert set(resources(builder, "app.crud.allowed")) == {"setting ALLOWED_HOSTS", "META HTTP_HOST"}


def test_augmented_index_only_changes_the_bm25_documents(repo):
    builder, _, root = repo
    nlq = NLQIndex(builder, root)
    docs = [tokenize(s.qualified_name) + tokenize(nlq._relative(s.file))
            + tokenize("\n".join(nlq._source(s.file)[s.line_range[0] - 1:s.line_range[1]])) for s in nlq.pool]
    question = "where is the order persisted to the database"
    assert augmented(nlq, docs, {}).ranked(question) == nlq.ranked(question)  # rebuilt docs == shipped docs
    boosted = augmented(nlq, docs, {"app.hub.leaf3": "persists order rows into the database"})
    assert boosted.ranked(question)[0] == "app.hub.leaf3"
    assert "app.hub.leaf3" not in nlq.ranked(question)  # the shipped index is untouched


def test_fielded_index_adds_an_independent_summary_field(repo):
    builder, _, root = repo
    nlq = NLQIndex(builder, root)
    question = "where is the order persisted to the database"
    assert fielded(nlq, {}, 1.0).ranked(question) == nlq.ranked(question)  # empty field == shipped ranking
    summaries = {"app.hub.leaf3": "persists order rows into the database"}
    assert fielded(nlq, summaries, 0.0).ranked(question) == nlq.ranked(question)  # w = 0 is a no-op
    assert fielded(nlq, summaries, 0.5).ranked(question)[0] == "app.hub.leaf3"


def test_summary_renders_held_out_and_q2e_tables():
    row = lambda r: {"id": "x", "ranks": {"g": r}, "recall@5": 1.0 if r <= 5 else 0.0, "recall@20": 1.0,
                     "pool_recall": 1.0, "mrr": 1 / r, "subject": "g"}
    res = lambda repo, r: {"repo": repo, "backend": "ollama", "model": "m", "scope": "full", "cards": 1, "summaries": 1,
                           "questions": [{"id": "x", "subject": "g", "gold": ["g"]}],
                           "search": {a: {"per_question": [row(r)], "recall@5": 1.0, "recall@20": 1.0,
                                          "pool_recall": 1.0, "mrr": 1 / r} for a in ("BASE", "+RES", "+SUM", "+SUM+RES", "FIELD w=0.5")},
                           "identifiability": {}, "query_matching": {"mrr": None},
                           "llm_usage": {"calls": 1, "failed": 0, "prompt_tokens": 1, "completion_tokens": 1},
                           "synth_seconds": 0.0}
    md = render({"styleguide": res("styleguide", 2), "express": res("express", 3), "django": res("django", 30)})
    assert "## Held-out set 2 (1 questions: styleguide)" in md and "## Held-out set 3 (1 questions: express)" in md
    assert "subject rank BASE / +SUM / FIELD w=0.5" in md and "| x | `g` | 30 / 30 / 30 |" in md
    assert "| FIELD w=0.5 | 1.000 | 1.000 | 1.000 | 0.333 |" in md


def test_card_v2_leaves_out_the_axes_line(repo):
    builder, entries, _ = repo
    v1 = build_card(builder, {}, entries, "app.crud.create_item")["card"]
    v2 = build_card(builder, {}, entries, "app.crud.create_item", axes=False)["card"]
    assert "Architectural Axes:" in v1 and "Architectural Axes:" not in v2
    assert [l for l in v1.splitlines() if not l.startswith("Architectural Axes:")] == v2.splitlines()


def test_lint_flags_copied_labels_identifiers_and_callee_lists_but_not_plain_english():
    from harness.experiments.purpose_search.lint import lint, rates
    card = "Direct Callees: lib.response.deprecate, lib.response.get, lib.response.header, lib.response.send"
    flagged = lambda s: {k for k, v in lint(s, card).items() if v}
    assert flagged("Attempt to reconnect via pure compute, no effect.") == {"axis_label"}
    assert flagged("Delays execution via TIME_IO.") == {"axis_label"}
    assert flagged("Restrict access via redirection command, no return.") == {"filler"}
    assert flagged("Handles rendering options during testing without returning any particular value.") == {"filler"}
    assert flagged("Fetch users via db.map, send HTML response.") == {"identifier"}
    assert flagged("Send JSON via deprecate, get, header and send.") == {"callee_list"}
    assert flagged("Authenticate a user by comparing password hashes, returning the query result.") == set()
    assert rates([("Serializes an object to JSON and sends it with the content type.", card)])["any"] == 0.0
    assert flagged("Creates a client for the Next.js app directory on Node.js 18.1.") == set()  # product names, numbers
    assert flagged("Deletes the file from disk using Node.js fs.promises.unlink.") == {"identifier"}


def test_problems_quote_the_offending_identifiers():
    from harness.experiments.purpose_search.lint import problems
    card = "Direct Callees: none"
    assert problems("Uses useEffect to manage component side effects.", card) == ["it contains code identifiers (useEffect)"]
    assert problems("Reads settings from environment variables and parses them as JSON.", card) == []
