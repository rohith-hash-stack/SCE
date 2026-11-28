"""Render Phase 2's Kaggle cells (AMENDMENTS "Phase 2, 4 arms"), pinned to a code commit and to the task list.

    python -m harness.experiments.r4.phase2_cell COMMIT
    # -> kaggle/r4_phase2_smoke_cell.py (the smoke test) and kaggle/r4_phase2_cell.py (the sharded 800 runs)

Both come from `kaggle/r4_phase2_cell.template.py`, with:
- the commit and the git tree ids of `src` and `harness` at it (the cell refuses to run on other code);
- the sha256 of `harness/experiments/r4/phase2/tasks.json`'s task list at that commit (checked against the file);
- `SMOKE` (True for the smoke cell);
- the results base `reports/r4/phase2` (`_smoke`, or `_seed<SEED>` per shard).
"""
from __future__ import annotations

import hashlib
import json
import sys

from harness.experiments.r3.cell import ROOT, git

TASKS = "harness/experiments/r4/phase2/tasks.json"
RESULTS = "reports/r4/phase2"
CELLS = {"r4_phase2_smoke_cell": "True", "r4_phase2_cell": "False"}


def render(commit: str) -> dict:
    commit = git("rev-parse", commit)
    doc = json.loads(git("show", f"{commit}:{TASKS}"))
    sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
    if sha != doc.get("tasks_sha256"):
        raise SystemExit(f"{TASKS} at {commit[:7]} does not match its own sha256")
    if doc.get("design") != "phase2":
        raise SystemExit(f"{TASKS} at {commit[:7]} is not marked as the Phase 2 design")
    fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src"),
             "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness"), "@@TASKS_SHA@@": sha,
             "@@RESULTS_PATH@@": RESULTS}
    template = (ROOT / "kaggle" / "r4_phase2_cell.template.py").read_text()
    for name, smoke in CELLS.items():
        text = template
        for k, v in {**fills, "@@SMOKE@@": smoke}.items():
            if k not in text:
                raise SystemExit(f"{k} missing from the template")
            text = text.replace(k, v)
        (ROOT / "kaggle" / f"{name}.py").write_text(text)
    return fills


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        raise SystemExit(__doc__)
    print(render(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
