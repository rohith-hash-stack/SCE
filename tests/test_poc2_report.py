"""POC 2's pre-registered analysis (`rebaseline_report`, design `poc2`; AMENDMENTS "Phase 3, POC 2"): BASELINE_LSP,
PRISM_LSP and PRISM_GW, the go/no-go over both PRISM arms, and the gateway-effectiveness record."""
import json
import os

from harness.experiments.r4 import rebaseline_report as R
from tests.test_phase2_report import _run, _task

ARMS = ("BASELINE_LSP", "PRISM_LSP", "PRISM_GW")


def _iv(i, kind, outcome, places_ok=None, blocking=None):
    return {"id": i, "type": kind, "blocking": kind.startswith("block_") if blocking is None else blocking,
            "outcome": outcome, "places_ok": places_ok, "chars": 100}


def _doc(tmp_path, solved, logs):
    d = tmp_path / "poc2"
    d.mkdir()
    tasks = [_task(f"p3-hidden-2{i}", family="rename_hidden", derived={"kind": "rename_sites"}) for i in range(1, 5)]
    tasks.append(_task("r4s-delete-08", family="delete", control=True))
    json.dump({"design": "poc2", "stage": "poc", "expected_runs": 45, "seeds": [201, 202, 203], "tasks": tasks},
              open(tmp_path / "t.json", "w"))
    for t in tasks:
        for arm in ARMS:
            for k, seed in enumerate((201, 202, 203)):
                ok = True if t.get("control") else k < solved[arm].get(t["id"], 0)
                _run(str(d), t["id"], arm, seed, ok)
                if arm == "PRISM_GW":
                    p = os.path.join(d, f"{t['id']}__{arm}__{seed}.score.json")
                    s = json.load(open(p))
                    s["gateway"] = {"target": None if t.get("control") else "x.y", "log": logs(t["id"], seed, ok)}
                    json.dump(s, open(p, "w"))
    return R.analyse(str(d), str(tmp_path / "t.json"))


def test_design_go_over_both_prism_arms(tmp_path):
    rep = _doc(tmp_path, {"BASELINE_LSP": {"p3-hidden-21": 1}, "PRISM_LSP": {"p3-hidden-21": 1},
                          "PRISM_GW": {"p3-hidden-21": 3, "p3-hidden-22": 3}}, lambda *_: [])
    assert rep["design"] == "poc2" and rep["arms"] == list(ARMS)
    assert set(rep["primary"]) == {"PRISM_LSP vs BASELINE_LSP", "PRISM_GW vs BASELINE_LSP"}
    assert set(rep["descriptive"]) == {"PRISM_GW vs PRISM_LSP"}
    g = rep["go_no_go"]
    assert set(g["arms"]) == {"PRISM_LSP", "PRISM_GW"} and g["arms"]["PRISM_GW"]["extra_solves"] == 5
    assert g["arms"]["PRISM_GW"]["go"] and not g["arms"]["PRISM_LSP"]["go"] and g["verdict"] == "GO"
    assert rep["gateway_effectiveness"]["interventions"] == 0 and rep["gateway_effectiveness"]["runs"] == 15


def test_gateway_effectiveness(tmp_path):
    def logs(tid, seed, ok):
        if tid == "r4s-delete-08":
            return [_iv(0, "block_syntax", "prevented")]
        return [_iv(0, "suggest_tool", "followed"),
                _iv(1, "worklist", "followed", places_ok=ok),
                _iv(2, "block_lookalike", "prevented" if seed == 201 else "ignored", places_ok=seed == 201),
                _iv(3, "block_done", "prevented", places_ok=ok),
                _iv(4, "stall", "ignored", places_ok=False)]
    rep = _doc(tmp_path, {"BASELINE_LSP": {}, "PRISM_LSP": {}, "PRISM_GW": {f"p3-hidden-2{i}": 2 for i in range(1, 5)}},
               logs)
    g = rep["gateway_effectiveness"]
    assert g["runs"] == 15 and g["runs_without_target"] == 3 and g["interventions"] == 12 * 5 + 3
    assert g["prevented_edits"] == 4 + 3                    # look-alike blocks at seed 201, and the control's syntax
    assert g["prevented_tool_calls"] == 12
    assert g["ignored_blocks"] == 8 and g["ignored_guidance"] == 12 and g["ignored"] == 20
    t = g["by_type"]
    assert t["suggest_tool"] == {"n": 12, "prevented": 0, "followed": 12, "ignored": 0, "places_ok_at_end": 0,
                                 "contributed": 8}            # followed, names no places, in the 8 solved runs
    assert t["worklist"]["contributed"] == 8 and t["worklist"]["places_ok_at_end"] == 8
    assert t["block_lookalike"]["contributed"] == 4           # seed 201 (prevented, places ok) is solved in every task
    assert t["stall"]["contributed"] == 0 and t["block_syntax"]["contributed"] == 3
    assert g["per_task"]["p3-hidden-21"] == {"runs": 3, "solved": 2, "interventions": 15, "contributed": 2 + 2 + 1 + 2}
    assert g["runs_acted_on_guidance"] == 15 and g["solve_rate_runs_acted_on_guidance"] == 11 / 15
    assert "loop_refused_per_run" in rep["process"]["BASELINE_LSP"]


def test_historical_references_section(tmp_path):
    rep = _doc(tmp_path, {"BASELINE_LSP": {}, "PRISM_LSP": {}, "PRISM_GW": {}}, lambda *_: [])
    assert "historical_references" not in rep                                  # no snapshot record: no section
    d = tmp_path / "poc2"
    for arm, given in (("BASELINE_LSP", False), ("PRISM_LSP", True)):
        p = d / f"r4s-delete-08__{arm}__201.score.json"
        doc = json.loads(p.read_text())
        doc["history"] = {"given_to_agent": given, "fingerprint_ok": True,
                          "snapshot": {"capture_s": 0.01, "reads": int(given)},
                          "end_state": {"m.f": {"asked": given, "places": 21, "gone": 3, "still_references": 18,
                                                "confirmed_broken": 0, "unresolved": ["a.py:1"]}}}
        p.write_text(json.dumps(doc))
    with open(d / "r4s-delete-08__PRISM_LSP__201.jsonl", "w") as fh:
        fh.write(json.dumps({"turn": 1, "tool": "prism_find_references",
                             "history": {"target": "m.f", "places": 21, "gone": 1}}) + "\n")
    rep = R.analyse(str(d), str(tmp_path / "t.json"))
    h = rep["historical_references"]
    assert set(h) == {"BASELINE_LSP", "PRISM_LSP"}
    assert h["BASELINE_LSP"]["given_to_agent"] == 0 and h["PRISM_LSP"]["runs_with_a_historical_answer"] == 1
    run = h["PRISM_LSP"]["per_run"][0]
    assert run["first_answer"]["target"] == "m.f" and "unresolved" not in run["end_state"]["m.f"]
