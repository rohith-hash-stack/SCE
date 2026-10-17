"""Breadcrumbs (`prism.graph.render_breadcrumbs`): the slice-boundary
invariants, the django_t02_016 regression shape, inline rendering and
byte-exact stripping."""
from __future__ import annotations

import ast
import textwrap

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from prism.cli import build_pipeline
from prism.graph.decorator_kinds import DecoratorKind, classify, find_qualified
from prism.graph.entry_points import EntryIndex
from prism.graph.render_breadcrumbs import (
    MAX_TOKENS, Breadcrumb, BreadcrumbInvariantError, inline, slice_breadcrumbs, slice_roots, strip, validate,
)

# The shape of Django's BaseDatabaseWrapper on django_t02_016: `@async_unsafe`
# is a `@wraps` closure, and savepoint() has no in-repo caller only because
# its real caller dispatches dynamically.
DJANGO_016 = '''
from functools import wraps


def async_unsafe(message):
    def decorator(func):
        @wraps(func)
        def inner(*args, **kwargs):
            return func(*args, **kwargs)
        return inner
    if callable(message):
        func = message
        message = "not from an event loop"
        return decorator(func)
    return decorator


class BaseDatabaseWrapper:
    @async_unsafe
    def savepoint(self):
        return self._savepoint()

    def _savepoint(self):
        return self.cursor()

    def get_autocommit(self):
        self.ensure_connection()

    @async_unsafe
    def cursor(self):
        return self._cursor()

    def _cursor(self):
        self.close_if_health_check_failed()
        self.ensure_connection()

    def close_if_health_check_failed(self):
        return None

    def ensure_connection(self):
        self.connect()

    @async_unsafe
    def connect(self):
        return None
'''
GOLD_016 = [f"db.base.BaseDatabaseWrapper.{m}" for m in
            ("_cursor", "close_if_health_check_failed", "connect", "cursor", "ensure_connection")]

APP = '''
from functools import wraps

ROUTES = []


def route(path):
    def wrap(fn):
        ROUTES.append((path, fn))
        return fn
    return wrap


@route("/users")
def register_user(payload):
    return create_account(payload)


@route("/admin/users")
def admin_create(payload):
    audit(payload)
    return create_account(payload)


def audit(payload):
    return log(payload)


def log(payload):
    return payload


def create_account(payload):
    validate_payload(payload)
    return insert_user(payload)


def validate_payload(payload):
    return normalize(payload)


def normalize(payload):
    return payload


def insert_user(payload):
    return write_row(payload)


def write_row(payload):
    return payload
'''
APP_FUNCS = ["register_user", "admin_create", "audit", "log", "create_account", "validate_payload", "normalize",
             "insert_user", "write_row"]


def _build(tmp_path_factory, name, files):
    root = tmp_path_factory.mktemp(name)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(textwrap.dedent(text))
    builder, _ = build_pipeline(str(root))
    return EntryIndex(builder)


@pytest.fixture(scope="module")
def django016(tmp_path_factory):
    return _build(tmp_path_factory, "d016", {"db/__init__.py": "", "db/base.py": DJANGO_016})


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return _build(tmp_path_factory, "app", {"app/__init__.py": "", "app/flow.py": APP})


def test_django_016_async_unsafe_is_a_wrapper_and_emits_nothing(django016):
    tree = ast.parse(textwrap.dedent(DJANGO_016))
    fn, cls = find_qualified(tree, ["async_unsafe"])
    assert classify(fn, False, cls, tree) == DecoratorKind.WRAPPER
    assert django016.entries == {}  # savepoint() was an "entry" under the old rule
    assert slice_breadcrumbs(django016, GOLD_016, strict=True) == []
    rendered = "<context>\n" + "".join(f'  <node id="{q}">\n  </node>\n' for q in GOLD_016) + "</context>\n"
    out, placed = inline(rendered, slice_breadcrumbs(django016, GOLD_016))
    assert (out, placed) == (rendered, [])  # zero breadcrumb tokens


def test_breadcrumb_stops_at_the_slice_and_never_redescribes_it(app):
    slice_ = ["app.flow.create_account", "app.flow.insert_user", "app.flow.write_row"]
    (crumb,) = slice_breadcrumbs(app, slice_, strict=True)
    assert crumb.root == "app.flow.create_account"
    assert crumb.text == "[Entry: /admin/users] -> admin_create() => enters at create_account()"
    assert not set(crumb.chain[:-1]) & set(slice_)


def test_single_symbol_context_matches_the_search_payload_format(app):
    assert app.context("app.flow.write_row") == [
        "[Entry: /admin/users] -> admin_create() -> create_account() -> insert_user() => enters at write_row()"]
    assert app.context("app.flow.register_user") == ["[Entry: /users] => enters at register_user()"]


