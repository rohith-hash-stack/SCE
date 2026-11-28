"""R2 held-out draw (amendment R2-2): 5 forward tasks per repository from the mined candidates.

    python -m harness.experiments.r2.select_heldout OUT.json MINED_DIR NAME=LANG [NAME=LANG ...] [--seed 5501]

- Eligibility is `suite.forced.eligible`, unchanged (forced change, a caller touching the call, one task per commit).
- Per repository, in `suite.forced`'s seeded order (`random.Random(f"{seed}:{repo}")`):
  - up to 2 easy, 2 medium and 1 hard (the suite's 7 : 7 : 6 quota, scaled to 5);
  - a short tier is filled from the others: hard, then medium, then easy;
  - 5 per repository.
- A repository with fewer than 5 eligible tasks is an error. The mining check (`MINING_CHECK.md`) admits only
  repositories with at least 5.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from harness.experiments.suite.forced import eligible

QUOTA = {"easy": 2, "medium": 2, "hard": 1}
PER_REPO = 5


def choose(pool: list[dict], repo: str, seed: int) -> list[dict]:
    pool = [t for t in pool if t["repo"] == repo]
    random.Random(f"{seed}:{repo}").shuffle(pool)
    chosen = []
    for tr, n in QUOTA.items():
        chosen += [t for t in pool if t["tier"] == tr][:n]
    for tr in ("hard", "medium", "easy"):
        for t in pool:
            if len(chosen) >= PER_REPO:
                break
            if t["tier"] == tr and t not in chosen:
                chosen.append(t)
    return chosen


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("mined")
    p.add_argument("repos", nargs="+", help="NAME=LANG")
    p.add_argument("--seed", type=int, default=5501)
    a = p.parse_args(argv)
    tasks = []
    for spec in a.repos:
        name, _, lang = spec.partition("=")
        pool = eligible(json.loads(Path(a.mined, name, "candidates.json").read_text()))
        if len(pool) < PER_REPO:
            raise SystemExit(f"{name}: {len(pool)} eligible, fewer than {PER_REPO}")
        for t in choose(pool, name, a.seed):
            tasks.append({**t, "language": lang, "split": "heldout"})
    text = json.dumps(tasks, indent=1) + "\n"
    Path(a.out).write_text(text)
    print(f"{len(tasks)} tasks -> {a.out}  sha256 {hashlib.sha256(text.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
