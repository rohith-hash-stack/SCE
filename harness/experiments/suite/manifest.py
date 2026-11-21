"""The suite's frozen task files and their task-list sha256, for the Kaggle cell to check.

    python -m harness.experiments.suite.manifest write      # harness/experiments/suite/manifest.json
    python -m harness.experiments.suite.manifest check      # every file still matches

Order of the run (`ORDER`): type 1 (`forward`, three versions) first, then the types of the model alone
(bugs, concepts, blast), then the code-writing types (newfn, tests). Type 6 answers are scored afterwards by
running them (`testgen score`), outside Kaggle.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path("harness/experiments/suite/repos")
MANIFEST = Path("harness/experiments/suite/manifest.json")
ORDER = ("forward", "bugs", "concepts", "blast", "newfn", "tests")
REPOS = ("marshmallow", "werkzeug", "rich", "axios", "mocha", "tornado", "hapi", "fastify", "scrapy", "aiohttp")
RUNNER = {"forward": "harness.experiments.change_history.run"}  # the rest: harness.experiments.suite.run <kind>
KIND = {"bugs": "bugs", "concepts": "concepts", "blast": "blast", "newfn": "newfn", "tests": "testgen"}
ARMS = {"forward": 3, "bugs": 3, "concepts": 3, "blast": 3, "newfn": 2, "tests": 2}


def sha(tasks: list) -> str:
    return hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()


def entries() -> list[dict]:
    out = []
    for kind in ORDER:
        for repo in REPOS:
            path = ROOT / repo / f"{kind}.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            if data.get("status") != "frozen" or not data["tasks"]:
                continue
            out.append({"repo": repo, "type": kind, "path": str(path), "tasks": len(data["tasks"]),
                        "calls": len(data["tasks"]) * ARMS[kind], "sha256": sha(data["tasks"])})
    return out


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["check"])[0]
    if cmd == "write":
        items = entries()
        MANIFEST.write_text(json.dumps({"order": list(ORDER), "files": items,
                                        "tasks": sum(x["tasks"] for x in items),
                                        "calls": sum(x["calls"] for x in items)}, indent=1) + "\n")
        print(f"{len(items)} files, {sum(x['tasks'] for x in items)} tasks, {sum(x['calls'] for x in items)} model calls")
        return 0
    bad = 0
    for x in json.loads(MANIFEST.read_text())["files"]:
        got = sha(json.loads(Path(x["path"]).read_text())["tasks"])
        if got != x["sha256"]:
            bad += 1
            print(f"CHANGED {x['path']}")
    print("manifest ok" if not bad else f"{bad} files changed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
