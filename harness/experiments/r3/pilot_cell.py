"""Render the R3 pilot's Kaggle cell, pinned to a code commit and to the frozen pilot task list.

    python -m harness.experiments.r3.pilot_cell COMMIT RESULTS_PATH   # template -> kaggle/r3_pilot_cell.py

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

TASKS = "harness/experiments/r3/pilot/widened/tasks_B.json"


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2 or not re.fullmatch(r"reports/r3/[a-z0-9_]+", args[1]):
        raise SystemExit(__doc__)
    commit = git("rev-parse", args[0])
    doc = json.loads(git("show", f"{commit}:{TASKS}"))
    sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
    if sha != doc.get("tasks_sha256"):
        raise SystemExit(f"{TASKS} at {commit[:7]} does not match its own sha256")
    fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src"),
             "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness"), "@@TASKS_SHA@@": sha,
             "@@RESULTS_PATH@@": args[1]}
    text = (ROOT / "kaggle" / "r3_pilot_cell.template.py").read_text()
    for k, v in fills.items():
        if k not in text:
            raise SystemExit(f"{k} missing from the template")
        text = text.replace(k, v)
    (ROOT / "kaggle" / "r3_pilot_cell.py").write_text(text)
    print(fills)
    return 0


if __name__ == "__main__":
    sys.exit(main())
