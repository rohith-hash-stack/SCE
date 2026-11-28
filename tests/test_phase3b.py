"""Phase 3b (AMENDMENTS "Phase 3b"): arm A is PRISM_GW unchanged; arm C (PRISM_GW_C) adds the exact replacement after
a refused edit; arm E (PRISM_GW_E) prints PRISM's source lines with their own indentation; BASELINE_LSP is untouched.
The replays of POC 2's recorded transcripts (`r4/phase3b_replay.py`, `r4/phase3b_compare.py`) check the same on real
runs; these tests pin each rule on small inputs."""
import json
import subprocess

import pytest

from harness.experiments.r3 import loop as L
from prism.gateway import Gateway
from tests.test_prism_history import DEF, _edit, layer  # noqa: F401  (the deletion fixture)

# pkg/a.py defines the target; pkg/b.py uses it on indented lines and on a column-0 continuation line (as
# tests/conftest.py:181 in p3-hidden-15); pkg/c.py has another object's `Server`.
A = "class Server:\n    pass\n"
B = ("from pkg.a import Server\n\n\ndef make(\n    x: int,\n) -> Server:\n    return Server()\n\n\n"
     "def again():\n\treturn Server()\n")
C = "class Other:\n    Server = 1\n"
PROMPT = ("Rename the method `Server` in `pkg/a.py` (it is defined at line 1) to `ServerImpl`. Update every use. "
          "When your changes are complete, call done.")
ROWS = {("pkg/a.py", 1, 6): ("definition", "pkg.a"), ("pkg/b.py", 1, 18): ("import", "<module>"),
        ("pkg/b.py", 6, 5): ("annotation", "pkg.b.make"), ("pkg/b.py", 7, 11): ("call", "pkg.b.make"),
        ("pkg/b.py", 11, 8): ("call", "pkg.b.again")}


def _refs(sym):
    if sym in ("pkg/a.py:1", "pkg.a.Server"):
        return {"target": "pkg.a.Server", "leaf": "Server", "rows": dict(ROWS), "possible": {}}
    raise KeyError(sym)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    for n, t in (("a", A), ("b", B), ("c", C)):
        (tmp_path / "pkg" / f"{n}.py").write_text(t)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _apply(gw, root, turn, path, old, new):
    args = {"path": path, "old": old, "new": new}
    d = gw.before(turn, "edit_file", args)
    assert not d.block
    p = root / path
    p.write_text(p.read_text().replace(old, new, 1))
    return gw.after(turn, "edit_file", args, f"edited {path} at line 1: replaced 1 lines with 1")


def _refuse(gw, turn, path, old, new, output=None):
    args = {"path": path, "old": old, "new": new}
    d = gw.before(turn, "edit_file", args)
    assert not d.block
    output = output or (f"error: the text to replace was not found in {path}: it matches once apart from whitespace "
                        f"(indentation), at line 6. These are the file's lines there")
    return gw.after(turn, "edit_file", args, output)


def _calls(root):
    """One call sequence, the same in every arm: an applied edit, a refused one at the column-0 line, a loop refusal
    of it, then the exact fix."""
    return [("apply", "pkg/b.py", "from pkg.a import Server", "from pkg.a import ServerImpl"),
            ("refuse", "pkg/b.py", "    ) -> Server:", "    ) -> ServerImpl:"),
            ("loop", "pkg/b.py", "    ) -> Server:", "    ) -> ServerImpl:"),
            ("apply", "pkg/b.py", ") -> Server:", ") -> ServerImpl:"),
            ("done",)]


def _drive(gw, root):
    outs = []
    for i, c in enumerate(_calls(root), 1):
        if c[0] == "apply":
            outs.append(_apply(gw, root, i, *c[1:]))
        elif c[0] == "refuse":
            outs.append(_refuse(gw, i, *c[1:]))
        elif c[0] == "loop":
            outs.append(_refuse(gw, i, *c[1:], output="not answered again: you made this exact call at turn 2"))
        else:
            d = gw.before(i, "done", {"summary": "x"})
            outs.append(d.message)
            gw.after(i, "done", {"summary": "x"}, d.message, blocked=d.block)
    return outs


