"""R3's analysis, fixed before any pilot or main-run answer is read (pre-registration, "Tests and decisions"; amendment
R3-1; BUILD_PLAN section 4).

    python -m harness.experiments.r3.analyze OUT.json --runs DIR --tasks TASKS_B.json [TASKS_N.json] \
        [--seeds 42 1001] [--pilot] [--gates GATE_DIR]

**Per run, one of five outcomes** (`runner.outcome`; BUILD_PLAN section 4, criterion 6):
- `resolved`: the agent called done, and the final tree passes every scored test;
- `unresolved`: it called done, and the tree does not pass (it tried and failed);
- `stuck`: the turn cap was reached without done;
- `budget`: the token budget ran out without done;
- `error`: the model call failed, or the harness died (`.error.json`).

A run with no result file at all is `missing`. Only `resolved` counts as resolved: runs that stop at the cap or the
budget count as unresolved (pre-registration). They are always reported apart, so "PRISM didn't help" is told apart
from "the agent got stuck". A diagnostic column, `tree_resolved`, scores the final tree whatever the stop. It never
enters a test.

**The primary table:** per family, version and seed (and both seeds pooled), the count of each outcome and the
resolved share.

**Tests, per family** (with both versions present):
- **Primary:** the per-task resolved share (the mean over seeds), PRISM vs baseline.
  - Wilcoxon signed-rank over tasks, and McNemar per seed;
  - Holm across families B and N (α = 0.05, two-sided);
  - bootstrap 95% interval of the mean per-task difference (PRISM minus baseline), 10,000 resamples, seed 5501.
- **Label:** `invalid`, `padding_better`, `not_shown`, `below_floor` or `pass`:
  - `invalid`: the versions' cap-hit rates (stuck or budget) differ by more than 20 points, or more than 20% of runs
    are `error` or `missing`;
  - otherwise, with the Holm-adjusted p and the interval, against the +0.10 floor.
- **Per seed:** every primary and secondary is also given for each seed. A family or secondary is flagged
  `unstable` when the PRISM-minus-baseline difference has opposite signs on the two seeds.
- **Scaling** (named secondary): Spearman's ρ between task depth and the per-task gap, one-sided (rising). The
  prediction holds when ρ > 0 with p < 0.05, and the deepest stratum's mean gap exceeds the shallowest's with a
  bootstrap interval above 0.
- **pass@2:** a task resolved by either seed's run. It is labelled a lower bound on the value of a retry.
- **Secondaries:** file F1, function F1, turns, tokens, `run_tests` calls, cap-hit rates per version, and the
  outcome split.
- **Family B interim look** (`interim_boundaries`): Lan-DeMets O'Brien-Fleming boundaries at any information
  fraction. Reproduces amendment R3-1's table at 0.5.

**`--pilot`** adds the pilot's pass criteria (BUILD_PLAN section 4) and the turn cap: the 90th percentile of turns
over all runs, both versions pooled, plus 5, rounded up, at most 30. It stays 30 when more than 20% of runs reach
30 turns. No result is read for a decision.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

import numpy as np
from scipy import stats as sps

from harness.experiments.suite import stats

VERSIONS = ("PRISM", "BASELINE")
OUTCOMES = ("resolved", "unresolved", "stuck", "budget", "error", "missing")
FLOOR = 0.10
ALPHA = 0.05
INVALID_GAP = 0.20
INVALID_DEATHS = 0.20
TURN_CAP_MAX = 30
TURN_CAP_BUFFER = 5
STRATA = {"B": ("1", "2-4", "5+"), "N": ("2", "3-4", "5+")}
# Family B's local α (amendment R3-9, 2026-11-08): Family N is dropped to R3b, so R3a's Holm family is Family B
# alone, and Holm over one hypothesis is α itself (0.05). Its sequential boundaries (R3-1 item 3, one interim look
# at 40 of 80 tasks): interim p < 0.0031 (z 2.96), final p < 0.0490 (z 1.97).
LOCAL_ALPHA = {"B": 0.05}
INTERIM_TASKS = {"B": 40}
PLANNED_TASKS = {"B": 80}


# ------------------------------------------------------------------------------------------------ loading
def load_tasks(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        data = json.load(open(p))
        out += data["tasks"] if isinstance(data, dict) else data
    return out


def load_runs(runs_dir: str, tasks: list[dict], seeds: list[int]) -> list[dict]:
    """One row per expected run (task × version × seed), present or not."""
    rows = []
    for t in tasks:
        for v in VERSIONS:
            for s in seeds:
                rid = f"{t['id']}__{v}__{s}"
                base = {"run": rid, "task": t["id"], "family": t["family"], "repo": t.get("repo"),
                        "language": t.get("language"), "depth": t.get("depth"), "stratum": t.get("stratum"),
                        "version": v, "seed": s}
                score_p, err_p = os.path.join(runs_dir, rid + ".score.json"), os.path.join(runs_dir, rid + ".error.json")
                if os.path.exists(score_p):
                    r = json.load(open(score_p))
                    from harness.experiments.r3.runner import outcome
                    sc = r.get("score") or {}
                    calls = r.get("tool_calls") or {}
                    rows.append({**base, "outcome": r.get("outcome") or outcome(r), "turns": r.get("turns"),
                                 "used_tokens": r.get("used_tokens"), "done_called": r.get("end") == "done",
                                 "tree_resolved": bool(sc.get("resolved")), "file_f1": sc.get("file_f1"),
                                 "function_f1": sc.get("function_f1"), "parse_errors": r.get("parse_errors", 0),
                                 "run_tests_calls": calls.get("run_tests", 0), "outputs_cut": r.get("outputs_cut", 0),
                                 "turns_stubbed": r.get("turns_stubbed", 0), "notice_turns": r.get("notice_turns", 0),
                                 **turn_flags(os.path.join(runs_dir, rid + ".jsonl"), r)})
                elif os.path.exists(err_p):
                    e = json.load(open(err_p))
                    # a run that died in the harness (no score file yet): retried by the next session, so it is
                    # not finished at an interim look (amendment R3-10); at the final analysis it counts as a death
                    rows.append({**base, "outcome": "error", "error_stage": e.get("stage"), "error": e.get("error"),
                                 "pending_retry": True})
                else:
                    rows.append({**base, "outcome": "missing"})
    return rows


# ------------------------------------------------------------------------------------------------ diagnostics
LOOP_REPEATS = 3          # the same tool with the same arguments this many times, no applied edit in between
EARLY_DONE_TURNS = 5      # done within this many turns, with no applied edit


def turn_flags(log_path: str, summary: dict) -> dict:
    """Per run, from its turn log (decided 2026-11-08, after the pilot; `PILOT_LOOP_INVESTIGATION.md`):
    - `looping`: some tool call (tool and arguments) made LOOP_REPEATS or more times with no applied edit between
      (an applied edit is an `edit_file` whose output starts "edited");
    - `early_done`: `done` within EARLY_DONE_TURNS turns with no applied edit;
    - `applied_edits`: the number of applied edits."""
    if not os.path.exists(log_path):
        return {"looping": None, "early_done": None, "applied_edits": None}
    seen, looping, edits = Counter(), False, 0
    for ln in open(log_path):
        t = json.loads(ln)
        if not t.get("tool"):
            continue
        if t["tool"] == "edit_file" and (t.get("output_head") or "").startswith("edited"):
            edits += 1
            seen.clear()
            continue
        key = (t["tool"], json.dumps(t.get("args") or {}, sort_keys=True))
        seen[key] += 1
        looping = looping or seen[key] >= LOOP_REPEATS
    early = summary.get("end") == "done" and (summary.get("turns") or 0) <= EARLY_DONE_TURNS and edits == 0
    return {"looping": looping, "early_done": early, "applied_edits": edits}


def diagnostics(rows: list[dict], covariates: dict | None) -> dict:
    """Looping and early-done runs per arm, and for each looping run whether BASELINE resolved the same task (same
    seed, and either seed). That tells "the loop is how PRISM fails" (BASELINE failed too) from "the loop suppresses
    PRISM's score" (BASELINE resolved it). With the covariate file: the missing-function split."""
    cov = (covariates or {}).get("tasks", {})
    resolved = {(r["task"], r["version"], r["seed"]) for r in rows if r["outcome"] == "resolved"}
    out = {"rule": {"looping": f"same tool and arguments {LOOP_REPEATS}+ times, no applied edit between",
                    "early_done": f"done within {EARLY_DONE_TURNS} turns, no applied edit"}, "by_version": {}}
    for v in VERSIONS:
        sel = [r for r in rows if r["version"] == v and r.get("looping") is not None]
        out["by_version"][v] = {"runs": len(sel), "looping": sum(r["looping"] for r in sel),
                                "early_done": sum(r["early_done"] for r in sel)}
    other = {"PRISM": "BASELINE", "BASELINE": "PRISM"}
    loops = []
    for r in rows:
        if not r.get("looping"):
            continue
        o = other[r["version"]]
        loops.append({"run": r["run"], "task": r["task"], "version": r["version"], "outcome": r["outcome"],
                      f"{o.lower()}_resolved_same_seed": (r["task"], o, r["seed"]) in resolved,
                      f"{o.lower()}_resolved_any_seed": any(k[0] == r["task"] and k[1] == o for k in resolved),
                      "missing_function": (cov.get(r["task"]) or {}).get("missing_function")})
    out["looping_runs"] = loops
    pl = [x for x in loops if x["version"] == "PRISM"]
    out["prism_loops_on_baseline_resolved_tasks"] = {
        "same_seed": sum(x["baseline_resolved_same_seed"] for x in pl),
        "both_failed_same_seed": sum(not x["baseline_resolved_same_seed"] for x in pl), "of": len(pl)}
    if cov:
        split = {}
        for flag in (True, False):
            for v in VERSIONS:
                sel = [r for r in rows if r["version"] == v and (cov.get(r["task"]) or {}).get("missing_function") is flag
                       and r["outcome"] != "missing"]
                split[f"{v}/missing_function={flag}"] = {
                    "runs": len(sel), "resolved": sum(r["outcome"] == "resolved" for r in sel),
                    "looping": sum(bool(r.get("looping")) for r in sel),
                    "early_done": sum(bool(r.get("early_done")) for r in sel)}
        out["missing_function_split"] = split
        out["missing_function_tasks"] = sum(1 for x in cov.values() if x.get("missing_function") is True)
    return out