def test_validate_names_each_broken_invariant(app):
    members = {"app.flow.create_account", "app.flow.insert_user"}
    inside = Breadcrumb("app.flow.insert_user", ("app.flow.register_user", "app.flow.create_account", "app.flow.insert_user"),
                        "x", 5)
    problems = validate(inside, members, app)
    assert any("inside the slice" in p for p in problems)
    assert any("mid-chain" in p for p in problems)
    assert any("does not describe" in p for p in problems)
    no_edge = Breadcrumb("app.flow.create_account", ("app.flow.register_user", "app.flow.log", "app.flow.create_account"),
                         "y", 5)
    assert any("no resolved call edge" in p for p in validate(no_edge, members, app))
    big = Breadcrumb("app.flow.create_account", ("app.flow.register_user", "app.flow.create_account"),
                     "[Entry: /users] -> register_user() => enters at create_account()", MAX_TOKENS + 1)
    assert any("tokens" in p for p in validate(big, members, app))


def test_violations_raise_in_strict_mode_and_drop_otherwise(app, monkeypatch):
    bad = [["app.flow.register_user", "app.flow.create_account", "app.flow.insert_user"]]
    monkeypatch.setattr(app, "paths", lambda root, blocked=frozenset(): bad)
    slice_ = ["app.flow.create_account", "app.flow.insert_user"]
    with pytest.raises(BreadcrumbInvariantError):
        slice_breadcrumbs(app, slice_, strict=True)
    assert slice_breadcrumbs(app, slice_, strict=False) == []


def test_token_cap_drops_later_breadcrumbs(app):
    slice_ = ["app.flow.audit", "app.flow.validate_payload", "app.flow.insert_user"]
    full = slice_breadcrumbs(app, slice_, strict=True)
    capped = slice_breadcrumbs(app, slice_, max_tokens=full[0].tokens, strict=True)
    assert len(full) > 1 and capped == full[:1]


def test_inline_places_under_the_root_node_and_strip_is_byte_exact(app):
    slice_ = ["app.flow.create_account", "app.flow.insert_user"]
    rendered = ("<context>\n  <nodes>\n"
                + "".join(f'    <node file="app/flow.py" id="{q}" line="1">\n      <body>x &lt; y</body>\n    </node>\n'
                          for q in slice_)
                + "  </nodes>\n</context>\n")
    out, placed = inline(rendered, slice_breadcrumbs(app, slice_, strict=True))
    assert len(placed) == 1
    lines = out.splitlines()
    i = next(n for n, line in enumerate(lines) if 'id="app.flow.create_account"' in line)
    assert lines[i + 1].strip().startswith("<entry_context>[Entry: /admin/users]")
    assert "=&gt;" not in lines[i + 1] and "->" in lines[i + 1]
    assert strip(out) == rendered


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(APP_FUNCS), min_size=1, max_size=6, unique=True),
       st.integers(min_value=10, max_value=MAX_TOKENS))
def test_invariants_hold_for_any_slice(app, names, cap):
    slice_ = [f"app.flow.{n}" for n in names]
    members = set(slice_)
    crumbs = slice_breadcrumbs(app, slice_, max_tokens=cap, strict=True)
    roots = slice_roots(app, slice_)
    assert sum(c.tokens for c in crumbs) <= cap
    assert len({c.root for c in crumbs}) == len(crumbs)  # one per root
    for c in crumbs:
        assert c.root in roots
        assert not members & set(c.chain[:-1])
        assert c.chain[0] in app.entries
        assert validate(c, members, app, cap) == []


DISPATCH = '''
ROUTES = []


def route(path):
    def wrap(fn):
        ROUTES.append((path, fn))
        return fn
    return wrap


class BaseWrapper:
    def ensure_timezone(self):
        return False

    def cursor(self):
        return 1


class PgWrapper(BaseWrapper):
    def ensure_timezone(self):
        return self.cursor()


@route("/tz")
def tz_changed(conn: BaseWrapper):
    return conn.ensure_timezone()
'''


def test_breadcrumbs_never_rest_on_override_fan_out(tmp_path_factory):
    """The residual django_t02_016 path rode the virtual-dispatch fan-out
    from a call on `Base.ensure_timezone` to an override: a "may call"
    edge, not a call the code makes."""
    index = _build(tmp_path_factory, "dispatch", {"app/__init__.py": "", "app/db.py": DISPATCH})
    graph = index.builder.graph
    fan_out = [d for _, v, d in graph.out_edges("app.db.tz_changed", data=True) if v == "app.db.PgWrapper.ensure_timezone"]
    assert fan_out and all("dispatch_of" in d for d in fan_out)  # the speculative edge exists
    assert slice_breadcrumbs(index, ["app.db.BaseWrapper.cursor"], strict=True) == []
    assert index.context("app.db.BaseWrapper.ensure_timezone") == ["[Entry: /tz] -> tz_changed() => enters at BaseWrapper.ensure_timezone()"]
