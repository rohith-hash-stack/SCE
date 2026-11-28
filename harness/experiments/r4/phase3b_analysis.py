"""Phase 3b's pre-registered analysis (AMENDMENTS "Phase 3b", sections 4–6). Run after the runs; reads only their
turn logs and score files, the task file, the answer keys and the repositories.

    python -B -m harness.experiments.r4.phase3b_analysis 'RESULTS_GLOB' TASKS.json --clones DIR [--out OUT.json]

**Target places:** the start-side lines the answer key changes that contain the old name. These equal the task's
recorded `places` plus the definition's line (checked in `phase3b/ADMISSION.md`).

**Reconstruction:**
- each target file is rebuilt from the task's start state;
- the run's applied `edit_file` calls are replayed in order, with the tool's semantics;
- each place is followed through the edits by a line diff.

**Trigger** (per place): the first `edit_file` call refused as "not found" (whitespace-only or not) whose error names
the place's current line ("at line N" / "from line N"). A refused call that names no line, or a line that is not a
place, is counted as `unlocated`.

**Recovery:** within the next 5 model turns after the trigger, the place's line holds the new name, and no more uses
of the old name than the answer key's line keeps.

**Mechanism** (the call that recovered it), first that applies:
1. `automatic_tool_correction`: never online (no arm has the edit fix);
2. `gateway_correction`: an `exact_fix` note (arm C) for that place was shown after the trigger, and the call's `old`
   and `new` are its text exactly;
3. `model_after_refusal`: a single-line edit by the model;
4. `other`: a multi-line edit or a rewrite.

**Places with no trigger** are prevention outcomes (`no_mismatch`), recorded with their final state.

**Run-level failure:** at least one triggered place not recovered within 5 turns.

**The single test:** on p3-hidden-15, PRISM_GW (A) against PRISM_GW_E (E), run-level failure, two-sided Fisher exact
test. Within an arm and task, runs whose sequences of (tool, arguments) are identical count once. The deduplicated
test is the result; the undeduplicated one is a sensitivity check. Everything else is descriptive.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from math import comb

from harness.experiments.r4.phase3b_edit_replay import Repo, apply_edit

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW_TURNS = 5
PRIMARY = "p3-hidden-15"
TEST = ("PRISM_GW", "PRISM_GW_E")
GUIDANCE = {"suggest_tool", "worklist", "stall", "wrong_target", "search_augment", "block_lookalike", "block_syntax",
            "block_done"}


def fisher_two_sided(a: int, n1: int, b: int, n2: int) -> float:
    """Two-sided Fisher exact p for failures a of n1 against b of n2 (sum of tables no more likely than observed)."""
    k, n = a + b, n1 + n2
    p = lambda x: comb(n1, x) * comb(n2, k - x) / comb(n, k)
    obs = p(a)
    return min(1.0, sum(p(x) for x in range(max(0, k - n2), min(n1, k) + 1) if p(x) <= obs * (1 + 1e-9)))


def _line_map(a: list[str], b: list[str]) -> dict[int, int | None]:
    from prism.history import line_map
    return line_map(a, b)


def target_places(task: dict) -> tuple[str, str, list[dict]]:
    """(old name, new name, places) from the prompt and the answer key: each place is its file, start line, and the
    number of old-name uses the answer key's line keeps."""
    old = task["derived"]["old"]
    new = task["derived"]["new"]
    patch = open(os.path.join(HERE, "phase3", "gold", f"{task['id']}.patch")).read()
    pat = re.compile(rf"\b{re.escape(old)}\b")
    places, f, ln, minus, plus = [], None, None, [], []

    def flush():
        for i, (start, text) in enumerate(minus):
            if pat.search(text):
                keep = len(pat.findall(plus[i])) if len(plus) == len(minus) else 0
                places.append({"file": f, "start": start, "keep": keep})
        minus.clear()
        plus.clear()
    for line in patch.splitlines():
        if line.startswith("--- a/"):
            flush()
            f = line[6:]
            continue
        if line.startswith(("+++", "diff ", "index ")):
            continue
        m = re.match(r"@@ -(\d+)", line)
        if m:
            flush()
            ln = int(m.group(1))
            continue
        if ln is None:
            continue
        if line.startswith("-"):
            if plus:
                flush()
            minus.append((ln, line[1:]))
            ln += 1
        elif line.startswith("+"):
            plus.append(line[1:])
        else:
            flush()
            ln += 1
    flush()
    return old, new, places


