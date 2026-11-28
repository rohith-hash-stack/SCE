"""Static call-graph fixes from the Layer 4 recall diagnostic (C-F): each
pattern is one a runtime trace showed the index missing (FastAPI routing,
Django middleware and sessions), reduced to a small repo.

- C: a closure gets an `ENCLOSES` edge from the function that defines it,
  which the blast-radius walk climbs at no extra hop (its name stays flat:
  frozen ground truth names closures that way).
- D: a constructor's callers are the callers of its class
  (`INSTANTIATES`), and calling a value that holds a class (`Type[C]`
  parameter, class default, `a or b`, `self.attr`) constructs it.
- E: `self.m()` with no `m` up the hierarchy reaches subclass definitions.
- F: a receiver named after a class that only inherits the method, while
  other families define their own, is ambiguous rather than guessed.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline
from prism.packer.candidate_index import _upstream_walk

FILES = {
    "app/__init__.py": "",
    "app/deprecation.py": '''
class MiddlewareMixin:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = None
        if hasattr(self, "process_request"):
            response = self.process_request(request)
        response = response or self.get_response(request)
        if hasattr(self, "process_response"):
            response = self.process_response(request, response)
        return response
''',
    "app/models.py": '''
class Model:
    def save(self, using=None):
        return using


class Session(Model):
    pass


class AppConfig:
    def ready(self):
        return True
''',
    "app/sessions.py": '''
from app.deprecation import MiddlewareMixin


class SessionBase:
    def save(self, must_create=False):
        raise NotImplementedError


class SessionStore(SessionBase):
    def save(self, must_create=False):
        return must_create


class SessionMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.session = self.make_store()

    def process_response(self, request, response):
        request.session.save()
        return response


def boot(app_config):
    return app_config.ready()
''',
    "app/routing.py": '''
from typing import Optional, Type, Union


def get_dependant(path, call):
    return (path, call)


def solve_dependencies(request):
    return get_dependant("/", request)


def get_request_handler(dependant):
    async def app(request):
        return await solve_dependencies(request)
    return app


def get_websocket_app(dependant):
    async def app(websocket):
        return await solve_dependencies(websocket)
    return app


class APIRoute:
    def __init__(self, path, endpoint):
        self.dependant = get_dependant(path, endpoint)


class OtherRoute:
    def __init__(self, path, endpoint):
        self.dependant = get_dependant(path, endpoint)


class APIWebSocketRoute:
    def __init__(self, path, endpoint):
        self.dependant = get_dependant(path, endpoint)


def make_route_class():
    return APIRoute


class APIRouter:
    def __init__(self, route_class: Type[APIRoute] = APIRoute):
        self.route_class = route_class

    def add_api_route(self, path, endpoint, route_class_override: Optional[Type[APIRoute]] = None):
        route_class = route_class_override or self.route_class
        return route_class(path, endpoint)

    def add_api_websocket_route(self, path, endpoint):
        return APIWebSocketRoute(path, endpoint)

    def add_either(self, path, endpoint, kind: Type[Union[APIRoute, OtherRoute]]):
        return kind(path, endpoint)

    def add_from_factory(self, path, endpoint):
        factory = make_route_class()
        return factory(path, endpoint)
''',
}


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text.lstrip("\n"))
    return build_pipeline(str(root))[0]


def _out(b, src, relation=None):
    return {v: (d.get("relation"), d.get("kind")) for _u, v, d in b.graph.out_edges(src, data=True)
            if relation is None or d.get("relation") == relation}


def test_a_closure_gets_an_encloses_edge_from_its_enclosing_function_and_keeps_its_name(builder):
    names = {s.qualified_name for s in builder.symbol_table if s.module == "app.routing"}
    assert {"app.routing.app", "app.routing.app#2"} <= names  # unchanged identities
    encloses = {(u, v) for u, v, d in builder.graph.edges(data=True) if d.get("relation") == "ENCLOSES"}
    assert encloses == {("app.routing.get_request_handler", "app.routing.app"),
                        ("app.routing.get_websocket_app", "app.routing.app#2")}


def test_the_caller_walk_crosses_a_closure_at_its_own_hop(builder):
    walk = _upstream_walk(builder, "app.routing.get_dependant", max_hops=2)
    # get_dependant <- solve_dependencies <- (closure app == get_request_handler at run time)
    assert walk["app.routing.solve_dependencies"] == 1
    assert walk["app.routing.app"] == 2 and walk["app.routing.get_request_handler"] == 2
    assert walk["app.routing.get_websocket_app"] == 2


def test_a_constructors_callers_are_the_callers_of_its_class(builder):
    walk = _upstream_walk(builder, "app.routing.get_dependant", max_hops=2)
    assert walk["app.routing.APIWebSocketRoute.__init__"] == 1
    assert walk["app.routing.APIRouter.add_api_websocket_route"] == 2  # `APIWebSocketRoute(...)`


def test_calling_a_value_that_holds_a_class_constructs_it(builder):
    assert _out(builder, "app.routing.APIRouter.add_api_route", "INSTANTIATES") == {
        "app.routing.APIRoute": ("INSTANTIATES", None)}  # Optional[Type[APIRoute]] or self.route_class
    walk = _upstream_walk(builder, "app.routing.get_dependant", max_hops=2)
    assert walk["app.routing.APIRouter.add_api_route"] == 2
    # Never a guess: two possible classes, or a value the annotations do not name
    assert _out(builder, "app.routing.APIRouter.add_either", "INSTANTIATES") == {}
    assert _out(builder, "app.routing.APIRouter.add_from_factory", "INSTANTIATES") == {}


def test_an_undefined_self_hook_reaches_the_subclass_definitions(builder):
    out = _out(builder, "app.deprecation.MiddlewareMixin.__call__")
    assert out["app.sessions.SessionMiddleware.process_response"] == ("CALLS", "TENTATIVE_CALL")
    assert out["app.sessions.SessionMiddleware.process_request"] == ("CALLS", "TENTATIVE_CALL")
    assert _upstream_walk(builder, "app.sessions.SessionMiddleware.process_response", max_hops=1) == {
        "app.deprecation.MiddlewareMixin.__call__": 1}


def test_a_receiver_named_after_a_class_that_only_inherits_the_method_is_ambiguous(builder):
    out = _out(builder, "app.sessions.SessionMiddleware.process_response")
    assert "app.models.Model.save" not in out  # `session` -> Session(Model) would have guessed Model.save
    assert any(str(t).startswith("<ambiguous:save@") for t in out)
    # The convention still links when the named class defines the method itself
    assert _out(builder, "app.sessions.boot")["app.models.AppConfig.ready"] == ("CALLS", "TENTATIVE_CALL")
