"""R4 candidate mining for the refactor, delete, move, tests and multi-function feature families (candidate lists only; no gating).

    python -B -m harness.experiments.r4.mine_families OUT_DIR --since 2024-12-01 NAME=CLONE ...

No model calls and no test runs. Each family's scope (task count, gating rules, start state) is decided after R3a;
this pass only finds commits of each shape, so the long pole (mining, then gating) starts now. A candidate is not a
task until a gate like R3's (`r3/gate.py`) has run on it.

**Shared:** the commit window and the drops are R3's (`r3/mine.py` `window_commits`: no merges, no backports or
cherry-picks, one commit per `git patch-id`). Per commit, the changed non-test source files are parsed at the parent
and at the commit (`change_history/mine.functions`), and functions are paired by normalized body similarity
(comments, docstrings and whitespace removed; the function's own name masked):
- **removed**: present at the parent, absent at the commit, with no similar body anywhere in the commit's
  changed files;
- **moved**: absent from file A, present in another file B under the same name, body similarity >= 0.85;
- **renamed**: absent under its old name, present under a new name (any changed file), similarity >= 0.85.

**Families (one candidate per commit; a commit may be a candidate in more than one family):**

| Family | A commit is a candidate when |
|---|---|
| delete | it removes a non-test function that had callers at the parent in other source files, and the commit changes those files so the name's mentions drop |
| move | it moves a non-test function between files, and at least one other file's import lines are changed to name it |
| refactor | (a) it renames a non-test function and updates at least one caller file (old name mentioned less, new name more); or (b) a refactor-worded commit (`refactor`, `extract`, `inline`, `simplify`, `clean up`, `restructure`), not a chore commit, changing 1-6 source files and **no test files**, so the existing tests are the specification |
| feature | it adds at least 2 new non-test, non-nested functions of 4+ lines (not paired as moved or renamed) and changes at least one test file that names at least one of them (roadmap D2) |
| tests | it is not a chore commit (lint, format, dependency bump: `CHORE`), changes only test files (plus at most docs/config) and adds at least one test function, and the new tests name at least one non-test function of the repository |

Size limits, as R3's Family B: 1-6 non-test source files and at most 400 changed source lines for delete, move and
refactor (wider than R3's 200: these changes touch many call sites). Tests: 1-4 test files, at most 300 added lines.

Output: `candidates_<family>.json` and `yield.json` (per repository and family: commits kept, then each step's
survivors). Resumable: a repository already in `yield.json` is skipped.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

from harness.experiments.change_history.mine import _lang, _normalized, functions, git
from harness.experiments.r3.mine import LANGUAGE, changed_files, commit_meta, window_commits
from harness.experiments.r3.pool_scan import SOURCE, TEST, show

FAMILIES = ("delete", "move", "refactor", "tests", "feature")
FEATURE_MIN_LINES = 4
SIMILAR = 0.85
MAX_SRC_FILES, MAX_SRC_LINES = 6, 400
MAX_TEST_FILES, MAX_TEST_ADDED = 4, 300
REFACTOR_WORDS = re.compile(r"\b(refactor\w*|extract\w*|inline[sd]?|simplif\w*|clean[- ]?up|restructur\w*)\b", re.I)
IMPORT_LINE = re.compile(r"^\s*(from\s+\S+\s+import\b|import\b|export\s+\{|export\s+\*|.*\brequire\()")
OTHER_OK = re.compile(r"\.(md|rst|txt|toml|cfg|ini|ya?ml|json|lock)$|(^|/)(docs?|changes?|CHANGES|news)/", re.I)
PY_TEST_FN = re.compile(r"^\+\s*(async\s+)?def\s+(test\w*)\s*\(", re.M)
JS_TEST_FN = re.compile(r"^\+.*\b(it|test)(\.\w+)?\(\s*['\"`]", re.M)
WORD = re.compile(r"[A-Za-z_$][\w$]*")
CHORE = re.compile(r"\b(ruff|lint\w*|flake8|pylint|mypy|pyright|eslint|biome|prettier|format\w*|typos?|bump\w*|deps?|"
                   r"dependabot|pre-commit|ci|codespell|isort|black|renovate)\b", re.I)


def _leaf(q: str) -> str:
    return q.rsplit(".", 1)[-1]


def _nameable(leaf: str) -> bool:
    """A name whose mentions can be counted: not a dunder, not anonymous, longer than 3 characters."""
    return len(leaf) > 3 and not (leaf.startswith("__") and leaf.endswith("__")) and not leaf.startswith("<")


def _body(code: str, leaf: str, lang: str) -> str:
    return re.sub(rf"(?<![\w$]){re.escape(leaf)}(?![\w$])", "\x00", _normalized(code, lang))


def _sim(a: str, b: str) -> float:
    if not a or not b or abs(len(a) - len(b)) > 0.4 * max(len(a), len(b)):
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _numstat(clone: str, parent: str, c: str) -> dict[str, tuple[int, int]]:
    out = {}
    for line in git(clone, "diff", "--numstat", "--no-renames", parent, c).splitlines():
        a, d, f = (line.split("\t", 2) + ["", ""])[:3]
        out[f] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    return out


def _mentions(text: bytes | None, name: str) -> int:
    if not text:
        return 0
    return len(re.findall(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text.decode("utf8", "replace")))


def _grep_files(clone: str, rev: str, name: str) -> list[str]:
    r = subprocess.run(["git", "-C", clone, "grep", "-l", "-w", "-F", name, rev, "--", *[f"*{e}" for e in SOURCE]],
                       capture_output=True, text=True, errors="replace")
    return [line.split(":", 1)[1] for line in r.stdout.splitlines() if ":" in line]


def _pairs(clone: str, parent: str, c: str, src: list[str]) -> dict:
    """Removed, added, moved and renamed functions among the commit's changed non-test source files."""
    before, after = {}, {}
    for f in src:
        old, new = show(clone, parent, f), show(clone, c, f)
        for side, text in ((before, old), (after, new)):
            if text:
                for q, d in functions(f, text).items():
                    side[(f, q)] = d
    gone = [k for k in before if k not in after]
    new = [k for k in after if k not in before]
    moved, renamed, used_new = [], [], set()
    for f, q in gone:
        lang, leaf = _lang(f), _leaf(q)
        b0 = _body(before[(f, q)]["code"], leaf, lang)
        best = None
        for g, r in new:
            if (g, r) in used_new or _lang(g) != lang:
                continue
            s = _sim(b0, _body(after[(g, r)]["code"], _leaf(r), lang))
            if s >= SIMILAR and (best is None or s > best[0]):
                best = (s, g, r)
        if best is None:
            continue
        s, g, r = best
        used_new.add((g, r))
        if _leaf(r) == leaf and g != f:
            moved.append({"from": f"{f}::{q}", "to": f"{g}::{r}", "similarity": round(s, 3)})
        elif _leaf(r) != leaf:
            renamed.append({"from": f"{f}::{q}", "to": f"{g}::{r}", "similarity": round(s, 3)})
    paired = {m["from"] for m in moved + renamed}
    removed = [{"function": f"{f}::{q}", "lines": before[(f, q)]["end"] - before[(f, q)]["start"] + 1}
               for f, q in gone if f"{f}::{q}" not in paired and _nameable(_leaf(q))]
    added = [(g, r) for g, r in new if (g, r) not in used_new]
    return {"removed": removed, "moved": moved, "renamed": renamed, "added": [f"{g}::{r}" for g, r in added],
            "added_lines": {f"{g}::{r}": after[(g, r)]["end"] - after[(g, r)]["start"] + 1 for g, r in added}}


