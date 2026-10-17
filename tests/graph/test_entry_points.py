"""Entry points: the 4-way decorator taxonomy (`prism.graph.decorator_kinds`),
route evidence for unreadable decorators, dependency-source inspection, TS
decorator arguments, and call-registered entries (`prism.graph.registrations`)."""
from __future__ import annotations

import ast
import textwrap

import pytest

from prism.cli import build_pipeline
from prism.graph.decorator_kinds import DecoratorKind, classify, find_qualified
from prism.graph.entry_points import EntryIndex, find_entry_points

DECOS = '''
from functools import wraps

REGISTRY = {}
ROUTES = []


def register(fn):
    REGISTRY[fn.__name__] = fn
    return fn


def route(path, methods=None):
    def wrap(fn):
        ROUTES.append((path, fn))
        return fn
    return wrap


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


def display(description=None):
    def decorator(func):
        func.short_description = description
        return func
    return decorator


def mixed(fn):
    if getattr(fn, "plain", False):
        return fn
    @wraps(fn)
    def wrapped(*args):
        return fn(*args)
    return wrapped


class Router:
    def __init__(self):
        self.routes = []

    def add_route(self, path, fn):
        self.routes.append((path, fn))

    def api_route(self, path):
        def decorator(func):
            self.add_route(path, func)
            return func
        return decorator

    def get(self, path):
        return self.api_route(path)
'''

VIEWS = '''
from app.decos import async_unsafe, display, mixed, register, route, Router
from extpkg import external_route, external_guard
from deplib.decorators import dep_guard, dep_register

router = Router()


def service():
    return 1


@register
def cmd_a():
    return service()


@route("/orders", methods=["POST"])
def orders():
    return service()


@async_unsafe
def cursor():
    return service()


@display(description="shown")
def shown():
    return service()


@display("/admin/report")
def report():
    return service()


@mixed
def maybe():
    return service()


@router.get("/items")
def items():
    return service()


@external_route("/ext")
def ext():
    return service()


@external_guard
def guarded():
    return service()


@dep_guard("/guarded")
def dep_guarded():
    return service()


@dep_register
def dep_registered():
    return service()


def admin_tool():
    return orders()
'''

DEPLIB = '''
from functools import wraps

REG = []


def dep_guard(path):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args):
            return fn(*args)
        return wrapper
    return deco


def dep_register(fn):
    REG.append(fn)
    return fn
'''

URLS = '''
from app import views
from app.cbv import OrderView
from django.urls import path


def health_check():
    return views.service()


urlpatterns = [
    path("orders/", views.cmd_a),
    path("cbv/", OrderView.as_view()),
]


def make_app(app):
    app.add_url_rule("/health", "health", health_check)
'''

CBV = '''
class OrderView:
    def get(self, request):
        return 1

    def post(self, request):
        return 2

    def helper(self):
        return 3
'''

NEST_TS = '''
export class ItemsController {
  @Get('/items/:id')
  findOne(id: string) { return this.load(id); }

  @UseGuards(AuthGuard)
  guardedOnly() { return 1; }

  load(id: string) { return id; }
}
'''

EXPRESS_JS = '''
const express = require('express');
const router = express.Router();

function findAll() { return []; }
function auth(req, res, next) { next(); }
function listUsers(req, res) { return findAll(); }
function updateUser(req, res) { return findAll(); }

router.get('/users', auth, listUsers);
router.route('/users/:id').put(updateUser);
router.get('etag');

function setup(app) {
  app.post('/inline', (req, res) => { findAll(); });
}

module.exports = { router, setup };
'''