# ------------------------------------------------------------------------------------------------ tables
def outcome_table(rows: list[dict], seeds: list[int]) -> dict:
    out = {}
    for fam in sorted({r["family"] for r in rows}):
        out[fam] = {}
        for v in VERSIONS:
            for s in [*seeds, "pooled"]:
                sel = [r for r in rows if r["family"] == fam and r["version"] == v and (s == "pooled" or r["seed"] == s)]
                c = Counter(r["outcome"] for r in sel)
                out[fam][f"{v}/{s}"] = {"runs": len(sel), **{o: c.get(o, 0) for o in OUTCOMES},
                                       "resolved_share": round(c.get("resolved", 0) / len(sel), 4) if sel else None,
                                       "tree_resolved_share": round(sum(r.get("tree_resolved", False) for r in sel)
                                                                    / len(sel), 4) if sel else None}
    return out


def _per_task(rows, fam, version, seeds, key=lambda r: r["outcome"] == "resolved") -> dict[str, float]:
    vals = defaultdict(list)
    for r in rows:
        if r["family"] == fam and r["version"] == version and r["seed"] in seeds:
            x = key(r)
            if x is not None:
                vals[r["task"]].append(float(x))
    return {t: sum(v) / len(v) for t, v in vals.items() if v}


def compare(rows, fam, seeds, key=lambda r: r["outcome"] == "resolved") -> dict:
    """PRISM vs baseline on per-task means of `key` over `seeds`: Wilcoxon and the bootstrap interval."""
    a, b = _per_task(rows, fam, "BASELINE", seeds, key), _per_task(rows, fam, "PRISM", seeds, key)
    tasks = sorted(set(a) & set(b))
    va, vb = [a[t] for t in tasks], [b[t] for t in tasks]
    if not tasks:
        return {"tasks": 0}
    w = stats.wilcoxon(va, vb)
    return {"tasks": len(tasks), "baseline": round(float(np.mean(va)), 4), "prism": round(float(np.mean(vb)), 4),
            "diff": round(float(np.mean(vb) - np.mean(va)), 4), "wilcoxon": w, "p": w["p"] if w else 1.0,
            "bootstrap": stats.bootstrap_diff(va, vb, tasks)}


