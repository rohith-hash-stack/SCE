"""The breadth study's rename prompts: name each target by its kind (`breadth/TASKS.md`, section 4, finding 2).

    python -B -m harness.experiments.r4.breadth.rename_wording TASKS.json OUT.json NAME=CLONE ...

Phase 3's task builder (`hidden_tasks.prompt_for`) calls every target "the method", including classes and module-level
functions. For each rename task, the definition at the task's start (`parent`, `derived.file`, `derived.qualname`)
gives its kind:
- a class: "the class `Q`";
- a function inside a class: "the method `Q`";
- any other function: "the function `Q`".

In the prompt, two phrases change: "Rename the method `Q`" and "every use of this method". `target_display` changes
with them. Nothing else in a task changes, and the other families' tasks are copied as they are.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys

from harness.experiments.r4.derived_checks import find_def


def kind_of(clone: str, task: dict) -> str:
    d = task["derived"]
    src = subprocess.run(["git", "-C", clone, "show", f"{task['parent']}:{d['file']}"], capture_output=True,
                         text=True, check=True).stdout
    tree = ast.parse(src)
    node = find_def(tree, d["qualname"])
    if node is None:
        raise ValueError(f"{task['id']}: `{d['qualname']}` not found in {d['file']}")
    if isinstance(node, ast.ClassDef):
        return "class"
    parent = find_def(tree, d["qualname"].rsplit(".", 1)[0]) if "." in d["qualname"] else None
    return "method" if isinstance(parent, ast.ClassDef) else "function"


def reword(task: dict, kind: str) -> dict:
    q = task["derived"]["qualname"]
    old_head, new_head = f"Rename the method `{q}`", f"Rename the {kind} `{q}`"
    old_use, new_use = "every use of this method", f"every use of this {kind}"
    p = task["prompt"]
    assert p.count(old_head) == 1 and p.count(old_use) == 1, task["id"]
    return {**task, "prompt": p.replace(old_head, new_head).replace(old_use, new_use),
            "target_display": f"the {kind} `{q}`"}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    src, out, clones = argv[0], argv[1], dict(a.split("=", 1) for a in argv[2:])
    doc = json.load(open(src))
    tasks = []
    for t in doc["tasks"]:
        if t["family"] == "rename_hidden":
            kind = kind_of(clones[t["repo"]], t)
            t = reword(t, kind)
            print(t["id"], kind, t["target_display"])
        tasks.append(t)
    sha = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    json.dump({**doc, "tasks": tasks, "tasks_sha256": sha, "from": {"tasks_sha256": doc.get("tasks_sha256")}},
              open(out, "w"), indent=1)
    print(f"{len(tasks)} tasks -> {out} ({sha[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
