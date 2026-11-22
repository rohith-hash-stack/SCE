"""Phase 3's hidden-dependency tasks (`harness/experiments/r4/hidden_tasks.py`, `hidden_scout.py`, and the
`rename_sites` kind of `derived_checks.py`): the answer key renames the method at PRISM's places only, and the check
fails both a place left over and a look-alike renamed."""
import os
import subprocess
import sys

from harness.experiments.r4 import derived_checks as DC
from harness.experiments.r4 import hidden_scout as HS
from harness.experiments.r4 import hidden_tasks as H

FILES = {
    "pkg/__init__.py": "",
    "pkg/conn.py": ("class Conn:\n"
                    "    def close(self):\n"
                    "        return 'conn closed'\n\n\n"
                    "class Other:\n"
                    "    def close(self):\n"
                    "        return 'other closed'\n"),
    "pkg/user.py": ("from pkg.conn import Conn, Other\n\n\n"
                    "def finish(c: Conn, o: Other):\n"
                    "    o.close()\n"
                    "    return c.close()\n\n\n"
                    "def shut(fh):\n"
                    "    fh.close()\n"),
    "pkg/svc.py": ("from pkg.conn import Conn\n\n\n"
                   "class Service:\n"
                   "    def __init__(self):\n"
                   "        self.conn = Conn()\n\n"
                   "    def stop(self):\n"
                   "        return self.conn.close()\n"),
}


def _repo(tmp_path):
    for rel, text in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "s"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    return str(tmp_path)


def _cand(work):
    sys.setrecursionlimit(10000)
    b, sites = HS.record_call_sites(work)
    q = next(s["target"] for s in sites if s["target"].endswith("Conn.close"))
    places = [{"file": os.path.relpath(b.symbol_table.get(s["caller"]).file, work), "line": s["line"],
               "col": s["col"], "how": "call"} for s in sites if s["target"] == q]
    return {"qname": q, "file": "pkg/conn.py", "leaf": "close", "kind": "method", "lines": [2, 3], "places": places,
            "files": sorted({p["file"] for p in places}), "test_files": []}


def test_prism_places_are_the_typed_calls_only(tmp_path):
    work = _repo(tmp_path)
    c = _cand(work)
    got = sorted((p["file"], p["line"]) for p in c["places"])
    assert got == [("pkg/svc.py", 9), ("pkg/user.py", 6)]          # not `o.close()`, not `fh.close()`


def test_gold_renames_the_places_and_leaves_lookalikes(tmp_path):
    work = _repo(tmp_path)
    c = _cand(work)
    gold = H.gold_sites(work, c, "close_impl")
    assert "def close_impl(self):\n        return 'conn closed'" in gold["pkg/conn.py"]
    assert "class Other:\n    def close(self):" in gold["pkg/conn.py"]
    assert "o.close()\n    return c.close_impl()" in gold["pkg/user.py"] and "fh.close()" in gold["pkg/user.py"]
    assert "self.conn.close_impl()" in gold["pkg/svc.py"]


def test_rename_sites_check(tmp_path):
    work = _repo(tmp_path)
    c = _cand(work)
    derived = {"kind": "rename_sites", "file": "pkg/conn.py", "qualname": "Conn.close", "old": "close",
               "new": "close_impl"}
    start = {f: open(os.path.join(work, f)).read() for f in FILES}
    gold = H.gold_sites(work, c, "close_impl")
    for f, t in gold.items():
        open(os.path.join(work, f), "w").write(t)
    derived["baseline"] = DC.measure(derived, work)["counts"]
    assert DC.check(derived, work)["ok"]
    # a place left over
    open(os.path.join(work, "pkg/svc.py"), "w").write(start["pkg/svc.py"])
    chk = DC.check(derived, work)
    assert not chk["ok"] and chk["remaining"] == 1 and "still use the old name" in chk["problems"][0]
    open(os.path.join(work, "pkg/svc.py"), "w").write(gold["pkg/svc.py"])
    # a look-alike renamed as well (the grep-and-replace change)
    open(os.path.join(work, "pkg/user.py"), "w").write(gold["pkg/user.py"].replace("o.close()", "o.close_impl()"))
    chk = DC.check(derived, work)
    assert not chk["ok"] and "belong to other objects" in chk["problems"][0]
    # a place renamed and a look-alike renamed in the same scope still fails: receivers differ
    open(os.path.join(work, "pkg/user.py"), "w").write(
        start["pkg/user.py"].replace("o.close()", "o.close_impl()"))
    assert not DC.check(derived, work)["ok"]


def test_scoped_uses_keys():
    text = ("class A:\n    def close(self):\n        self.f.close()\n\n\n"
            "def g(c):\n    c.close()\n    c.close()\n    close()\n    h(close=1)\n")
    assert DC.scoped_uses(text, "close") == {"A|<def>": 1, "A.close|self.f": 1, "g|c": 2, "g|<name>": 1,
                                             "g|<keyword>": 1}


def test_property_setter_is_not_a_candidate(tmp_path):
    (tmp_path / "m.py").write_text("class A:\n    @property\n    def v(self):\n        return 1\n\n"
                                   "    @v.setter\n    def v(self, x):\n        pass\n")
    c = {"file": "m.py", "qname": "m.A.v", "leaf": "v"}
    assert H.def_position(str(tmp_path), c) is None


def test_phase3_selection_rule():
    from harness.experiments.r4 import phase3_tasks as P

    def t(i, repo, gp, look):
        return {"id": f"p3-hidden-{i:02d}", "repo": repo, "anchor_qname": f"m.C.f{i}",
                "hidden": {"grep_precision": gp, "lookalikes": look}}
    pool = [t(1, "a", 0.1, 5), t(2, "a", 0.05, 9), t(3, "b", 0.2, 1), t(4, "c", 0.2, 7), t(5, "d", 0.3, 1),
            t(6, "a", 0.4, 1), t(7, "b", 0.5, 1), t(8, "a", 0.6, 1)]
    poc, conf = P.select(pool)
    assert [x["id"] for x in poc] == ["p3-hidden-02", "p3-hidden-04", "p3-hidden-03", "p3-hidden-05"]
    assert conf[:4] == poc
    assert sum(1 for x in conf if x["repo"] == "a") == 3          # fewer than 12 qualify at 2 per repository
