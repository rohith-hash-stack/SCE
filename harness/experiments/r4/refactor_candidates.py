"""R4 refactor candidates by the refactor signature, not by commit message (the user's decision, 2026-11-10).

    python -B -m harness.experiments.r4.refactor_candidates OUT_DIR --since 2024-12-01 NAME=CLONE ...

"The 628 'message mentions refactor, no tests changed' are actually the right signature: code changed, tests didn't
need to change (because behavior preserved)."

The signature, per commit in R3's window (`r3/mine.py` `window_commits`: no merges, backports or patch-id repeats):
- **source files changed in `src/`, `lib/` or equivalent:** 1-6 non-test source files (`pool_scan.SOURCE`, not
  `.d.ts`), none under docs, examples, benchmarks, scripts, tools, playgrounds or fixtures;
- **no test files changed** (`pool_scan.TEST`); other files (docs, config, changelogs) may change;
- **diff size > 5 lines** (source lines added + removed), at most 400; and at least one function's code differs once
  comments and whitespace are removed (`change_history.mine._normalized`), so a comment-only or format-only change
  is not a refactor;
- **"tests pass at parent AND at HEAD"** is the gate's check (`gate_families.py`, family `refactor`). It runs tests,
  so it is not done here.

The commit message is recorded but never filtered on. Output: `candidates_refactor_sig.json`, `yield.json`. Resumable
per repository.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from harness.experiments.change_history.mine import _lang, _normalized, functions
from harness.experiments.r3.mine import LANGUAGE, changed_files, commit_meta, window_commits
from harness.experiments.r3.pool_scan import SOURCE, TEST, show
from harness.experiments.r4.mine_families import _numstat

NON_PRODUCT = re.compile(r"(^|/)(docs?|examples?|benchmarks?|bench|scripts?|tools?|playgrounds?|fixtures?|"
                         r"__fixtures__|e2e|demo|samples?)/", re.I)
MIN_LINES, MAX_LINES, MAX_FILES = 6, 400, 6


def mine_repo(name: str, clone: str, commits: list[str]) -> tuple[list[dict], dict]:
    counts = {"source_only": 0, "product_paths": 0, "size_ok": 0, "code_differs": 0}
    out = []
    for c in commits:
        meta = commit_meta(clone, c)
        if not meta["parent"]:
            continue
        files = changed_files(clone, meta["parent"], c)
        src = [f for f in files if f.endswith(SOURCE) and not TEST.search(f) and not f.endswith(".d.ts")]
        if not src or any(TEST.search(f) for f in files):
            continue
        counts["source_only"] += 1
        if any(NON_PRODUCT.search(f) for f in src) or len(src) > MAX_FILES:
            continue
        counts["product_paths"] += 1
        stat = _numstat(clone, meta["parent"], c)
        lines = sum(sum(stat.get(f, (0, 0))) for f in src)
        if not MIN_LINES <= lines <= MAX_LINES:
            continue
        counts["size_ok"] += 1
        changed = []
        for f in src:
            old, new = show(clone, meta["parent"], f), show(clone, c, f)
            fo, fn = (functions(f, old) if old else {}), (functions(f, new) if new else {})
            lang = _lang(f)
            changed += [f"{f}::{q}" for q in sorted(set(fo) | set(fn))
                        if _normalized(fo.get(q, {}).get("code", ""), lang) != _normalized(fn.get(q, {}).get("code", ""), lang)]
        if not changed:
            continue
        counts["code_differs"] += 1
        out.append({"family": "refactor", "kind": "signature", "repo": name, "language": LANGUAGE.get(name, "python"),
                    **meta, "source_files": src, "test_files": [], "other_files": [f for f in files if f not in src],
                    "source_lines_changed": lines, "changed_functions": changed[:30],
                    "n_changed_functions": len(changed)})
    return out, counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--since", required=True)
    p.add_argument("--until", default="2099-12-31")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ypath, cpath = out / "yield.json", out / "candidates_refactor_sig.json"
    yld = json.loads(ypath.read_text()) if ypath.exists() else {}
    cands = json.loads(cpath.read_text()) if cpath.exists() else []
    for spec in a.repos:
        name, _, clone = spec.partition("=")
        if name in yld:
            continue
        commits, wcounts = window_commits(clone, a.since, a.until)
        got, counts = mine_repo(name, clone, commits)
        yld[name] = {**wcounts, "commits_kept": len(commits), **counts, "candidates": len(got)}
        cands = [t for t in cands if t["repo"] != name] + got
        cpath.write_text(json.dumps(cands, indent=1) + "\n")
        ypath.write_text(json.dumps(yld, indent=1) + "\n")
        print(name, json.dumps(yld[name]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
