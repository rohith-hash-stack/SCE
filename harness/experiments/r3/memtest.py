"""The process-per-run memory test (BUILD_PLAN section 7), before the pilot. No model calls.

    python -m harness.experiments.r3.memtest TASKS.json [TASKS.json ...] OUT_DIR --cache-py DIR --cache-js DIR \
        NAME=CLONE ... [--runs 20]

The driver runs `--runs` runs in sequence, each in its own child process (`dryrun.py` restricted to one task,
version and script): the per-run subprocess path the Kaggle cell uses. It samples its own RSS and the child's once a
second.

**Pass when** (amendment R3-7, which replaced the first rule after its 2026-11-07 result):
- (a) within a run, memory plateaus before the run ends, and does not grow with the number of tool calls:
  - plateau: neither of the child's last two steps between samples rises by more than max(2% of its peak, 10 MB)
    (a flat or falling end passes, a still-climbing end fails);
  - per call: every run starts with `--pad` read-only calls (the disk tools and the version's index-backed tool,
    `dryrun.pad_calls`). The loop logs the process's RSS after each call. The least-squares slope of RSS over pad
    calls 5 to the last (the first 4 include building the index) is at most 1 MB per call.
  - A single heavy phase late in a run is expected, not a failure.
- (b) across runs, peak memory repeats within 10%: every (task, version, script) runs twice, and its second peak is
  within 10% of its first;
- (c) memory is released after each run exits: the child has exited and holds nothing before the next starts, and
  the driver's RSS grows by less than 50 MB over all runs;
- (d) no orphaned processes survive: none whose command line or working directory is in a run's `/tmp/r3run_*`
  worktree;
- and every run ends as its script should (gold resolved, empty unresolved).

The first rule's late-growth figure (the peak of the last quarter of samples minus the peak of the rest) is still
recorded per run, as a diagnostic only.

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
LATE_GROWTH_MB = 100   # the first rule's figure; a diagnostic only since amendment R3-7
PEAK_DRIFT = 0.10
MB_PER_CALL = 1.0
PLATEAU_FRAC, PLATEAU_MB = 0.02, 10.0


def plateaued(samples: list[float]) -> bool:
    """The run's memory had stopped rising when it ended: neither of the last two steps between samples rises by more
    than max(2% of the peak, 10 MB). Flat and falling ends both pass; a still-climbing end fails."""
    if len(samples) < 3:
        return True
    tol, tail = max(PLATEAU_FRAC * max(samples), PLATEAU_MB), samples[-3:]
    return all(b - a <= tol for a, b in zip(tail, tail[1:]))


def _slope(ys: list[float]) -> float | None:
    if len(ys) < 2:
        return None
    n = len(ys)
    mx, my = (n - 1) / 2, sum(ys) / n
    return sum((x - mx) * (y - my) for x, y in enumerate(ys)) / sum((x - mx) ** 2 for x in range(n))


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
    p.add_argument("--pad", type=int, default=24)
    p.add_argument("--turn-cap", type=int, default=60)
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
               "--scripts", script, "--pad", str(a.pad), "--turn-cap", str(a.turn_cap)]
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
        seed = 42 if script == "gold" else 43
        log = os.path.join(runs_dir, str(i), f"{tid}__{version}__{seed}.jsonl")
        per_call = [r.get("rss_mb") for r in (json.loads(ln) for ln in open(log)) if r.get("rss_mb") is not None]\
            if os.path.exists(log) else []
        pad_rss = per_call[4:a.pad]
        slope = _slope(pad_rss)
        cut = max(1, int(len(samples) * 0.75))
        late = (max(samples[cut:]) - max(samples[:cut])) if len(samples) > 3 else 0.0
        rec = {"run": i, "task": tid, "version": version, "script": script, "rc": child.returncode,
               "outcome": res.get("outcome"), "ok": res.get("ok"), "seconds": round(time.time() - t0, 1),
               "child_peak_mb": max(samples) if samples else None, "late_growth_mb": round(late, 1),
               "exited_before_next": child.poll() is not None and rss_mb(child.pid) is None,
               "leftover_processes": left,
               "plateau": plateaued(samples), "pad_calls_measured": len(pad_rss),
               "mb_per_call": None if slope is None else round(slope, 3),
               "driver_mb": round(rss_mb(me), 1), "per_call_rss": per_call, "samples": samples}
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
        "a_plateau_within_a_run": all(r["plateau"] for r in records),
        "a_no_growth_with_tool_calls": all(r["mb_per_call"] is not None and r["pad_calls_measured"] >= 10
                                           and r["mb_per_call"] <= MB_PER_CALL for r in records),
        "b_peaks_repeat_within_10pct": bool(drift) and all(abs(d) <= PEAK_DRIFT for d in drift.values()),
        "c_released_after_exit": all(r["exited_before_next"] for r in records) and growth < DRIVER_GROWTH_MB,
        "d_no_orphans": all(not r["leftover_processes"] for r in records),
        "runs_as_expected": all(r["ok"] for r in records),
    }
    verdict = {"runs": len(records), "driver_start_mb": round(driver_start, 1), "driver_growth_mb": round(growth, 1),
               "peak_drift": drift, "checks": checks, "pass": all(checks.values()), "records": records}
    json.dump(verdict, open(os.path.join(out, "memtest.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in verdict.items() if k != "records"}, indent=1))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
