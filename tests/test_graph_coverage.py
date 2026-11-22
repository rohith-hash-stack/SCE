"""The graph-coverage benchmark (`harness/experiments/graph_coverage/benchmark.py`) on a small fixture repository with
one example of each kind of use: every use found and classified, and PRISM's recorded/surfaced edges read correctly."""
import os
import subprocess

import pytest

from harness.experiments.graph_coverage import benchmark as B

FILES = {
    "pkg/__init__.py": "from .core import helper_fn\n\n__all__ = [\"helper_fn\"]\n",
    "pkg/core.py": (
        "def helper_fn(x):\n    return x\n\n\n"
        "class BaseThing:\n    pass\n\n\n"
        "def deco_fn(f):\n    return f\n\n\n"
        "class ChildThing(BaseThing):\n"
        "    alias = helper_fn\n\n"
        "    @deco_fn\n"
        "    def run(self, v: BaseThing) -> int:\n"
        "        y = helper_fn(v)\n"
        "        if isinstance(v, BaseThing):\n"
        "            pass\n"
        "        registry = [helper_fn]\n"
        "        getattr(self, \"helper_fn\")\n"
        "        try:\n            pass\n        except BoomError:\n            pass\n"
        "        return helper_fn\n\n\n"
        "class BoomError(Exception):\n    pass\n\n\n"
        "TOP = helper_fn(1)\n"
    ),
    "tests/test_core.py": "from pkg.core import helper_fn\n\n\ndef test_it():\n    assert helper_fn(1) == 1\n",
    "README.md": "Use helper_fn to help.\n",
}


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    root = str(tmp_path_factory.mktemp("gcov") / "r")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
        open(os.path.join(root, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    builder, _ = build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=False)
    return B.audit_tree(root, builder)


def _cell(rep, kind, location, form="bare"):
    return rep["cells"].get(f"{kind}|{location}|{form}", {"uses": 0, "recorded": 0, "surfaced": 0})


def test_every_kind_is_found(report):
    expected = {("import", "module level"): 1, ("__all__ entry", "module level"): 1,
                ("base class", "module level"): 1, ("assigned as a value", "class body"): 1,
                ("decorator", "class body"): 1, ("type annotation", "class body"): 1,
                ("call", "function body"): 1, ("isinstance/issubclass", "function body"): 1,
                ("stored in a container", "function body"): 1, ("getattr-style string", "function body"): 1,
                ("except clause", "function body"): 1, ("returned as a value", "function body"): 1,
                ("call", "module level"): 1, ("import", "test file"): 1, ("call", "test file"): 1}
    for (kind, loc), n in expected.items():
        assert _cell(report, kind, loc)["uses"] == n, (kind, loc, report["cells"].keys())


def test_prism_edges_are_read(report):
    call = _cell(report, "call", "function body")
    assert call["recorded"] == 1 and call["surfaced"] == 1          # ChildThing.run -> helper_fn: CALLS
    base = _cell(report, "base class", "module level")
    assert base["recorded"] == 1                                      # ChildThing -> BaseThing: EXTENDS
    # getattr(self, "helper_fn") in `run` is covered by `run`'s CALLS edge: PRISM connects definitions, not lines
    assert _cell(report, "getattr-style string", "function body")["recorded"] == 1
    # module-level uses: recorded in the reference graph (Phase B) and, since B10, surfaced by the caller views
    # (labelled other uses in prism_change_context and prism_blast_radius)
    for kind, loc in (("import", "module level"), ("__all__ entry", "module level"), ("call", "module level")):
        c = _cell(report, kind, loc)
        assert c["recorded"] == 1 and c["surfaced"] == 1, (kind, loc, c)
    assert "CALLS" in report["surfaced_relations"]
    assert report["non_python"]["files_by_type"].get("docs") == 1     # README.md mentions helper_fn


def test_check_reports_a_drop(report):
    import copy
    worse = copy.deepcopy(report)
    worse["label"] = report["label"] = "fixture"
    for c in worse["cells"].values():
        c["recorded"] = c["surfaced"] = 0
    assert any("call" in d and "recorded" in d for d in B.check([report], [worse]))
    assert B.check([report], [copy.deepcopy(report)]) == []
