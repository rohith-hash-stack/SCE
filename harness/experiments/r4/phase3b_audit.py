"""Phase 3b's evidence audit (2026-11-28): an independent re-derivation of the 80 runs' results from the raw
artifacts. It is separate from the experiment's own analysis (`phase3b_analysis.py`) and shares no code with it.

    python -B -m harness.experiments.r4.phase3b_audit RESULTS_GLOB TASKS.json --clones DIR --analysis JSON --out DIR

**Read:** the score files and turn logs; the task file; the repositories at the task commits; and, for comparison
only, the experiment's analysis output.

**Not read:** the answer key (`phase3/gold/*.patch`) and the hidden tests. The target places are the task's recorded
`places` plus the definition's line, found in the source.

**Reimplemented from the amendment's text** (sections 4–5), with its own edit application and line tracking:
- triggers;
- recovery within 5 model turns;
- run-level failure;
- the two-sided Fisher test.

**Checks of its own:**
- every applied edit is re-applied, and its line is checked against the line the tool reported ("edited F at line
  N");
- whether any experiment artifact could have reached the model.

Writes `ledger.csv`, `ledger.json` and `audit.json` to `--out`. Changes nothing else.
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import glob
import json
import os
import re
import subprocess
import sys
from math import comb

WINDOW = 5
PRIMARY = "p3-hidden-15"
LINE_181 = "SessionMiddleware[ClientSideSessionBackend]:"


def fisher(a: int, n1: int, b: int, n2: int) -> float:
    k, n = a + b, n1 + n2
    pr = lambda x: comb(n1, x) * comb(n2, k - x) / comb(n, k)
    obs = pr(a)
    return min(1.0, sum(pr(x) for x in range(max(0, k - n2), min(n1, k) + 1) if pr(x) <= obs * (1 + 1e-9)))


def git_show(clone: str, commit: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", clone, "show", f"{commit}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def map_lines(before: list[str], after: list[str]) -> dict[int, int | None]:
    """1-based line in `before` -> its line in `after` (None if replaced or deleted)."""
    out: dict[int, int | None] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        for i in range(i1, i2):
            if tag == "equal":
                out[i + 1] = j1 + (i - i1) + 1
            elif tag == "replace" and i2 - i1 == j2 - j1:
                out[i + 1] = j1 + (i - i1) + 1           # a line changed in place keeps its position
            else:
                out[i + 1] = None
    return out


def apply(text: str, old: str, new: str, occurrence) -> tuple[str, int] | None:
    """The edit as applied: (new text, 1-based line of the match), or None if it cannot be re-applied."""
    hits, i = [], text.find(old) if old else -1
    while i != -1:
        hits.append(i)
        i = text.find(old, i + 1)
    if not hits:
        return None
    k = (int(occurrence) - 1) if occurrence else 0
    if not 0 <= k < len(hits):
        return None
    j = hits[k]
    return text[:j] + new + text[j + len(old):], line_of(text, j)


class Task:
    def __init__(self, t: dict, clone: str):
        self.t = t
        self.id = t["id"]
        self.old, self.new = t["derived"]["old"], t["derived"]["new"]
        self.p_old = re.compile(rf"\b{re.escape(self.old)}\b")
        self.p_new = re.compile(rf"\b{re.escape(self.new)}\b")
        self.clone, self.commit = clone, t["commit"]
        uses = collections.Counter((p["file"], p["line"]) for p in t["places"])
        dfile = t["derived"]["file"]
        src = git_show(clone, self.commit, dfile).split("\n")
        # the definition's line: the def/class of the old name nearest the recorded target line
        cands = [i + 1 for i, s in enumerate(src) if re.match(rf"\s*(?:async\s+)?(?:def|class)\s+{re.escape(self.old)}\b", s)]
        self.def_line = min(cands, key=lambda n: abs(n - t["target_line"]))
        uses[(dfile, self.def_line)] += 1
        self.places = {}
        self.start = {}
        for (f, ln), n in sorted(uses.items()):
            if f not in self.start:
                self.start[f] = git_show(clone, self.commit, f)
            line = self.start[f].split("\n")[ln - 1]
            self.places[(f, ln)] = {"text": line, "keep": max(0, len(self.p_old.findall(line)) - n)}


def audit_run(task: Task, recs: list[dict]) -> dict:
    text = dict(task.start)
    pos = {k: k[1] for k in task.places}
    st = {k: {"trigger": None, "recovered": None, "done_at": None, "mech": None} for k in task.places}
    notes: list[dict] = []
    blocks: list[dict] = []
    ev = collections.Counter()
    edits_log, mismatches, l181 = [], [], []

    def done(k) -> bool:
        ln = pos[k]
        if ln is None:
            return False
        lines = text[k[0]].split("\n")
        if not 0 < ln <= len(lines):
            return False
        s = lines[ln - 1]
        return bool(task.p_new.search(s)) and len(task.p_old.findall(s)) <= task.places[k]["keep"]

    for r in recs:
        if "tool" not in r:
            ev["parse_errors"] += 1
            continue
        turn, tool, args, out = r["turn"], r["tool"], r.get("args") or {}, r.get("output_head") or ""
        ev[f"tool:{tool}"] += 1
        for iv in r.get("gateway") or []:
            ev[f"gw:{iv.get('type')}"] += 1
            if str(iv.get("type", "")).startswith("block_"):
                blocks.append({"turn": turn, "type": iv.get("type"), "tool": tool,
                               "pending_now": sum(1 for k2, s2 in st.items() if s2["done_at"] is None)})
            if iv.get("type") == "exact_fix":
                notes.append({"turn": turn, "places": iv.get("places") or [], "old": iv.get("fix_old"),
                              "new": iv.get("fix_new"), "refusal": iv.get("refusal")})
        if tool != "edit_file":
            continue
        f = str(args.get("path", "")).replace("\\", "/").removeprefix("./")
        old, new = str(args.get("old", "")), str(args.get("new", ""))
        kind = ("applied" if out.startswith(("edited", "created")) else "loop_refused" if out.startswith(
            "not answered again") else "refused_ws" if out.startswith("error") and "apart from whitespace" in out
            else "refused_not_found" if out.startswith("error") and "not found" in out
            else "refused_other" if out.startswith("error") else "other")
        ev[f"edit:{kind}"] += 1
        if LINE_181 in old:
            first = next((ln for ln in old.split("\n") if LINE_181 in ln), "")
            l181.append({"turn": turn, "kind": kind, "lead": first[:len(first) - len(first.lstrip())],
                         "out": out[:160]})
        rec = {"turn": turn, "file": f, "kind": kind, "old": old, "new": new}
        if kind == "applied" and f in text:
            res = apply(text[f], old, new, args.get("occurrence"))
            m = re.match(r"edited \S+ at line (\d+)", out)
            if res is None or (m and int(m.group(1)) != res[1]):
                mismatches.append({"turn": turn, "file": f, "reported": m and int(m.group(1)),
                                   "reapplied": res and res[1]})
            if res is not None:
                before, after = text[f].split("\n"), res[0].split("\n")
                mp = map_lines(before, after)
                for k in pos:
                    if k[0] == f and pos[k] is not None:
                        pos[k] = mp.get(pos[k], pos[k])
                text[f] = res[0]
                # an unintended change: an applied edit that changes a line without the old name
                changed = [b for b in difflib.ndiff(before, after) if b[:2] in ("- ", "+ ")]
                rec["touches_other_lines"] = any(b.startswith("- ") and not task.p_old.search(b[2:])
                                                 and b[2:].strip() for b in changed)
                for k, s in st.items():
                    if k[0] == f and s["done_at"] is None and done(k):
                        s["done_at"] = turn
                        if s["trigger"] is not None and s["recovered"] is None and turn - s["trigger"] <= WINDOW:
                            s["recovered"] = turn
                            shown = [n for n in notes if s["trigger"] <= n["turn"] < turn]
                            exact = any(n["old"] == old.rstrip("\n") and n["new"] == new.rstrip("\n") for n in shown)
                            s["mech"] = ("gateway_correction" if exact else "model_after_refusal"
                                         if "\n" not in old.rstrip("\n") else "other")
                            s["note_shown"] = bool(shown)
        edits_log.append(rec)
        if kind in ("refused_ws", "refused_not_found"):
            m = re.search(r"\b(?:at|from) line (\d+)", out)
            hit = next((k for k in pos if m and k[0] == f and pos[k] == int(m.group(1))), None) if m else None
            if hit is None:
                ev["unlocated"] += 1
            elif st[hit]["trigger"] is None and st[hit]["done_at"] is None:
                st[hit]["trigger"] = turn
    places = []
    for k, s in st.items():
        oc = "no_mismatch" if s["trigger"] is None else "recovered" if s["recovered"] else "not_recovered"
        places.append({"place": f"{k[0]}:{k[1]}", "outcome": oc, "trigger": s["trigger"], "recovered": s["recovered"],
                       "mechanism": s["mech"], "note_shown": s.get("note_shown"), "done_at": s["done_at"],
                       "done_at_end": done(k)})
    # C's notes: what the model's next edit did with each
    follow = []
    for n in notes:
        nxt = next((e for e in edits_log if e["turn"] > n["turn"]), None)
        if nxt is None:
            resp = "no_later_edit"
        elif nxt["old"].rstrip("\n") == n["old"] and nxt["new"].rstrip("\n") == n["new"]:
            resp = "exact_applied" if nxt["kind"] == "applied" else f"exact_{nxt['kind']}"
        elif nxt["old"] == "" or n["old"] is None:
            resp = "other"
        else:
            resp = "different_text"
        follow.append({"turn": n["turn"], "response": resp, "next_turn": nxt and nxt["turn"],
                       "gap": nxt and nxt["turn"] - n["turn"], "repeat": None})
    seen = set()
    for n, fl in zip(notes, follow):
        key = (n["old"], n["new"])
        fl["repeat"] = key in seen
        seen.add(key)
    return {"places": places, "failed": any(p["outcome"] == "not_recovered" for p in places), "events": dict(ev),
            "reapply_mismatches": mismatches, "line181": l181, "blocks": blocks, "notes": follow,
            "unintended_applied_edits": sum(1 for e in edits_log if e.get("touches_other_lines")),
            "applied_edits": sum(1 for e in edits_log if e["kind"] == "applied")}


def reference_rows_indented(recs: list[dict]) -> tuple[int, int]:
    """(rows, rows whose source text keeps leading whitespace) in prism_find_references answers (first 300 chars)."""
    rows = ind = 0
    for r in recs:
        if r.get("tool") != "prism_find_references":
            continue
        for ln in (r.get("output_head") or "").split("\n")[1:-1]:       # the last line may be cut mid-row
            m = re.match(r"\s*\S+:\d+(?::\d+)?(?: \[[^\]]*\])?: (.*)$", ln)
            if m:
                rows += 1
                ind += m.group(1)[:1] in (" ", "\t")
    return rows, ind


LEAK_TERMS = ("hidden_patch", "gold_patch", "fail_to_pass", "pass_to_pass", "phase3/gold", ".score.json",
              "scoring_check", "answer key")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("results")
    p.add_argument("tasks")
    p.add_argument("--clones", required=True)
    p.add_argument("--analysis", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    doc = json.load(open(a.tasks))
    tasks = {t["id"]: Task(t, os.path.join(a.clones, t["repo"])) for t in doc["tasks"]}
    plan = {(t, s, v): k for k, runs in doc["shards"].items() for t, s, v in runs}
    exp = {r["run"]: r for r in json.load(open(a.analysis))["runs"]}
    ledger, audit_runs, problems = [], {}, []
    seen_ids = collections.Counter()
    for path in sorted(glob.glob(a.results)):
        name = os.path.basename(path)[:-len(".jsonl")]
        if name.count("__") != 2:
            continue
        tid, arm, seed = name.split("__")
        shard = os.path.basename(os.path.dirname(path)).replace("phase3b_shard", "")
        s = json.load(open(path[:-6] + ".score.json"))
        seen_ids[name] += 1
        if (s["task"], s["version"], str(s["seed"])) != (tid, arm, seed):
            problems.append(f"{name}: score file says {s['task']}/{s['version']}/{s['seed']}")
        if plan.get((tid, int(seed), arm)) != shard:
            problems.append(f"{name}: in shard {shard}, planned for {plan.get((tid, int(seed), arm))}")
        recs = [json.loads(ln) for ln in open(path)]
        res = audit_run(tasks[tid], recs)
        res["ref_rows"] = reference_rows_indented(recs)
        res["prompt_sha_turn1"] = recs[0].get("prompt_sha") if recs else None
        res["signature"] = json.dumps([[r.get("tool"), r.get("args")] for r in recs if "tool" in r], sort_keys=True)
        leaks = sorted({term for r in recs for term in LEAK_TERMS
                        if term in (r.get("output_head") or "") or term in (r.get("reply") or "")})
        res["leak_terms"] = leaks
        sc = s.get("score") or {}
        gw = s.get("gateway") or {}
        res.update({"resolved": bool(sc.get("resolved")), "end": s.get("end"), "outcome": s.get("outcome"),
                    "turns": s.get("turns"), "tokens": s.get("used_tokens"), "seconds": s.get("seconds"),
                    "tool_calls": s.get("tool_calls"), "gateway_summary": {k: gw.get(k) for k in (
                        "places", "lookalikes", "interventions", "prevented_tool_calls", "prevented_edits", "ignored",
                        "followed", "by_type", "pending_at_end")} if gw else None,
                    "review": {k: (s.get("review") or {}).get(k) for k in ("new_undefined_names", "new_syntax_errors")},
                    "turn_count_logged": len(recs)})
        e = exp.get(name)
        if e is None:
            problems.append(f"{name}: not in the experiment's analysis output")
        else:
            res["matches_experiment"] = {
                "failed": e["failed"] == res["failed"], "resolved": bool(e["resolved"]) == res["resolved"],
                "place_outcomes": sorted((p["place"], p["outcome"]) for p in e["places"])
                == sorted((p["place"], p["outcome"]) for p in res["places"])}
        audit_runs[name] = {"task": tid, "arm": arm, "seed": int(seed), "shard": shard, **res}
        bad = [p for p in res["places"] if p["outcome"] == "not_recovered"]
        reason = ("resolved" if res["resolved"] and not res["failed"] else
                  f"unrecovered trigger at {', '.join(p['place'] for p in bad)}" + (
                      f"; ended {res['end']}" if not res["resolved"] else "; resolved anyway")
                  if bad else f"not resolved; no unrecovered trigger; ended {res['end']}")
        ledger.append({"task": tid, "seed": int(seed), "arm": arm, "run_id": name, "shard": shard,
                       "resolved": res["resolved"], "run_failed": res["failed"], "turns": res["turns"],
                       "tokens": res["tokens"], "outcome_reason": reason})
    for n, c in seen_ids.items():
        if c > 1:
            problems.append(f"{n}: {c} copies")
    missing = [f"{t}__{v}__{s}" for (t, s, v) in plan if f"{t}__{v}__{s}" not in audit_runs]
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ledger.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ledger[0]))
        w.writeheader()
        for row in sorted(ledger, key=lambda r: (r["task"] != PRIMARY, r["task"], r["seed"], r["arm"])):
            w.writerow(row)
    json.dump(sorted(ledger, key=lambda r: (r["task"], r["seed"], r["arm"])), open(os.path.join(a.out, "ledger.json"), "w"),
              indent=1)
    json.dump({"problems": problems, "missing": missing, "runs": audit_runs}, open(os.path.join(a.out, "audit.json"), "w"),
              indent=1, default=str)
    print(json.dumps({"runs": len(audit_runs), "missing": missing, "problems": problems,
                      "definition_lines": {t.id: t.def_line for t in tasks.values()},
                      "places": {t.id: len(t.places) for t in tasks.values()}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