def analyse_run(task: dict, recs: list[dict], repo: Repo) -> dict:
    old, new, places = target_places(task)
    pat_old, pat_new = re.compile(rf"\b{re.escape(old)}\b"), re.compile(rf"\b{re.escape(new)}\b")
    files = sorted({p["file"] for p in places})
    text = {f: repo.start_text(task, f) for f in files}
    pos = {(p["file"], p["start"]): p["start"] for p in places}       # each place's current line
    state = {(p["file"], p["start"]): {"trigger": None, "recovered": None, "mechanism": None} for p in places}
    keep = {(p["file"], p["start"]): p["keep"] for p in places}
    fixes: list[dict] = []
    unlocated, edits, failed, ws, loops = 0, 0, 0, 0, 0
    done_at: dict[tuple, int] = {}

    def is_done(k) -> bool:
        f = k[0]
        line = pos[k]
        if line is None or text[f] is None:
            return False
        lines = text[f].split("\n")
        if not 0 < line <= len(lines):
            return False
        s = lines[line - 1]
        return bool(pat_new.search(s)) and len(pat_old.findall(s)) <= keep[k]

    for r in recs:
        if "tool" not in r:
            continue
        turn, tool, args = r["turn"], r["tool"], r.get("args") or {}
        out = r.get("output") or r.get("output_head") or ""
        for iv in r.get("gateway") or []:
            if iv.get("type") == "exact_fix":
                fixes.append({"turn": turn, **{k: iv.get(k) for k in ("places", "fix_old", "fix_new")}})
        if tool != "edit_file":
            continue
        edits += 1
        f = str(args.get("path", "")).replace("\\", "/").removeprefix("./")
        if out.startswith("not answered again"):
            loops += 1
        if out.startswith(("edited", "created")):
            if f in text:
                before = text[f]
                after = apply_edit(before, str(args.get("old", "")), str(args.get("new", "")), args.get("occurrence"))
                if after is not None:
                    m = _line_map((before or "").split("\n"), after.split("\n"))
                    for k in pos:
                        if k[0] == f and pos[k] is not None:
                            pos[k] = m.get(pos[k], pos[k])
                    text[f] = after
                for k, st in state.items():
                    if k[0] == f and k not in done_at and is_done(k):
                        done_at[k] = turn
                        if st["trigger"] is not None and st["recovered"] is None and \
                                turn - st["trigger"] <= WINDOW_TURNS:
                            st["recovered"] = turn
                            fix = next((x for x in fixes if x["turn"] >= st["trigger"] and
                                        f"{k[0]}:{pos[k]}" in (x["places"] or []) or
                                        x["turn"] >= st["trigger"] and x["fix_old"] == str(args.get("old", ""))
                                        .rstrip("\n")), None)
                            o, n = str(args.get("old", "")).rstrip("\n"), str(args.get("new", "")).rstrip("\n")
                            if fix and fix["fix_old"] == o and fix["fix_new"] == n:
                                st["mechanism"] = "gateway_correction"
                            elif "\n" not in o and "\n" not in n:
                                st["mechanism"] = "model_after_refusal"
                            else:
                                st["mechanism"] = "other"
            continue
        if out.startswith("error") or out.startswith("not answered again"):
            failed += 1
        if not out.startswith("error") or "not found" not in out:
            continue
        if "apart from whitespace" in out:
            ws += 1
        m = re.search(r"\b(?:at|from) line (\d+)", out)
        hit = None
        if m:
            n_ = int(m.group(1))
            hit = next((k for k in pos if k[0] == f and pos[k] == n_), None)
        if hit is None:
            unlocated += 1
            continue
        if state[hit]["trigger"] is None and hit not in done_at:
            state[hit]["trigger"] = turn
    per_place = []
    for k, st in state.items():
        final = is_done(k)
        if st["trigger"] is None:
            outcome = "no_mismatch"
        elif st["recovered"] is not None:
            outcome = "recovered"
        else:
            outcome = "not_recovered"
        per_place.append({"place": f"{k[0]}:{k[1]}", "outcome": outcome, "trigger_turn": st["trigger"],
                          "recovered_turn": st["recovered"], "mechanism": st["mechanism"], "done_at_end": final,
                          "turns_to_recovery": (st["recovered"] - st["trigger"]) if st["recovered"] else None})
    return {"failed": any(p["outcome"] == "not_recovered" for p in per_place), "places": per_place,
            "triggers": sum(1 for p in per_place if p["trigger_turn"] is not None), "unlocated": unlocated,
            "edits": edits, "failed_edits": failed, "whitespace_mismatches": ws, "loop_refusals": loops,
            "exact_fix_notes": len(fixes)}


def _signature(recs: list[dict]) -> str:
    return json.dumps([[r.get("tool"), r.get("args")] for r in recs if "tool" in r], sort_keys=True)