def _caller_files(clone: str, parent: str, c: str, changed: set[str], name: str, home: str,
                  drop_only: bool, new_name: str | None = None) -> tuple[list[str], list[str]]:
    """(files mentioning `name` at the parent outside its own file, those of them the commit updates)."""
    files = [f for f in _grep_files(clone, parent, name) if f != home and f.endswith(SOURCE)]
    updated = []
    for f in files:
        if f not in changed:
            continue
        a, b = _mentions(show(clone, parent, f), name), _mentions(show(clone, c, f), name)
        if b < a and (new_name is None or _mentions(show(clone, c, f), new_name) > _mentions(show(clone, parent, f),
                                                                                              new_name)):
            updated.append(f)
    return files, updated


def _import_changed(clone: str, parent: str, c: str, files: list[str], name: str) -> list[str]:
    out = []
    for f in files:
        diff = git(clone, "diff", "-U0", "--no-renames", parent, c, "--", f)
        lines = [ln[1:] for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
        if any(IMPORT_LINE.match(ln) and re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", ln) for ln in lines):
            out.append(f)
    return out


def mine_repo(name: str, clone: str, commits: list[str]) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    cands = {fam: [] for fam in FAMILIES}
    counts = {"delete": {"size_ok": 0, "function_removed": 0, "had_callers": 0, "callers_updated": 0},
              "move": {"size_ok": 0, "function_moved": 0, "imports_updated": 0},
              "refactor": {"size_ok": 0, "renamed_with_callers": 0, "worded_no_test_change": 0},
              "tests": {"tests_only": 0, "adds_test_functions": 0, "names_repo_function": 0},
              "feature": {"size_ok": 0, "adds_2_functions": 0, "tests_name_them": 0}}
    defined_at: dict[str, set[str]] = {}
    for c in commits:
        meta = commit_meta(clone, c)
        if not meta["parent"]:
            continue
        files = changed_files(clone, meta["parent"], c)
        stat = _numstat(clone, meta["parent"], c)
        src = [f for f in files if f.endswith(SOURCE) and not TEST.search(f) and not f.endswith(".d.ts")]
        tests = [f for f in files if TEST.search(f)]
        other = [f for f in files if f not in src and f not in tests]
        src_lines = sum(sum(stat.get(f, (0, 0))) for f in src)
        base = {"family": None, "repo": name, "language": LANGUAGE.get(name, "python"), **meta,
                "source_files": src, "test_files": tests, "source_lines_changed": src_lines}

        # tests: only test files (and docs/config) change, new test functions are added.
        chore = bool(CHORE.search(meta["subject"]))
        if tests and not src and not chore and len(tests) <= MAX_TEST_FILES and all(OTHER_OK.search(f) for f in other):
            added = sum(stat.get(f, (0, 0))[0] for f in tests)
            if added <= MAX_TEST_ADDED:
                counts["tests"]["tests_only"] += 1
                diff = git(clone, "diff", "-U0", "--no-renames", meta["parent"], c, "--", *tests)
                new_tests = [m.group(2) for m in PY_TEST_FN.finditer(diff)] + \
                            [m.group(1) for m in JS_TEST_FN.finditer(diff)]
                if new_tests:
                    counts["tests"]["adds_test_functions"] += 1
                    if meta["parent"] not in defined_at:
                        defined_at[meta["parent"]] = _defined(clone, meta["parent"])
                    added_text = "\n".join(ln for ln in diff.splitlines() if ln.startswith("+"))
                    named = sorted(set(WORD.findall(added_text)) & defined_at[meta["parent"]] - {"test", "it"})
                    if named:
                        counts["tests"]["names_repo_function"] += 1
                        cands["tests"].append({**base, "family": "tests", "new_tests": len(new_tests),
                                               "test_lines_added": added, "names": named[:20]})

        if not (1 <= len(src) <= MAX_SRC_FILES and src_lines <= MAX_SRC_LINES):
            continue
        for fam in ("delete", "move", "refactor", "feature"):
            counts[fam]["size_ok"] += 1
        pr = _pairs(clone, meta["parent"], c, src)
        changed = set(files)

        # feature: 2+ new production functions, named by a changed test
        new_fns = [k for k in pr["added"] if _nameable(_leaf(k.split("::", 1)[1]))
                   and pr["added_lines"].get(k, 0) >= FEATURE_MIN_LINES
                   and not any(k.split("::", 1)[1].startswith(o.split("::", 1)[1] + ".") for o in pr["added"]
                               if o != k and o.split("::")[0] == k.split("::")[0]
                               and not _leaf(o.split("::", 1)[1])[:1].isupper())]
        if len(new_fns) >= 2:
            counts["feature"]["adds_2_functions"] += 1
            if tests:
                ttext = b" ".join(show(clone, c, t) or b"" for t in tests)
                named = [k for k in new_fns if _mentions(ttext, _leaf(k.split("::", 1)[1]))]
                if named:
                    counts["feature"]["tests_name_them"] += 1
                    cands["feature"].append({**base, "family": "feature", "new_functions": new_fns[:20],
                                             "named_by_tests": named[:20]})

        # delete
        if pr["removed"]:
            counts["delete"]["function_removed"] += 1
            best = None
            for r in pr["removed"]:
                f, q = r["function"].split("::", 1)
                callers, updated = _caller_files(clone, meta["parent"], c, changed, _leaf(q), f, True)
                if callers and (best is None or len(updated) > len(best[2])):
                    best = (r, callers, updated)
            if best:
                counts["delete"]["had_callers"] += 1
                if best[2]:
                    counts["delete"]["callers_updated"] += 1
                    cands["delete"].append({**base, "family": "delete", "anchor": best[0]["function"],
                                            "anchor_lines": best[0]["lines"], "caller_files_at_parent": best[1],
                                            "caller_files_updated": best[2],
                                            "also_removed": [x["function"] for x in pr["removed"]][:10]})

        # move
        if pr["moved"]:
            counts["move"]["function_moved"] += 1
            for m in pr["moved"]:
                leaf = _leaf(m["from"].split("::", 1)[1])
                if not _nameable(leaf):
                    continue
                homes = {m["from"].split("::")[0], m["to"].split("::")[0]}
                imp = _import_changed(clone, meta["parent"], c, [f for f in files if f not in homes
                                                                   and f.endswith(SOURCE)], leaf)
                if imp:
                    counts["move"]["imports_updated"] += 1
                    cands["move"].append({**base, "family": "move", **m, "import_files_updated": imp,
                                          "also_moved": len(pr["moved"]) - 1})
                    break

        # refactor (a): rename with callers updated
        hit = False
        for rn in pr["renamed"]:
            f, q = rn["from"].split("::", 1)
            nq = rn["to"].split("::", 1)[1]
            if not (_nameable(_leaf(q)) and _nameable(_leaf(nq))):
                continue
            _callers, updated = _caller_files(clone, meta["parent"], c, changed, _leaf(q), f, False, _leaf(nq))
            if updated:
                counts["refactor"]["renamed_with_callers"] += 1
                cands["refactor"].append({**base, "family": "refactor", "kind": "rename", **rn,
                                          "caller_files_updated": updated})
                hit = True
                break
        # refactor (b): refactor-worded, no test change
        if not hit and not tests and not chore and REFACTOR_WORDS.search(meta["subject"]):
            counts["refactor"]["worded_no_test_change"] += 1
            cands["refactor"].append({**base, "family": "refactor", "kind": "worded", "added": pr["added"][:10],
                                      "removed": [x["function"] for x in pr["removed"]][:10],
                                      "renamed": pr["renamed"][:5], "moved": pr["moved"][:5]})
    return cands, counts


def _defined(clone: str, rev: str) -> set[str]:
    """Leaf names of non-test functions and classes at `rev` (by pattern, as `pool_scan.repo_functions`)."""
    r = subprocess.run(["git", "-C", clone, "grep", "-h", "-o", "-E",
                        r"(def|function|class)\s+[A-Za-z_$][A-Za-z0-9_$]*|(const|let)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*="
                        r"\s*(async\s*)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", rev, "--",
                        *[f"*{e}" for e in SOURCE]], capture_output=True, text=True, errors="replace")
    names = set()
    for line in r.stdout.splitlines():
        m = re.match(r"(?:def|function|class|const|let)\s+([A-Za-z_$][\w$]*)", line)
        if m and len(m.group(1)) > 3 and not m.group(1).startswith("test"):
            names.add(m.group(1))
    return names


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--since", required=True)
    p.add_argument("--until", default="2099-12-31")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ypath = out / "yield.json"
    yld = json.loads(ypath.read_text()) if ypath.exists() else {}
    allc = {fam: (json.loads((out / f"candidates_{fam}.json").read_text())
                  if (out / f"candidates_{fam}.json").exists() else []) for fam in FAMILIES}
    for spec in a.repos:
        name, _, clone = spec.partition("=")
        if name in yld:
            continue
        commits, wcounts = window_commits(clone, a.since, a.until)
        cands, counts = mine_repo(name, clone, commits)
        yld[name] = {**wcounts, "commits_kept": len(commits),
                     **{fam: {**counts[fam], "candidates": len(cands[fam])} for fam in FAMILIES}}
        for fam in FAMILIES:
            allc[fam] = [t for t in allc[fam] if t["repo"] != name] + cands[fam]
            (out / f"candidates_{fam}.json").write_text(json.dumps(allc[fam], indent=1) + "\n")
        ypath.write_text(json.dumps(yld, indent=1) + "\n")
        print(name, json.dumps(yld[name]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
