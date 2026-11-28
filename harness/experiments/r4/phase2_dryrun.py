"""Phase 2's dry run: the real runner on the derived tasks with a scripted model, in all four arms (no model calls).

    python -B -m harness.experiments.r4.phase2_dryrun TASKS.json GOLD_DIR OUT_DIR --cache DIR NAME=CLONE ... \
        [--ids ID ...] [--arms ARM ...]

Per task and arm, through `runner.run_task` with `--feedback` on (the worktree, the environment, the WorkingIndex,
the tool layer, the injection for INJECTED, the loop, the done gate, scoring):
- **gold** replays the answer key (`GOLD_DIR/<id>.patch`, without the generated hidden test) through `edit_file`:
  - the PRISM arms first call `prism_change_context` on the target;
  - PRISM_ONLY first calls `prism_search` for its name and `prism_read` on its file;
  - BASELINE_LSP first calls `find_references` on the target's line;
  - then `run_tests` and `done`.

  It must end `resolved`.
- **empty** calls `done` at once (BASELINE only). It must end unresolved or stuck at the done gate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from harness.experiments.r3.dryrun import Script, edits_for

ARMS = ("BASELINE", "PRISM", "INJECTED", "PRISM_ONLY", "BASELINE_LSP", "PRISM_LSP", "PRISM_GW", "PRISM_GW_C",
        "PRISM_GW_E")


def gold_contents(clone: str, task: dict, patch: str) -> dict[str, tuple[str | None, str | None]]:
    """{file: (start text, gold text)} for every file the answer key changes, the generated test left out."""
    tmp = tempfile.mkdtemp(prefix="p2dry_")
    work = os.path.join(tmp, "w")
    try:
        subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, task["parent"]], check=True)
        files = re.findall(r"^diff --git a/(\S+) b/", patch, re.M)
        gen = set(task["tests_run"]) - set(task["visible_tests"])
        before = {f: open(os.path.join(work, f)).read() if os.path.exists(os.path.join(work, f)) else None
                  for f in files}
        subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=work, input=patch, text=True, check=True)
        after = {f: open(os.path.join(work, f)).read() if os.path.exists(os.path.join(work, f)) else None
                 for f in files}
        return {f: (before[f], after[f]) for f in files if f not in gen}
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _qname(task: dict) -> str:
    """The target's dotted name: the derived tasks record it; for a delete task, its file's module and the name."""
    if task.get("anchor_qname"):
        return task["anchor_qname"]
    f, _, q = task["must_be_absent"].partition("::")
    return f[:-3].replace("/", ".") + "." + q


def gold_calls(task: dict, contents: dict, arm: str) -> list[dict]:
    calls = []
    f, _, q = (task.get("must_be_absent") or task["anchor"]).partition("::")     # a delete task: its target
    if arm in ("PRISM_LSP", "PRISM_ONLY"):                  # Phase 3: PRISM's own Find All References first
        calls.append({"tool": "prism_find_references", "args": {"symbol": _qname(task)}})
    if arm == "PRISM_ONLY":
        calls += [{"tool": "prism_search", "args": {"pattern": rf"\b{q.rsplit('.', 1)[-1]}\b"}},
                  {"tool": "prism_read", "args": {"path": f, "start": max(1, task["target_line"] - 5),
                                                  "end": task["target_line"] + 20}}]
    elif arm == "BASELINE_LSP":
        calls += [{"tool": "find_references", "args": {"symbol": f"{f}:{task['target_line']}"}},
                  {"tool": "semantic_search", "args": {"query": q.rsplit('.', 1)[-1].replace("_", " ")}},
                  {"tool": "ast_search", "args": {"pattern": f"$X.{q.rsplit('.', 1)[-1]}($$$A)"}},
                  {"tool": "run_shell", "args": {"command": f"git log --oneline -3 -- {f} | cat"}}]
    elif arm in ("PRISM", "INJECTED", "PRISM_LSP", "PRISM_GW", "PRISM_GW_C", "PRISM_GW_E"):
        calls.append({"tool": "prism_change_context", "args": {"symbol": _qname(task)}})
    for path, (old, new) in sorted(contents.items()):
        calls += [{"tool": "edit_file", "args": e} for e in edits_for(old, new, path)]
    calls.append({"tool": "run_tests", "args": {"files": task["visible_tests"][:1]}})
    calls.append({"tool": "done", "args": {"summary": "replayed the answer key"}})
    return calls


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("gold_dir")
    p.add_argument("out")
    p.add_argument("repos", nargs="+")
    p.add_argument("--cache", required=True)
    p.add_argument("--ids", nargs="*")
    p.add_argument("--arms", nargs="*", default=list(ARMS))
    p.add_argument("--turn-cap", type=int, default=60)
    p.add_argument("--guards", action="store_true", help="POC 2: the Layer 1 guards, every arm")
    p.add_argument("--edit-fix", action="store_true", help="the breadth study: the edit-fix rule, every arm")
    p.add_argument("--empty-every-arm", action="store_true",
                   help="Phase 3b: the empty script in every arm (default: one baseline arm only)")
    p.add_argument("--all-families", action="store_true",
                   help="the breadth study: every task in the file, delete tasks too (default: derived tasks only)")
    a = p.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from harness.experiments.r3.runner import run_task
    clones = dict(s.split("=", 1) for s in a.repos)
    os.makedirs(a.out, exist_ok=True)
    bad = 0
    for t in json.load(open(a.tasks))["tasks"]:
        if not (t.get("derived") or a.all_families) or (a.ids and t["id"] not in a.ids):
            continue
        clone = os.path.abspath(clones[t["repo"]])
        contents = gold_contents(clone, t, open(os.path.join(a.gold_dir, f"{t['id']}.patch")).read())
        runs = [(arm, "gold", 42) for arm in a.arms] + ([(arm, "empty", 43) for arm in a.arms] if a.empty_every_arm else
                                                       [(b, "empty", 43) for b in ("BASELINE", "BASELINE_LSP")
                                                        if b in a.arms][:1])
        for arm, kind, seed in runs:
            model = Script(gold_calls(t, contents, arm) if kind == "gold" else [])
            res = run_task(t, arm, seed, clone, a.out, a.cache, model, a.turn_cap, ledger=True, feedback=True,
                           guards=a.guards, edit_fix=a.edit_fix)
            # the empty script must not resolve: the done gate rejects it (`stuck_done`), or, in an arm with PRISM's
            # gateway (Phase 3b, every arm), the gateway blocks `done` until the turn cap (`stuck`)
            empty_ok = ("unresolved", "stuck_done", "stuck") if a.empty_every_arm else ("unresolved", "stuck_done")
            ok = res.get("outcome") == "resolved" if kind == "gold" else res.get("outcome") in empty_ok
            bad += not ok
            sc = res.get("score") or {}
            first = None
            log = os.path.join(a.out, f"{t['id']}__{arm}__{seed}.jsonl")
            if os.path.exists(log):
                first = next((json.loads(x) for x in open(log) if '"tool"' in x), None)
            print(json.dumps({"task": t["id"], "arm": arm, "script": kind, "outcome": res.get("outcome"), "ok": ok,
                              "first_tool": (first or {}).get("tool"),
                              "first_output": str((first or {}).get("output_head", ""))[:160],
                              "turns": res.get("turns"), "derived_check": sc.get("derived_check"),
                              "injected": (res.get("injected") or {}).get("tool"),
                              **({"gateway": {k: v for k, v in res["gateway"].items() if k != "log"},
                                  "gateway_log": [(x["type"], x["outcome"]) for x in res["gateway"]["log"]]}
                                 if res.get("gateway") else {})}), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
