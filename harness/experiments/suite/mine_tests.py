"""Type 6 (write tests for a function): mine commits that only add tests for existing code (Python).

    python -m harness.experiments.suite.mine_tests OUT.json NAME=CLONE [NAME=CLONE ...] [--since 2019-01-01]

A commit qualifies when (README, type 6):
- it changes only test files (no source file), and adds 1-6 new test functions (`def test_...`) to a test
  file that already existed;
- the new tests are 3-80 lines each;
- it is no older than `--since`. Older revisions rarely install on the Pythons available today, and the
  scores need the tests to run.

The target, the production function the new tests call most, is chosen at planning, in the parent's index.
The candidate keeps the names each new test calls.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from harness.experiments.change_history.mine import SOURCE_EXT, TEST_PARTS, functions, git
from harness.experiments.suite.newfn import calls

MAX_NEW_TESTS = 6
MIN_TEST_LINES, MAX_TEST_LINES = 3, 80


def mine(name: str, repo: str, since: str) -> list[dict]:
    log = git(repo, "log", "--no-merges", f"--since={since}", "--format=%x01%H%x00%s%x00%cI", "--numstat")
    out = []
    for block in log.split("\x01")[1:]:
        head, *rows = block.strip("\n").split("\n")
        sha, subject, date = head.split("\x00", 2)
        paths = [r.split("\t")[2] for r in rows if len(r.split("\t")) == 3]
        py = [p for p in paths if p.endswith(".py")]
        if not py or any(not TEST_PARTS.search(p) for p in py) or any(p.endswith(SOURCE_EXT) and not p.endswith(".py")
                                                                      for p in paths):
            continue  # a source file changed too (or nothing in Python)
        for f in py:
            try:
                old = git(repo, "show", f"{sha}^:{f}").encode()
                new = git(repo, "show", f"{sha}:{f}").encode()
            except subprocess.CalledProcessError:
                continue  # a new test file: no existing test style in it to follow
            before, after = functions(f, old), functions(f, new)
            added = [q for q in sorted(after.keys() - before.keys()) if q.rsplit(".", 1)[-1].startswith("test")
                     and MIN_TEST_LINES <= after[q]["end"] - after[q]["start"] + 1 <= MAX_TEST_LINES]
            if not 1 <= len(added) <= MAX_NEW_TESTS:
                continue
            out.append({"repo": name, "commit": sha, "parent": git(repo, "rev-parse", f"{sha}^").strip(),
                        "subject": subject, "date": date, "test_file": f,
                        "tests": [{"name": q, "code": after[q]["code"], "start": after[q]["start"],
                                   "calls": sorted({n for _r, n in calls(after[q]["code"])})} for q in added]})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("repos", nargs="+")
    p.add_argument("--since", default="2019-01-01")
    args = p.parse_args(argv)
    found = []
    for spec in args.repos:
        name, _, repo = spec.partition("=")
        got = mine(name, repo, args.since)
        print(f"{name}: {len(got)} test-adding candidates", flush=True)
        found += got
        Path(args.out).write_text(json.dumps(found, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
