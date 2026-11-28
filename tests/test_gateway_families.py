"""PRISM's gateway on delete, signature and move tasks (`families=True`; the breadth study, `breadth/GATEWAY.md`).

A fake `refs` stands in for PRISM's graph. With the switch off, these tasks get the syntax check only, as before."""
import subprocess

import pytest

from prism.gateway import Gateway
from prism.gateway.gateway import family_of

# pkg/u.py defines `load(path, mode)` and `Store.put(self, key, value)`; pkg/v.py uses both; pkg/w.py has another
# object's `load` (a look-alike) and another `put` call on something else.
U = ("def load(path, mode):\n    return (path, mode)\n\n\n"
     "class Store:\n    def put(self, key, value):\n        return key\n")
V = ("from pkg.u import load, Store\nimport pkg.u as uu\n\n\n"
     "def run():\n    a = load('p', 'r')\n    b = load(path='p', mode='r')\n"
     "    s = Store()\n    s.put('k', 1)\n    return uu.load('q', 'w')\n")
W = "class Other:\n    def load(self, x):\n        return x\n\n\ndef f(o, d):\n    d.put('z', 2)\n    return o.load(1)\n"


def _refs(sym):
    if sym in ("pkg/u.py:1", "pkg.u.load"):
        rows = {("pkg/u.py", 1, 4): ("definition", "pkg.u"), ("pkg/v.py", 1, 18): ("import", "<module>"),
                ("pkg/v.py", 6, 8): ("call", "pkg.v.run"), ("pkg/v.py", 7, 8): ("call", "pkg.v.run"),
                ("pkg/v.py", 10, 14): ("call", "pkg.v.run")}
        return {"target": "pkg.u.load", "leaf": "load", "rows": rows, "possible": {}, "file": "pkg/u.py",
                "def_line": 1}
    if sym in ("pkg/u.py:6", "pkg.u.Store.put"):
        rows = {("pkg/u.py", 6, 8): ("definition", "pkg.u.Store"), ("pkg/v.py", 9, 6): ("call", "pkg.v.run")}
        possible = {("pkg/w.py", 7, 6): ("possible", "pkg.w.f")}            # matched by name only: not a place
        return {"target": "pkg.u.Store.put", "leaf": "put", "rows": rows, "possible": possible, "file": "pkg/u.py",
                "def_line": 6}
    raise KeyError(sym)


DELETE = "Remove the function `load` from `pkg/u.py` (it is defined at line 1). Remaining tests must still pass."
SIG = ("Make the parameter `mode` of the function `load` in `pkg/u.py` (it is defined at line 1) keyword-only: callers "
       "must pass it as `mode=...`. Update every call that passes it positionally.")
SIG_M = ("Make the parameter `value` of the method `Store.put` in `pkg/u.py` (it is defined at line 6) keyword-only: "
         "callers must pass it as `value=...`.")
MOVE = ("Move the function `load` from `pkg/u.py` (it is defined at line 1) into a new module `pkg/u_load.py`. Update "
        "every import of it.")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    for n, t in (("u", U), ("v", V), ("w", W)):
        (tmp_path / "pkg" / f"{n}.py").write_text(t)
    (tmp_path / "pkg" / "__init__.py").write_text("")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _edit(gw, root, turn, path, old, new):
    args = {"path": path, "old": old, "new": new}
    d = gw.before(turn, "edit_file", args)
    if d.block:
        return d, gw.after(turn, "edit_file", args, d.message, blocked=True)
    p = root / path
    p.write_text(p.read_text().replace(old, new, 1) if p.exists() else new)
    return d, gw.after(turn, "edit_file", args, f"edited {path} at line 1: replaced 1 lines with 1")


def _done(gw, turn=99):
    d = gw.before(turn, "done", {"summary": "x"})
    gw.after(turn, "done", {"summary": "x"}, d.message if d.block else "done", blocked=d.block)
    return d


