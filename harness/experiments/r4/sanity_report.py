"""Report the R4 sanity run (both arms on the same sample; `ROADMAP_R4_R6.md` §4a′).

    python -B -m harness.experiments.r4.sanity_report RUNS_DIR TASKS.json [--seeds 42] [--out report.json]

Per family (delete, feature; Family B only if it was sampled):

| Layer | What | Pass |
|---|---|---|
| 1, scope (static) | the share of the family's gated pool that is bounded+clean; every sampled task is (the sample is drawn from them) | ≥ 80% of the sample (by construction) — the pool share is reported |
| 2, calibration | BASELINE resolved tasks | 1–4 of 10 (10–40% of the sample): task validity |
| 3, signal, each arm | runs with an applied edit; looping runs; early-done runs (R3's `analyze.turn_flags` rules) | edit ≥ 70%, loop < 20%, early-done < 20% |
| PRISM arm clean | every PRISM run finished with a score (no `error`, no `missing`) | all |

PRISM vs BASELINE resolved counts are printed as a smoke signal only: about 10 tasks per family, one seed, no test.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from harness.experiments.r3.analyze import load_runs, load_tasks

CALIBRATION = (0.10, 0.40)        # BASELINE solves 1-4 of 10
EDIT_MIN, LOOP_MAX, EARLY_MAX = 0.70, 0.20, 0.20


def _rate(rows, key):
    sel = [r for r in rows if r.get(key) is not None]
    return (sum(bool(r[key]) for r in sel) / len(sel)) if sel else None


def layer3(rows) -> dict:
    done = [r for r in rows if r["outcome"] not in ("missing", "error")]
    for r in done:
        r["edited"] = None if r.get("applied_edits") is None else r["applied_edits"] > 0
    out = {"runs": len(done), "edit_rate": _rate(done, "edited"), "loop_rate": _rate(done, "looping"),
           "early_done_rate": _rate(done, "early_done")}
    out["pass"] = (out["runs"] > 0 and out["edit_rate"] is not None and out["edit_rate"] >= EDIT_MIN
                   and (out["loop_rate"] or 0) < LOOP_MAX and (out["early_done_rate"] or 0) < EARLY_MAX)
    return out


EARLY_TURNS = 5          # R3's early-done window (analyze.EARLY_DONE_TURNS)


def gate_counts(runs_dir: str, rows) -> dict:
    """Amendment R4-SANITY-2's counters, per arm (runs without a gate count nothing):
    - `accepted_unresolved`: the gate accepted done, the scorer did not resolve the run (the gate's leniency);
    - `stuck_done`: runs ended by 3 rejected dones in a row with no applied edit;
    - `rejections`: rejected done calls, and the runs with at least one;
    - `early_done_attempts`: runs that called done (accepted or not) within 5 turns, with no applied edit before it."""
    out = {"accepted_unresolved": 0, "stuck_done": 0, "rejections": 0, "runs_rejected": 0, "early_done_attempts": 0,
           "gated_runs": 0}
    for r in rows:
        lp = os.path.join(runs_dir, r["run"] + ".jsonl")
        if r["outcome"] in ("missing", "error") or not os.path.exists(lp):
            continue
        turns = [json.loads(ln) for ln in open(lp)]
        gated = [t for t in turns if t.get("tool") == "done" and "gate" in t]
        if not gated:
            continue
        out["gated_runs"] += 1
        rej = sum(1 for t in gated if not t["gate"]["accepted"])
        out["rejections"] += rej
        out["runs_rejected"] += rej > 0
        out["stuck_done"] += r["outcome"] == "stuck_done"
        out["accepted_unresolved"] += any(t["gate"]["accepted"] for t in gated) and r["outcome"] != "resolved"
        edits = 0
        for t in turns:
            if t.get("tool") == "edit_file" and (t.get("output_head") or "").startswith(("edited", "created")):
                edits += 1
            if t.get("tool") == "done" and t.get("turn", 99) <= EARLY_TURNS and edits == 0:
                out["early_done_attempts"] += 1
                break
    return out


def report(runs_dir: str, tasks_path: str, seeds: list[int], layer1_pool: dict | None = None) -> dict:
    tasks = load_tasks([tasks_path])
    rows = load_runs(runs_dir, tasks, seeds)
    out = {"seeds": seeds, "families": {}}
    for fam in sorted({t["family"] for t in tasks}):
        ft = [t for t in tasks if t["family"] == fam]
        fr = [r for r in rows if r["family"] == fam]
        by = {v: [r for r in fr if r["version"] == v] for v in ("PRISM", "BASELINE")}
        bounded = sum(str(t.get("layer1", "")).startswith("bounded+clean") for t in ft)
        base_res = sum(r["outcome"] == "resolved" for r in by["BASELINE"])
        base_n = sum(r["outcome"] not in ("missing",) for r in by["BASELINE"])
        cal = base_res / len(ft) if ft else None
        prism_bad = [r["run"] for r in by["PRISM"] if r["outcome"] in ("error", "missing")]
        out["families"][fam] = {
            "tasks": len(ft),
            "layer1": {"sample_bounded_clean": bounded, "pass": bounded >= 0.8 * len(ft),
                       "pool": (layer1_pool or {}).get(fam)},
            "layer2": {"baseline_resolved": base_res, "baseline_runs": base_n, "rate": cal,
                       "pass": cal is not None and CALIBRATION[0] <= cal <= CALIBRATION[1]},
            "layer3": {v: layer3(by[v]) for v in by},
            "prism_clean": {"pass": not prism_bad, "bad_runs": prism_bad},
            "smoke": {v: {o: sum(r["outcome"] == o for r in by[v])
                          for o in ("resolved", "unresolved", "stuck", "budget", "stuck_done", "error", "missing")}
                      for v in by},
            "gate": {v: gate_counts(runs_dir, by[v]) for v in by},
        }
    return out


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def render(rep: dict) -> str:
    lines = ["| Family | L1 sample | L2 BASELINE resolved | L3 edit P/B | L3 loop P/B | L3 early P/B | L3 pass P/B | "
             "PRISM clean | smoke resolved P/B |", "|---|---|---|---|---|---|---|---|---|"]
    for fam, f in rep["families"].items():
        p, b = f["layer3"]["PRISM"], f["layer3"]["BASELINE"]
        ok = lambda x: "pass" if x else "FAIL"
        lines.append(
            f"| {fam} | {f['layer1']['sample_bounded_clean']}/{f['tasks']} {ok(f['layer1']['pass'])} "
            f"| {f['layer2']['baseline_resolved']}/{f['tasks']} {ok(f['layer2']['pass'])} "
            f"| {_pct(p['edit_rate'])}/{_pct(b['edit_rate'])} | {_pct(p['loop_rate'])}/{_pct(b['loop_rate'])} "
            f"| {_pct(p['early_done_rate'])}/{_pct(b['early_done_rate'])} | {ok(p['pass'])}/{ok(b['pass'])} "
            f"| {ok(f['prism_clean']['pass'])} | {f['smoke']['PRISM']['resolved']}/{f['smoke']['BASELINE']['resolved']} |")
    gated = [(fam, f) for fam, f in rep["families"].items() if any(g["gated_runs"] for g in f["gate"].values())]
    if gated:
        lines += ["", "| Family | Gate rejections P/B (runs) | stuck_done P/B | Early-done attempts P/B | "
                  "Gate accepted, scorer unresolved P/B |", "|---|---|---|---|---|"]
        for fam, f in gated:
            p, b = f["gate"]["PRISM"], f["gate"]["BASELINE"]
            lines.append(f"| {fam} | {p['rejections']} ({p['runs_rejected']}) / {b['rejections']} ({b['runs_rejected']}) | "
                         f"{p['stuck_done']} / {b['stuck_done']} | {p['early_done_attempts']} / {b['early_done_attempts']} | "
                         f"{p['accepted_unresolved']} / {b['accepted_unresolved']} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("runs")
    p.add_argument("tasks")
    p.add_argument("--seeds", type=int, nargs="+", default=[42])
    p.add_argument("--layer1-pool", help="JSON {family: {bounded_clean, gated}} from SANITY_SAMPLES")
    p.add_argument("--out")
    a = p.parse_args(argv)
    rep = report(a.runs, a.tasks, a.seeds, json.load(open(a.layer1_pool)) if a.layer1_pool else None)
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print(render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
