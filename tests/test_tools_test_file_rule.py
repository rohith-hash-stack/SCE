"""The test-file rule fix (2026-11-21: `spec/` is a test directory for JS/TS/Ruby only; a Python `test.py` is a test
by its content) as the agent's tools apply it: `prism_search` labels hits `[test; ...]` and `prism_find_references`
marks test files `[test]` and lists them after production files."""
import os
import subprocess

import pytest

FILES = {
    "spec/__init__.py": "",
    "spec/models.py": "from pkg.core import helper\n\n\ndef build():\n    return helper(1)\n",      # production
    "pkg/__init__.py": "",
    "pkg/core.py": "def helper(x):\n    return x\n",
    "pkg/test.py": "from pkg.core import helper\n\n\ndef run_check():\n    return helper(2)\n",  # no tests: production
    "app/__init__.py": "",
    "app/test.py": "from pkg.core import helper\n\n\ndef test_helper():\n    assert helper(3) == 3\n",  # a test
    "web/spec/a.spec.js": "const { helper } = require('../h');\ntest('x', () => helper(1));\n",
    "web/h.js": "function helper(x) { return x; }\nmodule.exports = { helper };\n",
}


class _Env:
    def run_test(self, rel, ids=None):
        return {}


@pytest.fixture()
def layer(tmp_path):
    from harness.experiments.r3.shared_tools import SharedTools
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    work = str(tmp_path / "work")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(work, p)), exist_ok=True)
        open(os.path.join(work, p), "w").write(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", *cmd], cwd=work, check=True)
    wi = WorkingIndex(work, str(tmp_path / "shadow"))
    lay = ToolLayer(wi, shared=SharedTools(work, _Env(), []))
    yield lay
    lay.close()
    wi.close()


def _line(out, start):
    return next(ln for ln in out.splitlines() if ln.startswith(start))


def test_search_labels_by_the_rule(layer):
    out = layer.call("prism_search", pattern=r"helper\(")
    assert "[test" not in _line(out, "spec/models.py:5")           # Python under spec/: production
    assert "[test" not in _line(out, "pkg/test.py:5")              # a test.py without tests: production
    assert _line(out, "app/test.py:5").split(": ", 1)[1].startswith("[test;")
    assert _line(out, "web/spec/a.spec.js:2").split(": ", 1)[1].startswith("[test;")


def test_find_references_groups_by_the_rule(layer):
    out = layer.call("prism_find_references", symbol="pkg.core.helper").splitlines()
    assert "spec/models.py" in out and "pkg/test.py" in out and "app/test.py [test]" in out
    assert out.index("app/test.py [test]") > max(out.index("spec/models.py"), out.index("pkg/test.py"))
    assert "; 2 in test files" in out[0]                               # app/test.py: its import and its call
