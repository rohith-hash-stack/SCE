"""Phase 2's pre-registered analysis (`rebaseline_report`, design `phase2`; AMENDMENTS "Phase 2, 4 arms"): four arms,
three comparisons against BASELINE (Holm over three), PRISM_ONLY and INJECTED against PRISM descriptively, the
derived tasks and the controls apart, and a derived task scoring 0 when its static check fails."""
import json
import os

from harness.experiments.r4 import rebaseline_report as R

ARMS4 = ("BASELINE", "PRISM", "INJECTED", "PRISM_ONLY")


def _task(tid, **kw):
    return {"id": tid, "family": kw.pop("family", "rename"), "pass_to_pass": {"t.py": ["a"]},
            "fail_to_pass": {"t.py": ["g"]}, **kw}


def _run(d, task, arm, seed, ok, derived_ok=True):
    run = f"{task}__{arm}__{seed}"
    tests = {"t.py::a": "passed", "t.py::g": "passed" if ok else "failed"}
    json.dump({"run": run, "task": task, "version": arm, "seed": seed, "turns": 4, "parse_errors": 0, "end": "done",
               "score": {"resolved": ok and derived_ok, "hidden_patch_applied": True, "tests": tests,
                         "target_absent": True, "derived_check": {"ok": derived_ok}}},
              open(os.path.join(d, run + ".score.json"), "w"))


def test_phase2_design(tmp_path):
    d = tmp_path / "phase2_seed42"
    d.mkdir()
    tasks = [_task(f"p2-rename-0{i}", derived={"kind": "rename"}) for i in range(1, 5)]
    tasks += [_task(f"r4s-delete-0{i}", family="delete", control=True) for i in range(1, 3)]
    doc = {"design": "phase2", "tasks": tasks}
    json.dump(doc, open(tmp_path / "tasks.json", "w"))
    for t in tasks:
        for arm in ARMS4:
            # PRISM arms solve the derived tasks, BASELINE does not; everyone solves the controls
            ok = arm != "BASELINE" or bool(t.get("control"))
            _run(str(d), t["id"], arm, 42, ok)
    rep = R.analyse(str(d), str(tmp_path / "tasks.json"))
    assert rep["design"] == "phase2" and rep["arms"] == list(ARMS4) and rep["runs"] == 24
    assert set(rep["primary"]) == {"PRISM vs BASELINE", "INJECTED vs BASELINE", "PRISM_ONLY vs BASELINE"}
    assert set(rep["descriptive"]) == {"INJECTED vs PRISM", "PRISM_ONLY vs PRISM"}
    p = rep["primary"]["PRISM_ONLY vs BASELINE"]
    assert p["tasks_favouring"] == {"PRISM_ONLY": 4, "BASELINE": 0, "tie": 2}
    assert all("wilcoxon_p_holm" in v for v in rep["primary"].values())
    assert rep["subsets"]["derived"]["tasks"] == 4 and rep["subsets"]["controls"]["tasks"] == 2
    assert rep["subsets"]["derived"]["PRISM vs BASELINE"]["mean_diff"] == 1.0     # 1.0 vs 0 (fail-to-pass fails)
    assert rep["subsets"]["controls"]["PRISM vs BASELINE"]["mean_diff"] == 0.0
    assert "PRISM_ONLY" in rep["process"]


def test_derived_task_scores_zero_when_its_check_fails():
    t = _task("p2-x", derived={"kind": "rename"})
    score = {"hidden_patch_applied": True, "tests": {"t.py::a": "passed", "t.py::g": "passed"}}
    assert R.run_score(t, {**score, "derived_check": {"ok": True}}) == 1.0
    assert R.run_score(t, {**score, "derived_check": {"ok": False}}) == 0.0
    assert R.run_score(t, score) == 0.0                                          # no check recorded: not complete


def test_rebaseline_design_unchanged(tmp_path):
    d = tmp_path / "rb"
    d.mkdir()
    tasks = [_task("r4s-feature-01", family="feature"), _task("r4s-feature-02", family="feature")]
    json.dump({"tasks": tasks}, open(tmp_path / "tasks.json", "w"))
    for t in tasks:
        for arm in R.ARMS:
            _run(str(d), t["id"], arm, 42, True)
    rep = R.analyse(str(d), str(tmp_path / "tasks.json"))
    assert rep["design"] == "rebaseline" and set(rep["primary"]) == {"PRISM vs BASELINE", "INJECTED vs BASELINE"}
    assert "subsets" not in rep
