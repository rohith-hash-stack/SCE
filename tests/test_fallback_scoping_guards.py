"""Scoping guards on the receiver-type-unknown fallbacks (Operation
Groundwire, Phase 1 / Spec 3, Part B).

`_fallback_candidate_allowed` filters candidates in every bare-name
fallback: G44 unique link, family root, scored polysemy,
`_resolve_by_attribute` and `_resolve_by_receiver_name`. It rejects a
candidate:

- in another language family (Python never binds to vendored JS);
- in test code, unless the caller is test code too;
- in vendored/bundled code, unless the caller is vendored too.

Builtin-named methods with an unknown receiver never take a fallback link
at any candidate count; they get an `UnresolvedPolymorphic` sentinel.
Scored polysemy also needs the candidate in the caller's own top-level
package, or its module imported directly - importing only a parent package
(`import tornado`) no longer exposes every internal module's methods.
"""
from __future__ import annotations

import textwrap

from prism.cli import build_pipeline


def _build(tmp_path, files: dict[str, str]):
    repo = tmp_path / "repo"
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    builder, _ = build_pipeline(str(repo))
    return builder


def _calls(builder, caller: str) -> dict[str, str | None]:
    assert caller in builder.graph, f"{caller} missing from graph"
    return {
        t: d.get("kind")
        for _, t, d in builder.graph.out_edges(caller, data=True)
        if d.get("relation", "CALLS") == "CALLS"
    }


def _sentinels(calls, name):
    return [t for t in calls if t.startswith("<ambiguous:") and f":{name}@" in t]


INIT = {"pkg/__init__.py": ""}


# --------------------------------------------------------------------- #
# 1. Language family
# --------------------------------------------------------------------- #
def test_python_never_binds_to_vendored_js_split(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(s):\n    return s.split(',')\n",
        "pkg/static/vendor.js": "function split(a, b) {\n  return a;\n}\n",
    })
    assert not any(t.endswith(".split") and not t.startswith("<") for t in _calls(builder, "pkg.app.f"))


def test_python_never_binds_to_js_unique_method(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(x):\n    return x.frobnicate(1)\n",
        "pkg/web/app.js": "class Widget {\n  frobnicate(a) {\n    return a;\n  }\n}\n",
    })
    assert _calls(builder, "pkg.app.f") == {}


def test_js_caller_may_bind_to_ts_candidate(tmp_path):
    builder = _build(tmp_path, {
        "web/main.js": "function f(x) {\n  return x.frobnicate(1);\n}\n",
        "web/widget.ts": "export class Widget {\n  frobnicate(a: number) {\n    return a;\n  }\n}\n",
    })
    assert _calls(builder, "web.main.f") == {"web.widget.Widget.frobnicate": "TENTATIVE_CALL"}


# --------------------------------------------------------------------- #
# 2. Builtin-named methods: sentinel at any candidate count
# --------------------------------------------------------------------- #
def test_builtin_named_unique_candidate_in_tests_is_not_linked(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(w):\n    return w.capitalize()\n",
        "pkg/tests/__init__.py": "",
        "pkg/tests/test_x.py": "class GeneratorCapClient:\n    def capitalize(self):\n        return 1\n",
    })
    calls = _calls(builder, "pkg.app.f")
    assert "pkg.tests.test_x.GeneratorCapClient.capitalize" not in calls


def test_builtin_named_unique_production_candidate_gets_sentinel(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(w):\n    return w.capitalize()\n",
        "pkg/text.py": "class Title:\n    def capitalize(self):\n        return 1\n",
    })
    calls = _calls(builder, "pkg.app.f")
    assert "pkg.text.Title.capitalize" not in calls
    assert _sentinels(calls, "capitalize")
    sentinel = builder.graph.nodes[_sentinels(calls, "capitalize")[0]]
    assert sentinel["candidates"] == ["pkg.text.Title.capitalize"]


def test_own_receiver_builtin_named_unique_method_still_links(tmp_path):
    """`this.set(...)` on an untyped prototype object (the Express `app`
    shape) is the object's own method, not a possible builtin `Map.set`."""
    builder = _build(tmp_path, {"lib/app.js": (
        "var app = exports = module.exports = {};\n"
        "app.configure = function configure() {\n  this.set('etag', 'weak');\n};\n"
        "app.set = function set(setting, val) {\n  return val;\n};\n"
    )})
    assert _calls(builder, "lib.app.configure") == {"lib.app.set": "TENTATIVE_CALL"}


