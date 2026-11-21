"""Type 1 (forward: update the callers a change breaks): select the suite's tasks from the mined candidates.

    python -m harness.experiments.suite.forced select OUT.json MINED_DIR NAME [NAME ...] [--seed 5501]

The filters are those of runs 2-3 (`change_history.select`):
- only forced changes (`forced_reasons`);
- a caller counts only when its changed lines touch the call (`touches_call`);
- an anchor is never its own caller;
- one task per commit (the anchor with the most changed callers).

Per repository, in a seeded order: up to 7 easy, 7 medium and 6 hard tasks; a tier short of tasks is filled from
the others (hard, then medium, then easy), up to 20. Planning (`change_history.plan --breaks --no-overview`)
then adds the break context; the versions are run by `change_history.run`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from harness.experiments.change_history.mine import tier
from harness.experiments.change_history.select import forced_reasons, touches_call

QUOTA = {"easy": 7, "medium": 7, "hard": 6}
PER_REPO = 20


def eligible(cands: list[dict]) -> list[dict]:
    kept = []
    for t in cands:
        why = forced_reasons(t)
        if not why:
            continue
        real = [c for c in t["changed_callers"] if c["qname"] != t["anchor"] and touches_call(t, c)]
        if not real:
            continue
        moved = [c["qname"] for c in t["changed_callers"] if c not in real and c["qname"] != t["anchor"]]
        t = {**t, "forced": why, "changed_callers": real,
             "unchanged_callers": sorted(set(t["unchanged_callers"]) | set(moved))}
        t["tier"] = tier(t["anchor_file"], real)
        kept.append(t)
    best: dict[str, dict] = {}
    for t in kept:
        cur = best.get(t["commit"])
        if cur is None or len(t["changed_callers"]) > len(cur["changed_callers"]):
            best[t["commit"]] = t
    return sorted(best.values(), key=lambda t: (t["repo"], t["commit"]))


def choose(pool: list[dict], repo: str, seed: int) -> list[dict]:
    pool = [t for t in pool if t["repo"] == repo]
    random.Random(f"{seed}:{repo}").shuffle(pool)
    chosen = []
    for tr, n in QUOTA.items():
        chosen += [t for t in pool if t["tier"] == tr][:n]
    for tr in ("hard", "medium", "easy"):  # fill a short tier from the others
        for t in pool:
            if len(chosen) >= PER_REPO:
                break
            if t["tier"] == tr and t not in chosen:
                chosen.append(t)
    return chosen


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("cmd", choices=("select",))
    p.add_argument("out")
    p.add_argument("mined")
    p.add_argument("repos", nargs="+")
    p.add_argument("--seed", type=int, default=5501)
    args = p.parse_args(argv)
    cands = []
    for r in args.repos:
        cands += json.loads(Path(args.mined, r, "candidates.json").read_text())
    pool = eligible(cands)
    tasks = []
    for r in sorted(args.repos):
        got = choose(pool, r, args.seed)
        tasks += got
        print(f"{r}: {len([t for t in pool if t['repo'] == r])} eligible; chose "
              f"{ {tr: sum(t['tier'] == tr for t in got) for tr in QUOTA} }")
    for i, t in enumerate(tasks, 1):
        t["id"] = f"fw{i:03d}"
    Path(args.out).write_text(json.dumps({
        "version": 1, "status": "frozen", "seed": args.seed, "forced": True, "no_self_callers": True,
        "quota": QUOTA, "per_repo": PER_REPO,
        "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest(), "tasks": tasks},
        indent=1) + "\n")
    print(f"{len(tasks)} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