# ---------------------------------------------------------------------------------------------------- the arms
def test_variants_have_prism_gws_prompt_and_tools():
    for v in ("PRISM_GW_C", "PRISM_GW_E"):
        assert L.base_version(v) == "PRISM_GW"
        assert L.system_prompt(v, guards=True) == L.system_prompt("PRISM_GW", guards=True)
        assert L.native_tools(v, True) == L.native_tools("PRISM_GW", True)
        assert set(L.shared_doc(v, True)) | set(L.index_doc(v)) == \
            set(L.shared_doc("PRISM_GW", True)) | set(L.index_doc("PRISM_GW"))
    assert L.base_version("PRISM_GW") == "PRISM_GW" and L.base_version("BASELINE_LSP") == "BASELINE_LSP"
    assert L.PHASE3B_ARMS == ("PRISM_GW", "PRISM_GW_C", "PRISM_GW_E", "BASELINE_LSP")


def test_runner_switches_one_per_arm_and_arm_a_is_poc2s_call():
    from harness.experiments.r3 import runner as R
    src = open(R.__file__).read()
    run_task = src[src.index("def run_task("):src.index("def main(")]
    assert 'if version == "PRISM_GW":                     # arm A: exactly POC 2\'s call' in run_task
    assert "gateway = Gateway(work, task[\"prompt\"], layer.reference_places)\n" in run_task     # POC 2's exact call
    assert 'exact_fix=version == "PRISM_GW_C", keep_indent=version == "PRISM_GW_E"' in run_task
    assert 'layer.keep_indent = version == "PRISM_GW_E"' in run_task
    assert run_task.count("keep_indent") == 2 and run_task.count("exact_fix") == 1


# ---------------------------------------------------------------------------------------------------- arm E
def test_shown_line_differs_only_by_leading_whitespace():
    from harness.experiments.r3.tools import ToolLayer
    lay = ToolLayer.__new__(ToolLayer)
    lay.keep_indent = False
    assert lay.shown_line("    return Server()  ") == "return Server()"
    assert lay.shown_line("\treturn x", 3) == "ret"
    lay.keep_indent = True
    assert lay.shown_line("    return Server()  ") == "    return Server()"
    assert lay.shown_line(") -> Server:") == ") -> Server:"                    # a column-0 line stays at column 0
    assert lay.shown_line("\treturn x", 3) == "\tret"                          # the cap counts after the indentation
    assert lay.shown_line("   ") == ""                                         # a blank line prints nothing


def test_gateway_listing_keeps_indentation_only_in_e(repo):
    a = Gateway(str(repo), PROMPT, _refs)
    e = Gateway(str(repo), PROMPT, _refs, keep_indent=True)
    la = a._listing(a.pending())
    le = e._listing(e.pending())
    assert "pkg/b.py:6: ) -> Server:" in la and "pkg/b.py:7: return Server()" in la
    assert "pkg/b.py:6: ) -> Server:" in le and "pkg/b.py:7:     return Server()" in le
    assert "pkg/b.py:11: \treturn Server()" in le
    from harness.experiments.r4.phase3b_compare import indent_only
    assert indent_only(la, le) and la != le


def test_e_changes_no_decision(repo, tmp_path_factory):
    import shutil
    other = tmp_path_factory.mktemp("e")
    shutil.copytree(repo, other, dirs_exist_ok=True)
    a, e = Gateway(str(repo), PROMPT, _refs), Gateway(str(other), PROMPT, _refs, keep_indent=True)
    oa, oe = _drive(a, repo), _drive(e, other)
    from harness.experiments.r4.phase3b_compare import indent_only
    assert all(indent_only(x, y) for x, y in zip(oa, oe))
    strip = lambda s: {k: v for k, v in s.items() if k != "log"}
    fa, fe = a.finish(), e.finish()
    assert strip(fa) == strip(fe)
    assert [(iv["type"], iv["outcome"], iv["places"]) for iv in fa["log"]] == \
        [(iv["type"], iv["outcome"], iv["places"]) for iv in fe["log"]]


