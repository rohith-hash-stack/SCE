"""Phase 3b's progress, read from the results the Kaggle shards pushed (no checkout needed). Blinded: it shows which
runs are finished, how long they took and the shards' checks, never an outcome (resolved, score, turns, tokens).

    python -B -m harness.experiments.r4.phase3b_progress [--ref origin/feature/option3-contract-axis] [--runs]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

TASKS = "harness/experiments/r4/phase3/phase3b.json"
SHORT = {"PRISM_GW": "A", "PRISM_GW_C": "C", "PRISM_GW_E": "E", "BASELINE_LSP": "B"}


def _git(*a: str) -> str:
    r = subprocess.run(["git", *a], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--ref", default="origin/feature/option3-contract-axis")
    p.add_argument("--runs", action="store_true", help="also list every run")
    a = p.parse_args(argv)
    doc = json.loads(_git("show", f"{a.ref}:{TASKS}"))
    files = set(_git("ls-tree", "-r", "--name-only", a.ref, "reports/r4/").split())
    rows, run_rows, total_done, total_min = [], [], 0, 0.0
    for k in sorted(doc["shards"], key=int):
        base = f"reports/r4/phase3b_shard{k}"
        shard = doc["shards"][k]
        secs = {}
        if f"{base}/pilot_runs.jsonl" in files:
            for ln in _git("show", f"{a.ref}:{base}/pilot_runs.jsonl").splitlines():
                rec = json.loads(ln)
                secs[rec["run"]] = (rec["seconds"], rec["rc"])
        done = [f"{t}__{v}__{s}" for t, s, v in shard if f"{base}/{t}__{v}__{s}.score.json" in files]
        env = [json.loads(ln) for ln in _git("show", f"{a.ref}:{base}/environment.jsonl").splitlines() if ln.strip()]
        pre = _git("show", f"{a.ref}:{base}/preflight.json")
        health = _git("show", f"{a.ref}:{base}/health.log").strip().splitlines()
        last = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M UTC", a.ref, "--", base).strip()
        mins = [secs[r][0] / 60 for r in done if r in secs]
        total_done += len(done)
        total_min += sum(mins)
        nxt = next((f"{t[-2:]}/{s}/{SHORT[v]}" for t, s, v in shard
                    if f"{t}__{v}__{s}" not in done), "-")
        state = ("not started" if not files & {f"{base}/environment.jsonl", f"{base}/pilot_runs.jsonl"}
                 else "STOPPED: environment" if env and not env[-1].get("ok")
                 else "done" if len(done) == len(shard) and health
                 else "running / paused")
        hc = "-"
        if health:
            try:
                hc = "passed" if json.loads(health[-1]).get("continue") else "FAILED"
            except ValueError:
                hc = "see log"
        rows.append((k, state, f"{len(done)}/{len(shard)}", nxt,
                     f"{sum(mins) / 60:.1f} h" if mins else "-", f"{sum(mins) / len(mins):.1f}" if mins else "-",
                     sum(1 for r in done if r in secs and secs[r][1] != 0),
                     ("ok" if env[-1].get("ok") else "MISMATCH") + f" ({len(env)})" if env else "-",
                     "ok" if pre else "-", hc, last or "-"))
        for t, s, v in shard:
            r = f"{t}__{v}__{s}"
            run_rows.append((k, t, s, v, "done" if r in done else "-",
                             f"{secs[r][0] / 60:.1f}" if r in secs else "-", secs[r][1] if r in secs else "-"))
    print("| Shard | State | Runs | Next (task/seed/arm) | Run time | Min/run | rc≠0 | Environment (sessions) "
          "| Pre-flight | Health | Last push |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(map(str, r)) + " |")
    print(f"| **All** | | **{total_done}/80** | | {total_min / 60:.1f} h | | | | | | |")
    if a.runs:
        print("\n| Shard | Task | Seed | Arm | Finished | Minutes | rc |\n|---|---|---|---|---|---|---|")
        for r in run_rows:
            print("| " + " | ".join(map(str, r)) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