TRPC_TS = '''
import { router, publicProcedure } from './trpc';

export function getUserById(opts: any) { return opts; }

export const appRouter = router({
  user: router({
    byId: publicProcedure.query(getUserById),
  }),
});

export function callIt(client: any) {
  return client.user.byId.query({ id: 1 });
}
'''


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("entries")
    files = {
        "app/__init__.py": "", "app/decos.py": DECOS, "app/views.py": VIEWS, "app/urls.py": URLS, "app/cbv.py": CBV,
        "web/items.controller.ts": NEST_TS, "web/routes.js": EXPRESS_JS, "web/server.ts": TRPC_TS,
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(textwrap.dedent(text))
    site = tmp_path_factory.mktemp("site")
    (site / "deplib").mkdir()
    (site / "deplib" / "__init__.py").write_text("")
    (site / "deplib" / "decorators.py").write_text(textwrap.dedent(DEPLIB))
    builder, _ = build_pipeline(str(root))
    return builder, str(site)


@pytest.fixture(scope="module")
def entries(repo):
    builder, site = repo
    return find_entry_points(builder, dependency_paths=[site])


def _kind(source: str, path: list[str], called: bool):
    tree = ast.parse(textwrap.dedent(source))
    fn, cls = find_qualified(tree, path)
    return classify(fn, called, cls, tree)


@pytest.mark.parametrize("path,called,kind", [
    (["register"], False, DecoratorKind.REGISTRATION),
    (["route"], True, DecoratorKind.REGISTRATION),
    (["async_unsafe"], False, DecoratorKind.WRAPPER),
    (["async_unsafe"], True, DecoratorKind.WRAPPER),
    (["display"], True, DecoratorKind.ANNOTATION),
    (["mixed"], False, DecoratorKind.UNKNOWN),
    (["Router", "get"], True, DecoratorKind.REGISTRATION),  # FastAPI-style factory through self.api_route
    (["Router", "api_route"], True, DecoratorKind.REGISTRATION),
])
def test_decorator_taxonomy(path, called, kind):
    assert _kind(DECOS, path, called) == kind


def test_dual_use_factory_branch_is_not_mistaken_for_a_wrapper():
    lib = '''
    class Library:
        def __init__(self):
            self.filters = {}
        def filter(self, name=None, filter_func=None):
            if name is None and filter_func is None:
                def dec(func):
                    return self.filter_function(func)
                return dec
            elif callable(name):
                return self.filter_function(name)
            self.filters[name] = filter_func
            return filter_func
        def filter_function(self, func):
            self.filters[func.__name__] = func
            return func
    '''
    assert _kind(lib, ["Library", "filter"], False) == DecoratorKind.REGISTRATION
    assert _kind(lib, ["Library", "filter"], True) == DecoratorKind.REGISTRATION


def test_registration_decorators_make_entries(entries):
    assert entries["app.views.cmd_a"].kind == "registration"
    orders = entries["app.views.orders"]
    assert (orders.method, orders.path, orders.label()) == ("POST", "/orders", "POST /orders")
    assert entries["app.views.items"].label() == "GET /items"  # instance decorator, factory followed or route evidence
    assert entries["app.views.dep_registered"].kind == "registration"  # read from dependency source


def test_wrappers_mixed_and_bare_annotations_never_make_entries(entries):
    for name in ("cursor", "maybe", "shown", "guarded"):
        assert f"app.views.{name}" not in entries, name


def test_annotation_and_unreadable_decorators_need_route_evidence(entries):
    assert entries["app.views.report"].path == "/admin/report" and entries["app.views.report"].kind == "annotation"
    assert entries["app.views.ext"].kind is None and entries["app.views.ext"].path == "/ext"


def test_dependency_source_rejects_a_wrapper_that_route_evidence_alone_would_admit(repo):
    builder, site = repo
    without = find_entry_points(builder)
    with_deps = find_entry_points(builder, dependency_paths=[site])
    assert "app.views.dep_guarded" in without  # unreadable + a path literal
    assert "app.views.dep_guarded" not in with_deps  # read: a wrapper


def test_no_in_repo_callers_is_metadata_not_a_trigger(entries):
    assert entries["app.views.orders"].called_in_repo  # admin_tool() calls it - still an entry
    assert "app.views.service" not in entries and "app.views.admin_tool" not in entries


def test_typescript_decorator_arguments_are_captured(entries):
    found = entries["web.items.controller.ItemsController.findOne"]
    assert (found.method, found.path) == ("GET", "/items/:id")
    assert "web.items.controller.ItemsController.guardedOnly" not in entries


def test_express_call_registrations(entries):
    assert entries["web.routes.listUsers"].label() == "GET /users"  # last function arg; auth is middleware
    assert entries["web.routes.updateUser"].label() == "PUT /users/:id"  # .route('/x').put(h)
    assert entries["web.routes.setup"].label() == "POST /inline"  # inline handler -> enclosing function
    assert entries["web.routes.listUsers"].source == "call:express"
    assert "web.routes.auth" not in entries and "web.routes.findAll" not in entries


def test_trpc_procedures_are_named_by_router_keys(entries):
    e = entries["web.server.getUserById"]
    assert (e.label(), e.source) == ("QUERY user.byId", "call:trpc")
    assert "web.server.callIt" not in entries  # a client call passes an object, not a resolver


def test_django_and_flask_registrations(entries):
    assert entries["app.views.cmd_a"].source == "decorator"  # decorator provenance wins over path()
    assert entries["app.cbv.OrderView.get"].label() == "URL cbv/"
    assert entries["app.cbv.OrderView.post"].source == "call:django"
    assert "app.cbv.OrderView.helper" not in entries
    assert entries["app.urls.health_check"].label() == "URL /health"


def test_entry_index_uses_the_same_entries(repo, entries):
    builder, site = repo
    assert EntryIndex(builder, dependency_paths=[site]).entries == entries


def test_route_handlers_defined_in_test_files_are_not_entries(tmp_path):
    """FastAPI's own tests define apps (`tests/test_dependency_cache.py:
    @app.get("/") def get2()`); those routes are test scaffolding."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "deps.py").write_text("def Depends(x=None):\n    return x\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cache.py").write_text(textwrap.dedent('''
        from pkg.deps import Depends
        app = object()


        @app.get("/")
        def get2():
            return Depends()


        def test_it():
            assert get2() is None
    '''))
    builder, _ = build_pipeline(str(tmp_path))
    assert find_entry_points(builder) == {}
    assert EntryIndex(builder).context("pkg.deps.Depends") == []
