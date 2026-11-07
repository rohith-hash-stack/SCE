"""The process-per-run memory test (BUILD_PLAN section 7), before the pilot. No model calls.

    python -m harness.experiments.r3.memtest TASKS.json [TASKS.json ...] OUT_DIR --cache-py DIR --cache-js DIR \
        NAME=CLONE ... [--runs 20]

The driver runs `--runs` runs in sequence, each in its own child process (`dryrun.py` restricted to one task,
version and script): the per-run subprocess path the Kaggle cell uses. It samples its own RSS and the child's once a
second.

**Pass when:**
- the driver's RSS grows by less than 50 MB over all runs;
- each child has exited before the next one starts, and no process of its run is left (none whose command line or
  working directory is in the run's `/tmp/r3run_*` worktree);
- within a run, the child's RSS does not keep growing: the peak of its last quarter of samples is at most 100 MB
  above the peak of the first three quarters;
- the children's peaks show no trend: every (task, version, script) runs twice, and its second peak is within 10% of
  its first.

`OUT_DIR/memtest.json` holds the samples, the per-run peaks and the verdict.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
import time

DRIVER_GROWTH_MB = 50
LATE_GROWTH_MB = 100
PEAK_DRIFT = 0.10


def rss_mb(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/status") as fh:
            for ln in fh:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) / 1024
    except OSError:
        return None
    return None


def leftovers() -> list[dict]:
    """Processes still running in, or from, a run's worktree (`/tmp/r3run_*`): a test's children that outlived it.

    The child's own exit is not enough: execa's IPC tests left node processes behind (2026-11-07), reparented away
    from the run, which holds their memory into the next run."""
    found = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf8", "replace")
            cwd = os.readlink(f"/proc/{d}/cwd")
        except OSError:
            continue
        if "/r3run_" in cmd or "/r3run_" in cwd:
            found.append({"pid": int(d), "rss_mb": round(rss_mb(int(d)) or 0.0, 1), "cmd": cmd[:160]})
    return found


def plan(tasks: list[dict], runs: int) -> list[tuple[str, str, str]]:
    """Two passes over the same (task, version, script) runs, so each is measured twice: up to `runs // 4` tasks,
    both languages interleaved, each as (PRISM, gold) and (BASELINE, empty)."""
    by_lang = {}
    for t in tasks:
        by_lang.setdefault(t["language"], []).append(t["id"])
    order = [x for pair in itertools.zip_longest(*by_lang.values()) for x in pair if x][:max(1, runs // 4)]
    one_pass = [(tid, v, s) for tid in order for v, s in (("PRISM", "gold"), ("BASELINE", "empty"))]
    return (one_pass + one_pass)[:runs]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("args", nargs="+", help="TASKS.json ... OUT_DIR NAME=CLONE ...")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--runs", type=int, default=20)
    a = p.parse_args(argv)
    task_files = [x for x in a.args if x.endswith(".json")]
    repos = [x for x in a.args if "=" in x]
    out = [x for x in a.args if x not in task_files and x not in repos][0]
    os.makedirs(out, exist_ok=True)
    tasks = [t for f in task_files for t in json.load(open(f))["tasks"]]
    merged = os.path.join(out, "tasks.json")
    json.dump({"tasks": tasks}, open(merged, "w"))
    runs_dir = os.path.join(out, "runs")
    me = os.getpid()
    driver_start = rss_mb(me)
    records = []
    for i, (tid, version, script) in enumerate(plan(tasks, a.runs)):
        cmd = [sys.executable, "-B", "-m", "harness.experiments.r3.dryrun", merged, os.path.join(runs_dir, str(i)),
               *repos, "--cache-py", a.cache_py, "--cache-js", a.cache_js, "--ids", tid, "--versions", version,
               "--scripts", script]
        t0 = time.time()
        child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        samples = []
        while child.poll() is None:
            r = rss_mb(child.pid)
            if r is not None:
                samples.append(round(r, 1))
            time.sleep(1)
        text = child.stdout.read()
        time.sleep(2)
        left = leftovers()
        line = next((ln for ln in text.splitlines() if ln.startswith("{")), "{}")
        res = json.loads(line)
        cut = max(1, int(len(samples) * 0.75))
        late = (max(samples[cut:]) - max(samples[:cut])) if len(samples) > 3 else 0.0
        rec = {"run": i, "task": tid, "version": version, "script": script, "rc": child.returncode,
               "outcome": res.get("outcome"), "ok": res.get("ok"), "seconds": round(time.time() - t0, 1),
               "child_peak_mb": max(samples) if samples else None, "late_growth_mb": round(late, 1),
               "exited_before_next": child.poll() is not None and rss_mb(child.pid) is None and not left,
               "leftover_processes": left,
               "driver_mb": round(rss_mb(me), 1), "samples": samples}
        records.append(rec)
        print(json.dumps({k: v for k, v in rec.items() if k != "samples"}), flush=True)
    growth = records[-1]["driver_mb"] - driver_start if records else 0.0
    drift = {}
    for key in {(r["task"], r["version"], r["script"]) for r in records}:
        peaks = [r["child_peak_mb"] for r in records if (r["task"], r["version"], r["script"]) == key
                 and r["child_peak_mb"]]
        if len(peaks) > 1:
            drift["/".join(key)] = round((peaks[-1] - peaks[0]) / peaks[0], 3)
    checks = {
        "driver_growth_under_50mb": growth < DRIVER_GROWTH_MB,
        "children_exited": all(r["exited_before_next"] for r in records),
        "no_late_growth_within_a_run": all(r["late_growth_mb"] <= LATE_GROWTH_MB for r in records),
        "no_peak_trend": all(abs(d) <= PEAK_DRIFT for d in drift.values()),
        "runs_as_expected": all(r["ok"] for r in records),
    }
    verdict = {"runs": len(records), "driver_start_mb": round(driver_start, 1), "driver_growth_mb": round(growth, 1),
               "peak_drift": drift, "checks": checks, "pass": all(checks.values()), "records": records}
    json.dump(verdict, open(os.path.join(out, "memtest.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in verdict.items() if k != "records"}, indent=1))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
