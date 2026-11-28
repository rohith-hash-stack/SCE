"""Rebuild the start state of Family B tasks whose anchor gained overloads, and gate them again.

    python -m harness.experiments.r3.repair_overloads TASKS.json OUT.json --cache-py DIR --cache-js DIR \
        NAME=CLONE ... --ids ID ...

The miner took the developer's new code for an anchor from the index, which keeps a name's first declaration: for
an anchor that gained `@overload` stubs (Python) or overload signatures (TypeScript), that is a signature without a
body, so the start state replaced the function with a stub (R3a: r3a-B-09, r3a-B-29). Here the start patch puts the
commit's whole overload group in the function's place (`mine.start_patch_for`), the prompt is rebuilt from it, and
the task is gated again by `gate.gate_one` (fail-to-pass and pass-to-pass sets, gold files and functions, and the
scoring check). Task ids and every other field are kept. A task that does not pass the gate again is reported and
left out of OUT.json's changes (it keeps its old record).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile

from harness.experiments.r3.gate import gate_one
from harness.experiments.r3.mine import b_prompt, class_start, start_patch_for
from harness.experiments.r3.pool_scan import show


def anchor_codes(clone: str, task: dict) -> tuple[str, str]:
    """The parent's anchor code and the stub the start patch put in its place: the changed span, whole lines."""
    path = task["anchor_file"]
    before = (show(clone, task["parent"], path) or b"").decode("utf8")
    tmp = tempfile.mkdtemp(prefix="r3fix_")
    try:
        subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", f"{tmp}/w", task["parent"]], check=True)
        subprocess.run(["git", "apply", "-"], cwd=f"{tmp}/w", input=task["start_patch"], text=True, check=True)
        start = open(f"{tmp}/w/{path}", encoding="utf8").read()
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", f"{tmp}/w"], capture_output=True)
    a, b = before.splitlines(keepends=True), start.splitlines(keepends=True)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    return "".join(a[ops[0][1]:ops[-1][2]]), "".join(b[ops[0][3]:ops[-1][4]])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--ids", nargs="+", required=True)
    a = p.parse_args(argv)
    clones = dict(r.split("=", 1) for r in a.repos)
    doc = json.load(open(a.tasks))
    report = []
    for i, t in enumerate(doc["tasks"]):
        if t["id"] not in a.ids:
            continue
        clone = clones[t["repo"]]
        old, stub = anchor_codes(clone, t)
        after_text = (show(clone, t["commit"], t["anchor_file"]) or b"").decode("utf8")
        sp = start_patch_for(clone, t["parent"], t["anchor_file"], old, stub, after_text=after_text,
                             lang=t["language"], after=class_start(after_text, t["anchor"]))
        if not sp or sp == t["start_patch"]:
            report.append({"id": t["id"], "repaired": False, "reason": "no overload group found"})
            continue
        fixed = {**t, "start_patch": sp, "prompt": b_prompt(t["anchor"], t["anchor_file"], sp)}
        cache = a.cache_js if t["language"] == "js" else a.cache_py
        scratch = tempfile.mkdtemp(prefix="r3fixgate_")
        rec = gate_one(fixed, os.path.abspath(clone), cache, scratch)
        entry = {"id": t["id"], "repaired": bool(rec.get("passed")), **{k: rec.get(k) for k in (
            "reason", "n_f2p", "n_p2p", "gold_resolved", "empty_resolved", "gold_file_f1", "gold_function_f1")}}
        if rec.get("passed"):
            doc["tasks"][i] = {**rec["task"], "env": t.get("env")}
            entry["env_now"] = rec.get("env")
        report.append(entry)
        print(json.dumps(entry), flush=True)
    json.dump(doc, open(a.out, "w"), indent=1)
    print(json.dumps({"report": report}), flush=True)
    return 0 if all(r["repaired"] for r in report) else 1


if __name__ == "__main__":
    sys.exit(main())
