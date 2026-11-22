"""Phase 3 (the user's decision, 2026-11-22): `prism_find_references`, PRISM's own Find All References for the PRISM
arms. Every place PRISM's graph resolves to one definition, at its exact position (call sites recorded per call on
the graph's edges), each labelled with how it uses the definition and the definition it is in; look-alikes (the same
name on other objects) are counted, not listed."""
import os
import subprocess

import pytest

FILES = {
    "pkg/__init__.py": "from .conn import Conn\n\n__all__ = [\"Conn\"]\n",
    "pkg/conn.py": ("class Conn:\n"
                    "    def close(self):\n"
                    "        return 'conn closed'\n\n\n"
                    "class Other:\n"
                    "    def close(self):\n"
                    "        return 'other closed'\n"),
    "pkg/user.py": ("from pkg.conn import Conn, Other\n\n\n"
                    "def finish(c: Conn, o: Other):\n"
                    "    o.close()\n"
                    "    c.close()\n"
                    "    return c.close()\n\n\n"
                    "def shut(fh):\n"
                    "    fh.close()\n"),
    "pkg/svc.py": ("from pkg.conn import Conn\n\n\n"
                   "class Service:\n"
                   "    def __init__(self):\n"
                   "        self.conn = Conn()\n\n"
                   "    def stop(self):\n"
                   "        return self.conn.close()\n\n\n"
                   "class Pooled(Conn):\n"
                   "    pass\n"),
    "tests/test_conn.py": "from pkg.conn import Conn\n\n\ndef test_close():\n    assert Conn().close() == 'conn closed'\n",
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
    shared = SharedTools(work, _Env(), [])
    lay = ToolLayer(wi, shared=shared)
    yield lay, shared
    lay.close()
    wi.close()


def test_call_edges_keep_every_call_site(layer):
    lay, _ = layer
    g = lay.wi.builder.graph
    e = g.edges["pkg.user.finish", "pkg.conn.Conn.close"]
    assert sorted(e["sites"]) == [[6, 6], [7, 13]]                  # two calls, one edge, both positions


def test_method_references_exact_and_labelled(layer):
    lay, _ = layer
    out = lay.call("prism_find_references", symbol="pkg.conn.Conn.close")
    lines = out.splitlines()
    assert lines[0] == ("`pkg.conn.Conn.close` (method, pkg/conn.py:2): 3 reference(s) in 2 file(s) (call 3); 0 in "
                        "test files; 2 possible (below)")
    assert "  6:7 [call] in `pkg.user.finish`: c.close()" in lines
    assert "  7:14 [call] in `pkg.user.finish`: return c.close()" in lines
    assert "  9:26 [call] in `pkg.svc.Service.stop`: return self.conn.close()" in lines
    assert "  2:9 [definition] in `pkg.conn.Conn`: def close(self):" in lines
    assert "o.close()" not in out
    # receivers of unknown type: listed apart, for the agent to check (`fh` is untyped; `Conn()` is a chained call)
    poss = lines.index(next(x for x in lines if x.startswith("Possible references (2)")))
    assert "  pkg/user.py:11:8 in `pkg.user.shut`: fh.close()" in lines[poss:]
    assert lines.index("pkg/user.py") > lines.index("pkg/svc.py")
    assert "Not references:" in lines[-1] and "other objects" in lines[-1]


def test_class_references_kinds(layer):
    lay, _ = layer
    out = lay.call("prism_find_references", symbol="pkg.conn.Conn")
    assert "  1:19 [import/export] in `<module>`: from .conn import Conn" in out          # re-exported
    assert "  3:13 [import/export] in `<module>`: __all__ = [\"Conn\"]" in out
    assert "  1:22 [import] in `<module>`: from pkg.conn import Conn" in out
    assert "[type annotation] in `pkg.user.finish`: def finish(c: Conn, o: Other):" in out
    assert "[subclass] in `pkg.svc.Pooled`" in out
    assert "[instantiation] in `pkg.svc.Service.__init__`: self.conn = Conn()" in out


def test_position_requests_and_the_edited_tree(layer):
    lay, shared = layer
    by_name = lay.call("prism_find_references", symbol="pkg.conn.Conn.close")
    assert lay.call("prism_find_references", symbol="pkg/user.py:7") == by_name      # a use's line
    assert lay.call("prism_find_references", symbol="pkg/conn.py:2") == by_name      # the definition's line
    shared.call("edit_file", path="pkg/svc.py", old="return self.conn.close()", new="return None")
    after = lay.call("prism_find_references", symbol="pkg.conn.Conn.close")
    assert "\npkg/svc.py\n" not in after and after.splitlines()[0].split(": ", 1)[1].startswith("2 reference(s)")
