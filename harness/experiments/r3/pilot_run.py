"""Run R3's runs (the pilot, and R3a): every task in TASKS.json, both versions, each seed, each run in its own child
process.

    python -m harness.experiments.r3.pilot_run TASKS.json OUT_DIR --cache-py DIR --cache-js DIR \
        --base-url URL [--seeds 42 1001] [--turn-cap 30] [--deadline EPOCH] NAME=CLONE ...

- Order: task by task in the frozen order; within a task, each seed, then PRISM and BASELINE. So a task's runs
  complete together, and the interim look's first 40 tasks are fixed before the run (amendment R3-1).

- One process per run (amendment R3-7): `python -m harness.experiments.r3.runner`, the same code as R3a.
- A run whose `<run>.score.json` exists is finished and never repeated, so the driver resumes after a restart.
- After each run, `memtest.leftovers()` is recorded: processes left in a `/tmp/r3run_*` worktree. They are killed by
  their process group when the builders' commands end; any left are logged here, never ignored.
- `OUT_DIR/pilot_runs.jsonl` gets one line per run started (exit code, seconds, leftovers).
- `--deadline`: no run starts after this time (seconds since the epoch), so a session ends between runs; exit 3.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

VERSIONS = ("PRISM", "BASELINE")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=[42])
    p.add_argument("--deadline", type=float, default=None)
    p.add_argument("--turn-cap", type=int, default=30)
    a = p.parse_args(argv)
    from harness.experiments.r3.memtest import leftovers
    clones = dict(r.split("=", 1) for r in a.repos)
    tasks = json.load(open(a.tasks))["tasks"]
    os.makedirs(os.path.join(a.out, "tasks"), exist_ok=True)
    log = os.path.join(a.out, "pilot_runs.jsonl")
    failed = 0
    for t in tasks:
        task_path = os.path.join(a.out, "tasks", t["id"] + ".json")
        json.dump(t, open(task_path, "w"))
        for seed, v in [(s, v) for s in a.seeds for v in VERSIONS]:
            run_id = f"{t['id']}__{v}__{seed}"
            if os.path.exists(os.path.join(a.out, run_id + ".score.json")):
                continue
            if a.deadline is not None and time.time() >= a.deadline:
                print(json.dumps({"stopped": "deadline", "next": run_id}), flush=True)
                return 3
            cache = a.cache_js if t["language"] == "js" else a.cache_py
            t0 = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "harness.experiments.r3.runner", task_path, v,
                                   str(seed), "--clone", os.path.abspath(clones[t["repo"]]), "--out", a.out,
                                   "--cache", cache, "--base-url", a.base_url, "--turn-cap", str(a.turn_cap)],
                                  capture_output=True, text=True)
            time.sleep(2)
            rec = {"run": run_id, "rc": proc.returncode, "seconds": round(time.time() - t0, 1),
                   "leftovers": leftovers(), "stdout": proc.stdout.strip()[-400:],
                   "stderr_tail": proc.stderr[-1500:] if proc.returncode else ""}
            failed += proc.returncode != 0
            with open(log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(json.dumps({k: rec[k] for k in ("run", "rc", "seconds", "leftovers", "stdout")}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
