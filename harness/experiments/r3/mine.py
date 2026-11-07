"""R3 task mining, both families, over a commit-date window (no model calls, no test runs).

    python -m harness.experiments.r3.mine OUT_DIR --since 2024-10-01 --until 2024-11-30 NAME=CLONE ...

The same code mines the pilot window (2024-10-01 to 2024-11-30, pre-registration "Pilot-only tasks") and, later,
R3a's main window (on or after 2024-12-01). Only the dates differ. A commit is in the window when its committer date
(git `--since`/`--until`, UTC) is in it, as in `pool_scan.py`.

**Family B** (pre-registration, "Task families": "R1 forward's miner is used, keeping commits with at least 1 changed
caller and a test change"):
- R1 forward's miner is suite type 1's: `change_history/mine.py --forced` (pass 1 and pass 2), then
  `change_history/select.py --forced --no-self-callers`'s caller filter. That means:
  - 1-6 non-test source files and at most 200 changed lines;
  - an anchor whose change forces its callers (`forced_reasons`);
  - production callers in PRISM's index at the parent that the commit changed;
  - each counted caller's change touches the call (`touches_call`);
  - the anchor is never its own caller;
  - one task per commit: the anchor with the most changed callers.
- Plus at least one changed test file (`pool_scan.TEST`).
- **Start state:** the parent, plus the developer's change to the anchor only. **Depth stratum:** the number of
  changed callers (1, 2-4, 5+).

**Family N** (relaxed rule, amendment R3-2, item 5: "any new function a changed test names"; `pool_scan.py`'s
`family_n_relaxed`):
- 1-15 non-test source files, at least one changed test file;
- a new, non-test, non-nested function of 6-80 lines, which a changed test file mentions by name, and whose body
  calls at least 2 functions defined in the repository at the parent. The first such function is the candidate.
- What the prompt needs, as in suite type 2 (the pre-registration's Family N prompt):
  - the function's file already exists at the parent;
  - its class, for a method, already exists;
  - it has a description of at least 5 words (the docstring, or the JSDoc above it). Without one the task would be
    a guess.
  Attrition at each of these steps is reported.
- **Start state:** the parent. **Depth stratum:** the number of existing repository functions the body calls
  (2, 3-4, 5+).

**Both families:** backports, cherry-picks and repeats of one diff (`git patch-id`) are dropped first
(`window_commits`): two tasks from one change are not independent.

**Both families, per task:**
- `hidden_patch` is the commit's change to its test files, applied only for gating and scoring.
- `visible_tests` are the changed test files that already exist at the parent.
- `gold_files` and `gold_functions` here are provisional (the commit's source changes from the parent). The gate
  replaces them with the developer's non-test changes diffed from the start state, by the same code that scores the
  agent (`runner.snapshot_tree`, `runner.changed_functions`).

Output: `candidates_B.json`, `candidates_N.json` and `yield.json` (per repository: commits in the window, then each
step's survivors).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.experiments.change_history.mine import (MAX_FILES, MAX_LINES, SOURCE_EXT, TEST_PARTS, _lang,
                                                      changed_functions, functions, git, pass2)
from harness.experiments.change_history.select import forced_reasons, touches_call
from harness.experiments.r3.pool_scan import CALL, SOURCE as N_SOURCE, TEST, repo_functions, show
from harness.experiments.suite.mine_newfn import jsdoc_before, split_signature

MIN_PURPOSE_WORDS = 5
N_MIN_LINES, N_MAX_LINES = 6, 80
N_MAX_SOURCE_FILES = 15
USED_REPOS = {"scrapy", "aiohttp", "werkzeug", "tornado"}       # the pool table's "used" repositories
LANGUAGE = {"vitest": "js", "nitro": "js", "h3": "js", "execa": "js", "unstorage": "js", "msw": "js",
            "unplugin": "js",
            # R3-8's pre-screen (2026-11-07)
            "rollup": "js", "prettier": "js", "eslint": "js", "svelte": "js"}


BACKPORT = re.compile(r"\bbackport\b|cherry picked from commit", re.I)


def window_commits(clone: str, since: str, until: str) -> tuple[list[str], dict]:
    """The window's non-merge commits, oldest first, without backports and repeats of one change.

    - A backport or cherry-pick (its message says so: aiohttp's `[PR #9771/e80d5854 backport][3.10] ...`) repeats a
      change made elsewhere, so it is not a separate task.
    - Of commits with the same `git patch-id` (the same diff), only the first is kept.
    Two tasks from one change are not independent, which the tests over tasks assume.
    """
    commits = git(clone, "rev-list", "--reverse", "--no-merges", f"--since={since}T00:00:00Z",
                  f"--until={until}T23:59:59Z", "HEAD").split()
    kept, seen, n_backport, n_repeat = [], set(), 0, 0
    for c in commits:
        if BACKPORT.search(git(clone, "log", "-1", "--format=%B", c)):
            n_backport += 1
            continue
        diff = subprocess.run(["git", "-C", clone, "show", "--format=", c], capture_output=True).stdout
        pid = subprocess.run(["git", "-C", clone, "patch-id", "--stable"], input=diff, capture_output=True,
                             text=False).stdout.split()[:1]
        if pid and pid[0] in seen:
            n_repeat += 1
            continue
        if pid:
            seen.add(pid[0])
        kept.append(c)
    return kept, {"commits_in_window": len(commits), "backports_dropped": n_backport, "repeats_dropped": n_repeat}


def commit_meta(clone: str, c: str) -> dict:
    sha, parent, cdate, adate, subject = git(clone, "log", "-1", "--format=%H%x00%P%x00%cI%x00%aI%x00%s", c).strip() \
        .split("\x00", 4)
    return {"commit": sha, "parent": parent.split()[0] if parent else "", "commit_date": cdate, "author_date": adate,
            "subject": subject}


def changed_files(clone: str, parent: str, c: str) -> list[str]:
    return [f for f in git(clone, "diff", "--name-only", "--no-renames", parent, c).split("\n") if f]


def test_patch(clone: str, parent: str, c: str, tests: list[str]) -> str:
    return git(clone, "diff", "--binary", "--no-renames", parent, c, "--", *tests) if tests else ""


def visible_tests(clone: str, parent: str, tests: list[str]) -> list[str]:
    exists = set(git(clone, "ls-tree", "-r", "--name-only", parent, "--", *tests).split("\n")) if tests else set()
    runnable = re.compile(r"(\.py|\.[mc]?[jt]sx?)$")
    return sorted(t for t in tests if t in exists and runnable.search(t) and not t.endswith(("conftest.py", "__init__.py")))


def source_changes(clone: str, parent: str, c: str) -> tuple[list[str], set[str]]:
    """The commit's changed source files and `file::qualified name` of every function added, removed or changed."""
    files = [f for f in changed_files(clone, parent, c) if f.endswith(N_SOURCE) and not TEST.search(f)
             and not f.endswith(".d.ts")]
    fns = set()
    for f in files:
        old, new = show(clone, parent, f), show(clone, c, f)
        fo, fn = (functions(f, old) if old else {}), (functions(f, new) if new else {})
        lang = _lang(f)
        from harness.experiments.change_history.mine import _normalized
        for q in set(fo) | set(fn):
            if _normalized(fo.get(q, {}).get("code", ""), lang) != _normalized(fn.get(q, {}).get("code", ""), lang):
                fns.add(f"{f}::{q}")
    return files, fns


def free_index_caches() -> None:
    """Each pass-2 index is built in its own worktree, so PRISM's parse cache (keyed by path) and its derived caches
    only grow over a repository's history. They are dropped after every index (the same leak `ToolLayer.close`
    fixes for runs)."""
    from prism.parser.cache import clear_cache
    from prism.traversal._cache_keys import evict_derived_caches
    clear_cache()
    evict_derived_caches()


def b_stratum(n: int) -> str:
    return "1" if n == 1 else "2-4" if n <= 4 else "5+"


def caller_bucket(n: int) -> str:
    """The caller-count distribution R3a's mining reports (finer than the stratum): 1, 2, 3-4, 5+."""
    return "1" if n == 1 else "2" if n == 2 else "3-4" if n <= 4 else "5+"


def n_stratum(n: int) -> str:
    return "2" if n == 2 else "3-4" if n <= 4 else "5+"


# ----------------------------------------------------------------------------------------------- Family B
def b_pass1(clone: str, commits: list[str]) -> tuple[list[dict], dict]:
    """`change_history.mine.pass1(forced=True)` over the given commits (it scans the newest N; here a date window)."""
    counts = {"size_ok": 0, "two_changed_functions": 0, "forced_anchor_with_caller": 0}
    out = []
    for c in commits:
        rows = git(clone, "show", "--no-renames", "--format=", "--numstat", c).splitlines()
        files, lines = [], 0
        for row in rows:
            parts = row.split("\t")
            if len(parts) == 3:
                added, removed, path = parts
                if path.endswith(SOURCE_EXT) and not TEST_PARTS.search(path) and not path.endswith(".d.ts"):
                    files.append(path)
                    lines += (int(added) if added.isdigit() else 0) + (int(removed) if removed.isdigit() else 0)
        if not 1 <= len(files) <= MAX_FILES or lines > MAX_LINES:
            continue
        counts["size_ok"] += 1
        changed = changed_functions(clone, c, files)
        if len(changed) < 2:
            continue
        counts["two_changed_functions"] += 1
        from harness.experiments.change_history.mine import MAX_ANCHOR_LINES
        pairs = []
        for anchor in changed:
            leaf = anchor[1].rsplit(".", 1)[-1]
            if leaf.startswith("__") or len(changed[anchor]["old"].splitlines()) > MAX_ANCHOR_LINES:
                continue
            if not forced_reasons({"anchor_old": changed[anchor]["old"], "anchor_new": changed[anchor]["new"]}):
                continue
            callers = [k for k in changed if k != anchor and re.search(rf"\b{re.escape(leaf)}\s*\(", changed[k]["old"])]
            if callers:
                pairs.append((anchor, callers))
        if pairs:
            counts["forced_anchor_with_caller"] += 1
            subject = git(clone, "log", "-1", "--format=%s", c).strip()
            out.append({"commit": c, "subject": subject, "files": files, "lines": lines, "changed": changed,
                        "pairs": pairs})
    return out, counts


def start_patch_for(clone: str, parent: str, path: str, old_code: str, new_code: str) -> str | None:
    """The parent's file with the anchor's code replaced by the developer's new code, as a patch; None when the old
    code is not found exactly once or the file does not end in a newline."""
    text = (show(clone, parent, path) or b"").decode("utf8")
    if text.count(old_code) != 1 or not text.endswith("\n"):
        return None
    new_text = text.replace(old_code, new_code, 1)
    return "".join(difflib.unified_diff(text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                        f"a/{path}", f"b/{path}"))


def b_prompt(anchor: str, path: str, patch: str) -> str:
    body = "".join(ln for ln in patch.splitlines(keepends=True)[2:])
    return (f"Function `{anchor}` in `{path}` was changed as shown:\n\n```diff\n{body}```\n\n"
            f"Update the rest of the project so its tests pass. The change to `{anchor}` is already in the working "
            f"tree. When your changes are complete, call done.")


def mine_b(name: str, clone: str, commits: list[str], scratch: str) -> tuple[list[dict], dict]:
    cands, counts = b_pass1(clone, commits)
    counts.update(index_callers_changed=0, caller_touches_call=0, test_change=0, start_patch_ok=0)
    tasks = []
    for cand in cands:
        try:
            found = pass2(name, clone, cand, scratch, 6000)
        except Exception as exc:  # one bad revision must not stop the mining
            print(f"  {name} {cand['commit'][:10]}: index skipped ({type(exc).__name__}: {exc})", flush=True)
            continue
        finally:
            free_index_caches()
        kept = []
        for t in found:
            real = [c for c in t["changed_callers"] if c["qname"] != t["anchor"] and touches_call(t, c)]
            if real:
                kept.append({**t, "changed_callers": real})
        if found:
            counts["index_callers_changed"] += 1
        if not kept:
            continue
        counts["caller_touches_call"] += 1
        t = max(kept, key=lambda t: len(t["changed_callers"]))
        meta = commit_meta(clone, t["commit"])
        files = changed_files(clone, meta["parent"], t["commit"])
        tests = [f for f in files if TEST.search(f)]
        if not tests:
            continue
        counts["test_change"] += 1
        sp = start_patch_for(clone, meta["parent"], t["anchor_file"], t["anchor_old"], t["anchor_new"])
        if not sp:
            continue
        counts["start_patch_ok"] += 1
        src_files, src_fns = source_changes(clone, meta["parent"], t["commit"])
        anchor_leafq = f"{t['anchor_file']}::" + next(
            (q for (f, q) in cand["changed"] if f == t["anchor_file"] and cand["changed"][(f, q)]["old"] == t["anchor_old"]),
            t["anchor"].rsplit(".", 1)[-1])
        tasks.append({
            "family": "B", "repo": name, "language": LANGUAGE.get(name, "python"), **meta,
            "anchor": t["anchor"], "anchor_file": t["anchor_file"], "forced": forced_reasons(t),
            "changed_callers": [c["qname"] for c in t["changed_callers"]],
            "depth": len(t["changed_callers"]), "stratum": b_stratum(len(t["changed_callers"])),
            "caller_bucket": caller_bucket(len(t["changed_callers"])),
            "start_patch": sp, "start_files": [t["anchor_file"]], "start_functions": [anchor_leafq],
            "after_index_patch": None, "hidden_patch": test_patch(clone, meta["parent"], t["commit"], tests),
            "test_files": tests, "visible_tests": visible_tests(clone, meta["parent"], tests),
            "gold_files": sorted(src_files), "gold_functions": sorted(src_fns - {anchor_leafq}),
            "prompt": b_prompt(t["anchor"], t["anchor_file"], sp)})
    return tasks, counts


# ----------------------------------------------------------------------------------------------- Family N
def n_prompt(path: str, qual: str, sig: str, doc: str, after: str | None) -> str:
    cls = qual.rsplit(".", 1)[0] if "." in qual else None
    where = f"in class `{cls}` of `{path}`" if cls else f"in `{path}`"
    place = f", after `{after}`" if after else ""
    return (f"Add a new function {where}{place}. Its signature and description:\n\n```\n{sig.strip()}\n```\n\n"
            f"{doc.strip()}\n\nImplement it so the project's tests pass. When your changes are complete, call done.")


def mine_n(name: str, clone: str, commits: list[str]) -> tuple[list[dict], dict]:
    counts = {"size_ok": 0, "relaxed_candidate": 0, "file_and_class_exist": 0, "description": 0}
    tasks, known_at = [], {}
    for c in commits:
        meta = commit_meta(clone, c)
        if not meta["parent"]:
            continue
        files = changed_files(clone, meta["parent"], c)
        src = [f for f in files if f.endswith(N_SOURCE) and not TEST.search(f)]
        tests = [f for f in files if TEST.search(f)]
        if not (tests and 1 <= len(src) <= N_MAX_SOURCE_FILES):
            continue
        counts["size_ok"] += 1
        texts = " ".join((show(clone, c, t) or b"").decode("utf8", "replace") for t in tests)
        pick = None
        for f in src:
            old, new = show(clone, meta["parent"], f), show(clone, c, f)
            if not new:
                continue
            fo, fn = (functions(f, old) if old else {}), functions(f, new)
            for q, d in fn.items():
                if q in fo or not N_MIN_LINES <= d["end"] - d["start"] + 1 <= N_MAX_LINES:
                    continue
                if any(q.startswith(p + ".") for p in fn if p != q and not p.rsplit(".", 1)[-1][:1].isupper()):
                    continue   # nested in another function
                leaf = q.rsplit(".", 1)[-1]
                if not re.search(rf"\b{re.escape(leaf)}\b", texts):
                    continue
                if meta["parent"] not in known_at:
                    known_at[meta["parent"]] = repo_functions(clone, meta["parent"])
                used = {m.group(1) for m in CALL.finditer(d["code"])} & known_at[meta["parent"]] - {leaf}
                if len(used) >= 2:
                    pick = (f, q, d, old, new, sorted(used))
                    break
            if pick:
                break
        if not pick:
            continue
        counts["relaxed_candidate"] += 1
        f, q, d, old, new, used = pick
        if old is None:
            continue
        if "." in q and not re.search(rf"(?m)^\s*(export\s+)?(default\s+)?class\s+"
                                      rf"{re.escape(q.rsplit('.', 1)[0].rsplit('.', 1)[-1])}\b",
                                      old.decode("utf8", "replace")):
            continue
        counts["file_and_class_exist"] += 1
        lang = _lang(f)
        sig, doc, _body = split_signature(d["code"], lang)
        if not doc and lang != "python":
            doc = jsdoc_before(new.decode("utf8", "replace"), d["start"])
        if len(re.findall(r"[A-Za-z]{2,}", doc)) < MIN_PURPOSE_WORDS:
            continue
        counts["description"] += 1
        fn_new = functions(f, new)
        scope = q.rsplit(".", 1)[0] if "." in q else None
        prev = [p for p, v in fn_new.items() if v["end"] < d["start"] and p in (functions(f, old) or {})
                and ((p.rsplit(".", 1)[0] == scope) if scope else "." not in p)]
        after = max(prev, key=lambda p: fn_new[p]["end"]) if prev else None
        src_files, src_fns = source_changes(clone, meta["parent"], c)
        tasks.append({
            "family": "N", "repo": name, "language": LANGUAGE.get(name, "python"), **meta,
            "target": f"{f}::{q}", "target_file": f, "signature": sig, "docstring": doc, "after": after,
            "helpers": used, "depth": len(used), "stratum": n_stratum(len(used)),
            "start_patch": None, "start_files": [], "start_functions": [], "after_index_patch": None,
            "hidden_patch": test_patch(clone, meta["parent"], c, tests), "test_files": tests,
            "visible_tests": visible_tests(clone, meta["parent"], tests),
            "gold_files": sorted(src_files), "gold_functions": sorted(src_fns),
            "prompt": n_prompt(f, q, sig, doc, after)})
    return tasks, counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE[:FAMILIES]",
                   help="FAMILIES is B, N or BN (default BN): the families the repository is in the pool for")
    p.add_argument("--since", required=True)
    p.add_argument("--until", required=True)
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="r3mine_", dir=str(out))
    ypath = out / "yield.json"
    yld = json.loads(ypath.read_text()) if ypath.exists() else {}
    allc = {fam: (json.loads((out / f"candidates_{fam}.json").read_text()) if (out / f"candidates_{fam}.json").exists()
                  else []) for fam in "BN"}
    for spec in a.repos:
        name, _, rest = spec.partition("=")
        clone, _, fams = rest.partition(":")
        fams = fams or "BN"
        commits, wcounts = window_commits(clone, a.since, a.until)
        for fam in fams:
            key = f"{name}:{fam}"
            if key in yld:
                continue
            if fam == "B":
                tasks, counts = mine_b(name, clone, commits, scratch)
            else:
                tasks, counts = mine_n(name, clone, commits)
            yld[key] = {"repo": name, "family": fam, **wcounts, "commits_kept": len(commits), **counts,
                        "candidates": len(tasks)}
            allc[fam] = [t for t in allc[fam] if t["repo"] != name] + tasks
            (out / f"candidates_{fam}.json").write_text(json.dumps(allc[fam], indent=1) + "\n")
            ypath.write_text(json.dumps(yld, indent=1) + "\n")
            print(json.dumps(yld[key]), flush=True)
    subprocess.run(["rm", "-rf", scratch])
    return 0


if __name__ == "__main__":
    sys.exit(main())
