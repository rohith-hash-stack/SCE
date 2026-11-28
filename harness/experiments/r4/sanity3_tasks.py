"""The third sanity run's task list (amendment R4-SANITY-3): the second run's 20 tasks with fixes 2-4.

    python -B -m harness.experiments.r4.sanity3_tasks TASKS_V2.json TASKS_V3.json NAME=CLONE ...

Per task:
- `explicit_tools`: the explicit read and edit messages (fixes 2-3, `r3/shared_tools.py`);
- delete tasks (fix 4, root cause R1): the prompt names the target as it is written in the code, with its kind and its
  line at the start ("Remove the method `_generate_definitions` of class `GenerateJsonSchema` from
  `pydantic/json_schema.py` (it is defined at line 377)"). `target_display` carries the same name into the gate
  paragraph and the gate's messages.

Nothing else changes: ids, test sets, scoring sets and the gate's checks are the second run's. Fix 1 (the gate's
no-change message) is in `done_gate.py`. The tasks' gate check (developer change accepted, start rejected) still holds:
the checks it ran are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys

from harness.experiments.change_history.mine import functions
from harness.experiments.r4.sanity_gate_tasks import gate_paragraph


def describe(q: str, defs: dict, line_text: str) -> str:
    """The target as a reader of the code would name it (fix 4)."""
    if "." in q:
        parent, leaf = q.rsplit(".", 1)
        if parent in defs:                      # nested in a function, e.g. zod's getter `shape` inside `object`
            kind = "getter" if line_text.lstrip().startswith("get ") else "function"
            return f"the {kind} `{leaf}` defined inside the function `{parent}`"
        if leaf == "constructor":
            return f"the constructor of class `{parent}`"
        return f"the method `{leaf}` of class `{parent}`"
    if line_text.lstrip().startswith("class "):
        return f"the class `{q}`"
    return f"the function `{q}`"


def prompt_delete(display: str, f: str, line: int) -> str:
    return (f"Remove {display} from `{f}` (it is defined at line {line}). Remaining tests must still pass.\n\n"
            "Update or remove whatever uses it so that the project still works without it. "
            "When your changes are complete, call done.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    a = p.parse_args(argv)
    clones = dict(s.split("=", 1) for s in a.repos)
    doc = json.load(open(a.tasks))
    tasks = []
    for t in doc["tasks"]:
        t = {**t, "explicit_tools": True}
        if t["family"] == "delete":
            f, q = t["must_be_absent"].split("::", 1)
            src = subprocess.run(["git", "-C", clones[t["repo"]], "show", f"{t['parent']}:{f}"], capture_output=True,
                                 check=True).stdout
            defs = functions(f, src)
            line = defs[q]["start"]
            display = describe(q, defs, src.decode("utf8", "replace").splitlines()[line - 1])
            t.update(target_display=display, target_line=line)
            t["prompt"] = prompt_delete(display, f, line) + "\n\n" + gate_paragraph(t)
        tasks.append(t)
        print(t["id"], t.get("target_display", ""), t.get("target_line", ""))
    sha = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    json.dump({"tasks": tasks, "tasks_sha256": sha, "repo_urls": doc["repo_urls"],
               "from": {"tasks_sha256": doc["tasks_sha256"]}}, open(a.out, "w"), indent=1)
    print(f"{len(tasks)} tasks -> {a.out} ({sha[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