def mcnemar_seed(rows, fam, seed) -> dict:
    by = defaultdict(dict)
    for r in rows:
        if r["family"] == fam and r["seed"] == seed:
            by[r["task"]][r["version"]] = r["outcome"] == "resolved"
    pairs = [(x["BASELINE"], x["PRISM"]) for x in by.values() if len(x) == 2]
    return {"tasks": len(pairs), **stats.mcnemar([p[0] for p in pairs], [p[1] for p in pairs])}


def cap_hit_rate(rows, fam, version) -> float | None:
    sel = [r for r in rows if r["family"] == fam and r["version"] == version and r["outcome"] not in ("missing",)]
    return round(sum(r["outcome"] in ("stuck", "budget") for r in sel) / len(sel), 4) if sel else None


def death_rate(rows, fam) -> float | None:
    sel = [r for r in rows if r["family"] == fam]
    return round(sum(r["outcome"] in ("error", "missing") for r in sel) / len(sel), 4) if sel else None


def label(primary: dict, p_holm: float, invalid: bool, threshold: float = ALPHA) -> str:
    if invalid:
        return "invalid"
    boot = primary.get("bootstrap") or {}
    significant = p_holm < threshold and (boot.get("low", 0) > 0 or boot.get("high", 0) < 0)
    if significant and primary["diff"] < 0:
        return "padding_better"
    if not significant:
        return "not_shown"
    if primary["diff"] < FLOOR:
        return "below_floor"
    return "pass"


