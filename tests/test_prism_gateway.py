"""PRISM's per-turn gateway (`src/prism/gateway`; POC 2's PRISM_GW arm). A fake `refs` stands in for PRISM's graph:
the target `pkg.a.Server` is defined in pkg/a.py and used in pkg/b.py; pkg/c.py has another object's `Server`."""
import json
import subprocess

import pytest

from prism.gateway import Gateway, infer_target

A = "class Server:\n    pass\n\n\ndef make():\n    return Server()\n"
B = "from pkg.a import Server\n\n\ndef run():\n    s = Server()\n    return s\n"
C = "class Other:\n    Server = 1\n\n\ndef f(o):\n    return o.Server  # Server here is Other's\n"
M = "import os\n\nif os.name:\n    X = 1\nelse:\n\n    def g():\n        return 2\n"
PROMPT = ("Rename the method `Server` in `pkg/a.py` (it is defined at line 1) to `ServerImpl`. Update every use. "
          "When your changes are complete, call done.")


def _refs(sym):
    if sym in ("pkg/a.py:1", "pkg.a.Server"):
        rows = {("pkg/a.py", 1, 6): ("definition", "pkg.a"), ("pkg/a.py", 6, 11): ("call", "pkg.a.make"),
                ("pkg/b.py", 1, 18): ("import", "<module>"), ("pkg/b.py", 5, 8): ("call", "pkg.b.run")}
        return {"target": "pkg.a.Server", "leaf": "Server", "rows": rows, "possible": {}}
    if sym == "pkg.c.Other.Server":
        return {"target": "pkg.c.Other.Server", "leaf": "Server", "rows": {}, "possible": {}}
    raise KeyError(sym)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    for n, t in (("a", A), ("b", B), ("c", C), ("m", M)):
        (tmp_path / "pkg" / f"{n}.py").write_text(t)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _edit(gw, root, turn, path, old, new, **extra):
    args = {"path": path, "old": old, "new": new, **extra}
    d = gw.before(turn, "edit_file", args)
    if d.block:
        return d, gw.after(turn, "edit_file", args, d.message, blocked=True)
    p = root / path
    p.write_text(p.read_text().replace(old, new, 1))
    return d, gw.after(turn, "edit_file", args, f"edited {path} at line 1: replaced 1 lines with 1")


def test_infer_target():
    assert infer_target(PROMPT)[0] == "pkg/a.py:1"
    assert "Server" in infer_target(PROMPT) and "ServerImpl" not in infer_target(PROMPT)


