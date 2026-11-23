"""POC 2's task file (AMENDMENTS "Phase 3, POC 2"), chosen by the rule pre-registered there.

    python -B -m harness.experiments.r4.phase3_poc2_tasks

From `phase3/candidates.json`, the validated hidden tasks that no Phase 3 run has seen (in neither `poc.json` nor
`tasks.json`), ranked as Phase 3 ranks them (`phase3_tasks.rank_key`): the top 4, at most one per repository; and the
control `r4s-delete-09`, copied unchanged from `phase2/tasks.json` (a Python control that Phase 3 neither ran nor
studied). Written to `phase3/poc2.json` (`"design": "poc2"`, `"stage": "poc"`, 45 runs, seeds 201-203).

One change to the copied tasks: each prompt's "You have 28 turns in total" says `TURN_CAP` (60), the turn cap the
Phase 3 cells run with. Phase 3's task files kept Phase 2's sentence after amendment 3 raised the cap to 60, so POC 1's
agents were told 28 (found 2026-11-23, after POC 1; recorded in AMENDMENTS "Phase 3, POC 2").
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from harness.experiments.r4.phase3_tasks import pick

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "phase3")
CONTROL = "r4s-delete-09"
SEEDS = [201, 202, 203]
HIDDEN = 4
TURN_CAP = 60
_TURNS = "You have 28 turns in total"


def _turns(task: dict) -> dict:
    assert task["prompt"].count(_TURNS) == 1, task["id"]
    return {**task, "prompt": task["prompt"].replace(_TURNS, f"You have {TURN_CAP} turns in total")}


def build() -> dict:
    cands = json.load(open(os.path.join(OUT, "candidates.json")))["candidates"]
    seen = {t["id"] for f in ("poc.json", "tasks.json") for t in json.load(open(os.path.join(OUT, f)))["tasks"]}
    fresh = [t for t in cands if t["id"] not in seen]
    hidden = pick(fresh, HIDDEN, 1)
    control = next(t for t in json.load(open(os.path.join(HERE, "phase2", "tasks.json")))["tasks"] if t["id"] == CONTROL)
    tasks = [_turns(t) for t in hidden + [control]]
    urls = {**json.load(open(os.path.join(OUT, "tasks.json"))).get("repo_urls", {}),
            **json.load(open(os.path.join(OUT, "poc.json"))).get("repo_urls", {}),
            **json.load(open(os.path.join(HERE, "phase2", "tasks.json"))).get("repo_urls", {})}
    return {"design": "poc2", "stage": "poc", "expected_runs": len(tasks) * 3 * len(SEEDS), "seeds": SEEDS,
            "repo_urls": {r: urls[r] for r in sorted({t["repo"] for t in tasks})}, "tasks": tasks,
            "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}


def main() -> int:
    doc = build()
    with open(os.path.join(OUT, "poc2.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    print("poc2:", [t["id"] for t in doc["tasks"]], "sha256", doc["tasks_sha256"][:12], "runs", doc["expected_runs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
