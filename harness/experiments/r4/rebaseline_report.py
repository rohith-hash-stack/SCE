"""The Phase 1 re-baseline's analysis (AMENDMENTS.md, "Phase 1 re-baseline"), fixed before the run.

    python -B -m harness.experiments.r4.rebaseline_report RESULTS_DIR TASKS.json [--seeds 42 ...] [--out report.json]
    python -B -m harness.experiments.r4.rebaseline_report RESULTS_DIR TASKS.json --health --seeds 42   # wave check

**Primary metric** (amendment "Phase 1: the primary metric changes", approved 2026-11-15): each run's score in [0, 1],
1 exactly when the run is resolved:
- feature task: (fail-to-pass passing / fail-to-pass) x (pass-to-pass passing / pass-to-pass);
- delete task: 0 if the target is still defined, else (pass-to-pass passing / pass-to-pass).
A test counts as passing only with the scorer's status `passed` (strict lists: pre-existing failures stay in).

**Primary analysis:** per task, the mean score over its seeds in each arm; the paired difference d_t = PRISM - BASELINE
over the 20 tasks. Reported: the mean difference with a 95% bootstrap interval (tasks resampled, 10,000 draws, fixed
seed), the Wilcoxon signed-rank test (two-sided, zero differences dropped) and the paired t-test, and how many tasks
favour each arm. The pre-registered test is the Wilcoxon signed-rank test at alpha 0.05.

**Secondary:** solve rate per arm (all runs; tasks solved at least once); McNemar's exact test on the task-seed pairs;
the score with pre-existing failures set aside (delete-01: 1 test; delete-02: 57 tests); per family.

**Process:** infrastructure deaths, parse errors per turn, endings (done / turn cap / budget / stuck), tokens per run,
PRISM-output share of the prompt, compaction counts, failed edits per turn.

`--health`: the check the cell runs after its first wave (amendment: stop if infrastructure deaths > 2 of the wave's
runs or parse errors >= 2% of its turns). Exit code 0 = continue, 4 = stop.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics as st
import sys

SETASIDE_SOURCES = {          # amendment "Phase 0 scoring": the unchanged-run evidence the secondary lists come from
    "r4s-delete-01": ["reports/r4/sanity/r4s-delete-01__BASELINE__42.score.json",
                      "reports/r4/sanity/r4s-delete-01__PRISM__42.score.json",
                      "reports/r4/sanity2/r4s-delete-01__BASELINE__42.score.json",
                      "reports/r4/sanity2/r4s-delete-01__PRISM__42.score.json"],
    "r4s-delete-02": ["reports/r4/sanity2/r4s-delete-02__PRISM__42.score.json",
                      "reports/r4/sanity3/r4s-delete-02__BASELINE__42.score.json"],
}


def ids(groups: dict) -> set[str]:
    return {f"{f}::{t}" for f, ts in (groups or {}).items() for t in ts}


def run_score(task: dict, score: dict, aside: set[str] = frozenset()) -> float:
    tests = score.get("tests") or {}
    if not score.get("hidden_patch_applied") or not tests:
        return 0.0
    p2p, f2p = ids(task.get("pass_to_pass")) - set(aside), ids(task.get("fail_to_pass"))
    share = lambda s: sum(tests.get(k) == "passed" for k in s) / len(s) if s else 1.0
    if task.get("family") == "delete":
        return 0.0 if not score.get("target_absent") else share(p2p)
    return share(f2p) * share(p2p)


def set_aside(root: str, tasks: dict) -> dict[str, set[str]]:
    out = {}
    for tid, files in SETASIDE_SOURCES.items():
        if tid not in tasks:
            continue
        p2p, sets = ids(tasks[tid]["pass_to_pass"]), []
        for f in files:
            path = os.path.join(root, f)
            if os.path.exists(path):
                tests = json.load(open(path))["score"].get("tests") or {}
                sets.append({k for k, v in tests.items() if k in p2p and v != "passed"})
        if sets:
            out[tid] = set.intersection(*sets)
    return out


def load_runs(results: str, tasks: dict, seeds: list[int] | None) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(results, "r4s-*__*__*.score.json"))):
        s = json.load(open(path))
        if s["task"] not in tasks or (seeds and s["seed"] not in seeds):
            continue
        log = path.replace(".score.json", ".jsonl")
        turns = [json.loads(ln) for ln in open(log)] if os.path.exists(log) else []
        rows.append({"run": s["run"], "task": s["task"], "arm": s["version"], "seed": s["seed"],
                     "family": tasks[s["task"]]["family"], "end": s.get("end"), "outcome": s.get("outcome"),
                     "score_doc": s.get("score") or {}, "turns": s.get("turns", 0), "used_tokens": s.get("used_tokens"),
                     "parse_errors": s.get("parse_errors", 0),
                     "infra": str(s.get("end") or "").startswith("model_error") or s.get("outcome") == "error",
                     "failed_edits": sum(1 for t in turns if t.get("tool") == "edit_file"
                                         and not str(t.get("output_head", "")).startswith(("edited", "created"))),
                     "prism_output_tokens": sum(t.get("output_tokens") or 0 for t in turns
                                                if str(t.get("tool") or "").startswith("prism_"))})
    return rows


def bootstrap_ci(diffs: list[float], draws: int = 10_000, seed: int = 20261116) -> tuple[float, float]:
    rnd = random.Random(seed)
    means = sorted(st.mean(rnd.choice(diffs) for _ in diffs) for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def analyse(results: str, tasks_path: str, seeds: list[int] | None = None, root: str = ".") -> dict:
    doc = json.load(open(tasks_path))
    tasks = {t["id"]: t for t in doc["tasks"]}
    aside = set_aside(root, tasks)
    rows = load_runs(results, tasks, seeds)
    for r in rows:
        t = tasks[r["task"]]
        r["score"] = run_score(t, r["score_doc"])
        r["score_setaside"] = run_score(t, r["score_doc"], aside.get(r["task"], set()))
        r["solved"] = bool(r["score_doc"].get("resolved"))
    per_task = {}
    for tid in sorted(tasks):
        cell = {}
        for arm in ("PRISM", "BASELINE"):
            rs = [r for r in rows if r["task"] == tid and r["arm"] == arm]
            if rs:
                cell[arm] = {"n": len(rs), "score": st.mean(r["score"] for r in rs),
                             "score_setaside": st.mean(r["score_setaside"] for r in rs),
                             "solves": sum(r["solved"] for r in rs)}
        if len(cell) == 2:
            cell["diff"] = cell["PRISM"]["score"] - cell["BASELINE"]["score"]
            cell["diff_setaside"] = cell["PRISM"]["score_setaside"] - cell["BASELINE"]["score_setaside"]
        per_task[tid] = cell
    paired = [c for c in per_task.values() if "diff" in c]
    rep = {"runs": len(rows), "tasks_paired": len(paired), "seeds": sorted({r["seed"] for r in rows}),
           "per_task": per_task}
    if len(paired) >= 2:
        d = [c["diff"] for c in paired]
        from scipy import stats
        nz = [x for x in d if abs(x) > 1e-12]
        wil = stats.wilcoxon(nz, alternative="two-sided") if len(nz) >= 2 else None
        tt = stats.ttest_1samp(d, 0.0)
        lo, hi = bootstrap_ci(d)
        rep["primary"] = {
            "mean_score": {arm: st.mean(c[arm]["score"] for c in paired) for arm in ("PRISM", "BASELINE")},
            "mean_diff": st.mean(d), "ci95_bootstrap": [lo, hi],
            "wilcoxon_p": float(wil.pvalue) if wil else None, "nonzero_diffs": len(nz),
            "paired_t_p": float(tt.pvalue),
            "tasks_favouring": {"PRISM": sum(x > 1e-12 for x in d), "BASELINE": sum(x < -1e-12 for x in d),
                                "tie": sum(abs(x) <= 1e-12 for x in d)}}
        ds = [c["diff_setaside"] for c in paired]
        rep["secondary_setaside"] = {"mean_diff": st.mean(ds), "ci95_bootstrap": list(bootstrap_ci(ds))}
        pairs = {}
        for r in rows:
            pairs.setdefault((r["task"], r["seed"]), {})[r["arm"]] = r["solved"]
        b = sum(1 for v in pairs.values() if v.get("PRISM") and v.get("BASELINE") is False)
        c = sum(1 for v in pairs.values() if v.get("BASELINE") and v.get("PRISM") is False)
        rep["secondary_solves"] = {
            arm: {"runs_solved": sum(r["solved"] for r in rows if r["arm"] == arm),
                  "runs": sum(1 for r in rows if r["arm"] == arm),
                  "tasks_solved_once": sum(1 for c_ in paired if c_[arm]["solves"] > 0)}
            for arm in ("PRISM", "BASELINE")}
        rep["secondary_solves"]["mcnemar_exact_p"] = (float(stats.binomtest(b, b + c, 0.5).pvalue)
                                                      if b + c else None)
        rep["secondary_solves"]["discordant"] = {"PRISM_only": b, "BASELINE_only": c}
        rep["by_family"] = {fam: {arm: st.mean(r["score"] for r in rows if r["family"] == fam and r["arm"] == arm)
                                  for arm in ("PRISM", "BASELINE")
                                  if any(r["family"] == fam and r["arm"] == arm for r in rows)}
                            for fam in sorted({r["family"] for r in rows})}
    rep["process"] = process(rows)
    return rep


def process(rows: list[dict]) -> dict:
    out = {}
    for arm in ("PRISM", "BASELINE"):
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        turns = sum(r["turns"] for r in rs) or 1
        ends = {}
        for r in rs:
            ends[r["end"] if not str(r["end"]).startswith("model_error") else "model_error"] = \
                ends.get(r["end"] if not str(r["end"]).startswith("model_error") else "model_error", 0) + 1
        out[arm] = {"runs": len(rs), "infra_deaths": sum(r["infra"] for r in rs), "turns": turns,
                    "parse_error_rate": sum(r["parse_errors"] for r in rs) / turns, "ends": ends,
                    "mean_tokens": st.mean(r["used_tokens"] or 0 for r in rs),
                    "failed_edits_per_turn": sum(r["failed_edits"] for r in rs) / turns,
                    "prism_output_tokens_per_run": st.mean(r["prism_output_tokens"] for r in rs)}
    return out


def health(results: str, tasks_path: str, seeds: list[int]) -> tuple[bool, dict]:
    doc = json.load(open(tasks_path))
    rows = load_runs(results, {t["id"]: t for t in doc["tasks"]}, seeds)
    turns = sum(r["turns"] for r in rows) or 1
    deaths = sum(r["infra"] for r in rows)
    rate = sum(r["parse_errors"] for r in rows) / turns
    ok = deaths <= 2 and rate < 0.02
    return ok, {"runs": len(rows), "infra_deaths": deaths, "parse_error_rate": round(rate, 4), "continue": ok}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("results")
    p.add_argument("tasks")
    p.add_argument("--seeds", type=int, nargs="+", default=None)
    p.add_argument("--root", default=".", help="the repository root (for the set-aside evidence)")
    p.add_argument("--health", action="store_true")
    p.add_argument("--out")
    a = p.parse_args(argv)
    if a.health:
        ok, info = health(a.results, a.tasks, a.seeds or [])
        print(json.dumps(info))
        return 0 if ok else 4
    rep = analyse(a.results, a.tasks, a.seeds, a.root)
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1, default=float)
    print(json.dumps({k: v for k, v in rep.items() if k != "per_task"}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