def test_other_receiver_builtin_named_unique_method_gets_sentinel(tmp_path):
    builder = _build(tmp_path, {"lib/app.js": (
        "var app = exports = module.exports = {};\n"
        "app.configure = function configure(opts) {\n  opts.set('etag', 'weak');\n};\n"
        "app.set = function set(setting, val) {\n  return val;\n};\n"
    )})
    calls = _calls(builder, "lib.app.configure")
    assert "lib.app.set" not in calls and _sentinels(calls, "set")


# --------------------------------------------------------------------- #
# 3/4. Test partition
# --------------------------------------------------------------------- #
def test_non_builtin_unique_production_candidate_keeps_g44_link(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(x):\n    return x.frobnicate(1)\n",
        "pkg/core.py": "class Engine:\n    def frobnicate(self, a):\n        return a\n",
    })
    assert _calls(builder, "pkg.app.f") == {"pkg.core.Engine.frobnicate": "TENTATIVE_CALL"}


def test_production_caller_never_binds_into_test_code(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(x):\n    return x.frobnicate(1)\n",
        "pkg/tests/__init__.py": "",
        "pkg/tests/helpers.py": "class FakeEngine:\n    def frobnicate(self, a):\n        return a\n",
    })
    assert _calls(builder, "pkg.app.f") == {}


def test_test_caller_may_bind_into_test_code(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/tests/__init__.py": "",
        "pkg/tests/helpers.py": "class FakeEngine:\n    def frobnicate(self, a):\n        return a\n",
        "pkg/tests/test_app.py": "def check(x):\n    return x.frobnicate(1)\n",
    })
    assert _calls(builder, "pkg.tests.test_app.check") == {"pkg.tests.helpers.FakeEngine.frobnicate": "TENTATIVE_CALL"}


def test_test_file_name_patterns_are_test_code(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(x):\n    return x.frobnicate(1)\n",
        "pkg/engine_test.py": "class FakeEngine:\n    def frobnicate(self, a):\n        return a\n",
    })
    assert _calls(builder, "pkg.app.f") == {}


def test_repo_under_a_test_named_directory_is_not_test_code(tmp_path):
    """Partitions use repo-relative paths: a checkout living under some
    `tests/` directory is still production code."""
    builder = _build(tmp_path / "tests", {
        **INIT,
        "pkg/app.py": "def f(x):\n    return x.frobnicate(1)\n",
        "pkg/core.py": "class Engine:\n    def frobnicate(self, a):\n        return a\n",
    })
    assert _calls(builder, "pkg.app.f") == {"pkg.core.Engine.frobnicate": "TENTATIVE_CALL"}


# --------------------------------------------------------------------- #
# Vendored / bundled partition
# --------------------------------------------------------------------- #
def test_first_party_js_never_binds_into_minified_bundle(tmp_path):
    builder = _build(tmp_path, {
        "web/main.js": "function f(x) {\n  return x.frobnicate(1);\n}\n",
        "web/lib/xregexp.min.js": "function frobnicate(a) {\n  return a;\n}\n",
    })
    assert _calls(builder, "web.main.f") == {}


def test_first_party_python_never_binds_into_vendor_directory(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/app.py": "def f(x):\n    return x.frobnicate(1)\n",
        "pkg/vendor/__init__.py": "",
        "pkg/vendor/lib.py": "class Engine:\n    def frobnicate(self, a):\n        return a\n",
    })
    assert _calls(builder, "pkg.app.f") == {}


def test_vendored_caller_may_bind_within_vendored_code(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/vendor/__init__.py": "",
        "pkg/vendor/lib.py": "class Engine:\n    def frobnicate(self, a):\n        return a\n",
        "pkg/vendor/use.py": "def f(x):\n    return x.frobnicate(1)\n",
    })
    assert _calls(builder, "pkg.vendor.use.f") == {"pkg.vendor.lib.Engine.frobnicate": "TENTATIVE_CALL"}


# --------------------------------------------------------------------- #
# 5/6. Scored polysemy: package scope or a direct import
# --------------------------------------------------------------------- #
DEP = {
    "app/__init__.py": "",
    "dep/__init__.py": "",
    "dep/internal/__init__.py": "",
    "dep/internal/stream.py": "class Stream:\n    def process(self, a):\n        return a\n",
    "other/__init__.py": "",
    "other/mod.py": "class Worker:\n    def process(self, a):\n        return a\n",
}