# ------------------------------------------------------------------------------------------------ the families
def test_family_of_and_the_switch_off_is_as_before(repo):
    assert family_of(DELETE, True) == "delete" and family_of(SIG, True) == "signature"
    assert family_of(MOVE, True) == "move" and family_of(MOVE, False) is None
    assert family_of("Rename the class `X` in `a.py` to `Y`.", False) == "rename"
    for prompt in (DELETE, SIG, MOVE):
        gw = Gateway(str(repo), prompt, _refs)
        assert gw.target is None and gw.error == "not a rename task: only the syntax check applies"
        assert not _done(gw).block


def test_enable_families_equals_the_constructor_switch(repo):
    for prompt in (DELETE, SIG, SIG_M, MOVE):
        a = Gateway(str(repo), prompt, _refs, families=True)
        b = Gateway(str(repo), prompt, _refs)
        b.enable_families(prompt)
        assert (a.target, a.places_total, a.lines, a.pending()) == (b.target, b.places_total, b.lines, b.pending())


def test_a_rename_is_unchanged_by_the_switch(repo):
    p = "Rename the function `load` in `pkg/u.py` (it is defined at line 1) to `fetch`. Update every use."
    a, b = Gateway(str(repo), p, _refs), Gateway(str(repo), p, _refs, families=True)
    assert (a.lines, a.places_total, a.pending(), a.error) == (b.lines, b.places_total, b.pending(), b.error)


# ------------------------------------------------------------------------------------------------------ delete
def test_delete_tracks_every_use_and_blocks_done_until_they_are_gone(repo):
    gw = Gateway(str(repo), DELETE, _refs, families=True)
    assert gw.family == "delete" and gw.target == "pkg.u.load"
    assert gw.pending() == [("pkg/u.py", 1), ("pkg/v.py", 1), ("pkg/v.py", 6), ("pkg/v.py", 7), ("pkg/v.py", 10)]
    assert {ln for ln, e in gw.lines["pkg/w.py"].items() if e["kind"] == "lookalike"} == {2, 8}
    d = _done(gw)
    assert d.block and "which is removed" in d.message and "pkg/v.py:6" in d.message
    d, _ = _edit(gw, repo, 2, "pkg/w.py", "return o.load(1)", "return o.get(1)")      # another object's `load`
    assert d.block and gw.interventions[-1]["type"] == "block_lookalike"
    _edit(gw, repo, 3, "pkg/u.py", "def load(path, mode):\n    return (path, mode)\n\n\n", "")
    _edit(gw, repo, 4, "pkg/v.py", "from pkg.u import load, Store\nimport pkg.u as uu\n", "from pkg.u import Store\n")
    _, note = _edit(gw, repo, 5, "pkg/v.py", "    a = load('p', 'r')\n    b = load(path='p', mode='r')\n", "")
    assert "1 of 5 place(s)" in note
    _edit(gw, repo, 6, "pkg/v.py", "    return uu.load('q', 'w')\n", "    return None\n")
    assert gw.pending() == [] and not _done(gw).block


# --------------------------------------------------------------------------------------------------- signature
def test_signature_tracks_the_definition_and_positional_calls_only(repo):
    gw = Gateway(str(repo), SIG, _refs, families=True)
    assert gw.family == "signature" and gw.limit == 1 and gw.qual == "load"
    # the definition, `load('p', 'r')` and `uu.load('q', 'w')`; not the import, not the keyword call
    assert gw.pending() == [("pkg/u.py", 1), ("pkg/v.py", 6), ("pkg/v.py", 10)] and gw.places_total == 3
    assert "pkg/w.py" not in gw.lines                                           # no look-alikes for this family
    d = _done(gw)
    assert d.block and "positionally" in d.message
    _, note = _edit(gw, repo, 2, "pkg/u.py", "def load(path, mode):", "def load(path, *, mode):")
    assert "2 of 3 place(s)" in note
    _edit(gw, repo, 3, "pkg/v.py", "load('p', 'r')", "load('p', mode='r')")
    _edit(gw, repo, 4, "pkg/v.py", "uu.load('q', 'w')", "uu.load('q', mode='w')")
    assert gw.pending() == [] and not _done(gw).block


