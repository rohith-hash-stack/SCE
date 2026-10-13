"""Regression tests for the resolver's parameter/local-shadowing defect
(Operation Groundwire, Phase 1 / Spec 1).

Before the fix, a function-local receiver with no type binding fell
through to Rule D's module-level bare-name lookup, and that index also
held class-member symbols - so `def f(client: HttpClient): client.post()`
in a module that also had some `self.client` attribute linked to the
phantom `pkg.app.<Class>.client.post` instead of `HttpClient.post`.

The fix has three parts:

- Python parameter annotations bind (`client: HttpClient`).
- Function-local names shadow module scope in `_resolve_segments`.
- Class members are no longer indexed under a bare module-level name for
  Python and JS/TS.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline

CLIENT_PY = textwrap.dedent(
    """
    class HttpClient:
        def __init__(self, timeout=10):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path):
            return path

        def fetch(self, path):
            return path


    class OtherClient:
        def post(self, path):
            return path


    def make_client():
        return HttpClient(timeout=10)


    def build():
        return object()


    def make_client_annot() -> HttpClient:
        return build()


    class Registry:
        _instance = None

        @classmethod
        def instance(cls):
            if cls._instance is None:
                cls._instance = HttpClient()
            return cls._instance
    """
)

# Classes whose `client` attribute collides, by bare name, with the
# parameters/locals the functions under test use. The last registered
# (`Holder`) used to win `_module_index["pkg.app"]["client"]`.
COLLIDING_ATTRS = textwrap.dedent(
    """


    class Injected:
        def __init__(self, factory):
            self.client = factory()

        def run(self):
            self.client.post("/e")


    class Holder:
        client = None
    """
)


def _build(tmp_path, app_source: str, extra: dict[str, str] | None = None):
    pkg = tmp_path / "repo" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "client.py").write_text(CLIENT_PY)
    (pkg / "app.py").write_text(textwrap.dedent(app_source))
    for name, text in (extra or {}).items():
        (pkg / name).write_text(textwrap.dedent(text))
    builder, _ = build_pipeline(str(tmp_path / "repo"))
    return builder


def _calls(builder, caller: str) -> dict[str, str | None]:
    """{target: kind} for every CALLS out-edge of `caller`."""
    graph = builder.graph
    assert caller in graph, f"{caller} missing from graph"
    return {
        target: data.get("kind")
        for _, target, data in graph.out_edges(caller, data=True)
        if data.get("relation", "CALLS") == "CALLS"
    }


def _no_attribute_symbol_targets(calls: dict[str, str | None], module: str = "pkg.app") -> None:
    """No call target may be routed through a class-member symbol of
    `module` used as if it were a module-level name."""
    for target in calls:
        if target.startswith("<"):
            continue
        parts = target.split(".")
        # pkg.app.<Class>.<attr>.<method>: a phantom chained through an attribute.
        assert not (target.startswith(f"{module}.") and len(parts) >= 5), target


# --------------------------------------------------------------------- #
# Case 1/2: annotated parameter whose name collides with a class attribute
# --------------------------------------------------------------------- #
def test_annotated_param_shadowing_class_attribute_binds_annotation(tmp_path):
    builder = _build(
        tmp_path,
        "from pkg.client import HttpClient\n\n\ndef f(client: HttpClient):\n    client.post('/e')\n" + COLLIDING_ATTRS,
    )
    calls = _calls(builder, "pkg.app.f")
    assert calls.get("pkg.client.HttpClient.post", "missing") is None  # confident edge, no kind
    assert "pkg.app.Injected.client.post" not in builder.graph
    assert "pkg.app.Holder.client.post" not in builder.graph
    _no_attribute_symbol_targets(calls)


def test_annotated_param_unique_method_binds_annotation(tmp_path):
    builder = _build(
        tmp_path,
        "from pkg.client import HttpClient\n\n\ndef f(client: HttpClient):\n    client.fetch('/e')\n" + COLLIDING_ATTRS,
    )
    calls = _calls(builder, "pkg.app.f")
    assert calls.get("pkg.client.HttpClient.fetch", "missing") is None
    _no_attribute_symbol_targets(calls)


# --------------------------------------------------------------------- #
# Case 3: untyped parameter never routes through an attribute symbol
# --------------------------------------------------------------------- #
def test_untyped_param_never_resolves_through_class_attribute(tmp_path):
    builder = _build(tmp_path, "def f(client):\n    client.post('/e')\n" + COLLIDING_ATTRS)
    calls = _calls(builder, "pkg.app.f")
    _no_attribute_symbol_targets(calls)
    assert not any(t.startswith(("pkg.app.Injected.client", "pkg.app.Holder.client")) for t in calls)


# --------------------------------------------------------------------- #
# Case 4: constructor-bound local is unaffected by the collision
# --------------------------------------------------------------------- #
def test_constructor_local_with_attribute_collision(tmp_path):
    builder = _build(
        tmp_path,
        "from pkg.client import HttpClient\n\n\ndef f():\n    client = HttpClient()\n    client.post('/e')\n"
        + COLLIDING_ATTRS,
    )
    calls = _calls(builder, "pkg.app.f")
    assert calls.get("pkg.client.HttpClient.post", "missing") is None


# --------------------------------------------------------------------- #
# Case 5: loop / with / except / comprehension / walrus targets are locals
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "body",
    [
        "def f(clients):\n    for client in clients:\n        client.post('/e')\n",
        "def f(make):\n    with make() as client:\n        client.post('/e')\n",
        "def f():\n    try:\n        pass\n    except Exception as client:\n        client.post('/e')\n",
        "def f(xs):\n    return [client.post('/e') for client in xs]\n",
        "def f(get):\n    if (client := get()):\n        client.post('/e')\n",
        "def f(pair):\n    client, other = pair\n    client.post('/e')\n",
        "def f():\n    return lambda client: client.post('/e')\n",
    ],
    ids=["for", "with", "except", "comprehension", "walrus", "tuple_unpack", "lambda"],
)
def test_local_binding_forms_never_resolve_through_attribute(tmp_path, body):
    builder = _build(tmp_path, body + COLLIDING_ATTRS)
    calls = _calls(builder, "pkg.app.f")
    _no_attribute_symbol_targets(calls)
    assert not any(t.startswith(("pkg.app.Injected.client", "pkg.app.Holder.client")) for t in calls)


def test_global_declaration_is_not_a_local(tmp_path):
    """`global client` makes the name module-scoped again, so Rule D still
    applies to it."""
    builder = _build(
        tmp_path,
        """
        from pkg.client import HttpClient

        client = HttpClient()


        def f():
            global client
            client = HttpClient()
            client.post('/e')


        def g():
            client.fetch('/e')
        """,
    )
    assert "pkg.client.HttpClient.post" in _calls(builder, "pkg.app.f")
    # A module-level variable has no symbol to bind through, so `g` must not
    # gain a phantom edge either way; it must not crash.
    _calls(builder, "pkg.app.g")


def test_called_local_never_binds_same_named_module_function(tmp_path):
    """`callback()` where `callback` is a parameter is not a call to the
    module-level function that happens to be named `callback`."""
    builder = _build(
        tmp_path,
        """
        def callback():
            return 1


        def f(callback):
            return callback()


        def g():
            return callback()
        """,
    )
    assert "pkg.app.callback" not in _calls(builder, "pkg.app.f")
    assert "pkg.app.callback" in _calls(builder, "pkg.app.g")


# --------------------------------------------------------------------- #
# Case 6: bare module-function call is not shadowed by a same-named method
# --------------------------------------------------------------------- #
def test_bare_call_resolves_module_function_not_later_method(tmp_path):
    builder = _build(
        tmp_path,
        """
        def run():
            return 1


        def main():
            return run()


        class Worker:
            def run(self):
                return 2
        """,
    )
    calls = _calls(builder, "pkg.app.main")
    assert "pkg.app.run" in calls
    assert "pkg.app.Worker.run" not in calls


def test_class_member_not_in_module_index_but_class_qualified_key_is(tmp_path):
    builder = _build(tmp_path, "class Worker:\n    limit = 3\n\n    def run(self):\n        return self.limit\n")
    table = builder.symbol_table
    assert table.resolve_in_module("pkg.app", "Worker") == "pkg.app.Worker"
    assert table.resolve_in_module("pkg.app", "run") is None
    assert table.resolve_in_module("pkg.app", "Worker.run") == "pkg.app.Worker.run"


NESTED_APP = """
class K:
    def meth(self):
        def s(x):
            return x

        return s(1)

    def get(self, flag):
        if flag:
            match = flag.parse
        else:
            def match(_):
                return True
        return match(1)


