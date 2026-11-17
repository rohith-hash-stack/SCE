"""The Phase 1 re-baseline's analysis (AMENDMENTS.md, "Phase 1 re-baseline"), fixed before the run.

    python -B -m harness.experiments.r4.rebaseline_report RESULTS_DIR TASKS.json [--seeds 42 ...] [--out report.json]
    python -B -m harness.experiments.r4.rebaseline_report RESULTS_DIR TASKS.json --health --seeds 42   # wave check

**Primary metric** (amendment "Phase 1: the primary metric changes", approved 2026-11-15): each run's score in [0, 1],
1 exactly when the run is resolved:
- feature task: (fail-to-pass passing / fail-to-pass) x (pass-to-pass passing / pass-to-pass);
- delete task: 0 if the target is still defined, else (pass-to-pass passing / pass-to-pass).
A test counts as passing only with the scorer's status `passed` (strict lists: pre-existing failures stay in).

**Primary analysis (3 arms: BASELINE, PRISM = PRISM tools available, INJECTED = PRISM context injected up front and
the same PRISM tools):**
per task, the mean score over its seeds in each arm; two paired comparisons over the 20 tasks, each against BASELINE:
d_t = PRISM - BASELINE ("does the tool help?") and d_t = INJECTED - BASELINE ("does injected context help?"), Holm-
corrected over the two. INJECTED - PRISM is reported descriptively. Reported: the mean difference with a 95% bootstrap interval (tasks resampled, 10,000 draws, fixed
seed), the Wilcoxon signed-rank test (two-sided, zero differences dropped) and the paired t-test, and how many tasks
favour each arm. The pre-registered test is the Wilcoxon signed-rank test at alpha 0.05.

**Secondary:** solve rate per arm (all runs; tasks solved at least once); McNemar's exact test on the task-seed pairs;
the score with pre-existing failures set aside (delete-01: 1 test; delete-02: 57 tests); per family.

**Process:** infrastructure deaths, parse errors per turn, endings (done / turn cap / budget / stuck), tokens per run,
PRISM-output share of the prompt, compaction counts, failed edits per turn.

**Turn efficiency** (user, 2026-11-16; descriptive, no test): per run, the turns by kind (PRISM calls; exploration:
`read_file`, `grep`, `list_dir`, `related_code`; edits applied and failed; test runs; parse errors), the turn of the
first applied edit, the turns spent between a PRISM call and the next applied edit, model seconds per turn and per run,
and the mean prompt size. Reported per arm, per task (paired PRISM - BASELINE), and for solved runs only (turns and
model seconds to the end of a solved run).

**Post-PRISM behaviour** (user, 2026-11-16, added after the re-baseline was launched, before any of its results were
read; descriptive, no test): for each PRISM call, the next action: an edit (PRISM's context used directly), another
PRISM call, a `grep` for the same symbol, a `read_file` of the file PRISM named (a "post-PRISM search"), or something
else. Also: the turn of the first PRISM call, the exploration turns before it, and the runs that never call PRISM.
Reported for both arms with PRISM tools (`post_prism_by_arm`: PRISM and INJECTED; `post_prism` stays PRISM's).
Limit: the turn log keeps the first 300 characters of each output, which name the target symbol and its file but not
the callers listed further down, so "the same thing PRISM already answered" is judged for the target only.

`--health`: the check the cell runs after its first wave (amendment: stop if infrastructure deaths > 2 of the wave's
runs or parse errors >= 2% of its turns). Exit code 0 = continue, 4 = stop.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
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


EXPLORE = ("read_file", "grep", "list_dir", "related_code")


def efficiency(turns: list[dict]) -> dict:
    """Turn efficiency of one run, from its turn log."""
    kinds = {"prism": 0, "explore": 0, "edit_applied": 0, "edit_failed": 0, "test": 0, "parse_error": 0, "other": 0}
    first_edit, after_prism, pending = None, 0, False
    for t in turns:
        tool = str(t.get("tool") or "")
        applied = tool == "edit_file" and str(t.get("output_head", "")).startswith(("edited", "created"))
        if t.get("parse_error"):
            kinds["parse_error"] += 1
        elif tool.startswith("prism_"):
            kinds["prism"] += 1
        elif tool in EXPLORE:
            kinds["explore"] += 1
        elif tool == "edit_file":
            kinds["edit_applied" if applied else "edit_failed"] += 1
        elif tool == "run_tests":
            kinds["test"] += 1
        else:
            kinds["other"] += 1
        if pending and not tool.startswith("prism_"):
            after_prism += 1
        if tool.startswith("prism_"):
            pending = True
        if applied:
            pending = False
            first_edit = first_edit or t.get("turn")
    n = len(turns) or 1
    secs = sum(t.get("model_seconds") or 0 for t in turns)
    return {"turns_by_kind": kinds, "first_edit_turn": first_edit, "turns_after_prism_before_edit": after_prism,
            "model_seconds": secs, "model_seconds_per_turn": secs / n,
            "mean_prompt_tokens": sum(t.get("prompt_tokens") or 0 for t in turns) / n}


_PRISM_FILE = re.compile(r"\n(\S+\.\w+), lines (\d+)-(\d+)")


def post_prism(turns: list[dict]) -> dict:
    """What follows each PRISM call (see the module docstring)."""
    nxt = {"edit": 0, "prism": 0, "grep_same_symbol": 0, "read_named_file": 0, "other": 0, "end": 0}
    first, before = None, 0
    for i, t in enumerate(turns):
        tool = str(t.get("tool") or "")
        if not tool.startswith("prism_"):
            if first is None and tool in ("read_file", "grep", "list_dir"):
                before += 1
            continue
        first = first or t.get("turn")
        a = t.get("args") or {}
        sym = a.get("symbol") or (a.get("symbols") or [""])[0] or a.get("name") or ""
        short = sym.split(".")[-1] if sym else ""
        m = _PRISM_FILE.search(str(t.get("output_head") or ""))
        named = m.group(1) if m else a.get("file")
        n = turns[i + 1] if i + 1 < len(turns) else None
        nt, na = (str(n.get("tool") or ""), n.get("args") or {}) if n else ("", {})
        if n is None:
            nxt["end"] += 1
        elif nt == "edit_file":
            nxt["edit"] += 1
        elif nt.startswith("prism_"):
            nxt["prism"] += 1
        elif nt == "grep" and short and short in str(na.get("pattern", "")):
            nxt["grep_same_symbol"] += 1
        elif nt == "read_file" and named and na.get("path") == named:
            nxt["read_named_file"] += 1
        else:
            nxt["other"] += 1
    return {"prism_calls": sum(nxt.values()), "next_action": nxt, "first_prism_turn": first,
            "exploration_before_first_prism": before if first else None}


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
                                                if str(t.get("tool") or "").startswith("prism_")),
                     "efficiency": efficiency(turns), "post_prism": post_prism(turns)})
    return rows


def bootstrap_ci(diffs: list[float], draws: int = 10_000, seed: int = 20261116) -> tuple[float, float]:
    rnd = random.Random(seed)
    means = sorted(st.mean(rnd.choice(diffs) for _ in diffs) for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


ARMS = ("BASELINE", "PRISM", "INJECTED")
# The pre-registered comparisons (3-arm design, AMENDMENTS "Phase 1 re-baseline, 3 arms"): each arm against BASELINE,
# Holm-corrected over the two. INJECTED against PRISM is reported descriptively.
COMPARISONS = (("PRISM", "BASELINE"), ("INJECTED", "BASELINE"))
DESCRIPTIVE = (("INJECTED", "PRISM"),)


def compare(rows: list[dict], per_task: dict, arm: str, ref: str) -> dict | None:
    """The paired comparison `arm` - `ref` over tasks (see the module docstring)."""
    paired = [c for c in per_task.values() if arm in c and ref in c]
    if len(paired) < 2:
        return None
    from scipy import stats
    d = [c[arm]["score"] - c[ref]["score"] for c in paired]
    nz = [x for x in d if abs(x) > 1e-12]
    wil = stats.wilcoxon(nz, alternative="two-sided") if len(nz) >= 2 else None
    tt = stats.ttest_1samp(d, 0.0)
    lo, hi = bootstrap_ci(d)
    ds = [c[arm]["score_setaside"] - c[ref]["score_setaside"] for c in paired]
    pairs = {}
    for r in rows:
        if r["arm"] in (arm, ref):
            pairs.setdefault((r["task"], r["seed"]), {})[r["arm"]] = r["solved"]
    b = sum(1 for v in pairs.values() if v.get(arm) and v.get(ref) is False)
    c_ = sum(1 for v in pairs.values() if v.get(ref) and v.get(arm) is False)
    return {"tasks": len(paired),
            "mean_score": {arm: st.mean(c[arm]["score"] for c in paired), ref: st.mean(c[ref]["score"] for c in paired)},
            "mean_diff": st.mean(d), "ci95_bootstrap": [lo, hi],
            "wilcoxon_p": float(wil.pvalue) if wil else None, "nonzero_diffs": len(nz), "paired_t_p": float(tt.pvalue),
            "tasks_favouring": {arm: sum(x > 1e-12 for x in d), ref: sum(x < -1e-12 for x in d),
                                "tie": sum(abs(x) <= 1e-12 for x in d)},
            "setaside": {"mean_diff": st.mean(ds), "ci95_bootstrap": list(bootstrap_ci(ds))},
            "solves": {"mcnemar_exact_p": float(stats.binomtest(b, b + c_, 0.5).pvalue) if b + c_ else None,
                       "discordant": {f"{arm}_only": b, f"{ref}_only": c_}}}


def holm(pvalues: dict) -> dict:
    """Holm's step-down adjustment over the named p-values (None stays None)."""
    named = sorted(((p, k) for k, p in pvalues.items() if p is not None))
    out, run = {k: None for k in pvalues}, 0.0
    for i, (p, k) in enumerate(named):
        run = max(run, min(1.0, (len(named) - i) * p))
        out[k] = run
    return out


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
    arms = [a for a in ARMS if any(r["arm"] == a for r in rows)]
    per_task = {}
    for tid in sorted(tasks):
        cell = {}
        for arm in arms:
            rs = [r for r in rows if r["task"] == tid and r["arm"] == arm]
            if rs:
                cell[arm] = {"n": len(rs), "score": st.mean(r["score"] for r in rs),
                             "score_setaside": st.mean(r["score_setaside"] for r in rs),
                             "solves": sum(r["solved"] for r in rs)}
        per_task[tid] = cell
    rep = {"runs": len(rows), "arms": arms, "seeds": sorted({r["seed"] for r in rows}), "per_task": per_task}
    comps = {f"{a} vs {b}": compare(rows, per_task, a, b) for a, b in COMPARISONS if a in arms and b in arms}
    adj = holm({k: (v or {}).get("wilcoxon_p") for k, v in comps.items()})
    for k, v in comps.items():
        if v:
            v["wilcoxon_p_holm"] = adj[k]
    rep["primary"] = comps
    rep["descriptive"] = {f"{a} vs {b}": compare(rows, per_task, a, b) for a, b in DESCRIPTIVE
                          if a in arms and b in arms}
    rep["solve_rates"] = {arm: {"runs_solved": sum(r["solved"] for r in rows if r["arm"] == arm),
                                "runs": sum(1 for r in rows if r["arm"] == arm),
                                "tasks_solved_once": sum(1 for c in per_task.values() if c.get(arm, {}).get("solves"))}
                          for arm in arms}
    rep["by_family"] = {fam: {arm: st.mean(r["score"] for r in rows if r["family"] == fam and r["arm"] == arm)
                              for arm in arms if any(r["family"] == fam and r["arm"] == arm for r in rows)}
                        for fam in sorted({r["family"] for r in rows})}
    rep["injection"] = injection_summary(results, rows)
    rep["process"] = process(rows, arms)
    rep["turn_efficiency"] = turn_efficiency(rows, per_task, arms)
    return rep


