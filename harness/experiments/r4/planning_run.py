"""The planning investigation's runs (`PLANNING_INVESTIGATION.md`): 3 sanity tasks × the model variants, PRISM arm,
seed 42, R3a's loop and turn cap. Each run is one `r3.runner` process; a run with a score file is never repeated.

    python -B -m harness.experiments.r4.planning_run TASKS.json OUT --variants V ... --cache-py DIR --cache-js DIR \
        --base-url URL [--deadline EPOCH] NAME=CLONE ...

The "Qwen 14B, current prompt" row is not re-run: it is the sanity run (`reports/r4/sanity`, same seed, code and
loop; the planning options are off by default).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

TASK_IDS = ("r4s-delete-02", "r4s-delete-06", "r4s-feature-04")     # instructor delete, pydantic delete, typer feature
VERSION, SEED, TURN_CAP = "PRISM", 42, 28
VARIANTS = {   # name: (model, runner flags)
    "qwen14b-plan": ("qwen2.5-coder:14b-instruct-q8_0", ["--plan"]),
    "qwen3-14b-think": ("qwen3:14b-q8_0", ["--think", "on", "--answer-tokens", "12288"]),
    "qwen3-14b-nothink": ("qwen3:14b-q8_0", ["--think", "off"]),     # the same weights without thinking: the control
    "qwq-32b": ("qwq:32b", ["--think", "on", "--answer-tokens", "12288"]),
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--variants", nargs="+", required=True, choices=list(VARIANTS))
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--deadline", type=float, default=None)
    p.add_argument("--no-json-format", action="store_true", help="passed to every run (a model that forced JSON breaks)")
    a = p.parse_args(argv)
    clones = dict(r.split("=", 1) for r in a.repos)
    tasks = {t["id"]: t for t in json.load(open(a.tasks))["tasks"] if t["id"] in TASK_IDS}
    failed = 0
    for var in a.variants:
        model, flags = VARIANTS[var]
        out = os.path.join(a.out, var)
        os.makedirs(os.path.join(out, "tasks"), exist_ok=True)
        for tid in (i for i in TASK_IDS if i in tasks):
            t = tasks[tid]
            run_id = f"{tid}__{VERSION}__{SEED}"
            if os.path.exists(os.path.join(out, run_id + ".score.json")):
                continue
            if a.deadline is not None and time.time() >= a.deadline:
                print(json.dumps({"stopped": "deadline", "next": f"{var}/{run_id}"}), flush=True)
                return 3
            tp = os.path.join(out, "tasks", tid + ".json")
            json.dump(t, open(tp, "w"))
            t0 = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "harness.experiments.r3.runner", tp, VERSION, str(SEED),
                                   "--clone", os.path.abspath(clones[t["repo"]]), "--out", out,
                                   "--cache", a.cache_js if t["language"] == "js" else a.cache_py,
                                   "--base-url", a.base_url, "--turn-cap", str(TURN_CAP), "--model", model, *flags,
                                   *(["--no-json-format"] if a.no_json_format else [])], capture_output=True, text=True)
            rec = {"variant": var, "run": run_id, "rc": proc.returncode, "minutes": round((time.time() - t0) / 60, 1),
                   "stdout": proc.stdout.strip()[-300:], "stderr_tail": proc.stderr[-1500:] if proc.returncode else ""}
            failed += proc.returncode != 0
            with open(os.path.join(a.out, "planning_runs.jsonl"), "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(json.dumps({k: rec[k] for k in ("variant", "run", "rc", "minutes", "stdout")}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
