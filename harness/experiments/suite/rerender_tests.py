"""Re-render the shown code of frozen type-6 tasks after the large-target cap (README, type 6, amendment 3).

    python -m harness.experiments.suite.rerender_tests REPO WORK_DIR

For each task of `repos/<REPO>/tests.json` not yet re-rendered: index the parent revision and recompute
`target_window` (`testgen.target_window`) and `prism_context` (`prism.impact.testcontext`, with its code cap).
Targets, gate results and coverage are unchanged. At most BATCH tasks per process (memory). Exit code 3: run again.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BATCH = 6


def main(argv=None) -> int:
    repo, work = (argv or sys.argv[1:])[:2]
    from prism.cli import build_pipeline
    from prism.impact.testcontext import TestContextBuilder, render
    from harness.experiments.suite.testgen import target_window
    path = Path("harness/experiments/suite/repos") / repo / "tests.json"
    data = json.loads(path.read_text())
    clone = str(Path(work) / "repos" / repo)
    done = 0
    for t in data["tasks"]:
        if t.get("rendered") == 3:
            continue
        if done >= BATCH:
            return 3
        wt = tempfile.mkdtemp(prefix="rr_")
        subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, t["parent"]], check=True,
                       capture_output=True)
        try:
            b = build_pipeline(wt, use_cache=False)[0]
            src = Path(wt, t["target_file"]).read_text(errors="replace").splitlines()
            lo, hi = t["target_lines"]
            a, z = target_window(lo, hi, len(src))
            t["target_window"], t["target_window_lines"] = "\n".join(src[a - 1:z]), [a, z]
            t["prism_context"] = render(TestContextBuilder(b, wt).build(t["target"], t["test_file"]))
            t["rendered"] = 3
        finally:
            subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
        data["tasks_sha256"] = hashlib.sha256(json.dumps(data["tasks"], sort_keys=True).encode()).hexdigest()
        path.write_text(json.dumps(data, indent=1) + "\n")
        done += 1
        print(f"  {t['id']}: re-rendered", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