def test_prism_search_and_references_keep_indentation_only_in_e():
    import inspect
    from harness.experiments.r3 import tools as T
    src = inspect.getsource(T.ToolLayer)
    refs = src[src.index("def _prism_find_references("):src.index("def reference_places(")]
    search = src[src.index("def _prism_search("):]
    assert refs.count("self.shown_line(lines[line - 1])") == 2 and ".strip()" not in refs.split("by_file")[1]
    assert "self.shown_line(text, 240)" in search
    hist = src[src.index("def _historical_references("):src.index("def _prism_search(")]
    assert "shown_line" not in hist and "start[line - 1].strip()" in hist     # the deletion answers: unchanged


# ---------------------------------------------------------------------------------------------------- arm C
def test_c_note_appears_only_after_a_refused_edit(repo):
    c = Gateway(str(repo), PROMPT, _refs, exact_fix=True)
    assert c.new_name == "ServerImpl"
    out = _apply(c, repo, 1, "pkg/b.py", "from pkg.a import Server", "from pkg.a import ServerImpl")
    assert "exact text" not in out                                           # an applied edit: no note
    before = (repo / "pkg" / "b.py").read_text()
    out = _refuse(c, 2, "pkg/b.py", "    ) -> Server:", "    ) -> ServerImpl:")
    assert out.count("PRISM gateway: the exact text") == 1
    assert 'old: ") -> Server:"' in out and 'new: ") -> ServerImpl:"' in out and "pkg/b.py:6" in out
    assert (repo / "pkg" / "b.py").read_text() == before                     # guidance only: no file changed
    assert out.rindex("PRISM gateway:") == out.rindex("PRISM gateway: the exact text")  # always the last note
    d = c.before(3, "done", {"summary": "x"})                                # done is not a refusal: no note
    assert "exact text" not in c.after(3, "done", {"summary": "x"}, d.message, blocked=d.block)


def test_c_changes_no_decision_and_adds_only_its_note(repo, tmp_path_factory):
    import shutil
    other = tmp_path_factory.mktemp("c")
    shutil.copytree(repo, other, dirs_exist_ok=True)
    a, c = Gateway(str(repo), PROMPT, _refs), Gateway(str(other), PROMPT, _refs, exact_fix=True)
    oa, oc = _drive(a, repo), _drive(c, other)
    notes = 0
    for x, y in zip(oa, oc):
        if x != y:
            assert y.startswith(x) and y[len(x):].startswith("\n\nPRISM gateway: the exact text")
            notes += 1
    assert notes == 2                                                        # the refusal and the loop refusal
    fa, fc = a.finish(), c.finish()
    keep = [iv for iv in fc["log"] if iv["type"] != "exact_fix"]
    assert [(iv["type"], iv["outcome"], iv["places"]) for iv in fa["log"]] == \
        [(iv["type"], iv["outcome"], iv["places"]) for iv in keep]
    fixes = [iv for iv in fc["log"] if iv["type"] == "exact_fix"]
    assert [iv["response"] for iv in fixes] == ["exact", "exact"] and all(iv["outcome"] == "followed" for iv in fixes)
    assert fixes[0]["warning_type"] == "actionable_correction" and fixes[0]["fix_old"] == ") -> Server:"
    assert fixes[0]["refused_old"] == "    ) -> Server:" and fixes[0]["refusal"].startswith("error")


def test_c_never_names_an_ambiguous_place(repo):
    # lines 7 and 11 qualify and are equally far (2) from line 9, the line the refusal names: no note
    c = Gateway(str(repo), PROMPT, _refs, exact_fix=True)
    out = _refuse(c, 1, "pkg/b.py", "x", "y", output="error: the text to replace was not found in pkg/b.py. The "
                                                    "closest lines in the file, from line 9 (each line ...)")
    assert "exact text" not in out
    # line 8 is nearest to 7 (distance 1): a note for 7
    out = _refuse(c, 2, "pkg/b.py", "x", "z", output="error: ... closest lines in the file, from line 8 (...)")
    assert "pkg/b.py:7" in out and 'old: "    return Server()"' in out
    # no line named and several places: no note
    c2 = Gateway(str(repo), PROMPT, _refs, exact_fix=True)
    assert "exact text" not in _refuse(c2, 1, "pkg/b.py", "x", "y", output="error: the text to replace was not found")