class Other:
    def s(self):
        return 0

    def match(self):
        return 0
"""


def test_nested_def_in_method_stays_resolvable_by_bare_name(tmp_path):
    """A closure registered as `K.s` (the ancestor walk names the class) is
    not a class member: a bare `s()` in the defining method links to it
    confidently, even with a same-named method on another class."""
    builder = _build(tmp_path, NESTED_APP)
    assert _calls(builder, "pkg.app.K.meth").get("pkg.app.K.s", "missing") is None
    assert builder.symbol_table.get("pkg.app.K.s").nested_in_function
    assert not builder.symbol_table.get("pkg.app.K.meth").nested_in_function


def test_nested_def_wins_over_same_named_assignment(tmp_path):
    builder = _build(tmp_path, NESTED_APP)
    assert "pkg.app.K.match" in _calls(builder, "pkg.app.K.get")


def test_cached_rebuild_preserves_bare_name_visibility(tmp_path):
    """The index cache rehydrates through `SymbolTable.add`, so the
    nested-in-function flag must round-trip for Rule D to agree."""
    first = _build(tmp_path, NESTED_APP)
    second, _ = build_pipeline(str(tmp_path / "repo"))
    for name in ("s", "match", "meth", "get", "K"):
        assert second.symbol_table.resolve_in_module("pkg.app", name) == first.symbol_table.resolve_in_module("pkg.app", name)
    assert second.symbol_table.get("pkg.app.K.s").nested_in_function
    assert _calls(second, "pkg.app.K.meth") == _calls(first, "pkg.app.K.meth")


# --------------------------------------------------------------------- #
# Case 7: annotation naming a class through a module alias (dotted name)
# --------------------------------------------------------------------- #
def test_dotted_annotation_through_module_import(tmp_path):
    builder = _build(
        tmp_path,
        "from pkg import client as cmod\n\n\ndef f(client: cmod.HttpClient):\n    client.post('/e')\n" + COLLIDING_ATTRS,
    )
    calls = _calls(builder, "pkg.app.f")
    assert calls.get("pkg.client.HttpClient.post", "missing") is None
    _no_attribute_symbol_targets(calls)


def test_default_valued_annotated_param_binds(tmp_path):
    builder = _build(
        tmp_path,
        "from pkg.client import HttpClient\n\n\ndef f(client: HttpClient = None):\n    client.post('/e')\n"
        + COLLIDING_ATTRS,
    )
    assert _calls(builder, "pkg.app.f").get("pkg.client.HttpClient.post", "missing") is None


def test_annotated_method_param_binds(tmp_path):
    builder = _build(
        tmp_path,
        """
        from pkg.client import HttpClient


        class Service:
            client = None

            def call(self, client: HttpClient):
                client.post('/e')
        """,
    )
    assert _calls(builder, "pkg.app.Service.call").get("pkg.client.HttpClient.post", "missing") is None


# --------------------------------------------------------------------- #
# Case 8: annotation naming an unindexed external class
# --------------------------------------------------------------------- #
def test_unindexed_external_annotation_is_ignored_safely(tmp_path):
    builder = _build(
        tmp_path,
        """
        import socket


        def f(sock: socket.socket):
            sock.sendall(b'x')


        class Conn:
            def __init__(self):
                self.sock = None
        """,
    )
    calls = _calls(builder, "pkg.app.f")
    assert not any(t.startswith("pkg.app.Conn.sock") for t in calls)


# --------------------------------------------------------------------- #
# Case 9: Java/C#/Go keep bare same-class resolution through Rule D
# --------------------------------------------------------------------- #
def test_java_bare_same_class_method_call_unchanged(tmp_path):
    pkg = tmp_path / "jrepo" / "src" / "main" / "java" / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "Service.java").write_text(
        "package com.example;\n"
        "\n"
        "public class Service {\n"
        "    public void run() {\n"
        "        helper();\n"
        "    }\n"
        "\n"
        "    private void helper() {\n"
        "    }\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(tmp_path / "jrepo"))
    assert builder.graph.has_edge("com.example.Service.run", "com.example.Service.helper")


# --------------------------------------------------------------------- #
# The 20-pattern receiver-typing probe (post-mortem census), pinned.
# --------------------------------------------------------------------- #
PROBE_APP = """
from pkg.client import HttpClient, Registry, make_client, make_client_annot