def unstable(per_seed_diffs: list[float | None]) -> bool:
    d = [x for x in per_seed_diffs if x is not None]
    return len(d) >= 2 and min(d) < 0 < max(d)


def scaling(rows, fam, seeds, resamples=stats.RESAMPLES, seed=stats.SEED) -> dict:
    a, b = _per_task(rows, fam, "BASELINE", seeds), _per_task(rows, fam, "PRISM", seeds)
    depth = {r["task"]: r["depth"] for r in rows if r["family"] == fam}
    strat = {r["task"]: r["stratum"] for r in rows if r["family"] == fam}
    tasks = sorted(t for t in set(a) & set(b) if depth.get(t) is not None)
    gaps = {t: b[t] - a[t] for t in tasks}
    if len(tasks) < 3 or len({depth[t] for t in tasks}) < 2:
        return {"tasks": len(tasks), "rho": None, "p_one_sided": None, "holds": False}
    rho, p_two = sps.spearmanr([depth[t] for t in tasks], [gaps[t] for t in tasks])
    rho = float(rho) if not math.isnan(rho) else None
    p_one = (p_two / 2 if rho > 0 else 1 - p_two / 2) if rho is not None else None
    lo_s, hi_s = STRATA[fam][0], STRATA[fam][-1]
    low, high = [gaps[t] for t in tasks if strat[t] == lo_s], [gaps[t] for t in tasks if strat[t] == hi_s]
    deep = None
    if low and high:
        rng = random.Random(seed)
        diffs = sorted(np.mean([rng.choice(high) for _ in high]) - np.mean([rng.choice(low) for _ in low])
                       for _ in range(resamples))
        deep = {"shallow": lo_s, "deep": hi_s, "n_shallow": len(low), "n_deep": len(high),
                "diff": round(float(np.mean(high) - np.mean(low)), 4),
                "low": float(diffs[int(0.025 * resamples)]), "high": float(diffs[int(0.975 * resamples) - 1])}
    holds = bool(rho is not None and rho > 0 and p_one is not None and p_one < 0.05 and deep and deep["low"] > 0)
    by_stratum = {s: {"tasks": sum(1 for t in tasks if strat[t] == s),
                      "mean_gap": round(float(np.mean([gaps[t] for t in tasks if strat[t] == s])), 4)
                      if any(strat[t] == s for t in tasks) else None} for s in STRATA[fam]}
    return {"tasks": len(tasks), "rho": rho, "p_one_sided": p_one, "deepest_vs_shallowest": deep,
            "by_stratum": by_stratum, "holds": holds}


def pass_at_2(rows, fam, seeds) -> dict:
    def per(version):
        out = defaultdict(bool)
        for r in rows:
            if r["family"] == fam and r["version"] == version and r["seed"] in seeds:
                out[r["task"]] |= r["outcome"] == "resolved"
        return out
    a, b = per("BASELINE"), per("PRISM")
    tasks = sorted(set(a) & set(b))
    if not tasks:
        return {"tasks": 0}
    va, vb = [float(a[t]) for t in tasks], [float(b[t]) for t in tasks]
    return {"tasks": len(tasks), "baseline": round(float(np.mean(va)), 4), "prism": round(float(np.mean(vb)), 4),
            "bootstrap": stats.bootstrap_diff(va, vb, tasks),
            "note": "a lower bound on the value of a retry: two independent seeds are not a retry that sees the first "
                    "failure; never rescues a primary"}