def test_signature_on_a_method_skips_self_and_ignores_name_only_matches(repo):
    gw = Gateway(str(repo), SIG_M, _refs, families=True)
    assert gw.limit == 1 and gw.qual == "Store.put"
    assert gw.pending() == [("pkg/u.py", 6), ("pkg/v.py", 9)]                 # not pkg/w.py's `d.put('z', 2)`
    _edit(gw, repo, 2, "pkg/u.py", "def put(self, key, value):", "def put(self, key, *, value):")
    _edit(gw, repo, 3, "pkg/v.py", "s.put('k', 1)", "s.put('k', value=1)")
    assert gw.pending() == []


def test_signature_a_call_moved_by_an_edit_is_still_tracked(repo):
    gw = Gateway(str(repo), SIG, _refs, families=True)
    _edit(gw, repo, 2, "pkg/v.py", "def run():\n", "def run():\n    '''Run.'''\n\n")       # lines shift by 2
    assert ("pkg/v.py", 8) in gw.pending() and ("pkg/v.py", 6) not in gw.pending()


def test_signature_with_no_such_parameter_is_the_syntax_check_only(repo):
    p = SIG.replace("`mode`", "`nope`")
    gw = Gateway(str(repo), p, _refs, families=True)
    assert gw.target is None and "not a positional parameter" in gw.error and not _done(gw).block


# -------------------------------------------------------------------------------------------------------- move
def test_move_tracks_the_definition_imports_and_the_new_module(repo):
    gw = Gateway(str(repo), MOVE, _refs, families=True)
    assert gw.family == "move"
    # the old definition, `from pkg.u import load`, `uu.load(...)` through `import pkg.u as uu`, the new module
    assert gw.pending() == [("pkg/u.py", 1), ("pkg/v.py", 1), ("pkg/v.py", 10), ("pkg/u_load.py", 0)]
    assert gw.places_total == 4
    d = _done(gw)
    assert d.block and "is not defined there yet" in d.message
    _edit(gw, repo, 2, "pkg/u_load.py", "", "def load(path, mode):\n    return (path, mode)\n")
    _edit(gw, repo, 3, "pkg/u.py", "def load(path, mode):\n    return (path, mode)\n\n\n", "")
    _edit(gw, repo, 4, "pkg/v.py", "from pkg.u import load, Store\n",
          "from pkg.u import Store\nfrom pkg.u_load import load\n")
    assert gw.pending() == [("pkg/v.py", 11)]                                  # `uu.load`, moved down one line
    _edit(gw, repo, 5, "pkg/v.py", "uu.load('q', 'w')", "load('q', 'w')")
    assert gw.pending() == [] and not _done(gw).block


def test_move_relative_imports_count(repo):
    (repo / "pkg" / "x.py").write_text("from .u import load\n\n\ndef g():\n    return load(1, 2)\n")
    gw = Gateway(str(repo), MOVE, _refs, families=True)
    assert ("pkg/x.py", 1) in gw.pending()


# ------------------------------------------------------------------------------------- the rest is shared
def test_the_syntax_block_and_overrides_work_in_every_family(repo):
    for prompt in (DELETE, SIG, MOVE):
        gw = Gateway(str(repo), prompt, _refs, families=True)
        d, _ = _edit(gw, repo, 1, "pkg/u.py", "class Store:\n    def put(self, key, value):\n        return key\n",
                     "class Store:\n")
        assert d.block and gw.interventions[-1]["type"] == "block_syntax"
        d = gw.before(2, "done", {"summary": "x", "confirm": True})
        assert not d.block
        rec = gw.finish()
        assert rec["error"] is None and rec["places"] == gw.places_total


def test_the_exact_text_note_stays_a_renames(repo):
    gw = Gateway(str(repo), SIG, _refs, families=True, exact_fix=True)
    args = {"path": "pkg/v.py", "old": "x", "new": "y"}
    assert not gw.before(1, "edit_file", args).block
    out = gw.after(1, "edit_file", args, "error: old not found at line 6")
    assert "exact text" not in out
