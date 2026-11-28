"""Type 3 (bug localisation): mine bug-fix commits whose fix is a few changed functions.

    python -m harness.experiments.suite.mine_bugs OUT.json NAME=CLONE [NAME=CLONE ...] [--scan 10000]

A commit qualifies when (README, type 3):
- its subject says it fixes something (`FIX`: fix, bug, regression, crash, broken, incorrect, wrong) and is not
  about docs, typos, lint, typing or CI (`NOT_A_BUG`);
- it also changes a test file (evidence of a behaviour fix);
- it modifies 1-4 existing source functions (no test file, no example) in 1-3 source files, with at most 120
  changed source lines.

The bug description is the commit's subject and body. Each changed function's name, its classes and its
file's name are masked as `<name>`, and issue numbers, URLs, trailers and SVN/Mercurial conversion lines are
removed. A description with fewer than 8 words besides "fix"/"bug" and `<name>` is dropped, and so is one in
which an answer name survives the masking. The changed functions are the answer key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from harness.experiments.change_history.mine import SOURCE_EXT, TEST_PARTS, changed_functions, git

FIX = re.compile(r"\b(fix(e[sd]|ing)?|bug(s|fix)?|regression|crash(es|ed)?|broken|incorrect|wrong)\b", re.I)
NOT_A_BUG = re.compile(r"\b(typos?|docs?|documentation|docstrings?|readme|changelog|lint|flake8|pylint|mypy|"
                       r"typing|type hints?|annotations?|ci|travis|workflows?|tox|deprecation warnings?|"
                       r"spelling|grammar|formatting|whitespace|style)\b", re.I)
MAX_SOURCE_FILES = 3
MAX_FUNCTIONS = 4
MAX_SOURCE_LINES = 120
MIN_WORDS = 8
_TRAILER = re.compile(r"(?im)^(co-authored-by|signed-off-by|reviewed-by|closes|fixes|refs?|see|cc):.*$"
                      r"|^--HG--.*$|^extra : .*$|^git-svn-id: .*$")
_FILLER = re.compile(r"\b(fix(e[sd]|ing)?|bugs?|bugfix)\b|<name>", re.I)


def _is_test(path: str) -> bool:
    return bool(TEST_PARTS.search(path))


def mask(text: str, names: set[str]) -> str:
    """`text` with each of `names` (whole words, any case) replaced by `<name>`, issue refs and URLs gone."""
    text = _TRAILER.sub("", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\(?#\d+\)?|\bGH-?\d+\b", "", text)
    for n in sorted(names, key=len, reverse=True):
        if len(n) >= 3:
            text = re.sub(rf"(?<![\w$]){re.escape(n)}(?![\w$])", "<name>", text, flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _names(file: str, qual: str) -> set[str]:
    base = os.path.splitext(os.path.basename(file))[0]
    parts = set(qual.split("."))
    return parts | {base, file, os.path.splitext(file)[0].replace("/", "."), qual}


def mine(name: str, repo: str, scan: int) -> list[dict]:
    log = git(repo, "log", "--no-merges", f"-n{scan}", "--format=%x01%H%x00%s", "--numstat")
    out = []
    for block in log.split("\x01")[1:]:
        head, *rows = block.strip("\n").split("\n")
        sha, subject = head.split("\x00", 1)
        if not FIX.search(subject) or NOT_A_BUG.search(subject):
            continue
        source, tests, lines = [], [], 0
        for row in rows:
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            if _is_test(path):
                tests.append(path)
            elif path.endswith(SOURCE_EXT) and not path.endswith(".d.ts"):
                source.append(path)
                lines += (int(added) if added.isdigit() else 0) + (int(removed) if removed.isdigit() else 0)
        if not tests or not 1 <= len(source) <= MAX_SOURCE_FILES or lines > MAX_SOURCE_LINES:
            continue
        changed = changed_functions(repo, sha, source)
        if not 1 <= len(changed) <= MAX_FUNCTIONS:
            continue
        body = git(repo, "log", "-1", "--format=%B", sha)
        names = set()
        for (f, qual) in changed:
            names |= _names(f, qual)
        description = mask(body, names)
        if len(re.findall(r"[A-Za-z]{2,}", _FILLER.sub(" ", description))) < MIN_WORDS:
            continue
        leaves = {q.rsplit(".", 1)[-1] for _f, q in changed}
        if any(len(x) >= 3 and re.search(rf"(?<![\w$]){re.escape(x)}(?![\w$])", description, re.I) for x in leaves):
            continue  # an answer name survived the masking
        out.append({
            "repo": name, "commit": sha, "parent": git(repo, "rev-parse", f"{sha}^").strip(), "subject": subject,
            "description": description, "test_files": tests, "source_lines": lines,
            "gold": [{"file": f, "name": qual, "start": v["start"], "end": v["end"], "old": v["old"], "new": v["new"]}
                     for (f, qual), v in sorted(changed.items())]})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("repos", nargs="+")
    p.add_argument("--scan", type=int, default=10000)
    args = p.parse_args(argv)
    found = []
    for spec in args.repos:
        name, _, repo = spec.partition("=")
        got = mine(name, repo, args.scan)
        print(f"{name}: {len(got)} bug-fix candidates", flush=True)
        found += got
        Path(args.out).write_text(json.dumps(found, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
