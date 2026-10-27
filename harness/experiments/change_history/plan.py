"""Add the map and second-ring plan to frozen change-propagation tasks (no model).

    python -m harness.experiments.change_history.plan TASKS.json NAME=CLONE [NAME=CLONE ...]

For each task: index the parent revision, rebuild the change context and check it is the task's
`prism_context` byte for byte (else abort: the code changed since mining), then add
`prism.impact.overview.plan_overview`'s plan as `overview_plan`. The task list's sha256 is recomputed; the
selection itself is not touched.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path, repos = Path(args[0]), dict(a.split("=", 1) for a in args[1:])
    data = json.loads(path.read_text())
    if data.get("status") != "frozen":
        raise SystemExit("tasks are not frozen")
    from prism.cli import build_pipeline
    from prism.impact.context import ChangeContextBuilder, render
    from prism.impact.overview import plan_overview
    for t in data["tasks"]:
        repo = repos[t["repo"]]
        work = tempfile.mkdtemp(prefix="plan_")
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "--detach", work, t["parent"]], check=True,
                       capture_output=True)
        try:
            b = build_pipeline(work, use_cache=False)[0]
            cb = ChangeContextBuilder(b, work)
            ctx = cb.build(t["anchor"], budget_tokens=6000, include_tests=False)
            if render(ctx) != t["prism_context"]:
                raise SystemExit(f"{t['id']}: the change context differs from the frozen one")
            t["overview_plan"] = plan_overview(cb, t["anchor"], ctx).to_dict()
            print(f"{t['id']}: {len(t['overview_plan']['ring'])} second-ring functions, "
                  f"{len(t['overview_plan']['outlines'])} files to describe", flush=True)
        finally:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", work], capture_output=True)
    data["tasks_sha256"] = hashlib.sha256(json.dumps(data["tasks"], sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(data, indent=1) + "\n")
    print(f"planned {len(data['tasks'])} tasks; sha256 {data['tasks_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
