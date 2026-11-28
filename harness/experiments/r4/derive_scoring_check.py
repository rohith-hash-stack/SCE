"""The last check of Phase E's derived tasks: each one scored end to end by the code that scores the runs.

    python -B -m harness.experiments.r4.derive_scoring_check TASKS.json GOLD_DIR --cache DIR --scratch DIR NAME=CLONE ...

Per task, in a fresh worktree of its start commit, with the repository's environment:
- **the gold tree** (the gold patch, without the generated hidden test: a run never has it) must score resolved by
  `r3/runner.py` `score` (the hidden tests applied over the tree, the static check against the gold counts), and the
  done gate (`r4/done_gate.py`) must accept it;
- **the unchanged start** must score unresolved, and the done gate must reject it.

The result is written into each task (`scoring_check`, `gate_check`, `env`); a task that fails is reported and left
out (`rejected`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, errors="replace")


def check_task(task: dict, gold_patch: str, work: str, env) -> dict:
    from harness.experiments.r3.gate import reset_to
    from harness.experiments.r3.runner import score, snapshot_tree
    from harness.experiments.r4.done_gate import DoneGate
    reset_to(work, task["parent"])
    start_tree = snapshot_tree(work)
    gen = [f for f in task["tests_run"] if f not in task["visible_tests"]]
    p = subprocess.run(["git", "-C", work, "apply", "--whitespace=nowarn", "-"], input=gold_patch, text=True,
                       capture_output=True)
    if p.returncode:
        return {"ok": False, "reason": f"gold patch does not apply: {p.stderr[:200]}"}
    for f in gen:                                                   # a run never has the generated hidden test
        if os.path.exists(os.path.join(work, f)):
            os.remove(os.path.join(work, f))
    gate_gold = DoneGate(task, work, env, start_tree).check()
    gold = score(task, work, env, start_tree)
    reset_to(work, task["parent"])
    gate_empty = DoneGate(task, work, env, start_tree).check()
    empty = score(task, work, env, start_tree)
    reset_to(work, task["parent"])
    out = {"gold_resolved": bool(gold.get("resolved")), "empty_resolved": bool(empty.get("resolved")),
           "gold_gate_accepted": gate_gold[0], "empty_gate_accepted": gate_empty[0],
           "gold_derived": gold.get("derived_check"), "empty_derived": empty.get("derived_check"),
           "gold_file_f1": gold.get("file_f1"), "gold_function_f1": gold.get("function_f1")}
    out["ok"] = out["gold_resolved"] and not out["empty_resolved"] and out["gold_gate_accepted"] \
        and not out["empty_gate_accepted"]
    if not out["ok"]:
        out["gold_bad_tests"] = {k: v for k, v in (gold.get("tests") or {}).items() if v != "passed"}
        out["gold_gate_message"] = gate_gold[1][:600]
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("gold_dir")
    p.add_argument("--cache", required=True)
    p.add_argument("--scratch", required=True)
    p.add_argument("clones", nargs="+")
    a = p.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from harness.experiments.r3.shared_tools import make_env
    clones = dict(x.split("=", 1) for x in a.clones)
    doc = json.load(open(a.tasks))
    by_repo: dict[str, list[dict]] = {}
    for t in doc["tasks"]:
        if "scoring_check" not in t:
            by_repo.setdefault(t["repo"], []).append(t)
    for repo, tasks in by_repo.items():
        work = os.path.join(a.scratch, f"score_{repo}")
        if not os.path.isdir(work):
            _git(clones[repo], "worktree", "prune")
            subprocess.run(["git", "-C", clones[repo], "worktree", "add", "-q", "--detach", work, tasks[0]["parent"]],
                           check=True)
        env = make_env(clones[repo], tasks[0]["parent"], work, a.cache)
        env.setup()
        for t in tasks:
            gold = open(os.path.join(a.gold_dir, f"{t['id']}.patch")).read()
            try:
                r = check_task(t, gold, work, env)
            except Exception as exc:
                r = {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
            t["scoring_check"] = {k: v for k, v in r.items() if k != "ok"}
            t["gate_check"] = {"gold": r.get("gold_gate_accepted"), "empty": r.get("empty_gate_accepted")}
            t["env"] = {k: getattr(env, k, None) for k in ("python_version", "strategy", "node_version", "pm_version",
                                                           "built")}
            if not r["ok"]:
                t["rejected"] = r.get("reason") or "scoring check failed"
            json.dump(doc, open(a.tasks, "w"), indent=1)
            print(t["id"], "OK" if r["ok"] else f"FAILED {t['scoring_check']}"[:400], flush=True)
        _git(clones[repo], "worktree", "remove", "--force", work)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
