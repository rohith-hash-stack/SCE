"""Phase 3b's transcript replays (AMENDMENTS "Phase 3b", the tests before the cell is pinned): recorded tool calls
replayed, unchanged and in their turns' order, through the real runner.

    python -B -m harness.experiments.r4.phase3b_replay TASKS.json OUT --version V --cache DIR \
        --transcripts 'GLOB' [--as-version V] NAME=CLONE ...

**How:**
- **The model:** `ReplayModel` replies to every turn with the call the transcript recorded at that turn: the tool
  and its arguments exactly, `confirm` included. After the last one it calls `done`.
- **The runner:** `runner.run_task` with POC 2's flags: the ledger, hygiene and compaction (`--phase1`), feedback,
  guards; turn cap 60; no scoring; `record_full`, so every answer is kept whole.
- **Output:** `OUT/<task>__<version>__<seed>.jsonl`, as the loop writes it.

**`--as-version`** replays a transcript recorded in one arm (`--version`, the one whose transcripts are read) as
another arm. For example, PRISM_GW's calls as PRISM_GW_E, to see E's answers to the same inputs.

Nothing here changes the harness, and it runs no model.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys


class ReplayModel:
    """A model that replays a transcript turn by turn, then calls `done`: each recorded call exactly, and a reply that
    is not a tool call where the transcript recorded a parse error (so every later call keeps its turn)."""

    NOT_A_CALL = "(replayed parse error: this reply is not a tool call)"

    def __init__(self, records: list[dict]):
        self.turns = [{"tool": r["tool"], "args": r.get("args") or {}} if "tool" in r else None for r in records
                      if "tool" in r or r.get("parse_error")]

    def __call__(self, msgs, seed):
        call = self.turns.pop(0) if self.turns else {"tool": "done", "args": {"summary": "replay ended"}}
        return {"text": self.NOT_A_CALL if call is None else json.dumps(call), "prompt_tokens": 100,
                "answer_tokens": 20, "done_reason": "stop", "seconds": 0.0}


def replay_one(task: dict, records: list[dict], version: str, seed: int, clone: str, out: str, cache: str) -> dict:
    from harness.experiments.r3.runner import run_task
    return run_task(task, version, seed, clone, out, cache, ReplayModel(records), 60, score_run=False, ledger=True,
                    record_full=True, hygiene=True, compact=True, feedback=True, guards=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", help="NAME=CLONE")
    p.add_argument("--version", required=True, help="the arm whose recorded transcripts are replayed")
    p.add_argument("--as-version", default=None, help="the arm to replay them as (default: the same)")
    p.add_argument("--transcripts", required=True, help="a glob of recorded .jsonl turn logs")
    p.add_argument("--cache", required=True)
    p.add_argument("--ids", nargs="*")
    a = p.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    tasks = {t["id"]: t for t in json.load(open(a.tasks))["tasks"]}
    clones = dict(r.split("=", 1) for r in a.repos)
    os.makedirs(a.out, exist_ok=True)
    as_version = a.as_version or a.version
    for path in sorted(glob.glob(a.transcripts)):
        name = os.path.basename(path)[:-len(".jsonl")]
        if name.count("__") != 2:
            continue
        tid, arm, seed = name.split("__")
        if arm != a.version or tid not in tasks or (a.ids and tid not in a.ids):
            continue
        records = [json.loads(ln) for ln in open(path)]
        task = tasks[tid]
        res = replay_one(task, records, as_version, int(seed), os.path.abspath(clones[task["repo"]]), a.out, a.cache)
        print(json.dumps({"run": f"{tid}__{as_version}__{seed}", "from": name, "turns": res.get("turns"),
                          "recorded_turns": len(records), "error": res.get("error")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
