"""Render Phase 3's Kaggle cells (AMENDMENTS "Phase 3, hidden dependencies, two stages"), pinned to a code commit and
to the task lists.

    python -m harness.experiments.r4.phase3_cell COMMIT
    # -> kaggle/r4_phase3_poc_cell.py (45 runs, shards 1-3 = seeds 42-44)
    #    kaggle/r4_phase3_cell.py (225 runs, shards 1-5 = seeds 101-105; run only after a GO)
"""
from __future__ import annotations

import hashlib
import json
import sys

from harness.experiments.r3.cell import ROOT, git

STAGES = {
    "r4_phase3_poc_cell": {"stage": "poc", "tasks": "harness/experiments/r4/phase3/poc.json",
                           "seeds": "(42, 43, 44)", "results": "reports/r4/phase3_poc"},
    "r4_phase3_cell": {"stage": "confirmatory", "tasks": "harness/experiments/r4/phase3/tasks.json",
                       "seeds": "(101, 102, 103, 104, 105)", "results": "reports/r4/phase3"},
}


def render(commit: str) -> dict:
    commit = git("rev-parse", commit)
    template = (ROOT / "kaggle" / "r4_phase3_cell.template.py").read_text()
    out = {}
    for name, st in STAGES.items():
        doc = json.loads(git("show", f"{commit}:{st['tasks']}"))
        sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
        if sha != doc.get("tasks_sha256") or doc.get("design") != "phase3" or doc.get("stage") != st["stage"]:
            raise SystemExit(f"{st['tasks']} at {commit[:7]} is not a sealed Phase 3 {st['stage']} task list")
        fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src"),
                 "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness"), "@@TASKS_SHA@@": sha,
                 "@@RESULTS_PATH@@": st["results"], "@@TASKS_PATH@@": st["tasks"], "@@SEEDS@@": st["seeds"],
                 "@@STAGE_KIND@@": st["stage"], "@@N_SHARDS@@": str(st["seeds"].count(",") + 1)}
        text = template
        for k, v in fills.items():
            if k not in text:
                raise SystemExit(f"{k} missing from the template")
            text = text.replace(k, v)
        if "@@" in text:
            raise SystemExit(f"an unfilled placeholder is left in {name}")
        (ROOT / "kaggle" / f"{name}.py").write_text(text)
        out[name] = {k: v for k, v in fills.items() if k in ("@@TASKS_SHA@@", "@@SEEDS@@")}
    return out


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        raise SystemExit(__doc__)
    print(render(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
