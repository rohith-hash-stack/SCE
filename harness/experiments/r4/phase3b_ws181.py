"""Phase 3b's line-181 whitespace investigation (2026-11-28; offline, after the runs; not pre-registered).

    python -B -m harness.experiments.r4.phase3b_ws181 LEDGER.json --clones DIR --out DIR

**Reads:**
- the verified ledger, to locate the runs;
- each run's turn log, from the ledger's run id and shard;
- the litestar source at the task's commit.

It never reads the answer key or the hidden tests.

**An attempt** is an `edit_file` call on `tests/conftest.py` whose `old` contains the line's distinctive text (`KEY`,
which occurs once in the file, at line 181):
- either alone (a one-line `old`) or inside a multi-line `old`;
- `partial` marks an `old` whose line-181 text starts mid-line.

**Writes:**
- `ws181_attempts.csv`: one row per attempt;
- `ws181_summary.json`: per-arm counts, the origin classification, and the recovery sequences.

**What is exact and what is not:**
- **Exact:** the submitted whitespace. The turn logs keep each call's arguments in full, and they equal the model's
  parsed reply.
- **Truncated:** what the model was shown is kept only as each answer's first 300 characters. Where an answer's
  content is not in those 300 characters, it is inferred from the tool's code and its arguments, and marked as
  inferred.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import subprocess
import sys

TASK = "p3-hidden-15"
FILE = "tests/conftest.py"
LINE_NO = 181
LINE = ") -> SessionMiddleware[ClientSideSessionBackend]:"
KEY = "SessionMiddleware[ClientSideSessionBackend]:"     # the line's distinctive text (once in the file, at 181)


def lead(s: str) -> str:
    """The leading whitespace on the line of `old` that holds line 181's text ("" if it starts mid-line)."""
    first = next((ln for ln in s.split("\n") if KEY in ln), "")
    if LINE not in first:
        return ""
    return first[:len(first) - len(first.lstrip(" \t"))]


def ws_type(w: str) -> str:
    if w == "":
        return "none"
    if set(w) == {" "}:
        return "spaces"
    if set(w) == {"\t"}:
        return "tabs"
    if set(w) <= {" ", "\t"}:
        return "mixed"
    return "other"


def kind(out: str) -> str:
    if out.startswith(("edited", "created")):
        return "accepted"
    if out.startswith("not answered again"):
        return "refused_loop_breaker"
    if out.startswith("error") and "apart from whitespace" in out:
        return "refused_whitespace_only"
    if out.startswith("error") and "not found" in out:
        return "refused_not_found_other"
    return "refused_other" if out.startswith("error") else "other"


