"""The second sanity run's task list (amendment R4-SANITY-2): the first run's 20 tasks, with the verification gate.

    python -B -m harness.experiments.r4.sanity_gate_tasks TASKS_V1.json TASKS_V2.json --cache-py DIR --cache-js DIR \
        NAME=CLONE ...

Per task:
- `done_gate` and `restore_scored_tests` are set (`r3/runner.py`; `done_gate.py`);
- the task message gains the paragraph that states the gate (the wording is shared by both arms; `gate_paragraph`);
- delete tasks get `visible_start_pass`: the visible tests that pass in all 3 runs at the start (flaky dropped);
- **the gate check:** the developer's change must pass the gate (delete: source and test changes, since the model
  must update or remove the tests that use the target too; feature: source changes), and the unchanged start must not.
  A task whose developer change the gate rejects is reported and left out, never forced in.

Nothing else changes: same ids, prompts otherwise, tests and scoring sets. The first run's list is kept as it was.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from harness.experiments.r3.gate import RUNS, reset_to, run_states, set_files_to
from harness.experiments.r3.runner import apply_patch, snapshot_tree

TURN_CAP = 28


def gate_paragraph(task: dict) -> str:
    """The task message's statement of the gate (amendment R4-SANITY-2, C2)."""
    if task["family"] == "delete":
        f, q = task["must_be_absent"].split("::", 1)
        name = task.get("target_display") or f"`{q}`"        # R4-SANITY-3: the name as written in the code
        files = ", ".join(f"`{x}`" for x in task.get("visible_tests") or []) or "the visible test files"
        what = (f"{name} is no longer defined in `{f}`, and the tests in {files} that passed at the start still pass "
                "(a test you delete is not counted)")
    elif task["family"] == "feature":
        what = ("the tests listed above pass and the other tests in those files still pass. Test files are checked as "
                "they were at the start, so change the source code, not the tests")
    else:
        what = "the visible tests that passed at the start still pass"
    return (f"When you call done, the harness checks your work: {what}. If the check fails, done is rejected, you are "
            f"told what is still missing, and you continue. Run `run_tests` yourself before calling done. You have "
            f"{TURN_CAP} turns in total; a rejected done uses one.")


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", check=True)


def upgrade(task: dict, clone: str, cache: str, scratch: str) -> dict:
    from harness.experiments.r3.shared_tools import make_env
    from harness.experiments.r4.done_gate import DoneGate
    t = {**task, "done_gate": True, "restore_scored_tests": True}
    t["prompt"] = task["prompt"] + "\n\n" + gate_paragraph(task)
    work = os.path.join(scratch, "w")
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(["git", "-C", clone, "worktree", "prune"], capture_output=True)
    _git("-C", clone, "worktree", "add", "-q", "--detach", work, task["parent"])
    rec = {"id": task["id"]}
    t0 = time.time()
    try:
        env = make_env(clone, task["parent"], work, cache)
        env.setup()
        apply_patch(work, task.get("start_patch"))
        start = snapshot_tree(work)
        if task["family"] == "delete":
            ensure = getattr(env, "ensure_current_build", None)
            if ensure:
                ensure()
            states = run_states(env, task.get("visible_tests") or [])
            states.pop("__unparsed__", None)
            vsp: dict[str, list[str]] = {}
            for key, outs in states.items():
                f, tid = key.split("::", 1)
                if len(outs) == RUNS and all(o == "passed" for o in outs):
                    vsp.setdefault(f, []).append(tid)
            t["visible_start_pass"] = {f: sorted(v) for f, v in sorted(vsp.items())}
            rec["visible_start_pass"] = sum(map(len, vsp.values()))
        empty_ok, empty_msg = DoneGate(t, work, env, start).check()
        changed = [f for f in _git("diff", "--name-only", "--no-renames", task["parent"], task["commit"],
                                   cwd=work).stdout.split("\n") if f]
        test_files = set(task.get("test_files") or [])
        gold_files = changed if task["family"] == "delete" else [f for f in changed if f not in test_files]
        set_files_to(work, task["commit"], gold_files)
        gold_ok, gold_msg = DoneGate(t, work, env, start).check()
        rec.update(gold_accepted=gold_ok, empty_accepted=empty_ok)
        if not gold_ok or empty_ok:
            rec.update(ok=False, gold_message=gold_msg[:600], empty_message=empty_msg[:300])
        else:
            rec["ok"] = True
            t["gate_check"] = {"gold": True, "empty": False}
        return {"rec": rec, "task": t}
    except Exception as exc:
        rec.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        return {"rec": rec, "task": None}
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    a = p.parse_args(argv)
    clones = dict(s.split("=", 1) for s in a.repos)
    doc = json.load(open(a.tasks))
    log_path = a.out + ".log.jsonl"
    done = {}
    if os.path.exists(log_path):
        for ln in open(log_path):
            r = json.loads(ln)
            done[r["rec"]["id"]] = r
    scratch = tempfile.mkdtemp(prefix="r4gate2_")
    try:
        for task in doc["tasks"]:
            if task["id"] in done:
                continue
            cache = a.cache_js if task["language"] == "js" else a.cache_py
            r = upgrade(task, clones[task["repo"]], cache, scratch)
            done[task["id"]] = r
            with open(log_path, "a") as fh:
                fh.write(json.dumps(r) + "\n")
            print(json.dumps(r["rec"]), flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    tasks = [done[t["id"]]["task"] for t in doc["tasks"] if done[t["id"]]["rec"].get("ok")]
    sha = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    json.dump({"tasks": tasks, "tasks_sha256": sha, "repo_urls": doc["repo_urls"],
               "from": {"tasks_sha256": doc["tasks_sha256"]}}, open(a.out, "w"), indent=1)
    print(f"{len(tasks)}/{len(doc['tasks'])} tasks pass the gate check -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
