"""`prism_read_code` (R4 amendment, before the 3-arm re-baseline): the exact code of an indexed definition, with
`read_file`'s line numbers, blank lines and indentation kept, for the PRISM arm only."""
import os
import subprocess

import pytest

from harness.experiments.r3.loop import BASELINE_DOC, PARAM_SCHEMAS, PRISM_DOC, native_tools, system_prompt

SRC = '''import functools


def plain(a, b):
    """Add.

    Twice blank.


    End."""
    x = a + b

    return x


class Box:
    """A box."""

    @property
    def size(self) -> int:

        return 3

    @functools.lru_cache(maxsize=None)
    def area(self, k):
        if k:

            return k * 2
        return 0
'''


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    root = tmp_path_factory.mktemp("rc")
    work = root / "work"
    (work / "pkg").mkdir(parents=True)
    (work / "pkg" / "__init__.py").write_text("")
    (work / "pkg" / "mod.py").write_text(SRC)
    (work / "pkg" / "other.py").write_text("def plain():\n    return 1\n")
    git = lambda *a: subprocess.run(["git", "-C", str(work), *a], check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    wi = WorkingIndex(str(work), str(root / "shadow"))
    lay = ToolLayer(wi)
    yield lay, work
    lay.close()
    wi.close() if hasattr(wi, "close") else None


def _numbered(lo, hi):
    lines = SRC.splitlines()
    return "\n".join(f"{i}\t{lines[i - 1]}" for i in range(lo, hi + 1))


def test_qualified_name_gives_exact_lines(layer):
    lay, _ = layer
    out = lay.call("prism_read_code", symbol="pkg.mod.plain")
    head, body = out.split("\n", 1)
    assert head.startswith("pkg/mod.py (lines 4-13 of 29") and "`pkg.mod.plain`" in head
    assert body == _numbered(4, 13)                          # blank lines and indentation as in the file


def test_decorated_method_and_partial_name(layer):
    lay, _ = layer
    out = lay.call("prism_read_code", symbol="Box.area")
    body = out.split("\n", 1)[1]
    assert "def area(self, k):" in body
    lo = int(out.split("(lines ", 1)[1].split("-", 1)[0])
    assert body == _numbered(lo, 29)
    assert body.splitlines()[-1] == "29\t        return 0"


def test_location_and_file(layer):
    lay, _ = layer
    out = lay.call("prism_read_code", symbol="pkg/mod.py:22")          # inside Box.size: the innermost definition
    assert "`pkg.mod.Box.size`" in out.split("\n", 1)[0]
    whole = lay.call("prism_read_code", symbol="pkg/mod.py")
    assert whole.split("\n", 1)[1] == _numbered(1, 29) and "whole file" in whole.split("\n", 1)[0]


def test_errors_go_back_to_the_agent(layer):
    lay, _ = layer
    assert lay.call("prism_read_code", symbol="pkg.mod.nope").startswith("error: symbol 'pkg.mod.nope' was not found")
    amb = lay.call("prism_read_code", symbol="plain")
    assert amb.startswith("error:") and "pkg.mod.plain" in amb and "pkg.other.plain" in amb
    assert lay.call("prism_read_code", symbol="pkg/mod.py:999").startswith("error: pkg/mod.py has 29 lines")
    assert "error" in lay.call("prism_read_code", symbol="../../etc/passwd")


def test_reads_the_edited_file(layer):
    lay, work = layer
    p = work / "pkg" / "other.py"
    p.write_text("def plain():\n\n    y = 2\n    return y\n")
    out = lay.call("prism_read_code", symbol="pkg.other.plain")
    assert out.split("\n", 1)[1] == "1\tdef plain():\n2\t\n3\t    y = 2\n4\t    return y"


def test_prism_arm_only():
    assert "prism_read_code" in PRISM_DOC and "prism_read_code" not in BASELINE_DOC
    assert PARAM_SCHEMAS["prism_read_code"] == ({"symbol": {"type": "string"}}, ["symbol"])
    assert "prism_read_code" in [t["function"]["name"] for t in native_tools("PRISM")]
    assert "prism_read_code" not in [t["function"]["name"] for t in native_tools("BASELINE")]
    assert "prism_read_code" in system_prompt("PRISM") and "prism_read_code" not in system_prompt("BASELINE")


def test_decorators_are_part_of_the_block(layer):
    lay, _ = layer
    for sym, first in (("pkg.mod.Box.area", 24), ("pkg.mod.Box.size", 19)):
        out = lay.call("prism_read_code", symbol=sym)
        assert out.split("\n", 2)[1].startswith(f"{first}\t    @")
