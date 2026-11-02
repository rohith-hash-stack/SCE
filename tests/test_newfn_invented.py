"""newfn invented calls, scorer v2 (harness/experiments/suite/invented.py; reports/r1/REPORT.md, post-hoc §5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.experiments.suite.fences import strip_outer_fence
from harness.experiments.suite.invented import _supplement, class_attributes, invented, scan_tree

ROOT = Path(__file__).resolve().parents[1]


def _task(task_id: str) -> dict:
    repo = task_id.split("-")[0]
    return next(t for t in json.loads((ROOT / "harness/experiments/suite/repos" / repo / "newfn.json").read_text())["tasks"]
                if t["id"] == task_id)


def _answer(task_id: str, version: str = "PRISM") -> str:
    repo = task_id.split("-")[0]
    raw = json.loads((ROOT / "reports/suite" / repo / "newfn/answers.json").read_text())[f"{task_id}/{version}"]["raw"]
    return strip_outer_fence(json.loads(strip_outer_fence(raw))["code"])


# The four confirmed real-code cases from the R1 investigation, and the one confirmed invention.
@pytest.mark.parametrize("task_id, name, why", [
    ("aiohttp-newfn-005", "a", "a word in a comment: 'a (`user`, ...) tuple'"),
    ("aiohttp-newfn-019", "websocket_mask", "a module-level alias in aiohttp/_websocket/helpers.py"),
    ("tornado-newfn-020", "_socket_action", "an instance attribute holding a callable (Python 2 code)"),
    ("werkzeug-newfn-002", "get", "inherited from dict (class Authorization(dict))"),
])
def test_real_code_is_not_flagged(task_id, name, why):
    _supplement.cache_clear()
    got = invented(_task(task_id), _answer(task_id))
    assert name not in got["invented"] and name not in got["unresolved"], why


def test_parse_ansi_is_still_an_invention():
    """rich has no parse_ansi; the answer imports it from `.ansi`, the repository's own module."""
    _supplement.cache_clear()
    assert "parse_ansi" in invented(_task("rich-newfn-008"), _answer("rich-newfn-008"))["invented"]


# Each exclusion rule on its own.
PY_TASK = {"file": "pkg/m.py", "repo_leaves": ["helper"], "class_methods": ["own"], "class_qname": "pkg.m.C",
           "file_imports": ["os"]}
NAMES = {"module": ["alias"], "imported": ["cast"], "attributes": ["_cb"], "inherited": ["get"], "bases_unresolved": False}


def _inv(code: str, task=PY_TASK, names=NAMES) -> list[str]:
    return invented(task, code, names)["invented"]


def test_python_rules():
    assert _inv("def f(self, func, *rest, **kw):\n    return func(1) + rest[0](kw)\n") == []          # parameters
    assert _inv("def f(self):\n    g = make()\n    return g()\n") == ["make"]                          # local, then real
    assert _inv("def f(self):\n    for h in hs:\n        h()\n    with open(p) as fh:\n        fh()\n") == []
    assert _inv("def f(self):\n    return ', '.join(x.strip() for x in xs)\n") == []                  # methods on expressions
    assert _inv("def f(self):\n    # call nothing(here)\n    s = 'nope(1)'\n    return s\n") == []    # comments, strings
    assert _inv("def f(self):\n    return alias(cast(int, helper()))\n") == []                       # repo names
    assert _inv("def f(self):\n    return self.own() + self._cb() + self.get('k')\n") == []          # members
    assert _inv("def f(self):\n    return self.made_up()\n") == ["made_up"]
    assert _inv("def f(self):\n    from .sub import ghost\n    return ghost()\n") == ["ghost"]          # internal import
    assert _inv("def f(self):\n    from json import loads\n    return loads(s)\n") == []               # external import
    assert invented(PY_TASK, "def f(self):\n    return self.x()\n", {**NAMES, "bases_unresolved": True}) == \
        {"invented": [], "unresolved": ["x"], "read_as": "ast"}


def test_python2_and_broken_code_fall_back_to_tokens():
    got = invented(PY_TASK, "def f(self):\n    try:\n        made_up()\n    except E, e:\n        e()\n", NAMES)
    assert got["read_as"] == "pattern" and got["invented"] == ["made_up"]


def test_javascript_rules():
    task = {"file": "lib/m.js", "repo_leaves": ["helper"], "class_methods": [], "class_qname": None, "file_imports": []}
    code = ("function go(opts, done) {\n  // never(called)\n  const fn = opts.fn;\n  const s = 'no(1)';\n"
            "  [1].map((x) => x.y());\n  fn(); done(helper()); return made();\n}\n")
    assert invented(task, code, {})["invented"] == ["made"]


def test_repository_scan(tmp_path):
    (tmp_path / "a.py").write_text("import os as o\nif X:\n    alias = f\nclass Base(dict):\n    def m(self): self.z = 1\n"
                                   "class C(Base, ext.Thing):\n    k = 1\n")
    acc = scan_tree(str(tmp_path))
    assert {"alias", "X"} - acc["module"] == {"X"} and "o" in acc["imported"] and "z" in acc["attributes"]
    members, unresolved = class_attributes(acc, "C")
    assert {"k", "m", "get", "items"} <= members and unresolved  # `ext.Thing` is from another package


def test_fenced_and_python2_method_calls_are_not_bare_calls():
    code = "```python\ndef f(self):\n    os.chmod(p, 0755)\n    os . unlink(p)\n    return made_up()\n```"
    got = invented(PY_TASK, code, NAMES)
    assert got["invented"] == ["made_up"] and got["read_as"] == "pattern"
    assert invented(PY_TASK, "```python\ndef f(self):\n    return xs.pop()\n```", NAMES) == \
        {"invented": [], "unresolved": [], "read_as": "ast"}