def test_parent_package_import_does_not_expose_internal_methods(tmp_path):
    builder = _build(tmp_path, {**DEP, "app/main.py": "import dep\n\n\ndef f(x):\n    return x.process(1)\n"})
    calls = _calls(builder, "app.main.f")
    assert "dep.internal.stream.Stream.process" not in calls
    assert _sentinels(calls, "process")


def test_direct_module_import_still_scores(tmp_path):
    builder = _build(tmp_path, {
        **DEP,
        "app/main.py": "from dep.internal import stream\n\n\ndef f(x):\n    return x.process(1)\n",
    })
    assert _calls(builder, "app.main.f") == {"dep.internal.stream.Stream.process": None}


def test_imported_class_still_scores(tmp_path):
    builder = _build(tmp_path, {
        **DEP,
        "app/main.py": "from dep.internal.stream import Stream\n\n\ndef f(x):\n    return x.process(1)\n",
    })
    assert _calls(builder, "app.main.f") == {"dep.internal.stream.Stream.process": None}


def test_unimported_candidates_in_other_packages_get_sentinel(tmp_path):
    builder = _build(tmp_path, {**DEP, "app/main.py": "def f(x):\n    return x.process(1)\n"})
    calls = _calls(builder, "app.main.f")
    assert not any(not t.startswith("<") for t in calls) and _sentinels(calls, "process")


# --------------------------------------------------------------------- #
# 7. Attribute-index and receiver-name fallbacks honour the partitions
# --------------------------------------------------------------------- #
def test_attribute_index_ignores_test_only_assignments(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/client.py": "class HttpClient:\n    def frobnicate(self, a):\n        return a\n",
        "pkg/tests/__init__.py": "",
        "pkg/tests/test_svc.py": (
            "from pkg.client import HttpClient\n\n\n"
            "class Holder:\n    def setup(self):\n        self.client = HttpClient()\n"
        ),
        "pkg/svc.py": "class Service:\n    def run(self, owner):\n        return owner.client.frobnicate(1)\n",
        "pkg/other.py": "class Other:\n    def frobnicate(self, a):\n        return a\n",
    })
    calls = _calls(builder, "pkg.svc.Service.run")
    assert "pkg.client.HttpClient.frobnicate" not in calls


def test_attribute_index_uses_production_assignments(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/client.py": "class HttpClient:\n    def frobnicate(self, a):\n        return a\n",
        "pkg/holder.py": (
            "from pkg.client import HttpClient\n\n\n"
            "class Holder:\n    def setup(self):\n        self.client = HttpClient()\n"
        ),
        "pkg/svc.py": "class Service:\n    def run(self, owner):\n        return owner.client.frobnicate(1)\n",
        "pkg/other.py": "class Other:\n    def frobnicate(self, a):\n        return a\n",
    })
    assert _calls(builder, "pkg.svc.Service.run").get("pkg.client.HttpClient.frobnicate") == "TENTATIVE_CALL"


def test_receiver_name_convention_ignores_test_classes(tmp_path):
    builder = _build(tmp_path, {
        **INIT,
        "pkg/tests/__init__.py": "",
        "pkg/tests/fakes.py": "class Engine:\n    def frobnicate(self, a):\n        return a\n",
        "pkg/real.py": "class Other:\n    def frobnicate(self, a):\n        return a\n",
        "pkg/app.py": "def f(engine):\n    return engine.frobnicate(1)\n",
    })
    assert "pkg.tests.fakes.Engine.frobnicate" not in _calls(builder, "pkg.app.f")


# --------------------------------------------------------------------- #
# 9. Go / Java unaffected (no bare-name fallback language family change)
# --------------------------------------------------------------------- #
def test_java_same_package_sibling_still_resolves(tmp_path):
    builder = _build(tmp_path, {
        "src/main/java/com/ex/Service.java": (
            "package com.ex;\n\npublic class Service {\n"
            "    public void run() {\n        Validator v = new Validator();\n        v.check(1);\n    }\n}\n"
        ),
        "src/main/java/com/ex/Validator.java": (
            "package com.ex;\n\npublic class Validator {\n    public boolean check(int a) {\n        return a > 0;\n    }\n}\n"
        ),
    })
    assert builder.graph.has_edge("com.ex.Service.run", "com.ex.Validator.check")
