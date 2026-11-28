"""The planning investigation's table (`PLANNING_INVESTIGATION.md`), from the run logs.

    python -B -m harness.experiments.r4.planning_report OUT [--sanity reports/r4/sanity]

Per run:
- **plan before acting:** the first turn carries a plan (`--plan`) or thinking (reasoning models); and the share of
  turns that do;
- **edit success:** applied `edit_file` calls / attempted ones;
- **hallucinated done:** `done` was called and the run is not resolved;
- **verified before done:** `run_tests` after the last applied edit and before `done` (no edit: no);
- turns, ending, outcome, thinking tokens per turn, minutes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from harness.experiments.r4.planning_run import SEED, TASK_IDS, VARIANTS, VERSION


def run_row(out_dir: str, tid: str) -> dict | None:
    rid = f"{tid}__{VERSION}__{SEED}"
    sp, lp = os.path.join(out_dir, rid + ".score.json"), os.path.join(out_dir, rid + ".jsonl")
    if not os.path.exists(sp):
        return None
    s = json.load(open(sp))
    turns = [json.loads(ln) for ln in open(lp)] if os.path.exists(lp) else []
    applied = attempted = 0
    last_edit = last_test = None
    planned = 0
    think = []
    for i, t in enumerate(turns):
        planned += bool(t.get("plan") or t.get("thinking"))
        if t.get("thinking_tokens"):
            think.append(t["thinking_tokens"])
        if t.get("tool") == "edit_file":
            attempted += 1
            if (t.get("output_head") or "").startswith(("edited", "created")):
                applied += 1
                last_edit = i
        if t.get("tool") == "run_tests":
            last_test = i
    done = s.get("end") == "done"
    resolved = bool((s.get("score") or {}).get("resolved"))
    return {"turns": s.get("turns"), "end": s.get("end"), "resolved": resolved,
            "plan_first": bool(turns and (turns[0].get("plan") or turns[0].get("thinking"))),
            "plan_share": round(planned / len(turns), 2) if turns else None,
            "edits": f"{applied}/{attempted}", "edit_rate": round(applied / attempted, 2) if attempted else None,
            "hallucinated_done": done and not resolved,
            "verified_before_done": done and last_edit is not None and last_test is not None and last_test > last_edit,
            "thinking_tokens_per_turn": round(sum(think) / len(think)) if think else 0,
            "minutes": round((s.get("seconds") or 0) / 60, 1), "parse_errors": s.get("parse_errors", 0)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("--sanity", default="reports/r4/sanity")
    a = p.parse_args(argv)
    rows = {"qwen14b (sanity, current prompt)": a.sanity, **{v: os.path.join(a.out, v) for v in VARIANTS}}
    table = {}
    print("| Variant | Task | Plan first / share | Edits applied | Hallucinated done | Verified before done | Ending | "
          "Resolved | Think tok/turn | Min |\n|---|---|---|---|---|---|---|---|---|---|")
    for var, d in rows.items():
        for tid in TASK_IDS:
            r = run_row(d, tid)
            if r is None:
                continue
            table.setdefault(var, {})[tid] = r
            print(f"| {var} | {tid} | {'yes' if r['plan_first'] else 'no'} / {r['plan_share']} | {r['edits']} | "
                  f"{'YES' if r['hallucinated_done'] else 'no'} | {'yes' if r['verified_before_done'] else 'no'} | "
                  f"{r['end']} ({r['turns']}) | {'yes' if r['resolved'] else 'no'} | {r['thinking_tokens_per_turn']} | "
                  f"{r['minutes']} |")
    json.dump(table, open(os.path.join(a.out, "planning_report.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
