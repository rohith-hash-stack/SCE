"""Type 2 (backward): mine commits that add a new function which reuses the project's existing code.

    python -m harness.experiments.suite.mine_newfn OUT.json NAME=CLONE [NAME=CLONE ...] [--scan 10000]

A function qualifies when (README, type 2):
- the commit changes at most 3 source files (no test files counted), with at most 300 changed lines;
- the function is new (absent before the commit) in a file that already existed;
- it is 6-60 lines, not a test, not `__dunder__`, and not nested in another function;
- its body calls at least two names (`name(` / `obj.name(`). Whether at least two of them are functions that
  already existed is decided at planning, in the parent's index.

- its class (for a method) already existed;
- it has a description of what it does: its docstring (Python) or the `/** */` comment above it (JavaScript),
  of at least 5 words. A commit subject is not used instead: in a sample of candidates it rarely said what
  the function does ("cleaned up ... module").

The task gives the function's signature and that description.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from harness.experiments.change_history.mine import SOURCE_EXT, TEST_PARTS, functions, git

MAX_SOURCE_FILES = 3
MAX_LINES = 300
MIN_FN_LINES, MAX_FN_LINES = 6, 60
MIN_PURPOSE_WORDS = 5
_CALL = re.compile(r"(?<![\w$.])(?:[A-Za-z_$][\w$]*\.)*([A-Za-z_$][\w$]*)\s*\(")
_KEYWORDS = frozenset("if for while return elif not and or in is print switch catch function typeof await new "
                      "super lambda with assert yield del raise except".split())


def called_names(code: str) -> list[str]:
    body = code.split("\n", 1)[1] if "\n" in code else ""
    return sorted({m.group(1) for m in _CALL.finditer(body) if m.group(1) not in _KEYWORDS})


def split_signature(code: str, lang: str) -> tuple[str, str, str]:
    """`(signature, docstring, body)` of a function's code."""
    if lang == "python":
        m = re.match(r"(?s)(\s*(?:@[^\n]*\n\s*)*(?:async\s+)?def\s+\w+\s*\(.*?\)\s*(?:->[^:]*)?:)", code)
        if not m:
            return code.split("\n", 1)[0], "", code
        sig, rest = m.group(1), code[m.end():]
        d = re.match(r'(?s)\s*(r?"""|r?\'\'\')(.*?)\1', rest)
        if d:
            return sig, d.group(2).strip(), rest[d.end():]
        return sig, "", rest
    i = code.find("{")
    return (code[:i].rstrip(), "", code[i:]) if i > 0 else (code.split("\n", 1)[0], "", code)


def jsdoc_before(source: str, start: int) -> str:
    """The `/** ... */` comment right above line `start` (1-based), as plain text."""
    lines = source.splitlines()[:start - 1]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[-1].strip().endswith("*/"):
        return ""
    block = []
    while lines:
        ln = lines.pop()
        block.append(ln)
        if ln.strip().startswith("/**"):
            break
    else:
        return ""
    text = "\n".join(reversed(block))
    text = re.sub(r"^\s*/\*\*|\*/\s*$", "", text.strip())
    text = "\n".join(re.sub(r"^\s*\*\s?", "", ln) for ln in text.splitlines())
    return text.split("\n@", 1)[0].strip() if not text.lstrip().startswith("@") else ""


def mine(name: str, repo: str, scan: int) -> list[dict]:
    log = git(repo, "log", "--no-merges", f"-n{scan}", "--format=%x01%H%x00%s", "--numstat")
    out = []
    for block in log.split("\x01")[1:]:
        head, *rows = block.strip("\n").split("\n")
        sha, subject = head.split("\x00", 1)
        source, lines = [], 0
        for row in rows:
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            if path.endswith(SOURCE_EXT) and not TEST_PARTS.search(path) and not path.endswith(".d.ts"):
                source.append(path)
                lines += (int(added) if added.isdigit() else 0) + (int(removed) if removed.isdigit() else 0)
        if not 1 <= len(source) <= MAX_SOURCE_FILES or lines > MAX_LINES:
            continue
        for f in source:
            try:
                old = git(repo, "show", f"{sha}^:{f}").encode()
                new = git(repo, "show", f"{sha}:{f}").encode()
            except subprocess.CalledProcessError:
                continue  # a new or deleted file
            before, after = functions(f, old), functions(f, new)
            lang = "python" if f.endswith(".py") else "js"
            for qual in sorted(after.keys() - before.keys()):
                info = after[qual]
                leaf = qual.rsplit(".", 1)[-1]
                n = info["end"] - info["start"] + 1
                if not MIN_FN_LINES <= n <= MAX_FN_LINES or leaf.startswith(("__", "test")):
                    continue
                if any(qual.startswith(p + ".") for p in after if p != qual and p in before | after
                       and not p[:1].isupper()):
                    continue  # nested in another function (classes are capitalised by convention)
                calls = called_names(info["code"])
                if len(calls) < 2:
                    continue
                if "." in qual and not re.search(rf"(?m)^\s*(export\s+)?(default\s+)?class\s+"
                                                 rf"{re.escape(qual.rsplit('.', 1)[0].rsplit('.', 1)[-1])}\b",
                                                 old.decode("utf8", "replace")):
                    continue  # its class is new too: there is nothing to add it to
                sig, doc, _body = split_signature(info["code"], lang)
                if not doc and lang != "python":
                    doc = jsdoc_before(new.decode("utf8", "replace"), info["start"])
                if len(re.findall(r"[A-Za-z]{2,}", doc)) < MIN_PURPOSE_WORDS:
                    continue  # no description of what it should do: the task would be a guess
                prev = [q for q, v in after.items() if v["end"] < info["start"] and q in before
                        and q.rsplit(".", 1)[0] == qual.rsplit(".", 1)[0] if "." in qual] if "." in qual else \
                    [q for q, v in after.items() if v["end"] < info["start"] and q in before and "." not in q]
                out.append({
                    "repo": name, "commit": sha, "parent": git(repo, "rev-parse", f"{sha}^").strip(),
                    "subject": subject, "file": f, "name": qual, "start": info["start"], "end": info["end"],
                    "code": info["code"], "signature": sig, "docstring": doc,
                    "purpose": doc, "purpose_from": "docstring" if lang == "python" else "jsdoc",
                    "after": max(prev, key=lambda q: after[q]["end"]) if prev else None,
                    "calls": calls})
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
        print(f"{name}: {len(got)} new-function candidates", flush=True)
        found += got
        Path(args.out).write_text(json.dumps(found, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
