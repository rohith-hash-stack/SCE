"""POC 2 amendment 1: PRISM's historical references (`src/prism/history.py`, `prism_find_references`' fallback). A
definition the agent deletes can still be asked about, from an immutable snapshot of the start index; each place's
current state is kept apart (no longer present / still uses the name / confirmed broken). Pull only, deletion tasks
only, the PRISM arms only; the gateway never reads it."""
import json
import os
import subprocess

import pytest

FILES = {
    "pkg/__init__.py": "from .params import Dependency, Other\n\n__all__ = [\"Dependency\", \"Other\"]\n",
    "pkg/params.py": ("def Dependency(default=None):\n"
                      "    return default\n\n\n"
                      "def Other():\n"
                      "    return 1\n"),
    "pkg/di.py": "class Dependency:\n    pass\n",                                       # a look-alike
    "pkg/use.py": ("from pkg.params import Dependency\n"
                   "from pkg.di import Dependency as DiDependency\n\n\n"
                   "def handler(x=Dependency(3)):\n"
                   "    return x\n\n\n"
                   "def other():\n"
                   "    return DiDependency()\n"),
    "pkg/late.py": ("def late():\n"
                    "    from pkg.params import Dependency\n"
                    "    return Dependency()\n"),
}
DEF = "def Dependency(default=None):\n    return default\n\n\n"


class _Env:
    def run_test(self, rel, ids=None):
        return {}


@pytest.fixture()
def layer(tmp_path):
    from harness.experiments.r3.shared_tools import SharedTools
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    from prism.history import HistoricalGraph
    work = str(tmp_path / "work")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(work, p)), exist_ok=True)
        open(os.path.join(work, p), "w").write(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", *cmd], cwd=work, check=True)
    wi = WorkingIndex(work, str(tmp_path / "shadow"))
    shared = SharedTools(work, _Env(), [])
    lay = ToolLayer(wi, shared=shared)
    lay.history = HistoricalGraph(wi.builder, wi.shadow)
    yield lay, shared, work
    lay.close()
    wi.close()


def _edit(work, rel, old, new):
    p = os.path.join(work, rel)
    s = open(p).read()
    assert old in s
    open(p, "w").write(s.replace(old, new, 1))


def test_before_deletion_the_answer_is_the_current_one(layer):
    lay, _, _ = layer
    out = lay.call("prism_find_references", symbol="pkg.params.Dependency")
    assert "snapshot" not in out and lay.last_history is None
    assert "pkg/use.py" in out and "pkg/late.py" in out


def test_deleted_anchor_answered_from_history(layer):
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "")
    out = lay.call("prism_find_references", symbol="pkg.params.Dependency")
    assert "not in PRISM's current graph" in out and "historical references" in out
    assert "not a list of broken code" in out
    for where in ("pkg/use.py", "pkg/late.py", "pkg/__init__.py"):
        assert where in out
    # the import of the removed name and the module-level import in __init__ are confirmed broken
    assert "imports_a_removed_name" in out
    # the definition's own line is gone
    assert "[no longer present]" in out
    h = lay.last_history
    assert h["target"] == "pkg.params.Dependency" and h["confirmed_broken"] >= 2 and h["gone"] >= 1
    assert lay.history.stats["reads"] == 1


def test_lookalikes_are_counted_not_listed(layer):
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "")
    out = lay.call("prism_find_references", symbol="pkg.params.Dependency")
    assert "pkg/di.py" not in out.split("Not references")[0]                   # the other class never listed
    assert "Not references: at the start," in out


def test_path_and_line_current_graph_first(layer):
    """`path:line` is resolved in the current graph first (as before): after the deletion, line 1 of pkg/params.py
    holds `Other`, so the answer is about `Other`. Only a position no current definition holds goes to the snapshot."""
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "\n" * 20)                                       # line 1 holds no definition
    out = lay.call("prism_find_references", symbol="pkg/params.py:1")
    assert "From PRISM's snapshot" in out and "`pkg.params.Dependency`" in out
    _edit(work, "pkg/params.py", "\n" * 20, "")
    out = lay.call("prism_find_references", symbol="pkg/params.py:1")
    assert out.startswith("`pkg.params.Other`") and "snapshot" not in out              # the current definition


def test_stale_reference_already_repaired_or_moved(layer):
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "")
    _edit(work, "pkg/use.py", "from pkg.params import Dependency\n", "")                 # repaired: import removed
    _edit(work, "pkg/use.py", "def handler(x=Dependency(3)):", "# moved down\n\n\ndef handler(x=Dependency(3)):")
    out = lay.call("prism_find_references", symbol="pkg.params.Dependency")
    st = lay.history.status([("pkg/use.py", 1), ("pkg/use.py", 5)], "Dependency", "pkg/params.py", work)
    assert st[("pkg/use.py", 1)]["status"] == "gone"
    moved = st[("pkg/use.py", 5)]
    assert moved["now"] == 7 and moved["status"] == "confirmed_broken" and "undefined_name" in moved["why"]
    assert "7 -> 5 [confirmed broken" in out