def test_c_skips_lines_it_cannot_identify(repo):
    # a line that also holds a look-alike use of the name, and a line whose text occurs twice, never qualify
    (repo / "pkg" / "b.py").write_text("from pkg.a import Server\nx = Server()\nx = Server()\n")
    rows = {("pkg/a.py", 1, 6): ("definition", "pkg.a"), ("pkg/b.py", 1, 18): ("import", "<module>"),
            ("pkg/b.py", 2, 4): ("call", "<module>"), ("pkg/b.py", 3, 4): ("call", "<module>")}
    c = Gateway(str(repo), PROMPT, lambda s: {"target": "pkg.a.Server", "leaf": "Server", "rows": rows,
                                              "possible": {}}, exact_fix=True)
    out = _refuse(c, 1, "pkg/b.py", "x", "y", output="error: the text to replace was not found in pkg/b.py: it "
                                                    "matches once apart from whitespace (indentation), at line 2.")
    assert "pkg/b.py:1" in out and 'old: "from pkg.a import Server"' in out   # 2 and 3 share their text
    assert "pkg/b.py:2" not in out.split("exact text")[1]


def test_c_off_and_on_identical_without_refusals(repo):
    a, c = Gateway(str(repo), PROMPT, _refs), Gateway(str(repo), PROMPT, _refs, exact_fix=True)
    for g in (a, c):
        g.before(1, "prism_find_references", {"symbol": "pkg.a.Server"})
    assert a.after(1, "prism_find_references", {"symbol": "pkg.a.Server"}, "x") == \
        c.after(1, "prism_find_references", {"symbol": "pkg.a.Server"}, "x")


# ---------------------------------------------------------------------------------------------------- history, baseline
def test_historical_answers_are_unchanged_with_either_switch(layer):
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "")                                   # the deletion task's target removed
    outs = []
    for keep in (False, True, False):
        lay.keep_indent = keep
        outs.append(lay.call("prism_find_references", symbol="pkg.params.Dependency"))
    assert outs[0] == outs[1] == outs[2] and "not in PRISM's current graph" in outs[0]


def test_prism_search_and_find_references_differ_only_by_indentation(layer):
    # POC 2's PRISM_GW transcripts never called prism_search, so the replays cannot exercise it: checked here
    from harness.experiments.r4.phase3b_compare import indent_only
    lay, _, work = layer
    lay.history = None
    for tool, kw in (("prism_search", {"pattern": "Dependency"}),
                     ("prism_find_references", {"symbol": "pkg.params.Dependency"})):
        lay.keep_indent = False
        a = lay.call(tool, **kw)
        lay.keep_indent = True
        e = lay.call(tool, **kw)
        assert a != e and indent_only(a, e), tool
        assert "    return Dependency()" in e or "def handler" in e


def test_baseline_is_untouched():
    from harness.experiments.r3 import runner as R
    src = open(R.__file__).read()
    run_task = src[src.index("def run_task("):src.index("def main(")]
    assert "BASELINE" not in run_task.split("layer.keep_indent")[1].split("\n")[0]
    assert L.system_prompt("BASELINE_LSP", guards=True) == \
        L.SYSTEM.format(tools=L.tool_docs("BASELINE_LSP", True))
    assert L.base_version("BASELINE_LSP") == "BASELINE_LSP"


# ---------------------------------------------------------------------------------------------------- the task file
def test_task_file_one_for_every_arm_with_only_the_turn_sentence_changed():
    from harness.experiments.r4 import phase3b_tasks as P
    doc = P.build()
    ids = [t["id"] for t in doc["tasks"]]
    assert ids == ["p3-hidden-15", "p3-hidden-03"]
    src15 = next(t for t in json.load(open(P.POC2))["tasks"] if t["id"] == "p3-hidden-15")
    src03 = next(t for t in json.load(open(P.POC1))["tasks"] if t["id"] == "p3-hidden-03")
    t15, t03 = doc["tasks"]
    assert t15 == src15                                                      # unchanged
    assert {k for k in set(src03) | set(t03) if src03.get(k) != t03.get(k)} == {"prompt"}
    assert t03["prompt"] == src03["prompt"].replace("You have 28 turns in total", "You have 60 turns in total")
    assert src03["prompt"].count("You have 28 turns in total") == 1
    assert doc["seeds"] == {"p3-hidden-15": list(range(301, 316)), "p3-hidden-03": list(range(301, 306))}
    assert doc["arms"] == list(L.PHASE3B_ARMS) and doc["expected_runs"] == 80
