"""The repository names scorer v2 needs (`invented.py`), for newfn task sets planned before it existed.

    python -m harness.experiments.suite.newfn_names REPO CLONE     # -> repos/REPO/newfn_names.json

For each task: module-level names, names imported anywhere, instance attributes, and the members of the new
function's class and its bases, all from the tree at the task's parent revision. The task file itself is unchanged
(it is frozen); new task sets carry the same data as `repo_names`.
"""
from __future__ import annotations

import hashlib
import json
import sys

from harness.experiments.suite.invented import ROOT, repo_names_at


def build(repo: str, clone: str) -> int:
    tasks = json.loads((ROOT / repo / "newfn.json").read_text())["tasks"]
    out = {t["id"]: repo_names_at(clone, t["parent"], t.get("class_qname")) for t in tasks}
    body = {"type": "newfn repository names (scorer v2)", "repo": repo, "tasks": out,
            "tasks_sha256": hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()}
    (ROOT / repo / "newfn_names.json").write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    print(f"{repo}: {len(out)} tasks, {sum(v['bases_unresolved'] for v in out.values())} with unresolved bases")
    return 0


if __name__ == "__main__":
    sys.exit(build(*sys.argv[1:3]))
