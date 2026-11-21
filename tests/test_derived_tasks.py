"""Phase E's derived tasks (`harness/experiments/r4/derive_tasks.py`, `derived_checks.py`): the gold changes made from
the start tree pass the static check, the start tree does not, and the checks see what a run could leave behind
(an alias under the old name, a positional call, an import from the old module).
"""
import os
import subprocess
import sys

import pytest

from harness.experiments.r4 import derive_tasks as D
from harness.experiments.r4 import derived_checks as DC

FILES = {
    "pkg/__init__.py": "from .core import get_value, Client\nfrom .util import slugify\n\n__all__ = [\"get_value\", \"Client\", \"slugify\"]\n",
    "pkg/core.py": (
        "import re\n\n\n"
        "def get_value(key, default):\n    return {\"a\": 1}.get(key, default)\n\n\n"
        "class Client:\n"
        "    def send(self, msg, timeout):\n        return (msg, timeout)\n\n"
        "    def ping(self):\n        return self.send(\"ping\", 1)\n"
    ),
    "pkg/util.py": "import re\n\n\ndef slugify(text):\n    return re.sub(r\"\\W+\", \"-\", text).strip(\"-\").lower()\n\n\n"
                   "def title_slug(text):\n    return slugify(text.title())\n",
    "pkg/api.py": ("from pkg.core import Client, get_value\nfrom pkg.util import slugify\n\n\n"
                   "def run(c: Client):\n    return c.send(get_value(\"a\", 0), 5), slugify(\"A b\")\n"),
    "pkg/cli.py": "from .util import slugify as make_slug\nfrom .core import Client\n\n\ndef main():\n"
                  "    return make_slug(\"X Y\"), Client().send(\"m\", 2)\n",
    "tests/test_pkg.py": ("from pkg import get_value, slugify\nfrom pkg.core import Client\n\n\n"
                          "def test_all():\n    assert get_value(\"a\", 0) == 1 and slugify(\"A b\") == \"a-b\"\n"
                          "    assert Client().send(\"x\", 3) == (\"x\", 3)\n"),
}


@pytest.fixture()
def repo(tmp_path):
    for p, t in FILES.items():
        os.makedirs(os.path.dirname(tmp_path / p), exist_ok=True)
        (tmp_path / p).write_text(t)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return str(tmp_path)


def _cand(qname, file, kind, deps, tests=("tests/test_pkg.py",), **kw):
    return {"qname": qname, "file": file, "kind": kind, "leaf": qname.rsplit(".", 1)[-1], "dependents": list(deps),
            "test_dependents": list(tests), "lines": [1, 1], **kw}


def _apply(work, changed):
    for f, t in changed.items():
        D._write(work, f, t)


def _pytest(work):
    return subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=work,
                          capture_output=True, text=True, env={**os.environ, "PYTHONPATH": work})


def test_rename(repo):
    c = _cand("pkg.core.get_value", "pkg/core.py", "function", ["pkg/__init__.py", "pkg/api.py", "tests/test_pkg.py"])
    new = D.new_name("get_value", lambda n: False)
    assert new == "fetch_value"
    derived = {"kind": "rename", "file": c["file"], "qualname": "get_value", "old": "get_value", "new": new}
    assert not DC.check(derived, repo)["ok"]
    _apply(repo, D.gold_rename(repo, c, new))
    derived["baseline"] = DC.measure(derived, repo)["counts"]
    assert DC.check(derived, repo)["ok"] and _pytest(repo).returncode == 0
    with open(os.path.join(repo, "pkg/core.py"), "a") as fh:            # an alias left under the old name
        fh.write("\nget_value = fetch_value\n")
    r = DC.check(derived, repo)
    assert not r["ok"] and r["remaining"] == 1 and "still used in 1 place" in r["problems"][-1]


def test_signature(repo):
    c = _cand("pkg.core.Client.send", "pkg/core.py", "method", ["pkg/api.py", "pkg/cli.py", "tests/test_pkg.py"])
    changed, details = D.gold_signature(repo, c)
    assert details == {"param": "timeout", "max_positional": 1, "required": True}
    derived = {"kind": "signature", "file": c["file"], "qualname": "Client.send", **details}
    assert not DC.check(derived, repo)["ok"]
    _apply(repo, changed)
    assert "def send(self, msg, *, timeout):" in open(os.path.join(repo, "pkg/core.py")).read()
    assert 'self.send("ping", timeout=1)' in open(os.path.join(repo, "pkg/core.py")).read()
    derived["baseline"] = DC.measure(derived, repo)["counts"]
    assert DC.check(derived, repo)["ok"] and _pytest(repo).returncode == 0
    with open(os.path.join(repo, "pkg/extra.py"), "w") as fh:            # a new positional call
        fh.write("from pkg.core import Client\n\n\ndef f():\n    return Client().send('a', 9)\n")
    assert DC.check(derived, repo)["remaining"] == 1


def test_move(repo):
    c = _cand("pkg.util.slugify", "pkg/util.py", "function", ["pkg/__init__.py", "pkg/api.py", "pkg/cli.py",
                                                             "tests/test_pkg.py"], move_free_names=["re"])
    changed = D.gold_move(repo, c, "pkg/util_slugify.py")
    derived = {"kind": "move", "file": "pkg/util.py", "name": "slugify", "new_file": "pkg/util_slugify.py"}
    assert not DC.check(derived, repo)["ok"]
    _apply(repo, changed)
    new = open(os.path.join(repo, "pkg/util_slugify.py")).read()
    assert new.startswith("import re\n") and "def slugify(text):" in new
    old = open(os.path.join(repo, "pkg/util.py")).read()
    assert "def slugify" not in old and "from .util_slugify import slugify" in old     # still used by title_slug
    assert "from .util_slugify import slugify as make_slug" in open(os.path.join(repo, "pkg/cli.py")).read()
    assert "from pkg.util_slugify import slugify" in open(os.path.join(repo, "pkg/api.py")).read()
    derived["baseline"] = DC.measure(derived, repo)["counts"]
    assert DC.check(derived, repo)["ok"] and _pytest(repo).returncode == 0
    with open(os.path.join(repo, "pkg/extra.py"), "w") as fh:            # an import from the old module
        fh.write("from pkg.util import slugify\n")
    assert DC.check(derived, repo)["remaining"] == 1


def test_importable_and_generated_tests(repo):
    os.makedirs(os.path.join(repo, "src/lib"), exist_ok=True)
    open(os.path.join(repo, "src/lib/__init__.py"), "w").close()
    assert D.importable(repo, "src/lib/mod.py") == "lib.mod"
    assert D.importable(repo, "pkg/core.py") == "pkg.core"
    assert D.importable(repo, "pkg/__init__.py") == "pkg"
    for fam, qual, det in (("rename", "Client.send", {"new": "dispatch"}),
                           ("signature", "Client.send", {"param": "timeout"}),
                           ("move", "slugify", {"new_module": "pkg.util_slugify"})):
        text = D.generated_test(fam, "pkg.core" if fam != "move" else "pkg.util", qual, det)
        compile(text, "t.py", "exec")
    # at the start each generated test fails: the change has not been made
    gen = D.generated_test("signature", "pkg.core", "Client.send", {"param": "timeout"})
    open(os.path.join(repo, "tests/test_gen.py"), "w").write(gen)
    assert _pytest(repo).returncode != 0
