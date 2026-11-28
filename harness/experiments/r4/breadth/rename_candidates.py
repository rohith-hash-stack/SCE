"""The breadth study's rename family: the held-out candidates, by the design's rule (`breadth/DESIGN.md`, section 5).

    python -B -m harness.experiments.r4.breadth.rename_candidates OUT.json

**The pool:** Phase 3's 20 validated hidden-dependency renames (`phase3/candidates.json`), minus every one any phase
has run.

**The order:** grep's precision lowest first, then the most dependent files.

**The pick:** the first 8 in that order, at most 2 per repository. The rest are kept as spares, in order, should one
fail admission.

**The task text:** each candidate's turn sentence is corrected, "28 turns" → "60 turns", by POC 2's own function, as
the turn cap is 60. Nothing else in a task changes.

No model, no run; it writes the candidate list that admission then checks.
"""
from __future__ import annotations

import collections
import json
import os
import sys

from harness.experiments.r4.phase3_poc2_tasks import _turns

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_BEFORE = {"p3-hidden-01", "p3-hidden-02", "p3-hidden-03", "p3-hidden-04",      # Phase 3 POC
              "p3-hidden-06", "p3-hidden-13", "p3-hidden-15", "p3-hidden-16"}      # POC 2 (p3-hidden-15, -03: 3b)
WANT, PER_REPO = 8, 2


def ordered() -> list[dict]:
    cands = json.load(open(os.path.join(HERE, "phase3", "candidates.json")))["candidates"]
    pool = [t for t in cands if t["id"] not in RUN_BEFORE]
    # Phase 3 recorded grep precision and the dependent files in `hidden`; sort as its selection did
    return sorted(pool, key=lambda t: (t["hidden"]["grep_precision"], -len(t["hidden"]["files"]), t["id"]))


def pick(pool: list[dict]) -> tuple[list[dict], list[dict]]:
    per, chosen, spares = collections.Counter(), [], []
    for t in pool:
        if len(chosen) < WANT and per[t["repo"]] < PER_REPO:
            chosen.append(t)
            per[t["repo"]] += 1
        else:
            spares.append(t)
    return chosen, spares


def main(argv=None) -> int:
    out = (argv or sys.argv[1:])[0]
    chosen, spares = pick(ordered())
    doc = {"family": "rename_hidden", "rule": "grep precision ascending, then dependent files descending; "
                                              f"first {WANT}, at most {PER_REPO} per repository",
           "chosen": [_turns(t) for t in chosen], "spares": [_turns(t) for t in spares]}
    json.dump(doc, open(out, "w"), indent=1)
    for t in chosen + spares:
        print(("CHOSEN " if t in chosen else "spare  ") + f"{t['id']} {t['repo']} "
              f"precision={t['hidden']['grep_precision']} files={len(t['hidden']['files'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
