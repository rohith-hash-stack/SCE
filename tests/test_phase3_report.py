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


def test_new_tools_are_counted_and_never_crash(tmp_path):
    """Turn logs with every Phase 3 tool: the report counts the IDE tools and the two reference searches apart."""
    d = tmp_path / "phase3_poc"
    d.mkdir()
    tasks = [_task("p3-hidden-01", family="rename_hidden", derived={"kind": "rename_sites"}),
             _task("p3-hidden-02", family="rename_hidden", derived={"kind": "rename_sites"})]
    json.dump({"design": "phase3", "stage": "confirmatory", "tasks": tasks}, open(tmp_path / "t.json", "w"))
    logs = {"BASELINE_LSP": [("find_references", 160), ("semantic_search", 1200), ("ast_search", 1500),
                             ("run_shell", 40), ("edit_file", 10)],
            "PRISM_LSP": [("prism_find_references", 290), ("prism_change_context", 1700), ("grep", 300),
                          ("edit_file", 10)],
            "PRISM_ONLY": [("prism_find_references", 290), ("prism_search", 3400), ("prism_read", 380),
                           ("edit_file", 10)]}
    for t in tasks:
        for arm, calls in logs.items():
            _run(str(d), t["id"], arm, 42, True)
            with open(d / f"{t['id']}__{arm}__42.jsonl", "w") as fh:
                for i, (tool, tok) in enumerate(calls, 1):
                    fh.write(json.dumps({"turn": i, "tool": tool, "args": {"symbol": "pkg.m.f"}, "output_tokens": tok,
                                         "output_head": "edited" if tool == "edit_file" else "x"}) + "\n")
    rep = R.analyse(str(d), str(tmp_path / "t.json"))
    p = rep["process"]
    assert p["BASELINE_LSP"]["reference_tool_output_tokens_per_run"] == 160
    assert p["PRISM_LSP"]["reference_tool_output_tokens_per_run"] == 290
    assert p["BASELINE_LSP"]["index_tool_output_tokens_per_run"] == 160 + 1200 + 1500 + 40
    assert p["PRISM_ONLY"]["index_tool_output_tokens_per_run"] == 290 + 3400 + 380
    assert p["PRISM_LSP"]["tool_calls_per_run"]["prism_find_references"] == 1
    assert p["BASELINE_LSP"]["prism_output_tokens_per_run"] == 0
    eff = rep["turn_efficiency"]
    assert eff is not None
    assert "PRISM_LSP" in rep["process"] and "post_prism_by_arm" in eff and "PRISM_LSP" in eff["post_prism_by_arm"]


def test_old_outputs_of_every_arms_index_tools_are_summarised_alike():
    """Compaction (Phase 1 item 2) summarises an old index-tool output to its first lines; Phase 3's IDE tools and
    `prism_find_references` follow the same rule, so no arm keeps re-sending its old tool outputs in full."""
    from harness.experiments.r3.compaction import compact
    big = "\n".join(f"line {i}" for i in range(60))
    for tool in ("find_references", "semantic_search", "ast_search", "run_shell", "prism_find_references",
                 "prism_change_context"):
        hist = [{"role": "user", "content": big, "tool_output": True, "tool": tool, "args": {"q": tool}, "turn": 1}]
        hist += [{"role": "user", "content": f"short {i}", "tool_output": True, "tool": "edit_file", "args": {},
                  "turn": i + 2} for i in range(8)]
        out, counts = compact(hist, lambda s: len(s) // 4)
        assert counts["summarised"] == 1, tool
        assert out[0]["content"].startswith("line 0\nline 1") and "omitted" in out[0]["content"], tool
        assert len(out[0]["content"].splitlines()) == 13, tool