def p01_local_ctor():
    client = HttpClient(timeout=10)
    client.post("/e")


def p02_param_untyped(client):
    client.post("/e")


def p02u_param_untyped_unique_name(client):
    client.fetch("/e")


def p03_param_annotated(client: HttpClient):
    client.post("/e")


def p04_factory_inferable_return():
    c = make_client()
    c.post("/e")


def p05_factory_annotated_return():
    c = make_client_annot()
    c.post("/e")


def p06_chained_factory():
    make_client().post("/e")


def p07_singleton_from_class_state():
    Registry.instance().post("/e")


def p08_with_statement():
    with HttpClient() as c:
        c.post("/e")


def p09_conditional(flag):
    c = HttpClient() if flag else None
    c.post("/e")


def p10_container():
    clients = [HttpClient()]
    clients[0].post("/e")


def p11_param_default_or(client=None):
    client = client or HttpClient()
    client.post("/e")


def p12_getattr():
    getattr(HttpClient(), "post")("/e")


class S1_attr_ctor:
    def __init__(self):
        self.client = HttpClient()

    def run(self):
        self.client.post("/e")


class S2_attr_injected_untyped:
    def __init__(self, client):
        self.client = client

    def run(self):
        self.client.post("/e")


class S2u_attr_injected_untyped_unique_name:
    def __init__(self, client):
        self.client = client

    def run(self):
        self.client.fetch("/e")


