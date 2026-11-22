"""Phase 2 requirements not built yet (`harness/experiments/r4/PHASE2_PLAN.md`), each a strict expected failure: it
fails today, and an unexpected pass fails the suite, so the marker comes off the moment the requirement is met.

- B6: dynamic string references (`getattr(obj, "name")`), marked low-certainty;
- B7: references from test code, kept apart from production references;
- B8: across files and languages (a template placeholder filled from Python);
- JS/TS: the same reference kinds as Python (imports, type use, `instanceof`, values);
- C5: `prism_ask` says plainly when no symbol has the name asked about;
- C6: `prism_read_code` accepts a module name and a module-level variable;
- C7 (built, Phase 3): `prism_change_context` and `prism_read_code` show the block enclosing a conditionally defined
  function (delete-05).
"""
import os
import subprocess

import pytest

PY = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "import sys\n\n"
        "LIMIT = 3\n\n"
        "if sys.version_info >= (3, 9):\n    MODE = 'new'\nelse:\n\n"
        "    def legacy_mode():\n        return 'old'\n\n\n"
        "def helper(x):\n    return x\n\n\n"
        "def dynamic(obj):\n    return getattr(obj, \"helper\")\n\n\n"
        "def render(tpl):\n    return tpl.format(plain_traceback=helper(1))\n"
    ),
    "pkg/templates/page.html": "<pre>{plain_traceback}</pre>\n",
    "tests/test_core.py": "from pkg.core import helper\n\n\ndef test_helper():\n    assert helper(1) == 1\n",
}
JS = {
    "package.json": '{"name": "fx", "version": "1.0.0"}\n',
    "src/core.ts": (
        "export class Shape {\n  area(): number { return 0; }\n}\n\n"
        "export function helper(x: number): number {\n  return x;\n}\n"
    ),
    "src/use.ts": (
        "import { helper, Shape } from './core';\n\n"
        "export function check(s: Shape): boolean {\n  return s instanceof Shape;\n}\n\n"
        "export const table = [helper];\n"
    ),
}


def _repo(root, files):
    for p, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)) or root, exist_ok=True)
        open(os.path.join(root, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=root, check=True)


@pytest.fixture(scope="module")
def py_builder(tmp_path_factory):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    root = str(tmp_path_factory.mktemp("p2py") / "r")
    _repo(root, PY)
    return build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=False)[0]


@pytest.fixture(scope="module")
def js_builder(tmp_path_factory):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    root = str(tmp_path_factory.mktemp("p2js") / "r")
    _repo(root, JS)
    return build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=False)[0]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    root = tmp_path_factory.mktemp("p2tools")
    work = str(root / "work")
    os.makedirs(work)
    _repo(work, PY)
    wi = WorkingIndex(work, str(root / "shadow"))
    lay = ToolLayer(wi)
    yield lay
    lay.close()
    wi.close()


def kinds(b, src, dst) -> set:
    g = getattr(b, "reference_graph", None)
    return set(g.edges[src, dst].get("ref_kinds", ())) if g is not None and g.has_edge(src, dst) else set()


# ---------------------------------------------------------------------------------------------- graph
@pytest.mark.xfail(strict=True, reason="B6: dynamic string references")
def test_b6_getattr_string_reference(py_builder):
    e = py_builder.reference_graph.edges["pkg.core.dynamic", "pkg.core.helper"]
    assert "string" in e["ref_kinds"] and e.get("certainty") == "low"


@pytest.mark.xfail(strict=True, reason="B7: test references kept apart from production ones")
def test_b7_test_reference_is_labelled(py_builder):
    e = py_builder.reference_graph.edges["tests.test_core", "pkg.core.helper"]
    assert e.get("from_test") is True and "import" in e["ref_kinds"]


@pytest.mark.xfail(strict=True, reason="B8: template placeholders filled from Python")
def test_b8_template_placeholder(py_builder):
    assert "template" in kinds(py_builder, "pkg.core.render", "pkg/templates/page.html")


@pytest.mark.xfail(strict=True, reason="JS/TS references")
def test_js_ts_import_type_and_instanceof(js_builder):
    assert "import" in kinds(js_builder, "src.use", "src.core.helper")
    assert "value" in kinds(js_builder, "src.use", "src.core.helper")            # table = [helper]
    assert {"annotation", "isinstance"} <= kinds(js_builder, "src.use.check", "src.core.Shape")


# ---------------------------------------------------------------------------------------------- tools
@pytest.mark.xfail(strict=True, reason="C5: prism_ask says when no symbol has the name")
def test_c5_ask_about_a_missing_name(layer):
    out = layer.call("prism_ask", question="where is composeMiddleware defined")
    assert out.lstrip().lower().startswith("no symbol named") and "composeMiddleware" in out


@pytest.mark.xfail(strict=True, reason="C6: prism_read_code takes module names and module-level variables")
def test_c6_read_module_and_variable(layer):
    whole = layer.call("prism_read_code", symbol="pkg.core")
    assert whole.startswith("pkg/core.py (lines 1-")
    var = layer.call("prism_read_code", symbol="pkg.core.LIMIT")
    assert "3\tLIMIT = 3" in var


def test_c7_change_context_shows_the_enclosing_block(layer):
    out = layer.call("prism_change_context", symbol="pkg.core.legacy_mode")
    assert "if sys.version_info >= (3, 9):" in out and "else:" in out
    assert "inside the `else:` branch of the if/else block" in out and "only statement in that branch" in out


def test_c7_read_code_shows_the_enclosing_block(layer):
    # delete-05: `prism_read_code` gave the two `def` lines; deleting them left `else:` empty
    out = layer.call("prism_read_code", symbol="pkg.core.legacy_mode")
    assert "\tif sys.version_info >= (3, 9):" in out and "\telse:" in out and "\t    def legacy_mode():" in out
    assert "with its enclosing block" in out and "only statement in that branch" in out
    plain = layer.call("prism_read_code", symbol="pkg.core.helper")          # not nested: unchanged
    assert "enclosing block" not in plain


def test_tools_work_on_the_fixture(layer):
    # the fixture itself is sound: today's tools answer for the function they know
    assert layer.call("prism_read_code", symbol="pkg.core.helper").startswith("pkg/core.py (lines ")