SECONDARY = {
    "file_f1": lambda r: r.get("file_f1") if r["outcome"] not in ("error", "missing") else None,
    "function_f1": lambda r: r.get("function_f1") if r["outcome"] not in ("error", "missing") else None,
    "turns": lambda r: r.get("turns") if r["outcome"] not in ("error", "missing") else None,
    "used_tokens": lambda r: r.get("used_tokens") if r["outcome"] not in ("error", "missing") else None,
    "run_tests_calls": lambda r: r.get("run_tests_calls") if r["outcome"] not in ("error", "missing") else None,
    "stuck": lambda r: r["outcome"] == "stuck" if r["outcome"] != "missing" else None,
    "unresolved_after_done": lambda r: r["outcome"] == "unresolved" if r["outcome"] != "missing" else None,
}


def family_analysis(rows, fam, seeds) -> dict:
    prim = compare(rows, fam, seeds)
    per_seed = {s: {**compare(rows, fam, [s]), "mcnemar": mcnemar_seed(rows, fam, s)} for s in seeds}
    caps = {v: cap_hit_rate(rows, fam, v) for v in VERSIONS}
    deaths = death_rate(rows, fam)
    gap = abs(caps["PRISM"] - caps["BASELINE"]) if None not in caps.values() else None
    invalid_why = []
    if gap is not None and gap > INVALID_GAP:
        invalid_why.append(f"cap-hit rates differ by {gap:.2f} (> {INVALID_GAP})")
    if deaths is not None and deaths > INVALID_DEATHS:
        invalid_why.append(f"{deaths:.2f} of runs are error or missing (> {INVALID_DEATHS})")
    sec = {}
    for name, key in SECONDARY.items():
        c = compare(rows, fam, seeds, key)
        ps = {s: compare(rows, fam, [s], key).get("diff") for s in seeds}
        sec[name] = {**c, "per_seed_diff": ps, "unstable": unstable(list(ps.values()))}
    return {"primary": prim, "per_seed": per_seed,
            "unstable": unstable([per_seed[s].get("diff") for s in seeds]),
            "cap_hit_rate": caps, "death_rate": deaths, "invalid": invalid_why,
            "scaling": scaling(rows, fam, seeds), "pass_at_2": pass_at_2(rows, fam, seeds), "secondary": sec}


# ------------------------------------------------------------------------------------------------ interim
def interim_boundaries(fraction: float, alpha: float) -> dict:
    """Lan-DeMets O'Brien-Fleming-type two-sided boundaries for one interim look at `fraction` and the final look.

    The spending function is the two-sided O'Brien-Fleming type, alpha*(f) = 4 - 4 Φ(z_{α/4} / √f), the form that
    gives amendment R3-1's table (at f = 0.5: 0.0008 for α = 0.025 and 0.0031 for α = 0.05). The final boundary c2
    solves P(|Z1| < c1, |Z2| < c2) = 1 - α, with corr(Z1, Z2) = √f.
    """
    z = sps.norm.ppf(1 - alpha / 4)
    a1 = 4 - 4 * sps.norm.cdf(z / math.sqrt(fraction))
    c1 = sps.norm.ppf(1 - a1 / 2)
    rho = math.sqrt(fraction)
    mvn = sps.multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]])

    def inside(c2):   # P(|Z1| < c1, |Z2| < c2)
        return (mvn.cdf([c1, c2]) - mvn.cdf([-c1, c2]) - mvn.cdf([c1, -c2]) + mvn.cdf([-c1, -c2]))

    lo, hi = 1.0, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if inside(mid) < 1 - alpha:
            lo = mid
        else:
            hi = mid
    c2 = (lo + hi) / 2
    return {"fraction": fraction, "alpha": alpha, "interim_z": round(float(c1), 2), "interim_p": round(float(a1), 4),
            "final_z": round(float(c2), 2), "final_p": round(float(2 * (1 - sps.norm.cdf(c2))), 4)}


def threshold(fam: str) -> float:
    """The final rejection threshold: the group-sequential final boundary for a family with an interim look (Family
    B), else its local α."""
    alpha = LOCAL_ALPHA.get(fam, ALPHA)
    if fam in INTERIM_TASKS:
        return interim_boundaries(INTERIM_TASKS[fam] / PLANNED_TASKS[fam], alpha)["final_p"]
    return alpha