class S3_attr_injected_annotated:
    def __init__(self, client: HttpClient):
        self.client = client

    def run(self):
        self.client.post("/e")


class S4_class_annotation:
    client: HttpClient

    def run(self):
        self.client.post("/e")


class S5_attr_factory:
    def __init__(self):
        self.client = make_client()

    def run(self):
        self.client.post("/e")


class S6_attr_injected_factory:
    def __init__(self, factory):
        self.client = factory()

    def run(self):
        self.client.post("/e")
"""

POST = "pkg.client.HttpClient.post"
FETCH = "pkg.client.HttpClient.fetch"
TENTATIVE = "TENTATIVE_CALL"

#: caller -> (expected target, expected edge kind). `None` kind = confident.
#: The three parameter rows (p02, p02u, p03) and p11 are the ones this fix
#: changes: before it, all four linked to the phantom
#: `pkg.app.S6_attr_injected_factory.client.<method>`.
PROBE_EXPECTED = {
    "p01_local_ctor": (POST, None),
    "p02_param_untyped": (POST, None),
    "p02u_param_untyped_unique_name": (FETCH, TENTATIVE),
    "p03_param_annotated": (POST, None),
    "p04_factory_inferable_return": (POST, TENTATIVE),
    "p05_factory_annotated_return": (POST, TENTATIVE),
    "p06_chained_factory": (POST, TENTATIVE),
    "p08_with_statement": (POST, None),
    "p09_conditional": (POST, None),
    "p11_param_default_or": (POST, None),
    "S1_attr_ctor.run": (POST, None),
    "S2_attr_injected_untyped.run": (POST, TENTATIVE),
    "S2u_attr_injected_untyped_unique_name.run": (FETCH, TENTATIVE),
    "S3_attr_injected_annotated.run": (POST, TENTATIVE),
    "S4_class_annotation.run": (POST, TENTATIVE),
    "S5_attr_factory.run": (POST, TENTATIVE),
    "S6_attr_injected_factory.run": (POST, TENTATIVE),
}

#: Patterns that still have no edge to `HttpClient.post` (out of Spec 1's scope).
PROBE_UNRESOLVED = ("p07_singleton_from_class_state", "p10_container", "p12_getattr")


@pytest.fixture(scope="module")
def probe_builder(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("probe"), PROBE_APP)


@pytest.mark.parametrize("caller", sorted(PROBE_EXPECTED))
def test_receiver_typing_probe_matrix(probe_builder, caller):
    target, kind = PROBE_EXPECTED[caller]
    calls = _calls(probe_builder, f"pkg.app.{caller}")
    assert target in calls, calls
    assert calls[target] == kind, calls
    _no_attribute_symbol_targets(calls)


@pytest.mark.parametrize("caller", PROBE_UNRESOLVED)
def test_receiver_typing_probe_out_of_scope_patterns(probe_builder, caller):
    calls = _calls(probe_builder, f"pkg.app.{caller}")
    assert POST not in calls
    _no_attribute_symbol_targets(calls)
