"""Facade re-exports on the method-call path (Operation Groundwire).

`requests.post(...)` is a call on receiver `requests` whose `__init__` only
re-exports `post` (`from .api import post`). The method-call path used to
build the phantom `requests.post`; it now follows the facade to the real
definition (`requests.api.post`), the same barrel lookup a full dotted
reference already got.
"""
from __future__ import annotations

import textwrap

from prism.cli import build_pipeline

API = "def post(url, data=None):\n    return url\n\n\ndef get(url):\n    return url\n"
CLIENT = "class Client:\n    @classmethod\n    def create(cls):\n        return cls()\n\n    def send(self):\n        return 1\n"


def _build(tmp_path, files: dict[str, str]):
    repo = tmp_path / "repo"
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    builder, _ = build_pipeline(str(repo))
    return builder


def _succ(builder, caller: str) -> dict[str, str]:
    return {t: d.get("relation", "CALLS") for _, t, d in builder.graph.out_edges(caller, data=True)}


FACADE = {
    "lib/__init__.py": "from .api import get, post\nfrom .client import Client\n",
    "lib/api.py": API,
    "lib/client.py": CLIENT,
    "app/__init__.py": "",
}


def test_module_facade_call_resolves_to_definition(tmp_path):
    b = _build(tmp_path, {**FACADE, "app/main.py": "import lib\n\n\ndef f():\n    return lib.post('u')\n"})
    assert _succ(b, "app.main.f") == {"lib.api.post": "CALLS"}
    assert "lib.post" not in b.graph


def test_aliased_module_facade_call(tmp_path):
    b = _build(tmp_path, {**FACADE, "app/main.py": "import lib as http\n\n\ndef f():\n    return http.get('u')\n"})
    assert _succ(b, "app.main.f") == {"lib.api.get": "CALLS"}


def test_wildcard_facade(tmp_path):
    b = _build(tmp_path, {
        "lib/__init__.py": "from .api import *\n",
        "lib/api.py": API,
        "app/__init__.py": "",
        "app/main.py": "import lib\n\n\ndef f():\n    return lib.post('u')\n",
    })
    assert _succ(b, "app.main.f") == {"lib.api.post": "CALLS"}


def test_chained_facades(tmp_path):
    b = _build(tmp_path, {
        "lib/__init__.py": "from .sub import post\n",
        "lib/sub/__init__.py": "from .impl import post\n",
        "lib/sub/impl.py": API,
        "app/__init__.py": "",
        "app/main.py": "import lib\n\n\ndef f():\n    return lib.post('u')\n",
    })
    assert _succ(b, "app.main.f") == {"lib.sub.impl.post": "CALLS"}


def test_reexported_class_constructor_and_classmethod(tmp_path):
    b = _build(tmp_path, {
        **FACADE,
        "app/main.py": "import lib\n\n\ndef f():\n    return lib.Client()\n\n\ndef g():\n    return lib.Client.create()\n",
    })
    assert _succ(b, "app.main.f") == {"lib.client.Client": "INSTANTIATES"}
    assert "lib.client.Client.create" in _succ(b, "app.main.g")


def test_direct_submodule_call_is_unchanged(tmp_path):
    b = _build(tmp_path, {**FACADE, "app/main.py": "import lib.api\n\n\ndef f():\n    return lib.api.post('u')\n"})
    assert _succ(b, "app.main.f") == {"lib.api.post": "CALLS"}


def test_name_the_facade_does_not_export_stays_unresolved(tmp_path):
    b = _build(tmp_path, {**FACADE, "app/main.py": "import lib\n\n\ndef f():\n    return lib.missing('u')\n"})
    succ = _succ(b, "app.main.f")
    assert not any(t.startswith("lib.api.") or t.startswith("lib.client.") for t in succ)


def test_local_shadowing_still_wins(tmp_path):
    b = _build(tmp_path, {**FACADE, "app/main.py": "import lib\n\n\ndef f(lib):\n    return lib.post('u')\n"})
    assert "lib.api.post" not in _succ(b, "app.main.f")


def test_requests_shaped_facade_with_chained_result(tmp_path):
    b = _build(tmp_path, {
        "requests/__init__.py": "from .api import delete, get, post, request\n",
        "requests/api.py": (
            "def request(method, url):\n    return method\n\n\n"
            "def get(url):\n    return request('get', url)\n\n\n"
            "def post(url):\n    return request('post', url)\n\n\n"
            "def delete(url):\n    return request('delete', url)\n"
        ),
        "app/__init__.py": "",
        "app/main.py": (
            "import requests\n\n\n"
            "def fetch():\n    return requests.get('u')\n\n\n"
            "def send():\n    return requests.request('POST', 'u')\n"
        ),
    })
    assert _succ(b, "app.main.fetch") == {"requests.api.get": "CALLS"}
    assert _succ(b, "app.main.send") == {"requests.api.request": "CALLS"}
    assert "requests.api.request" in _succ(b, "requests.api.get")
