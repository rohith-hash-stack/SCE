"""Phase 3b's task file (AMENDMENTS "Phase 3b"): one file for every arm.

    python -B -m harness.experiments.r4.phase3b_tasks

**The tasks:**
- `p3-hidden-15`, the primary (the stuck-run task), copied unchanged from `phase3/poc2.json`.
- `p3-hidden-03`, the recovery task, copied from `phase3/poc.json`. Its one change is the turn sentence, "You have
  28 turns in total" → "You have 60 turns in total", by POC 2's own function (`phase3_poc2_tasks._turns`).
- `p2-rename-04` failed admission (`phase3b/ADMISSION.md`) and is not used.

**Seeds:** 301–315 for p3-hidden-15 (15 per arm) and 301–305 for p3-hidden-03 (5 per arm).

**Arms:** `loop.PHASE3B_ARMS`. 80 runs.

**Shards:** 5. Shard k holds seeds 3k−2 to 3k of p3-hidden-15 and seed k of p3-hidden-03, so 16 runs per shard,
the arms interleaved in a fixed rotation (`shard_order`).

Written to `phase3/phase3b.json`, with the sha256 of its task list.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from harness.experiments.r3.loop import PHASE3B_ARMS
from harness.experiments.r4.phase3_poc2_tasks import _turns

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "phase3")
POC2 = os.path.join(OUT, "poc2.json")
POC1 = os.path.join(OUT, "poc.json")
PRIMARY, RECOVERY = "p3-hidden-15", "p3-hidden-03"
SEEDS = {PRIMARY: list(range(301, 316)), RECOVERY: list(range(301, 306))}
SHARDS = 5


def build() -> dict:
    poc2, poc1 = json.load(open(POC2)), json.load(open(POC1))
    primary = next(t for t in poc2["tasks"] if t["id"] == PRIMARY)
    recovery = _turns(next(t for t in poc1["tasks"] if t["id"] == RECOVERY))
    tasks = [primary, recovery]
    urls = {**poc1.get("repo_urls", {}), **poc2.get("repo_urls", {})}
    return {"design": "phase3b", "stage": "phase3b", "arms": list(PHASE3B_ARMS), "seeds": SEEDS,
            "expected_runs": sum(len(s) for s in SEEDS.values()) * len(PHASE3B_ARMS),
            "shards": {k: shard_order(k) for k in range(1, SHARDS + 1)},
            "repo_urls": {r: urls[r] for r in sorted({t["repo"] for t in tasks})}, "tasks": tasks,
            "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}


def shard_order(k: int) -> list[list]:
    """Shard k's runs as [task, seed, arm], in run order: each task-and-seed block runs every arm, and the arms' order
    rotates by one from block to block, so that no arm always runs first or last."""
    blocks = [(PRIMARY, s) for s in SEEDS[PRIMARY][3 * (k - 1):3 * k]] + [(RECOVERY, SEEDS[RECOVERY][k - 1])]
    out, arms = [], list(PHASE3B_ARMS)
    for i, (tid, seed) in enumerate(blocks):
        r = (i + k - 1) % len(arms)
        out += [[tid, seed, a] for a in arms[r:] + arms[:r]]
    return out


def main() -> int:
    doc = build()
    with open(os.path.join(OUT, "phase3b.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    print("phase3b:", [t["id"] for t in doc["tasks"]], "sha256", doc["tasks_sha256"][:12], "runs",
          doc["expected_runs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
