"""Ranking priors (class vs. member, migration files) and the display-only
entry context on search hits."""
from __future__ import annotations

import textwrap

import pytest

from harness.experiments.query_to_evidence.nlq.lexical import LexicalIndex
from prism.cli import build_pipeline
from prism.graph.entry_points import _parse_decorator, entry_paths, find_entry_points, format_entry_context

ROUTES = """
from app.repo import UserRepository


def route(path, methods=None):
    def wrap(fn):
        return fn
    return wrap


def login_required(fn):
    return fn


@route("/users", methods=["POST"])
@login_required
def register_user(payload):
    return create_account(payload)


def create_account(payload):
    return UserRepository().insert_user(payload)


@property
def not_an_entry(self):
    return 1
"""

REPO = """
class UserRepository:
    \"\"\"Stores user rows: insert user, delete user, find user.\"\"\"

    def insert_user(self, payload):
        return self.db.execute("insert into user values (?)", payload)

    def delete_user(self, user_id):
        return self.db.execute("delete from user where id = ?", user_id)
"""

MIGRATION = """
def upgrade():
    op.create_table("user", sa.Column("id"))
    op.execute("insert user rows")


def downgrade():
    op.drop_table("user")
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("priors")
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "routes.py").write_text(textwrap.dedent(ROUTES))
    (root / "app" / "repo.py").write_text(textwrap.dedent(REPO))
    (root / "migrations" / "versions").mkdir(parents=True)
    (root / "migrations" / "versions" / "a1_users.py").write_text(textwrap.dedent(MIGRATION))
    builder, _ = build_pipeline(str(root), use_cache=False)
    return builder, str(root)


def _index(built, **priors):
    builder, root = built
    return LexicalIndex(builder, root, relative_paths=True, **priors)


def test_a_class_never_outranks_its_own_best_member(built):
    q = "insert user rows"
    plain = _index(built).ranked(q, "NB")
    fixed = _index(built, member_over_class=True).ranked(q, "NB")
    assert plain.index("app.repo.UserRepository") < plain.index("app.repo.UserRepository.insert_user")  # the bias
    assert fixed.index("app.repo.UserRepository.insert_user") < fixed.index("app.repo.UserRepository")
    assert fixed.index("app.repo.UserRepository") == fixed.index("app.repo.UserRepository.insert_user") + 1


def test_migrations_go_last_unless_the_query_is_about_them(built):
    fixed = _index(built, demote_migrations=True)
    ranked = fixed.ranked("insert user rows", "NB")
    migration = [i for i, n in enumerate(ranked) if n.startswith("migrations.")]
    assert migration and min(migration) > max(i for i, n in enumerate(ranked) if not n.startswith("migrations."))
    about = fixed.ranked("which migration creates the user table", "NB")
    assert about[0].startswith("migrations.")  # asked for: not demoted


def test_priors_are_off_by_default(built):
    plain = _index(built)
    rows = plain.bm25["NB"].rank(__import__("harness.experiments.query_to_evidence.nlq.lexical", fromlist=["tokenize"]).tokenize("insert user rows"))
    assert plain.ranked("insert user rows", "NB") == [plain.names[i] for i in rows]


def test_decorator_parsing_is_framework_agnostic():
    assert _parse_decorator("@bp.route('/follow/<u>', methods=['POST'])") == ("bp.route", "POST", "/follow/<u>")
    assert _parse_decorator('@router.delete("/{u}/follow", name="x")') == ("router.delete", "DELETE", "/{u}/follow")
    assert _parse_decorator("@login_required") == ("login_required", None, None)


def test_entry_points_are_registered_never_called_functions(built):
    builder, _ = built
    entries = find_entry_points(builder)
    assert set(entries) == {"app.routes.register_user"}  # `route`/`wrap` are called; @property is builtin
    assert entries["app.routes.register_user"].label() == "POST /users"


def test_labels_show_a_path_only_when_it_is_one():
    from prism.graph.entry_points import EntryPoint
    assert EntryPoint("q", "router.post", "POST", "").label() == "POST @router.post"
    assert EntryPoint("q", "pytest.mark.parametrize", None, "author, result").label() == "@pytest.mark.parametrize"
    assert EntryPoint("q", "bp.route", None, "/notifications").label() == "/notifications"


def test_test_code_is_never_an_entry(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import pytest\n\n\n@pytest.fixture\ndef user():\n    return 1\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert find_entry_points(builder) == {}


def test_entry_context_on_hits_is_display_only(built):
    builder, _ = built
    ix = _index(built, member_over_class=True)
    hits = ix.hits("insert user rows", "NB", 5)
    assert [h["qname"] for h in hits] == ix.ranked("insert user rows", "NB")[:5]  # order untouched
    insert = next(h for h in hits if h["qname"] == "app.repo.UserRepository.insert_user")
    assert insert["entry_context"] == ["[Entry: POST /users] -> register_user() -> create_account() -> UserRepository.insert_user()"]
    assert insert["location"] == "app/repo.py:5"
    entries = find_entry_points(builder)
    chain = entry_paths(builder, "app.routes.register_user", entries)
    assert chain == [["app.routes.register_user"]] and format_entry_context(chain[0], entries) == "[Entry: POST /users] -> register_user()"
