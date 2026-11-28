"""The breadth study's delete family: R4's delete tasks, built from freshly gated commits (`breadth/DESIGN.md`, section 5).

    python -B -m harness.experiments.r4.breadth.delete_tasks CANDIDATES.json GATE.jsonl OUT.json GOLD_DIR \
        --cache-py DIR --cache-js DIR NAME=CLONE ...

**The pool:** the commits that passed `gate_families --family delete` (GATE.jsonl), in the gate's own seeded order.
`CANDIDATES.json` is the miner's list, the commits of every earlier delete task already removed.

**Per commit, R4's existing steps, unchanged:**
1. `sanity_tasks.build_one`: the task, its visible and scored tests (3 runs each), and R3's gate (the developer's change
   resolves, the start does not);
2. `sanity_gate_tasks.upgrade`: the done gate and its paragraph; the gate check (the developer's change passes the
   gate, the start does not);
3. the third sanity run's fixes (`sanity3_tasks`): explicit tool messages; the prompt names the target as written in
   the code, with its kind and its line;
4. the turn sentence says 60 turns (`phase3_poc2_tasks._turns`).

**The pick:** the first 8 that pass every step, at most 2 per repository (the gate already caps it); the rest are spares.

**The answer key** (`GOLD_DIR/<id>.patch`, for the dry run): the developer's diff over the task's source and test
files, never given to a run.

Ids are `bd-delete-NN`, so they cannot be mistaken for the earlier `r4s-delete-NN`. No model, no run.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

from harness.experiments.change_history.mine import functions
from harness.experiments.r4.phase3_poc2_tasks import _turns
from harness.experiments.r4.sanity3_tasks import describe, prompt_delete
from harness.experiments.r4.sanity_gate_tasks import gate_paragraph, upgrade
from harness.experiments.r4.sanity_tasks import build_one

WANT, PER_REPO = 8, 2


def sanity3(t: dict, clone: str) -> dict:
    """`sanity3_tasks.main`'s per-task step, as it is there."""
    t = {**t, "explicit_tools": True}
    f, q = t["must_be_absent"].split("::", 1)
    src = subprocess.run(["git", "-C", clone, "show", f"{t['parent']}:{f}"], capture_output=True, check=True).stdout
    defs = functions(f, src)
    line = defs[q]["start"]
    display = describe(q, defs, src.decode("utf8", "replace").splitlines()[line - 1])
    t.update(target_display=display, target_line=line)
    t["prompt"] = prompt_delete(display, f, line) + "\n\n" + gate_paragraph(t)
    return t


def gold_patch(clone: str, t: dict) -> str:
    files = sorted(set(t["gold_files"]) | set(t["test_files"]))          # the developer's source and test changes
    if not files:
        files = [p for p in subprocess.run(["git", "-C", clone, "diff", "--name-only", "--no-renames", t["parent"],
                                            t["commit"]], capture_output=True, text=True).stdout.split("\n") if p]
    return subprocess.run(["git", "-C", clone, "diff", "--no-renames", t["parent"], t["commit"], "--", *files],
                          capture_output=True, text=True, check=True).stdout


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("candidates")
    p.add_argument("gate")
    p.add_argument("out")
    p.add_argument("gold_dir")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    a = p.parse_args(argv)
    clones = dict(s.split("=", 1) for s in a.repos)
    doc = json.load(open(a.candidates))
    cands = {c["commit"]: c for c in (doc["candidates"] if isinstance(doc, dict) else doc)}
    passed = [r for r in map(json.loads, open(a.gate)) if r.get("passed")]
    log_path = a.out + ".log.jsonl"
    done = {r["commit"]: r for r in map(json.loads, open(log_path))} if os.path.exists(log_path) else {}
    os.makedirs(a.gold_dir, exist_ok=True)
    chosen, spares, per = [], [], collections.Counter()
    scratch = tempfile.mkdtemp(prefix="bd_delete_")
    try:
        for g in passed:
            c = cands[g["commit"]]
            if len(chosen) >= WANT or per[c["repo"]] >= PER_REPO:
                spares.append(c["commit"])
                continue
            if c["commit"] not in done:
                clone = clones[c["repo"]]
                rec = {"commit": c["commit"], "repo": c["repo"]}
                built = build_one("delete", c, g, clone, a.cache_py, scratch)
                if not built.get("built"):
                    rec.update(ok=False, step="build", reason=built.get("reason", ""))
                else:
                    task = {**built["task"], "id": f"bd-delete-{len(chosen) + 1:02d}", "source_commit": c["commit"]}
                    up = upgrade(task, clone, a.cache_py, scratch)
                    if not up["rec"].get("ok"):
                        rec.update(ok=False, step="gate check", reason=json.dumps(up["rec"])[:300])
                    else:
                        rec.update(ok=True, task=_turns(sanity3(up["task"], clone)))
                done[c["commit"]] = rec
                with open(log_path, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
            rec = done[c["commit"]]
            print(f"{c['repo']:14} {c['commit'][:10]} {'OK ' + rec['task']['id'] if rec['ok'] else 'FAIL ' + rec['step'] + ': ' + rec['reason'][:120]}",
                  flush=True)
            if rec["ok"]:
                t = {**rec["task"], "id": f"bd-delete-{len(chosen) + 1:02d}"}
                chosen.append(t)
                per[c["repo"]] += 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for t in chosen:
        open(os.path.join(a.gold_dir, f"{t['id']}.patch"), "w").write(gold_patch(clones[t["repo"]], t))
    urls = {}
    for name in sorted({t["repo"] for t in chosen}):
        url = subprocess.run(["git", "-C", clones[name], "remote", "get-url", "origin"], capture_output=True,
                             text=True).stdout.strip()
        urls[name] = url if url.endswith(".git") else url + ".git"
    sha = hashlib.sha256(json.dumps(chosen, sort_keys=True).encode()).hexdigest()
    json.dump({"family": "delete", "tasks": chosen, "tasks_sha256": sha, "repo_urls": urls, "spares": spares},
              open(a.out, "w"), indent=1)
    print(f"{len(chosen)} delete tasks -> {a.out} ({sha[:12]}); {len(spares)} spares")
    return 0


if __name__ == "__main__":
    sys.exit(main())