def interim_look(rows, tasks, fam: str, seeds) -> dict:
    """Amendment R3-1 item 3 (α from R3-9): the first INTERIM_TASKS tasks of the frozen order, every run finished
    with a real result (R3-10: a harness error awaiting its retry is not finished).
    Stop, in either direction, if the Wilcoxon p on per-task means is below the interim boundary; the label then
    comes from the usual table at that boundary. The `invalid` checks run at the look. No futility stop."""
    order = [t["id"] for t in tasks if t["family"] == fam][:INTERIM_TASKS[fam]]
    sel = [r for r in rows if r["family"] == fam and r["task"] in set(order)]
    # finished = a real result; a missing run or a harness error awaiting its retry is not (amendment R3-10)
    missing = sorted({r["task"] for r in sel if r["outcome"] == "missing" or r.get("pending_retry")})
    if missing or len(order) < INTERIM_TASKS[fam]:
        return {"ready": False, "tasks_missing_runs": missing}
    b = interim_boundaries(len(order) / PLANNED_TASKS[fam], LOCAL_ALPHA.get(fam, ALPHA))
    prim = compare(sel, fam, seeds)
    z = float(sps.norm.ppf(1 - prim["p"] / 2)) if prim.get("p", 1.0) < 1 else 0.0
    fa = family_analysis(sel, fam, seeds)
    stop = prim["p"] < b["interim_p"]
    lab = "invalid" if fa["invalid"] else (label(prim, prim["p"], False, b["interim_p"]) if stop else "continue")
    return {"ready": True, "tasks": len(order), "boundaries": b, "primary": prim, "z": round(z, 2),
            "stop": stop or bool(fa["invalid"]), "label": lab, "invalid": fa["invalid"],
            "unstable": fa["unstable"], "per_seed": fa["per_seed"]}


# ------------------------------------------------------------------------------------------------ pilot
def turn_cap(rows) -> dict:
    turns = [r["turns"] for r in rows if r.get("turns") is not None]
    if not turns:
        return {"runs": 0, "cap": None}
    at_30 = sum(t >= TURN_CAP_MAX for t in turns) / len(turns)
    p90 = float(np.percentile(turns, 90))
    cap = TURN_CAP_MAX if at_30 > 0.20 else min(TURN_CAP_MAX, math.ceil(p90 + TURN_CAP_BUFFER))
    return {"runs": len(turns), "p90": p90, "share_at_30": round(at_30, 4), "cap": cap,
            "rule": "p90 of turns (both versions pooled) + 5, rounded up, at most 30; 30 when more than 20% of runs "
                    "reach 30 turns (amendment R3-1, item 2; R3-2, item 3)"}


