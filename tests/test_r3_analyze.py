"""R3's analysis end to end on synthetic run files: outcome split, tests, labels, pilot criteria, turn cap."""
from __future__ import annotations

import json
import os

from harness.experiments.r3 import analyze as A
from harness.experiments.r3.runner import outcome


def write_tasks(path, fam, n, depths):
    tasks = [{"id": f"t-{fam}-{i:02d}", "family": fam, "repo": "r", "language": "python" if i % 2 else "js",
              "depth": depths[i % len(depths)], "stratum": None, "scoring_check": {"gold": True, "empty": False}}
             for i in range(n)]
    for t in tasks:
        d = t["depth"]
        t["stratum"] = (("1" if d == 1 else "2-4" if d <= 4 else "5+") if fam == "B"
                        else ("2" if d == 2 else "3-4" if d <= 4 else "5+"))
    json.dump({"tasks": tasks}, open(path, "w"))
    return tasks


def write_run(d, tid, version, seed, end, resolved, turns=8, tests_ok=True):
    rid = f"{tid}__{version}__{seed}"
    stop = {"done": "done", "turn_cap": "stuck", "budget": "budget"}.get(end, "error")
    r = {"run": rid, "task": tid, "version": version, "seed": seed, "turns": turns, "end": end,
         "done_called": end == "done", "stop": stop, "used_tokens": 1000 * turns, "parse_errors": 1,
         "tool_calls": {"run_tests": 2}, "outputs_cut": 0, "turns_stubbed": 0, "notice_turns": 0,
         "score": {"resolved": resolved, "file_f1": 0.5, "function_f1": 0.4}}
    r["outcome"] = outcome(r)
    json.dump(r, open(os.path.join(d, rid + ".score.json"), "w"))
    with open(os.path.join(d, rid + ".jsonl"), "w") as fh:
        fh.write(json.dumps({"turn": 1, "tool": "run_tests",
                             "output_head": "tests/x.py: 3 passed, 0 failed, 0 skipped" if tests_ok else "error"}) + "\n")


def test_outcome_names_every_stop():
    assert outcome({"stop": "done", "score": {"resolved": True}}) == "resolved"
    assert outcome({"stop": "done", "score": {"resolved": False}}) == "unresolved"
    # a cap-hit run whose tree happens to pass is still not resolved: stopping is part of the outcome
    assert outcome({"stop": "stuck", "score": {"resolved": True}}) == "stuck"
    assert outcome({"end": "budget"}) == "budget"
    assert outcome({"end": "model_error: x"}) == "error"


def test_interim_boundaries_reproduce_amendment_r3_1():
    b = A.interim_boundaries(0.5, 0.025)
    assert (b["interim_p"], b["interim_z"], b["final_p"], b["final_z"]) == (0.0008, 3.34, 0.0247, 2.25)
    b = A.interim_boundaries(0.5, 0.05)
    assert (b["interim_p"], b["interim_z"], b["final_p"], b["final_z"]) == (0.0031, 2.96, 0.049, 1.97)


def test_label_rules():
    good = {"diff": 0.2, "bootstrap": {"low": 0.05, "high": 0.3}}
    assert A.label(good, 0.01, False) == "pass"
    assert A.label(good, 0.01, True) == "invalid"
    assert A.label({"diff": 0.05, "bootstrap": {"low": 0.01, "high": 0.1}}, 0.01, False) == "below_floor"
    assert A.label({"diff": -0.2, "bootstrap": {"low": -0.3, "high": -0.1}}, 0.01, False) == "padding_better"
    assert A.label({"diff": 0.2, "bootstrap": {"low": -0.01, "high": 0.3}}, 0.01, False) == "not_shown"
    assert A.label(good, 0.2, False) == "not_shown"
    assert A.unstable([0.1, -0.05]) and not A.unstable([0.1, 0.05])