def analyse(results_glob: str, tasks_path: str, clones: str) -> dict:
    doc = json.load(open(tasks_path))
    tasks = {t["id"]: t for t in doc["tasks"]}
    repos: dict[str, Repo] = {}
    runs = []
    for path in sorted(glob.glob(os.path.join(results_glob, "*.jsonl")) if os.path.isdir(results_glob)
                       else glob.glob(results_glob)):
        name = os.path.basename(path)[:-6]
        if name.count("__") != 2:
            continue
        tid, arm, seed = name.split("__")
        if tid not in tasks:
            continue
        task = tasks[tid]
        if task["repo"] not in repos:
            repos[task["repo"]] = Repo(clones, task["repo"], doc["repo_urls"][task["repo"]])
        recs = [json.loads(ln) for ln in open(path)]
        res = analyse_run(task, recs, repos[task["repo"]])
        score = path[:-6] + ".score.json"
        s = json.load(open(score)) if os.path.exists(score) else {}
        gw = s.get("gateway") or {}
        warn = {"problem_description": sum(1 for iv in gw.get("log", []) if iv.get("type") in GUIDANCE),
                "actionable_correction": sum(1 for iv in gw.get("log", []) if iv.get("type") == "exact_fix")}
        responses = {}
        for iv in gw.get("log", []):
            if iv.get("type") == "exact_fix":
                responses[iv.get("response") or "none"] = responses.get(iv.get("response") or "none", 0) + 1
        runs.append({"run": name, "task": tid, "arm": arm, "seed": int(seed), "signature": _signature(recs),
                     "resolved": (s.get("score") or {}).get("resolved"), "turns": s.get("turns", len(recs)),
                     "tokens": s.get("used_tokens"), "warnings": warn, "exact_fix_responses": responses, **res})
    groups: dict[tuple, list] = {}
    for r in runs:
        groups.setdefault((r["task"], r["arm"]), []).append(r)
    dedup = {}
    for k, rs in groups.items():
        seen: dict[str, dict] = {}
        for r in sorted(rs, key=lambda x: x["seed"]):
            seen.setdefault(r["signature"], r)
        dedup[k] = list(seen.values())

    def test(sets):
        a, e = sets.get((PRIMARY, TEST[0]), []), sets.get((PRIMARY, TEST[1]), [])
        fa, fe = sum(r["failed"] for r in a), sum(r["failed"] for r in e)
        return {"A": {"n": len(a), "failed": fa}, "E": {"n": len(e), "failed": fe},
                "p_two_sided_fisher": fisher_two_sided(fa, len(a), fe, len(e)) if a and e else None}
    desc = {}
    for (tid, arm), rs in sorted(groups.items()):
        n = len(rs)
        pl = [p for r in rs for p in r["places"]]
        tr = [p for p in pl if p["trigger_turn"] is not None]
        desc[f"{tid}|{arm}"] = {
            "runs": n, "distinct_transcripts": len(dedup[(tid, arm)]),
            "failed_runs": sum(r["failed"] for r in rs), "resolved": sum(bool(r["resolved"]) for r in rs),
            "places": len(pl), "no_mismatch_places": sum(1 for p in pl if p["outcome"] == "no_mismatch"),
            "triggers": len(tr), "recovered": sum(1 for p in tr if p["outcome"] == "recovered"),
            "mechanisms": {m: sum(1 for p in tr if p["mechanism"] == m)
                           for m in ("automatic_tool_correction", "gateway_correction", "model_after_refusal",
                                     "other")},
            "turns_to_recovery": sorted(p["turns_to_recovery"] for p in tr if p["turns_to_recovery"] is not None),
            "triggers_per_run": len(tr) / n, "triggers_per_100_edits": 100 * len(tr) / max(1, sum(r["edits"] for r in rs)),
            "whitespace_mismatches_per_100_edits": 100 * sum(r["whitespace_mismatches"] for r in rs) /
            max(1, sum(r["edits"] for r in rs)),
            "failed_edits_per_run": sum(r["failed_edits"] for r in rs) / n,
            "loop_refusals_per_run": sum(r["loop_refusals"] for r in rs) / n,
            "unlocated_per_run": sum(r["unlocated"] for r in rs) / n,
            "mean_turns": sum(r["turns"] for r in rs) / n,
            "mean_tokens": (sum(r["tokens"] or 0 for r in rs) / n) if any(r["tokens"] for r in rs) else None,
            "warnings": {k: sum(r["warnings"][k] for r in rs) for k in ("problem_description",
                                                                         "actionable_correction")},
            "exact_fix_responses": {k: sum(r["exact_fix_responses"].get(k, 0) for r in rs)
                                    for k in ("exact", "different", "none")}}
    return {"design": "phase3b", "primary_test": {"deduplicated": test(dedup), "undeduplicated": test(groups),
                                                   "rule": "two-sided Fisher exact, A against E, p3-hidden-15, "
                                                           "run-level failure; alpha 0.05"},
            "descriptive": desc,
            "runs": [{k: v for k, v in r.items() if k != "signature"} for r in runs]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("results")
    p.add_argument("tasks")
    p.add_argument("--clones", required=True)
    p.add_argument("--out")
    a = p.parse_args(argv)
    rep = analyse(a.results, a.tasks, a.clones)
    text = json.dumps(rep, indent=1)
    if a.out:
        open(a.out, "w").write(text)
    print(json.dumps({"primary_test": rep["primary_test"], "descriptive": rep["descriptive"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
