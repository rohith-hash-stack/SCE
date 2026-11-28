"""Render R3's Kaggle cells (the pilot, and R3a's main run), pinned to a code commit and to a frozen task list.

    python -m harness.experiments.r3.pilot_cell COMMIT RESULTS_PATH           # -> kaggle/r3_pilot_cell.py
    python -m harness.experiments.r3.pilot_cell COMMIT RESULTS_PATH --r3a     # -> kaggle/r3a_cell.py

- COMMIT: the commit holding the harness;
- RESULTS_PATH: where results go under the repository (a new path per pilot: finished runs there are skipped).

It fills in the commit, the git tree ids of `src` and `harness` at that commit, and the pilot task list's sha256 at
that commit. The cell refuses to run if the code or the task list it checks out differs.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

from harness.experiments.r3.cell import ROOT, git

CELLS = {"pilot": ("harness/experiments/r3/pilot/widened/tasks_B.json", "r3_pilot_cell"),
         "r3a": ("harness/experiments/r3/r3a_tasks/tasks_B.json", "r3a_cell")}


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    which = "r3a" if "--r3a" in args else "pilot"
    args = [a for a in args if a != "--r3a"]
    if len(args) != 2 or not re.fullmatch(r"reports/r3/[a-z0-9_]+", args[1]):
        raise SystemExit(__doc__)
    TASKS, cell = CELLS[which]
    commit = git("rev-parse", args[0])
    doc = json.loads(git("show", f"{commit}:{TASKS}"))
    sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
    if sha != doc.get("tasks_sha256"):
        raise SystemExit(f"{TASKS} at {commit[:7]} does not match its own sha256")
    fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src"),
             "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness"), "@@TASKS_SHA@@": sha,
             "@@RESULTS_PATH@@": args[1]}
    text = (ROOT / "kaggle" / f"{cell}.template.py").read_text()
    for k, v in fills.items():
        if k not in text:
            raise SystemExit(f"{k} missing from the template")
        text = text.replace(k, v)
    (ROOT / "kaggle" / f"{cell}.py").write_text(text)
    print(fills)
    return 0


if __name__ == "__main__":
    sys.exit(main())