def pilot_criteria(rows, tasks, gates_dir: str | None, runs_dir: str) -> dict:
    out = {}
    bad = [r for r in rows if r["outcome"] in ("error", "missing")]
    out["1_no_harness_error"] = {"pass": not bad, "error_or_missing": [(r["run"], r["outcome"], r.get("error_stage"))
                                                                       for r in bad]}
    parse = {}
    for v in VERSIONS:
        sel = [r for r in rows if r["version"] == v and r.get("turns")]
        t, e = sum(r["turns"] for r in sel), sum(r.get("parse_errors", 0) for r in sel)
        parse[v] = {"turns": t, "parse_errors": e, "valid_share": round(1 - e / t, 4) if t else None}
    out["2_valid_tool_calls"] = parse
    tests_run = {}
    for lang in sorted({r["language"] for r in rows if r.get("language")}):
        sel = [r for r in rows if r.get("language") == lang and r.get("turns")]
        ok_calls = 0
        for r in sel:
            p = os.path.join(runs_dir, r["run"] + ".jsonl")
            if os.path.exists(p):
                for ln in open(p):
                    t = json.loads(ln)
                    if t.get("tool") == "run_tests" and " passed, " in (t.get("output_head") or ""):
                        ok_calls += 1
        tests_run[lang] = {"runs": len(sel), "runs_calling_run_tests": sum(1 for r in sel if r.get("run_tests_calls")),
                           "run_tests_calls_with_results": ok_calls}
    out["3_tests_found_and_run"] = {"pass": bool(tests_run) and all(v["run_tests_calls_with_results"] > 0
                                                                    for v in tests_run.values()), "by_language": tests_run}
    out["4_truncation_and_caps"] = {v: {"outputs_cut": sum(r.get("outputs_cut", 0) for r in rows if r["version"] == v),
                                        "turns_stubbed": sum(r.get("turns_stubbed", 0) for r in rows if r["version"] == v),
                                        "stuck": sum(r["outcome"] == "stuck" for r in rows if r["version"] == v),
                                        "budget": sum(r["outcome"] == "budget" for r in rows if r["version"] == v)}
                                    for v in VERSIONS}
    checks = [(t["id"], (t.get("scoring_check") or {})) for t in tasks]
    out["5_scoring_path"] = {"pass": all(c.get("gold") is True and c.get("empty") is False for _i, c in checks),
                             "tasks": len(checks), "failing": [i for i, c in checks
                                                               if not (c.get("gold") is True and c.get("empty") is False)]}
    out["6_done_vs_stuck"] = {v: dict(Counter(r["outcome"] for r in rows if r["version"] == v)) for v in VERSIONS}
    # flagged 2026-11-08 (the R3a dry run's gold scripts hit the cap; one edit per hunk): if pilot runs also reach
    # the cap without done, the cap is recalibrated before R3a; if not, it stays
    hit = [r for r in rows if r["outcome"] == "stuck"
           or ((r.get("turns") or 0) >= TURN_CAP_MAX and r.get("turns") is not None and not r.get("done_called"))]
    out["7_runs_hit_30_turn_cap_without_done"] = {
        "runs": len(hit), "of": len(rows), "share": round(len(hit) / len(rows), 4) if rows else None,
        "by_version": {v: sorted(r["run"] for r in hit if r["version"] == v) for v in VERSIONS}}
    return out


def analyze(runs_dir: str, task_paths: list[str], seeds: list[int], pilot: bool, gates_dir: str | None,
            covariates_path: str | None = None, interim: bool = False) -> dict:
    tasks = load_tasks(task_paths)
    rows = load_runs(runs_dir, tasks, seeds)
    fams = sorted({t["family"] for t in tasks})
    res = {"seeds": seeds, "tasks": {f: sum(t["family"] == f for t in tasks) for f in fams},
           "outcomes": outcome_table(rows, seeds), "families": {}}
    for f in fams:
        res["families"][f] = family_analysis(rows, f, seeds)
    pv = {f: res["families"][f]["primary"].get("p", 1.0) for f in fams if res["families"][f]["primary"].get("tasks")}
    holm = stats.holm(pv) if pv else {}
    for f in fams:
        e = res["families"][f]
        e["p_holm"] = holm.get(f)
        e["threshold"] = threshold(f)
        e["label"] = label(e["primary"], holm[f], bool(e["invalid"]), e["threshold"]) if f in holm else None
    if interim:
        res["interim"] = {f: interim_look(rows, tasks, f, seeds) for f in fams if f in INTERIM_TASKS}
    res["diagnostics"] = diagnostics(rows, json.load(open(covariates_path)) if covariates_path else None)
    if pilot:
        res["pilot"] = {"criteria": pilot_criteria(rows, tasks, gates_dir, runs_dir), "turn_cap": turn_cap(rows),
                        "note": "harness checks only; no result is read for a decision"}
    res["runs"] = rows
    return res


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("--runs", required=True)
    p.add_argument("--tasks", nargs="+", required=True)
    p.add_argument("--seeds", nargs="+", type=int, default=[42, 1001])
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--gates", default=None)
    p.add_argument("--covariates", default=None, help="covariates.py's output for the task list")
    p.add_argument("--interim", action="store_true", help="the interim look (amendment R3-1 item 3)")
    a = p.parse_args(argv)
    res = analyze(a.runs, a.tasks, a.seeds, a.pilot, a.gates, a.covariates, a.interim)
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1, default=str)
    brief = {"outcomes": res["outcomes"], "labels": {f: e["label"] for f, e in res["families"].items()},
             "diagnostics": {k: v for k, v in res["diagnostics"].items() if k != "looping_runs"}}
    if a.pilot:
        brief["pilot"] = res["pilot"]
    if a.interim:
        brief["interim"] = {f: {k: v for k, v in x.items() if k != "per_seed"} for f, x in res["interim"].items()}
    print(json.dumps(brief, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