def test_still_references_is_not_called_broken(layer):
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "Dependency = Other\n\n\n")                         # re-pointed, not removed
    st = lay.history.status([("pkg/use.py", 1)], "Dependency", "pkg/params.py", work)
    assert st[("pkg/use.py", 1)]["status"] == "still_references"


def test_unknown_symbol_keeps_the_current_error(layer):
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "")
    out = lay.call("prism_find_references", symbol="pkg.nothing.Here")
    assert out.startswith("error:") and "snapshot" not in out and lay.last_history is None


def test_snapshot_is_immutable_across_rebuilds(layer):
    lay, _, work = layer
    snap = lay.history
    fp, builder = snap.fingerprint, snap.builder
    _edit(work, "pkg/params.py", DEF, "")
    lay.call("prism_find_references", symbol="pkg.use.handler")                       # a rebuild of the current index
    assert lay.wi.builder is not builder                                               # the current graph is new
    assert "pkg.params.Dependency" not in lay.wi.builder.symbol_table
    assert "pkg.params.Dependency" in snap.builder.symbol_table                        # the snapshot still has it
    snap.verify()
    assert snap.fingerprint == fp
    assert snap.lines_at("pkg/params.py")[0] == "def Dependency(default=None):"         # the start text, not the disk
    with pytest.raises(TypeError):
        snap._texts["pkg/params.py"] = "x"                                             # read-only


def test_a_changed_snapshot_never_answers(layer):
    from prism.history import SnapshotChanged
    lay, _, work = layer
    lay.history.builder.graph.add_node("tampered")
    _edit(work, "pkg/params.py", DEF, "")
    with pytest.raises(SnapshotChanged):
        lay.call("prism_find_references", symbol="pkg.params.Dependency")


def test_without_a_snapshot_nothing_changes(layer):
    lay, _, work = layer
    lay.history = None
    _edit(work, "pkg/params.py", DEF, "")
    out = lay.call("prism_find_references", symbol="pkg.params.Dependency")
    assert out.startswith("error:") and lay.last_history is None


# ---------------------------------------------------------------------------------------------- arm isolation
def test_runner_gives_the_snapshot_to_the_prism_arms_only():
    from harness.experiments.r3 import runner as R
    assert R.HISTORY_ARMS == ("PRISM_LSP", "PRISM_GW")
    assert "BASELINE_LSP" not in R.HISTORY_ARMS and "BASELINE_LSP" in R.HISTORY_MEASURED
    src = open(R.__file__).read()
    # built only on deletion tasks; attached to the tool layer only for HISTORY_ARMS
    assert 'task.get("family") == "delete" and version in HISTORY_MEASURED' in src
    assert "if version in HISTORY_ARMS:\n                layer.history = snapshot" in src


def test_prompts_and_tools_are_unchanged():
    from harness.experiments.r3 import loop as L
    for v in ("BASELINE_LSP", "PRISM_LSP", "PRISM_GW"):
        docs = L.tool_docs(v, guards=True)
        assert "snapshot" not in docs and "historical" not in docs.lower()
    assert L.system_prompt("PRISM_GW", guards=True) == L.system_prompt("PRISM_LSP", guards=True)


def test_gateway_never_reads_the_snapshot(layer):
    from prism.gateway import Gateway
    lay, _, work = layer
    prompt = "Remove the function `Dependency` from `pkg/params.py` (it is defined at line 1)."
    gw = Gateway(work, prompt, lay.reference_places)
    assert gw.target is None and "not a rename" in gw.error                           # deletions: syntax check only
    reads = lay.history.stats["reads"]
    _edit(work, "pkg/params.py", DEF, "")
    gw.before(1, "done", {"summary": "x"})
    gw.after(1, "done", {"summary": "x"}, "done")
    assert lay.history.stats["reads"] == reads
    # reference_places itself (what the gateway is given) never falls back to the snapshot
    from harness.experiments.r3.tools import ToolError
    lay.call("prism_find_references", symbol="pkg.use.handler")                       # the index follows the edit
    with pytest.raises(ToolError):
        lay.reference_places("pkg.params.Dependency")


def test_rename_task_behaviour_is_unchanged_without_a_snapshot(layer):
    """On rename tasks the runner builds no snapshot, so a renamed-away name gives the pre-registered error."""
    lay, _, work = layer
    lay.history = None
    _edit(work, "pkg/params.py", "def Dependency(", "def DependencyImpl(")
    out = lay.call("prism_find_references", symbol="pkg.params.Dependency")
    assert out.startswith("error:")


def test_history_record_end_state(layer):
    from harness.experiments.r3.runner import history_record
    lay, _, work = layer
    _edit(work, "pkg/params.py", DEF, "")
    _edit(work, "pkg/late.py", "    from pkg.params import Dependency\n    return Dependency()\n", "    return None\n")
    rec = history_record(lay.history, {"must_be_absent": "pkg/params.py::Dependency"}, work, given=True)
    assert rec["given_to_agent"] and rec["fingerprint_ok"]
    es = rec["end_state"]["pkg.params.Dependency"]
    assert es["asked"] is False and es["gone"] >= 3                                    # def + both late.py lines
    assert "pkg/use.py:1" in es["unresolved"] and not any(u.startswith("pkg/late.py") for u in es["unresolved"])
    json.dumps(rec)