def test_end_to_end_on_synthetic_runs(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    tb = write_tasks(tmp_path / "tb.json", "B", 12, [1, 3, 6])
    tn = write_tasks(tmp_path / "tn.json", "N", 10, [2, 4, 5])
    # Family B: PRISM resolves every task; the baseline resolves the first 2 and is stuck on 4 of the rest.
    for i, t in enumerate(tb):
        for s in (42, 1001):
            write_run(runs, t["id"], "PRISM", s, "done", True)
            if i < 2:
                write_run(runs, t["id"], "BASELINE", s, "done", True)
            elif i < 6:
                write_run(runs, t["id"], "BASELINE", s, "turn_cap", False, turns=30)
            else:
                write_run(runs, t["id"], "BASELINE", s, "done", False)
    # Family N: identical versions, except one harness error and one missing run.
    for i, t in enumerate(tn):
        for s in (42, 1001):
            for v in A.VERSIONS:
                if (i, v, s) == (0, "PRISM", 42):
                    json.dump({"stage": "environment", "error": "SetupError: x"},
                              open(runs / f"{t['id']}__{v}__{s}.error.json", "w"))
                elif (i, v, s) == (1, "BASELINE", 1001):
                    continue
                else:
                    write_run(runs, t["id"], v, s, "done", i % 2 == 0)
    out = tmp_path / "report.json"
    assert A.main([str(out), "--runs", str(runs), "--tasks", str(tmp_path / "tb.json"), str(tmp_path / "tn.json"),
                   "--pilot"]) == 0
    res = json.load(open(out))
    ob = res["outcomes"]["B"]
    assert ob["PRISM/pooled"]["resolved"] == 24 and ob["PRISM/pooled"]["stuck"] == 0
    assert ob["BASELINE/pooled"] == {**ob["BASELINE/pooled"], "resolved": 4, "stuck": 8, "unresolved": 12,
                                     "budget": 0, "error": 0, "missing": 0}
    on = res["outcomes"]["N"]
    assert on["PRISM/42"]["error"] == 1 and on["BASELINE/1001"]["missing"] == 1
    fb = res["families"]["B"]
    assert fb["primary"]["diff"] > 0.8 and fb["primary"]["bootstrap"]["low"] > 0
    assert fb["cap_hit_rate"] == {"PRISM": 0.0, "BASELINE": round(8 / 24, 4)}
    assert fb["invalid"] and fb["label"] == "invalid"           # cap-hit gap 0.33 > 0.20
    fn = res["families"]["N"]
    assert fn["label"] == "not_shown" and not fn["invalid"]
    assert set(res["families"]) == {"B", "N"} and fb["p_holm"] >= fb["primary"]["p"]
    assert fb["scaling"]["tasks"] == 12 and "by_stratum" in fb["scaling"]
    assert fb["pass_at_2"]["prism"] == 1.0
    assert fb["secondary"]["stuck"]["diff"] < 0
    pc = res["pilot"]["criteria"]
    assert not pc["1_no_harness_error"]["pass"] and len(pc["1_no_harness_error"]["error_or_missing"]) == 2
    assert pc["3_tests_found_and_run"]["pass"] and set(pc["3_tests_found_and_run"]["by_language"]) == {"python", "js"}
    assert pc["5_scoring_path"]["pass"]
    assert pc["6_done_vs_stuck"]["BASELINE"]["stuck"] == 8
    tc = res["pilot"]["turn_cap"]
    assert tc["p90"] == 8.0 and tc["cap"] == 13 and tc["share_at_30"] < 0.2      # 8 runs of 86 at 30 turns


def test_turn_cap_rule():
    rows = [{"turns": t} for t in [5, 6, 6, 7, 8, 8, 9, 10, 12, 14]]
    assert A.turn_cap(rows)["cap"] == 18                       # p90 = 12.2, + 5 = 17.2, rounded up
    rows = [{"turns": 30}] * 3 + [{"turns": 5}] * 7             # 30% at 30: the p90 is not informative
    assert A.turn_cap(rows)["cap"] == 30
