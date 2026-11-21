"""Python annotation typing (Operation Groundwire, Phase 1 / Spec 2).

The shared `_python_annotation_type` helper unwraps what real code writes:

- dotted names and string forward references;
- `Optional`/`| None`/`Union[T, None]`/`Annotated` wrappers;
- multi-class unions, which link as `TENTATIVE_CALL`;
- containers, which bind as builtin receivers but keep their element type
  for `for` loops.

It types parameters, annotated locals (`x: T = ...`), `self.attr: T`,
class-level `attr: T`, and return annotations.

`ping` is defined on several imported classes on purpose. With no real
type binding, the bare-name fallback picks `Base.ping` (see the control
test), so an edge to exactly `HttpClient.ping` proves the annotation did
the work.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline

CLIENT_PY = textwrap.dedent(
    """
    class HttpClient:
        def ping(self, path):
            return path


    class OtherClient:
        def ping(self, path):
            return path

        def only_other(self):
            return 1


    class Base:
        def ping(self, path):
            return path


    class Sub(Base):
        def ping(self, path):
            return path


    class Unrelated:
        def ping(self, path):
            return path


    class Bag:
        def append(self, item):
            return item


    def make():
        return object()
    """
)

HEADER = (
    "import socket\n"
    "import typing\n"
    "from typing import Annotated, Callable, Dict, List, Optional, Sequence, Union\n"
    "from pkg import client\n"
    "from pkg.client import Bag, Base, HttpClient, OtherClient, Sub, Unrelated, make\n\n\n"
)

PING = "pkg.client.HttpClient.ping"
TENTATIVE = "TENTATIVE_CALL"


def _build(tmp_path, body: str):
    pkg = tmp_path / "repo" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "client.py").write_text(CLIENT_PY)
    (pkg / "app.py").write_text(HEADER + textwrap.dedent(body))
    builder, _ = build_pipeline(str(tmp_path / "repo"))
    return builder


def _calls(builder, caller: str) -> dict[str, str | None]:
    graph = builder.graph
    assert caller in graph, f"{caller} missing from graph"
    return {
        target: data.get("kind")
        for _, target, data in graph.out_edges(caller, data=True)
        if data.get("relation", "CALLS") == "CALLS"
    }


def _ambiguous(calls, name="ping"):
    return [t for t in calls if t.startswith("<ambiguous:") and f":{name}@" in t]


def test_untyped_receiver_does_not_reach_httpclient(tmp_path):
    """Control: the discriminator works - without an annotation the
    bare-name fallback never reaches `HttpClient.ping`."""
    calls = _calls(_build(tmp_path, "def f():\n    x = make()\n    x.ping('/e')\n"), "pkg.app.f")
    assert PING not in calls


# --------------------------------------------------------------------- #
# Wrapped single-class forms on an annotated local
# --------------------------------------------------------------------- #
SINGLE_CLASS_FORMS = [
    "HttpClient",
    "client.HttpClient",
    '"HttpClient"',
    "'client.HttpClient'",
    "Optional[HttpClient]",
    "typing.Optional[HttpClient]",
    "HttpClient | None",
    "None | HttpClient",
    "Union[HttpClient, None]",
    "typing.Union[None, HttpClient]",
    'Annotated[HttpClient, "meta"]',
    '"Optional[HttpClient]"',
    'Optional["HttpClient"]',
    "Optional[Annotated[HttpClient, 1]]",
]


@pytest.mark.parametrize("form", SINGLE_CLASS_FORMS)
def test_annotated_local_single_class_forms(tmp_path, form):
    calls = _calls(_build(tmp_path, f"def f():\n    x: {form} = make()\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get(PING, "missing") is None, calls
    assert "pkg.client.Base.ping" not in calls and not _ambiguous(calls)


@pytest.mark.parametrize("form", ["Optional[HttpClient]", '"HttpClient"', "HttpClient | None", "client.HttpClient"])
def test_annotated_parameter_wrapped_forms(tmp_path, form):
    calls = _calls(_build(tmp_path, f"def f(x: {form}):\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get(PING, "missing") is None, calls


def test_declaration_only_annotation_binds(tmp_path):
    calls = _calls(_build(tmp_path, "def f():\n    x: HttpClient\n    x = make()\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get(PING, "missing") is None, calls


# --------------------------------------------------------------------- #
# Multi-class unions: tentative, never guessed confidently
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("form", ["Union[HttpClient, OtherClient]", "HttpClient | OtherClient", "Optional[Union[HttpClient, OtherClient]]"])
def test_multi_class_union_links_tentatively(tmp_path, form):
    calls = _calls(_build(tmp_path, f"def f(x: {form}):\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get(PING) == TENTATIVE, calls


def test_union_picks_the_member_that_defines_the_method(tmp_path):
    calls = _calls(_build(tmp_path, "def f(x: Union[HttpClient, OtherClient]):\n    x.only_other()\n"), "pkg.app.f")
    assert calls.get("pkg.client.OtherClient.only_other") == TENTATIVE, calls
    assert "pkg.client.HttpClient.only_other" not in calls


@pytest.mark.parametrize("form", ["Union[HttpClient, socket.socket]", "Callable[..., HttpClient]", "HttpClient | int"])
def test_partially_unknown_or_callable_annotation_does_not_bind(tmp_path, form):
    calls = _calls(_build(tmp_path, f"def f(x: {form}):\n    x.ping('/e')\n"), "pkg.app.f")
    assert PING not in calls or calls[PING] == TENTATIVE  # never a confident guess
    assert calls.get(PING, TENTATIVE) == TENTATIVE


# --------------------------------------------------------------------- #
# Annotation vs right-hand side
# --------------------------------------------------------------------- #
def test_subclass_constructor_refines_annotation(tmp_path):
    calls = _calls(_build(tmp_path, "def f():\n    x: Base = Sub()\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get("pkg.client.Sub.ping", "missing") is None, calls


def test_unrelated_constructor_does_not_override_annotation(tmp_path):
    calls = _calls(_build(tmp_path, "def f():\n    x: Base = Unrelated()\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get("pkg.client.Base.ping", "missing") is None, calls
    assert "pkg.client.Unrelated.ping" not in calls


def test_unresolvable_annotation_keeps_constructor_binding(tmp_path):
    calls = _calls(_build(tmp_path, "def f():\n    x: socket.socket = HttpClient()\n    x.ping('/e')\n"), "pkg.app.f")
    assert calls.get(PING, "missing") is None, calls


# --------------------------------------------------------------------- #
# Containers: builtin receiver, element type kept for iteration
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("form", ["list[HttpClient]", "List[HttpClient]", "Sequence[HttpClient]", "Optional[list[HttpClient]]", "tuple[HttpClient, ...]"])
def test_sequence_annotation_is_builtin_receiver_with_element_type(tmp_path, form):
    body = (
        f"def f(xs: {form}):\n"
        "    xs.append(1)\n"
        "    for c in xs:\n"
        "        c.ping('/e')\n"
    )
    calls = _calls(_build(tmp_path, body), "pkg.app.f")
    assert "pkg.client.Bag.append" not in calls and not _ambiguous(calls, "append")
    assert calls.get(PING, "missing") is None, calls


def test_annotated_local_container_iteration(tmp_path):
    body = "def f():\n    xs: list[HttpClient] = []\n    for c in xs:\n        c.ping('/e')\n"
    assert _calls(_build(tmp_path, body), "pkg.app.f").get(PING, "missing") is None


def test_dict_iteration_yields_key_type(tmp_path):
    body = "def f(d: Dict[HttpClient, OtherClient]):\n    for k in d:\n        k.ping('/e')\n"
    calls = _calls(_build(tmp_path, body), "pkg.app.f")
    assert calls.get(PING, "missing") is None, calls
    assert "pkg.client.OtherClient.ping" not in calls


# --------------------------------------------------------------------- #
# Class attributes
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "decl",
    [
        "    client: Optional[HttpClient] = None\n",
        "    client: HttpClient\n",
        '    client: "HttpClient"\n',
    ],
    ids=["class_optional", "class_bare", "class_string"],
)
def test_class_level_annotation_types_self_attribute(tmp_path, decl):
    body = "class Service:\n" + decl + "\n    def run(self):\n        self.client.ping('/e')\n"
    calls = _calls(_build(tmp_path, body), "pkg.app.Service.run")
    assert calls.get(PING, "missing") is None, calls


@pytest.mark.parametrize("decl", ["self.client: HttpClient = make()", "self.client: Optional[HttpClient] = None"])
def test_self_attribute_annotation(tmp_path, decl):
    body = (
        "class Service:\n"
        "    def __init__(self):\n"
        f"        {decl}\n\n"
        "    def run(self):\n"
        "        self.client.ping('/e')\n"
    )
    calls = _calls(_build(tmp_path, body), "pkg.app.Service.run")
    assert calls.get(PING, "missing") is None, calls


def test_self_attribute_container_iteration(tmp_path):
    body = (
        "class Service:\n"
        "    clients: list[HttpClient]\n\n"
        "    def run(self):\n"
        "        self.clients.append(1)\n"
        "        for c in self.clients:\n"
        "            c.ping('/e')\n"
    )
    calls = _calls(_build(tmp_path, body), "pkg.app.Service.run")
    assert calls.get(PING, "missing") is None, calls
    assert "pkg.client.Bag.append" not in calls


def test_class_union_attribute_is_tentative(tmp_path):
    body = "class Service:\n    client: Union[HttpClient, OtherClient]\n\n    def run(self):\n        self.client.ping('/e')\n"
    assert _calls(_build(tmp_path, body), "pkg.app.Service.run").get(PING) == TENTATIVE


# --------------------------------------------------------------------- #
# Return annotations
# --------------------------------------------------------------------- #
def test_optional_return_annotation(tmp_path):
    body = (
        "def make_opt() -> Optional[HttpClient]:\n"
        "    return make()\n\n\n"
        "def f():\n"
        "    make_opt().ping('/e')\n\n\n"
        "def g():\n"
        "    c = make_opt()\n"
        "    c.ping('/e')\n"
    )
    builder = _build(tmp_path, body)
    assert _calls(builder, "pkg.app.f").get(PING) == TENTATIVE
    assert _calls(builder, "pkg.app.g").get(PING) == TENTATIVE


def test_string_return_annotation(tmp_path):
    body = "def make_s() -> 'HttpClient':\n    return make()\n\n\ndef f():\n    make_s().ping('/e')\n"
    assert _calls(_build(tmp_path, body), "pkg.app.f").get(PING) == TENTATIVE