def test_start_state(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    assert gw.target == "pkg.a.Server" and gw.places_total == 4
    assert gw.pending() == [("pkg/a.py", 1), ("pkg/a.py", 6), ("pkg/b.py", 1), ("pkg/b.py", 5)]
    kinds = {(f, ln): e["kind"] for f in gw.lines for ln, e in gw.lines[f].items()}
    assert kinds[("pkg/c.py", 2)] == "lookalike" and kinds[("pkg/c.py", 6)] == "lookalike"   # names, not the comment
    assert gw.changed() == []


def test_full_rename_is_never_blocked_and_done_passes(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    for path, old, new in (("pkg/a.py", "class Server:", "class ServerImpl:"),
                           ("pkg/a.py", "return Server()", "return ServerImpl()"),
                           ("pkg/b.py", "import Server", "import ServerImpl"),
                           ("pkg/b.py", "s = Server()", "s = ServerImpl()")):
        d, extra = _edit(gw, repo, 1, path, old, new)
        assert not d.block and "PRISM gateway:" in extra
    assert gw.pending() == [] and "0 of 4 place(s)" in extra
    assert not gw.before(9, "done", {"summary": "x"}).block
    s = gw.finish()
    assert s["pending_at_end"] == 0 and s["prevented_edits"] == 0 and s["by_type"]["worklist"] == 4
    assert all(iv["outcome"] == "followed" for iv in s["log"] if iv["type"] == "worklist" and iv["pending_at"])


def test_lookalike_edit_is_blocked_then_overridden(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    d, _ = _edit(gw, repo, 1, "pkg/c.py", "    Server = 1", "    ServerImpl = 1")
    assert d.block and "belongs to another object" in d.message and "pkg/c.py:2" in d.message
    assert (repo / "pkg" / "c.py").read_text() == C                               # not applied
    d2, _ = _edit(gw, repo, 2, "pkg/c.py", "    Server = 1", "    ServerImpl = 1", confirm=True)
    assert not d2.block
    s = gw.finish()
    blk = [iv for iv in s["log"] if iv["type"] == "block_lookalike"]
    assert len(blk) == 1 and blk[0]["outcome"] == "ignored" and blk[0]["places_ok"] is False
    assert s["ignored"] >= 1 and s["prevented_edits"] == 0 and s["lookalikes_changed_at_end"] == 1


def test_lookalike_block_counts_as_prevented_when_not_overridden(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    d, _ = _edit(gw, repo, 1, "pkg/c.py", "    return o.Server  #", "    return o.ServerImpl  #")
    assert d.block
    for t in range(2, 5):
        gw.before(t, "read_file", {"path": "pkg/a.py"})
        gw.after(t, "read_file", {"path": "pkg/a.py"}, "pkg/a.py (lines 1-6 of 6)\n...")
    s = gw.finish()
    assert s["prevented_edits"] == 1 and s["log"][[x["type"] for x in s["log"]].index("block_lookalike")][
        "places_ok"] is True


def test_comment_mention_on_a_lookalike_line_is_free(repo):
    # changing only the comment's word keeps the name token: not a look-alike change
    gw = Gateway(str(repo), PROMPT, _refs)
    d, _ = _edit(gw, repo, 1, "pkg/c.py", "# Server here", "# ServerImpl here")
    assert not d.block


def test_branch_left_empty_is_blocked(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    d, _ = _edit(gw, repo, 1, "pkg/m.py", "    def g():\n        return 2\n", "")
    assert d.block and "unparseable" in d.message and "pass" in d.message
    assert gw.interventions[-1]["type"] == "block_syntax"


def test_syntax_block_without_a_target(repo):
    gw = Gateway(str(repo), "Remove the function `nothing` from `pkg/zz.py`.", _refs)
    assert gw.target is None and gw.error
    d, _ = _edit(gw, repo, 1, "pkg/m.py", "    def g():\n        return 2\n", "")
    assert d.block


def test_a_deletion_gets_the_syntax_check_only(repo):
    gw = Gateway(str(repo), "Remove the function `Server` from `pkg/a.py` (it is defined at line 1).", _refs)
    assert gw.target is None and "not a rename" in gw.error
    assert not gw.before(1, "done", {"summary": "x"}).block                       # uses left: not the gateway's call
    gw.after(1, "done", {"summary": "x"}, "done")
    d, _ = _edit(gw, repo, 2, "pkg/m.py", "    def g():\n        return 2\n", "")
    assert d.block and gw.finish()["by_type"] == {"block_syntax": 1}


def test_done_blocked_while_places_pending(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    _edit(gw, repo, 1, "pkg/a.py", "class Server:", "class ServerImpl:")
    d = gw.before(2, "done", {"summary": "x"})
    assert d.block and "3 of 4 place(s)" in d.message and "pkg/b.py:5" in d.message
    gw.after(2, "done", {"summary": "x"}, d.message, blocked=True)
    d2 = gw.before(3, "done", {"summary": "x"})                                   # again: the same intervention
    assert d2.block and d2.intervention == d.intervention
    gw.after(3, "done", {"summary": "x"}, d2.message, blocked=True)
    assert not gw.before(4, "done", {"summary": "x", "confirm": True}).block      # the override
    gw.after(4, "done", {"summary": "x", "confirm": True}, "done")
    s = gw.finish()
    blk = [iv for iv in s["log"] if iv["type"] == "block_done"]
    assert len(blk) == 1 and blk[0]["repeats"] == 1 and blk[0]["outcome"] == "ignored"
    assert s["prevented_tool_calls"] == 0


def test_line_tracking_follows_inserted_lines(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    _edit(gw, repo, 1, "pkg/b.py", "from pkg.a import Server\n", "import sys\nimport os\nfrom pkg.a import Server\n")
    assert ("pkg/b.py", 7) in gw.pending() and ("pkg/b.py", 3) in gw.pending()   # lines 1 and 5 moved down by 2


def test_search_augment_and_suggestion(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    out = "pkg/a.py:1:class Server:\npkg/b.py:5:    s = Server()\npkg/c.py:2:    Server = 1"
    extra = gw.after(1, "grep", {"pattern": "Server"}, out) if not gw.before(1, "grep", {"pattern": "Server"}).block \
        else None
    assert "2 are uses of `pkg.a.Server` and 1 belong to other objects" in extra and "pkg/c.py:2" in extra
    assert 'prism_find_references("pkg.a.Server")' in extra
    gw.before(2, "prism_find_references", {"symbol": "pkg.a.Server"})
    gw.after(2, "prism_find_references", {"symbol": "pkg.a.Server"}, "`pkg.a.Server` ...")
    s = gw.finish()
    sug = [iv for iv in s["log"] if iv["type"] == "suggest_tool"]
    assert sug and sug[0]["outcome"] == "followed" and gw.refs_asked


def test_wrong_target_note(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    gw.before(1, "prism_find_references", {"symbol": "pkg.c.Other.Server"})
    extra = gw.after(1, "prism_find_references", {"symbol": "pkg.c.Other.Server"}, "...")
    assert "another object named `Server`" in extra


def test_stall_note_after_a_loop_refusal(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    gw.before(1, "read_file", {"path": "pkg/a.py"})
    extra = gw.after(1, "read_file", {"path": "pkg/a.py"}, "not answered again: you made this exact call ...")
    assert "Next:" in extra and gw.interventions[-1]["type"] == "stall"


def test_finish_is_json(repo):
    gw = Gateway(str(repo), PROMPT, _refs)
    _edit(gw, repo, 1, "pkg/a.py", "class Server:", "class ServerImpl:")
    json.dumps(gw.finish())


# ---------------------------------------------------------------------------------------------- in the agent loop
def test_gateway_in_the_agent_loop(repo, tmp_path):
    from harness.experiments.r3 import loop as L
    from harness.experiments.r3.shared_tools import SharedTools
    from tests.test_phase2_feedback import _Scripted

    class _Index:
        def call(self, tool, **args):
            return f"{tool} answer"

    shared = SharedTools(str(repo), None, [], feedback=True, guards=True)
    gw = Gateway(str(repo), PROMPT, _refs)
    call = lambda tool, **a: json.dumps({"tool": tool, "args": a})
    look = dict(path="pkg/c.py", old="    Server = 1", new="    ServerImpl = 1")
    replies = [call("edit_file", **look),                                         # blocked: a look-alike
               call("edit_file", **look, confirm=True),                           # overridden: applied
               call("edit_file", path="pkg/a.py", old="class Server:", new="class ServerImpl:"),
               call("done", summary="x"),                                         # blocked: places pending
               call("done", summary="x", confirm=True)]                           # overridden: the run ends
    L.AgentRun({"id": "t", "prompt": PROMPT}, "PRISM_GW", 42, _Scripted(replies), shared, _Index(),
               feedback=True, guards=True, gateway=gw).run(str(tmp_path / "out"))
    log = [json.loads(ln) for ln in open(tmp_path / "out" / "t__PRISM_GW__42.jsonl")]
    assert log[0]["gateway_blocked"] is True and log[0]["gateway"][0]["type"] == "block_lookalike"
    assert log[1]["output_head"].startswith("edited pkg/c.py") and "gateway_blocked" not in log[1]
    assert "ServerImpl = 1" in (repo / "pkg" / "c.py").read_text()
    assert log[2]["gateway"][0]["type"] == "worklist"
    assert log[3]["gateway_blocked"] is True and "3 of 4 place(s)" in log[3]["output_head"]
    assert len(log) == 5
    summary = json.load(open(tmp_path / "out" / "t__PRISM_GW__42.json"))
    g = summary["gateway"]
    assert summary["end"] == "done" and g["ignored"] == 3 and g["prevented_edits"] == 0   # 2 overridden blocks, the last worklist
    outcomes = {iv["type"]: iv["outcome"] for iv in g["log"]}
    assert outcomes["block_lookalike"] == "ignored" and outcomes["block_done"] == "ignored"
    assert g["lookalikes_changed_at_end"] == 1 and g["pending_at_end"] == 3


def test_prism_gw_prompt_equals_prism_lsp():
    from harness.experiments.r3 import loop as L
    assert L.system_prompt("PRISM_GW", guards=True) == L.system_prompt("PRISM_LSP", guards=True)
    assert L.native_tools("PRISM_GW", True) == L.native_tools("PRISM_LSP", True)