def injection_summary(results: str, rows: list[dict]) -> dict | None:
    """What Arm 3 was given: the retrieval used, errors and fallbacks, and its size."""
    recs = []
    for r in rows:
        if r["arm"] != "INJECTED":
            continue
        s = json.load(open(os.path.join(results, r["run"] + ".score.json")))
        if s.get("injected"):
            recs.append(s["injected"])
    if not recs:
        return None
    return {"runs": len(recs), "tools": {t: sum(1 for x in recs if x["tool"] == t) for t in {x["tool"] for x in recs}},
            "fallbacks": sum(1 for x in recs if x.get("fallback_from")), "errors": sum(1 for x in recs if x.get("error")),
            "mean_tokens": st.mean(x.get("tokens") or 0 for x in recs)}


def turn_efficiency(rows: list[dict], per_task: dict, arms) -> dict:
    """Descriptive (no test): where the turns and the model time go, per arm, per task, and in solved runs."""
    def summary(rs):
        if not rs:
            return None
        e = [r["efficiency"] for r in rs]
        kinds = {k: st.mean(x["turns_by_kind"][k] for x in e) for k in e[0]["turns_by_kind"]}
        fe = [x["first_edit_turn"] for x in e if x["first_edit_turn"]]
        return {"runs": len(rs), "turns": st.mean(r["turns"] for r in rs), "turns_by_kind": kinds,
                "first_edit_turn": st.mean(fe) if fe else None, "runs_without_an_edit": len(e) - len(fe),
                "turns_after_prism_before_edit": st.mean(x["turns_after_prism_before_edit"] for x in e),
                "model_seconds": st.mean(x["model_seconds"] for x in e),
                "model_seconds_per_turn": st.mean(x["model_seconds_per_turn"] for x in e),
                "mean_prompt_tokens": st.mean(x["mean_prompt_tokens"] for x in e)}
    out = {"per_arm": {arm: summary([r for r in rows if r["arm"] == arm]) for arm in arms},
           "solved_runs": {arm: summary([r for r in rows if r["arm"] == arm and r["solved"]]) for arm in arms}}
    paired = {}
    for arm, ref in COMPARISONS:
        if arm not in arms or ref not in arms:
            continue
        per = {}
        for tid in per_task:
            a = [r for r in rows if r["task"] == tid and r["arm"] == arm]
            b = [r for r in rows if r["task"] == tid and r["arm"] == ref]
            if a and b:
                per[tid] = {"turns_diff": st.mean(r["turns"] for r in a) - st.mean(r["turns"] for r in b),
                            "model_seconds_diff": st.mean(r["efficiency"]["model_seconds"] for r in a)
                            - st.mean(r["efficiency"]["model_seconds"] for r in b)}
        if per:
            paired[f"{arm} vs {ref}"] = {"per_task": per,
                                         "mean_turns_diff": st.mean(v["turns_diff"] for v in per.values()),
                                         "mean_model_seconds_diff": st.mean(v["model_seconds_diff"] for v in per.values())}
    out["paired"] = paired
    for arm in ("PRISM", "INJECTED"):                     # both arms have the PRISM tools
        s = _post_prism_summary([r["post_prism"] for r in rows if r["arm"] == arm])
        if s:
            out.setdefault("post_prism_by_arm", {})[arm] = s
            if arm == "PRISM":
                out["post_prism"] = s
    return out


def _post_prism_summary(pr: list[dict]) -> dict | None:
    if pr:
        calls = sum(x["prism_calls"] for x in pr)
        nxt = {k: sum(x["next_action"][k] for x in pr) for k in pr[0]["next_action"]}
        firsts = [x["first_prism_turn"] for x in pr if x["first_prism_turn"]]
        return {
            "prism_calls": calls, "next_action": nxt,
            "post_prism_search_share": (nxt["grep_same_symbol"] + nxt["read_named_file"]) / calls if calls else None,
            "edit_next_share": nxt["edit"] / calls if calls else None,
            "runs_never_calling_prism": sum(1 for x in pr if not x["first_prism_turn"]), "runs": len(pr),
            "first_prism_turn": st.mean(firsts) if firsts else None,
            "exploration_before_first_prism": st.mean(x["exploration_before_first_prism"] for x in pr
                                                      if x["first_prism_turn"]) if firsts else None}
    return None


def process(rows: list[dict], arms=ARMS) -> dict:
    out = {}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        turns = sum(r["turns"] for r in rs) or 1
        ends = {}
        for r in rs:
            k = "model_error" if str(r["end"]).startswith("model_error") else r["end"]
            ends[k] = ends.get(k, 0) + 1
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
