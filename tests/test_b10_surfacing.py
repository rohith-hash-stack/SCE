"""B10 (`harness/experiments/r4/PHASE2_PLAN.md`): the reference graph's connections are shown to the agent.

`prism_change_context` (section 4, after the callers) and `prism_blast_radius` (after the transitive callers) list a
symbol's uses other than calls, each with its source, file and lines, labelled by kind ("imports it", "lists it in
`__all__`", "type-checks against it", ...). Inheritance edges the callers list leaves out are listed as "subclasses
it". Test code is left out where the tool leaves out test callers (the harness: `include_tests=False`).
"""
import os
import subprocess

import pytest

from tests.test_graph_references import FILES

TEST_FILE = {"tests/test_core.py": "from pkg.core import helper\n\n\ndef test_h():\n    assert helper(1) == 1\n"}


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    root = tmp_path_factory.mktemp("b10")
    work = str(root / "work")
    for p, text in {**FILES, **TEST_FILE}.items():
        os.makedirs(os.path.dirname(os.path.join(work, p)), exist_ok=True)
        open(os.path.join(work, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=work, check=True)
    wi = WorkingIndex(work, str(root / "shadow"))
    lay = ToolLayer(wi)
    yield lay
    lay.close()
    wi.close()


def _section4(text: str) -> str:
    return text[text.index("## 4."):text.index("## 5.")]


def test_change_context_lists_imports_exports_and_type_use(layer):
    s = _section4(layer.call("prism_change_context", symbol="pkg.core.BaseThing"))
    assert "Other uses (not calls)" in s
    assert "- `pkg` (module, pkg/__init__.py:1, 4): imports it; lists it in `__all__`" in s
    run = next(ln for ln in s.splitlines() if ln.startswith("- `pkg.core.Child.run`"))
    assert "type-checks against it (isinstance)" in run and "uses it as a type annotation" in run
    assert "reads an attribute of it" in run and "pkg/core.py:" in run


def test_subclass_is_listed_when_the_callers_leave_it_out(layer):
    s = _section4(layer.call("prism_change_context", symbol="pkg.core.BaseThing"))
    assert any(ln.startswith("- `pkg.core.Child` (pkg/core.py:") and "subclasses it" in ln for ln in s.splitlines())


def test_no_callers_message_when_only_other_uses_exist(layer):
    s = _section4(layer.call("prism_change_context", symbol="pkg.core.BaseThing"))
    assert "No callers in the index (an entry point" not in s
    assert "No calls to it in the index; its other uses are listed below." in s


def test_except_decorator_and_module_level_call(layer):
    assert "catches it (except)" in _section4(layer.call("prism_change_context", symbol="pkg.core.BoomError"))
    assert "uses it as a decorator" in _section4(layer.call("prism_change_context", symbol="pkg.core.deco"))
    s = _section4(layer.call("prism_change_context", symbol="pkg.core.helper"))
    mod = next(ln for ln in s.splitlines() if ln.startswith("- `pkg.core` (module"))
    assert "calls it (outside a function)" in mod and "uses it as a value" in mod


def test_delete05_class_body_use_under_a_conditional_definition(layer):
    s = _section4(layer.call("prism_change_context", symbol="pkg.compat.GenericAlias"))
    assert any(ln.startswith("- `pkg.compat.Holder` (pkg/compat.py:13)") for ln in s.splitlines())


def test_blast_radius_lists_other_uses(layer):
    out = layer.call("prism_blast_radius", symbol="pkg.core.helper")
    assert "Other uses of `pkg.core.helper` (not calls; 4)" in out
    assert "- `pkg` (module, pkg/__init__.py:1, 4): imports it; lists it in `__all__`" in out


def test_tests_are_left_out_as_test_callers_are(layer):
    # the harness asks with include_tests=False: the test module's import is not listed
    assert "tests.test_core" not in layer.call("prism_change_context", symbol="pkg.core.helper")
    assert "tests.test_core" not in layer.call("prism_blast_radius", symbol="pkg.core.helper")


def test_references_to_with_tests_and_skip(layer):
    from prism.graph.references import references_to
    b = layer.wi.builder
    rows = references_to(b, "pkg.core.helper", include_tests=True)
    test_row = next(r for r in rows if r["source"] == "tests.test_core")
    assert test_row["is_test"] and test_row["is_module"] and "import" in test_row["kinds"]
    assert rows[-1]["is_test"]                                             # tests listed last
    assert "pkg" not in {r["source"] for r in references_to(b, "pkg.core.helper", skip={"pkg"})}


def test_mcp_returns_carry_the_references(layer):
    from prism.mcp import server
    cc = server.prism_change_context(repo_path=layer.wi.shadow, symbol="pkg.core.BaseThing", include_tests=False)
    assert "pkg" in cc["references"] and cc["references_total"] >= 3
    br = server.prism_blast_radius(repo_path=layer.wi.shadow, seed_symbol="pkg.core.helper", include_tests=False)
    row = next(r for r in br["references"] if r["source"] == "pkg")
    assert row["file"] == "pkg/__init__.py" and row["lines"] == [1, 4] and row["kinds"] == ["import", "export"]
