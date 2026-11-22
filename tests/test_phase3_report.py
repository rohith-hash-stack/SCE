"""Phase 3's pre-registered analysis (`rebaseline_report`, design `phase3`; AMENDMENTS "Phase 3"): three arms, PRISM and
PRISM_ONLY against BASELINE (Holm over two), the hidden tasks and the controls apart, and the proof of concept's
go/no-go verdict computed by the fixed rule."""
import json
import os

from harness.experiments.r4 import rebaseline_report as R
from tests.test_phase2_report import _run, _task

ARMS3 = ("BASELINE_LSP", "PRISM_LSP", "PRISM_ONLY")


def _doc(tmp_path, solved, stage="poc", missing=()):
    """`solved[arm]` = how many of the 3 seeds solve each of the 4 hidden tasks; the control is solved by all."""
    d = tmp_path / "phase3_poc"
    d.mkdir()
    tasks = [_task(f"p3-hidden-0{i}", family="rename_hidden", derived={"kind": "rename_sites"}) for i in range(1, 5)]
    tasks.append(_task("r4s-delete-05", family="delete", control=True))
    json.dump({"design": "phase3", "stage": stage, "expected_runs": 45, "tasks": tasks}, open(tmp_path / "t.json", "w"))
    for t in tasks:
        for arm in ARMS3:
            for k, seed in enumerate((42, 43, 44)):
                if (t["id"], arm, seed) in missing:
                    continue
                ok = True if t.get("control") else k < solved[arm].get(t["id"], 0)
                _run(str(d), t["id"], arm, seed, ok)
    return R.analyse(str(d), str(tmp_path / "t.json"))


def test_design_and_go(tmp_path):
    rep = _doc(tmp_path, {"BASELINE_LSP": {"p3-hidden-01": 1},
                          "PRISM_LSP": {"p3-hidden-01": 3, "p3-hidden-02": 2, "p3-hidden-03": 1},
                          "PRISM_ONLY": {"p3-hidden-01": 1}})
    assert rep["design"] == "phase3" and rep["arms"] == list(ARMS3) and rep["runs"] == 45
    assert set(rep["primary"]) == {"PRISM_LSP vs BASELINE_LSP", "PRISM_ONLY vs BASELINE_LSP"}
    assert set(rep["descriptive"]) == {"PRISM_ONLY vs PRISM_LSP"} and rep["primary_tasks"] == 4
    assert rep["primary"]["PRISM_LSP vs BASELINE_LSP"]["tasks"] == 4                      # the control is not in it
    assert rep["subsets"]["hidden"]["tasks"] == 4 and rep["subsets"]["controls"]["tasks"] == 1
    g = rep["go_no_go"]
    assert g["hidden_solved_runs"] == {"BASELINE_LSP": 1, "PRISM_LSP": 6, "PRISM_ONLY": 1}
    assert g["arms"]["PRISM_LSP"] == {"extra_solves": 5, "hidden_tasks_lost": 0, "go": True}
    assert g["arms"]["PRISM_ONLY"]["go"] is False and g["verdict"] == "GO"


def test_no_go_when_the_gain_is_small_or_baseline_wins_tasks(tmp_path):
    rep = _doc(tmp_path, {"BASELINE_LSP": {"p3-hidden-01": 1}, "PRISM_LSP": {"p3-hidden-01": 3}, "PRISM_ONLY": {}})
    assert rep["go_no_go"]["arms"]["PRISM_LSP"]["extra_solves"] == 2 and rep["go_no_go"]["verdict"] == "NO-GO"
    (tmp_path / "phase3_poc").rename(tmp_path / "old")
    rep = _doc(tmp_path, {"BASELINE_LSP": {"p3-hidden-01": 2, "p3-hidden-02": 2},
                          "PRISM_LSP": {"p3-hidden-03": 3, "p3-hidden-04": 3}, "PRISM_ONLY": {}})
    g = rep["go_no_go"]["arms"]["PRISM_LSP"]
    assert g["extra_solves"] == 2 and g["hidden_tasks_lost"] == 2 and not g["go"]


def test_no_go_when_runs_are_missing(tmp_path):
    missing = {("p3-hidden-02", "BASELINE_LSP", 42), ("p3-hidden-02", "BASELINE_LSP", 43), ("p3-hidden-03", "PRISM_LSP", 44)}
    rep = _doc(tmp_path, {"BASELINE_LSP": {}, "PRISM_LSP": {"p3-hidden-01": 3, "p3-hidden-02": 3}, "PRISM_ONLY": {}},
               missing=missing)
    assert rep["go_no_go"]["infrastructure_deaths"] == 3 and rep["go_no_go"]["verdict"] == "NO-GO"


def test_confirmatory_stage_has_no_verdict(tmp_path):
    rep = _doc(tmp_path, {"BASELINE_LSP": {}, "PRISM_LSP": {}, "PRISM_ONLY": {}}, stage="confirmatory")
    assert "go_no_go" not in rep