def shown_range(out: str) -> tuple[int, int] | None:
    """The line range a numbered listing (read_file, prism_read) says it shows, from its header."""
    m = re.match(r"\S+ \((?:lines )?(\d+)-(\d+) of \d+", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def displays_181(r: dict) -> str | None:
    """Whether this turn's answer showed line 181 in the numbered `N<TAB>text` layout, and how (None if not)."""
    tool, args, out = r.get("tool"), r.get("args") or {}, r.get("output_head") or ""
    if tool in ("read_file", "prism_read") and FILE in (str(args.get("path", "")) + out[:80]):
        rng = shown_range(out)
        if rng and rng[0] <= LINE_NO <= rng[1]:
            return f"{tool}:numbered"                       # the header says 181 is in the listing
    if tool == "edit_file" and str(args.get("path", "")).endswith(FILE) and out.startswith("error"):
        if "apart from whitespace" in out and f"at line {LINE_NO}." in out:
            return "edit_error:numbered"                    # R4-SANITY-4: the file's own line, numbered
        m = re.search(r"from line (\d+) \(each line is its number, a tab", out)
        if m and int(m.group(1)) <= LINE_NO <= int(m.group(1)) + 14:
            return "edit_error_closest:numbered?"           # closest lines from N: 181 within the shown window
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("ledger")
    p.add_argument("--clones", required=True)
    p.add_argument("--results", default="reports/r4")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    ledger = [r for r in json.load(open(a.ledger)) if r["task"] == TASK]
    src = subprocess.run(["git", "-C", os.path.join(a.clones, "litestar"), "show",
                          "d6e5c785700ef1db43f4087836ca9d26a164df97:" + FILE], capture_output=True, text=True).stdout
    src_lines = src.split("\n")
    source = {"line_181": repr(src_lines[LINE_NO - 1]), "line_181_leading": repr(lead(src_lines[LINE_NO - 1])),
              "neighbours": {n: repr(src_lines[n - 1]) for n in (174, 175, 179, 180, 182)},
              "tabs_in_file": src.count("\t")}
    rows, runs = [], {}
    for led in sorted(ledger, key=lambda r: (r["arm"], r["seed"])):
        path = os.path.join(a.results, f"phase3b_shard{led['shard']}", led["run_id"] + ".jsonl")
        recs = [json.loads(ln) for ln in open(path)]
        seen_display: list[tuple[int, str]] = []
        listed: list[int] = []          # turns whose answer (first 300 chars) lists the place as `conftest.py:181: `
        notes: list[tuple[int, str]] = []
        prev_calls: dict[str, int] = {}
        prev_applied_lead = None
        variants_first: dict[str, int] = {}
        seq = []
        n = 0
        for r in recs:
            if "tool" not in r:
                continue
            turn, args, out = r["turn"], r.get("args") or {}, r.get("output_head") or ""
            if r["tool"] == "edit_file" and KEY in str(args.get("old", "")) and \
                    str(args.get("path", "")).endswith(FILE):
                n += 1
                w = lead(str(args["old"]))
                t = ws_type(w)
                k = kind(out)
                sig = json.dumps([args.get("old"), args.get("new"), args.get("occurrence")])
                repeat_of = prev_calls.get(sig)
                prev_calls.setdefault(sig, n)
                before_disp = [d for d in seen_display if d[0] < turn]
                before_notes = [x for x in notes if x[0] < turn]
                # the origin: only where the transcript supports it
                if t == "none":
                    origin, cands = "correct (no leading whitespace)", ""
                elif repeat_of is not None:
                    origin, cands = f"repeat of attempt {repeat_of} (identical call)", ""
                else:
                    c = []
                    if t in ("tabs", "mixed"):
                        if any(d[1].startswith(("read_file", "prism_read")) for d in before_disp):
                            c.append("1:read_file/prism_read listing")
                        if any(d[1].startswith("edit_error") for d in before_disp):
                            c.append("2:edit-tool error listing")
                        c.append("4:model-generated")              # cannot be excluded
                        # a tab cannot come from a neighbouring source line: the file has no tab
                    else:
                        c.append("3:neighbouring indented line (174-175, 180, 182 have 4 spaces)"
                                 if w == "    " else "3:neighbouring indented line?")
                        c.append("4:model-generated")
                    if len(c) == 1:
                        origin = c[0]
                    elif t in ("tabs", "mixed") and len(c) == 2:
                        origin = f"most plausible {c[0]} (competing: {c[1]})"
                    elif t in ("tabs", "mixed"):
                        last = max(before_disp)[1] if before_disp else "?"
                        origin = f"1 or 2 (both shown before; most recent: {last}); 4 not excluded"
                    else:
                        origin = ("3 or 4: same 4 spaces as the model's previous applied edit"
                                  if prev_applied_lead == w else "3 or 4")
                    cands = "; ".join(c)
                    variants_first.setdefault(t + ":" + repr(w), turn)
                rows.append({"run_id": led["run_id"], "task": TASK, "arm": led["arm"], "seed": led["seed"],
                             "attempt_number": n, "turn": turn,
                             "old_lines": len(str(args["old"]).rstrip("\n").split("\n")),
                             "partial": next((ln for ln in str(args["old"]).split("\n") if KEY in ln), "")
                             .lstrip(" \t").startswith(LINE) is False,
                             "place_listed_before": sum(1 for x in listed if x < turn),
                             "leading_spaces": w.count(" "),
                             "leading_tabs": w.count("\t"), "whitespace_type": t,
                             "accepted_or_refused": k, "repeat_of_attempt": repeat_of or "",
                             "line181_numbered_display_before": len(before_disp),
                             "last_display_before": (max(before_disp)[1] + f"@t{max(before_disp)[0]}")
                             if before_disp else "",
                             "c_note_before": len(before_notes), "origin": origin, "origin_candidates": cands})
                seq.append((turn, t, w.count(" "), w.count("\t"), k))
            if r["tool"] == "edit_file" and out.startswith("edited"):
                prev_applied_lead = re.match(r"[ \t]*", str(args.get("old", ""))).group(0)
            if f"{FILE}:{LINE_NO}: " in out:
                listed.append(turn)
            d = displays_181(r)
            if d:
                seen_display.append((turn, d))
            for iv in r.get("gateway") or []:
                if iv.get("type") == "exact_fix" and iv.get("fix_old") == LINE:
                    notes.append((turn, iv.get("fix_new")))
        runs[led["run_id"]] = {"arm": led["arm"], "seed": led["seed"], "resolved": led["resolved"],
                               "run_failed": led["run_failed"], "sequence": seq,
                               "first_variant_turns": variants_first,
                               "c_notes_for_181": [t for t, _ in notes],
                               "numbered_displays_of_181": seen_display}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ws181_attempts.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summ = {"source": source, "attempts": len(rows), "by_arm": {}, "runs": runs}
    for arm in ("PRISM_GW", "PRISM_GW_C", "PRISM_GW_E", "BASELINE_LSP"):
        rs = [r for r in rows if r["arm"] == arm]
        first = [r for r in rs if r["attempt_number"] == 1]
        new_variants = [r for r in rs if not r["repeat_of_attempt"] and r["whitespace_type"] != "none"]
        summ["by_arm"][arm] = {
            "runs_with_attempts": len({r["run_id"] for r in rs}), "attempts": len(rs),
            "whitespace_type": dict(collections.Counter(r["whitespace_type"] for r in rs)),
            "leading": dict(collections.Counter(f"{r['leading_spaces']}sp+{r['leading_tabs']}tab" for r in rs)),
            "outcome": dict(collections.Counter(r["accepted_or_refused"] for r in rs)),
            "first_attempt_whitespace": dict(collections.Counter(r["whitespace_type"] +
                                                                 f"({r['leading_spaces']}sp,{r['leading_tabs']}tab)"
                                                                 for r in first)),
            "repeats": sum(1 for r in rs if r["repeat_of_attempt"]),
            "single_line_attempts": sum(1 for r in rs if r["old_lines"] == 1),
            "multi_line_attempts": sum(1 for r in rs if r["old_lines"] > 1),
            "first_attempt_single_line": sum(1 for r in first if r["old_lines"] == 1),
            "first_attempt_after_numbered_display": sum(1 for r in first if r["line181_numbered_display_before"]),
            "new_wrong_variants": len(new_variants),
            "new_wrong_variants_origin": dict(collections.Counter(r["origin"].split(" (")[0].split(";")[0]
                                                                  for r in new_variants)),
            "tab_variant_first_after": dict(collections.Counter(
                (r["last_display_before"].split("@")[0] or "no numbered display before")
                for r in new_variants if r["whitespace_type"] in ("tabs", "mixed")))}
    json.dump(summ, open(os.path.join(a.out, "ws181_summary.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in summ.items() if k != "runs"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
