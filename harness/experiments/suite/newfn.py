"""Type 2 (backward: a new function that reuses the project's code): select, plan, prompt and score.

    python -m harness.experiments.suite.newfn select CANDIDATES.json OUT.json [--per-repo 20] [--seed 5501]
    python -m harness.experiments.suite.newfn plan TASKS.json NAME=CLONE ...

Planning indexes each task's parent revision and works out the existing helpers the developer's function
calls (`existing_helpers`). The task is kept when there are at least two. Candidates are taken in the
seeded order until `--per-repo` tasks are planned, at most one per commit.

A call counts as a call to an existing helper when:
- `self.x(` / `cls.x(` / `this.x(`, and the class already had a method `x`; or
- a bare `x(` or `obj.x(`, where the repository already defined a non-test function or class named `x`, and
  `x` is not a builtin or a generic method name (`GENERIC`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.experiments.suite.fences import strip_outer_fence

ARMS = ("FILE", "PRISM")
FILE_WINDOW = 300
GENERIC = frozenset((
    "get set update format join append extend pop items keys values split strip replace startswith endswith "
    "encode decode read write close copy lower upper add remove push map filter reduce call apply bind then "
    "catch resolve reject emit on once toString hasOwnProperty assign create isinstance len str int float list "
    "dict tuple bool print range enumerate zip sorted reversed min max sum any all super type getattr setattr "
    "hasattr iter next open repr hash id callable vars dir isArray parse stringify log error warn info debug "
    "send end write_message fetch run start stop init setup next_tick setTimeout clearTimeout Promise Error "
    "Object Array String Number Boolean JSON Math Date RegExp Symbol Buffer require exec match test search "
    "slice splice concat indexOf includes forEach some every find findIndex sort shift unshift trim lstrip rstrip "
    "count index insert clear discard union intersection difference issubset sleep time now wait gather"
).split())
_CALL = re.compile(r"(?<![\w$])(?:((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*)\.)?([A-Za-z_$][\w$]*)\s*\(")
_KEYWORDS = frozenset("if for while return elif not and or in is switch catch function typeof await new super lambda "
                      "with assert yield del raise except def class async".split())

INSTRUCTIONS = """You are a software engineer working in the repository `{repo}`.

Add a new function to `{file}`{where}:

Signature:
```
{signature}
```
What it must do:
---
{purpose}
---

Write the complete function (its signature line and body), in the style of the code around it. Use the
repository's existing functions where they do part of the work instead of re-writing them.

Respond with a JSON object: {{"code": "<the complete function>"}}
"""

FILE_BLOCK = """The file `{file}` (lines {lo}-{hi}, as it is now; the new function goes {position}):
```
{text}
```
"""

PRISM_BLOCK = """PRISM context for the new function:

{context}
"""


def calls(code: str) -> list[tuple[str | None, str]]:
    """`(receiver, name)` of each call in the body of `code` (its first line, the signature, skipped)."""
    body = code.split("\n", 1)[1] if "\n" in code else ""
    body = re.sub(r'(?s)("""|\'\'\').*?\1', "", body)
    out = []
    for m in _CALL.finditer(body):
        recv, name = m.group(1), m.group(2)
        if name in _KEYWORDS:
            continue
        out.append((recv, name))
    return out


def existing_helpers(code: str, repo_leaves: set[str], class_methods: set[str]) -> list[str]:
    found = set()
    for recv, name in calls(code):
        if recv in ("self", "cls", "this"):
            if name in class_methods:
                found.add(name)
        elif name in repo_leaves and name not in GENERIC:
            found.add(name)
    return sorted(found)


# ------------------------------------------------------------------ selection / planning
def select(cands: list[dict], seed: int) -> list[dict]:
    by_repo: dict[str, list[dict]] = {}
    for c in sorted(cands, key=lambda c: (c["repo"], c["commit"], c["name"])):
        by_repo.setdefault(c["repo"], []).append(c)
    out = []
    for repo in sorted(by_repo):
        pool = by_repo[repo]
        random.Random(f"{seed}:{repo}").shuffle(pool)
        out += [{**c, "order": i} for i, c in enumerate(pool)]
    return out


def _window(text: str, at_line: int) -> tuple[str, int, int]:
    lines = text.splitlines()
    if len(lines) <= FILE_WINDOW:
        return "\n".join(lines), 1, len(lines)
    start = max(1, at_line - FILE_WINDOW // 2)
    end = min(len(lines), start + FILE_WINDOW - 1)
    return "\n".join(lines[start - 1:end]), start, end


def plan_one(task: dict, work: str) -> dict | None:
    from harness.experiments.change_history.mine import functions
    from prism.cli import build_pipeline
    from prism.impact.newcode import NewFunctionContextBuilder, render
    b = build_pipeline(work, use_cache=False)[0]
    path = os.path.join(work, task["file"])
    old_text = Path(path).read_text(errors="replace")
    leaves = {q.rsplit(".", 1)[-1].split("#", 1)[0] for q in b._def_nodes
              if (s := b.symbol_table.get(q)) is not None and not b._in_test_file(s.file)
              and s.kind in ("function", "method", "class")}
    cls = None
    if "." in task["name"]:
        cls_leaf = task["name"].rsplit(".", 1)[0].rsplit(".", 1)[-1]
        cls = next((q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None and s.file == path
                    and s.kind == "class" and q.rsplit(".", 1)[-1] == cls_leaf), None)
        if cls is None:
            return None
    methods = {q.rsplit(".", 1)[-1] for q in b._def_nodes if cls and q.startswith(cls + ".")
               and q.count(".") == cls.count(".") + 1}
    helpers = existing_helpers(task["code"], leaves, methods)
    if len(helpers) < 2:
        return None
    before = functions(task["file"], old_text.encode())
    at = before[task["after"]]["end"] + 1 if task.get("after") in before else (
        before[next(iter(sorted(before, key=lambda q: before[q]["end"])))]["start"] if before else 1)
    text, lo, hi = _window(old_text, at)
    position = f"right after `{task['after']}` (line {at - 1})" if task.get("after") in before else \
        f"near line {at}"
    ctx = NewFunctionContextBuilder(b, work).build(task["file"], task["name"].rsplit(".", 1)[-1],
                                                   signature=task["signature"], purpose=task["purpose"], cls=cls)
    return {**task, "class_qname": cls, "existing_helpers": helpers, "repo_leaves": sorted(leaves),
            "file_window": text, "window": [lo, hi], "position": position, "insert_line": at,
            "class_methods": sorted(methods), "prism_context": render(ctx), "prism_helpers": [h.qname for h in ctx.helpers],
            "file_imports": sorted(set(re.findall(r"(?m)^\s*(?:from\s+[\w.]+\s+)?import\s+(.+)$", old_text)) |
                                   set(re.findall(r"(?m)(?:const|let|var)\s+\{?\s*([\w$, ]+)\s*\}?\s*=\s*require", old_text)))}


def plan(path: Path, repos: dict[str, str], per_repo: int) -> None:
    data = json.loads(path.read_text())
    planned = {f"{t['commit']}:{t['name']}": t for t in data.get("tasks", [])}
    skipped = set(data.get("skipped", []))
    for repo in sorted({c["repo"] for c in data["candidates"]}):
        for c in sorted((c for c in data["candidates"] if c["repo"] == repo), key=lambda c: c["order"]):
            key = f"{c['commit']}:{c['name']}"
            if sum(1 for t in planned.values() if t["repo"] == repo) >= per_repo:
                break
            if key in planned or key in skipped or any(t["commit"] == c["commit"] for t in planned.values()):
                continue
            work = tempfile.mkdtemp(prefix="nf_")
            subprocess.run(["git", "-C", repos[repo], "worktree", "add", "-q", "--detach", work, c["parent"]],
                           check=True, capture_output=True)
            try:
                got = plan_one(c, work)
            except Exception as exc:
                print(f"  {repo} {c['commit'][:10]} {c['name']}: error {type(exc).__name__}: {exc}", flush=True)
                got = None
            finally:
                subprocess.run(["git", "-C", repos[repo], "worktree", "remove", "--force", work], capture_output=True)
            if got is None:
                skipped.add(key)
            else:
                planned[key] = got
                print(f"  {repo} {c['commit'][:10]} {c['name']}: helpers {got['existing_helpers']}; "
                      f"in PRISM's list {sum(any(h.rsplit('.', 1)[-1] == x for h in got['prism_helpers']) for x in got['existing_helpers'])}",
                      flush=True)
            data["tasks"], data["skipped"] = list(planned.values()), sorted(skipped)
            path.write_text(json.dumps(data, indent=1) + "\n")
    tasks = sorted(planned.values(), key=lambda t: (t["repo"], t["order"]))
    for i, t in enumerate(tasks, 1):
        t["id"] = f"nf{i:03d}"
    data["tasks"], data["status"] = tasks, "frozen"
    data["tasks_sha256"] = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(data, indent=1) + "\n")
    print(f"planned {len(tasks)} tasks ({len(skipped)} skipped); sha256 {data['tasks_sha256']}")


# ------------------------------------------------------------------ prompts / scoring
def prompt(task: dict, arm: str) -> str:
    where = f", as a method of the class `{task['class_qname']}`" if task.get("class_qname") else ""
    lo, hi = task["window"]
    parts = [INSTRUCTIONS.format(repo=task["repo"], file=task["file"], where=where, signature=task["signature"].strip(),
                                 purpose=task["purpose"]),
             FILE_BLOCK.format(file=task["file"], lo=lo, hi=hi, text=task["file_window"], position=task["position"])]
    if arm == "PRISM":
        parts.append(PRISM_BLOCK.format(context=task["prism_context"]))
    return "\n".join(parts)


def _parses(code: str, file: str) -> bool:
    from prism.parser.tree_sitter_loader import parse_source
    parsed = parse_source(file, code.encode())
    return parsed is not None and parsed.root_node is not None and not parsed.root_node.has_error


def _defined_locally(code: str) -> set[str]:
    return set(re.findall(r"\b(?:def|function|class)\s+([A-Za-z_$][\w$]*)", code)) | \
        set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:lambda|function|\(|async)", code))


_BUILTINS = frozenset(dir(__import__("builtins"))) | frozenset((
    "require setTimeout setInterval clearTimeout clearInterval setImmediate process console Buffer Promise Error "
    "TypeError RangeError Object Array String Number Boolean JSON Math Date RegExp Symbol Map Set WeakMap parseInt "
    "parseFloat isNaN encodeURIComponent decodeURIComponent URL URLSearchParams fetch queueMicrotask"
).split())


def score_task(task: dict, raw: str, outer_fence: bool = False) -> dict:
    """reuse: share of the developer's existing helpers the answer also calls. invented: calls to names that
    exist nowhere - `self.x(` with no method `x` in the class, or a bare `x(` that is not in the repository,
    not imported, not a builtin and not defined in the answer itself. With `outer_fence` (a sensitivity analysis,
    the same for every version): a fence wrapping the whole answer or the whole code is removed first."""
    try:
        got = json.loads(strip_outer_fence(raw) if outer_fence else raw)
        code = got.get("code", "") if isinstance(got, dict) else ""
        if outer_fence and isinstance(code, str):
            code = strip_outer_fence(code)
    except (ValueError, TypeError):
        code = ""
    if not isinstance(code, str) or not code.strip():
        return {"answered": False, "reuse": 0.0, "invented": 0, "parses": False}
    used = {name for _recv, name in calls(code)}
    reuse = sum(h in used for h in task["existing_helpers"]) / len(task["existing_helpers"])
    local = _defined_locally(code)
    methods = set(task["class_methods"])
    imports = " ".join(task.get("file_imports", []))
    repo = set(task.get("repo_leaves", []))
    invented = 0
    for recv, name in calls(code):
        if recv in ("self", "cls", "this"):
            if task.get("class_qname") and name not in methods and name not in local:
                invented += 1
        elif recv is None and name not in repo and name not in local and name not in _BUILTINS \
                and not re.search(rf"\b{re.escape(name)}\b", imports) and name[:1].islower():
            invented += 1
    return {"answered": True, "reuse": reuse, "invented": invented, "parses": _parses(code, task["file"]),
            "helpers_used": sorted(h for h in task["existing_helpers"] if h in used), "imports_seen": bool(imports)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("candidates")
    s.add_argument("out")
    s.add_argument("--seed", type=int, default=5501)
    s.add_argument("--per-repo", type=int, default=20)
    pl = sub.add_parser("plan")
    pl.add_argument("tasks")
    pl.add_argument("repos", nargs="+")
    args = p.parse_args(argv)
    if args.cmd == "select":
        cands = select(json.loads(Path(args.candidates).read_text()), args.seed)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"type": "new function", "status": "selected", "seed": args.seed,
                                              "per_repo": args.per_repo, "candidates": cands}, indent=1) + "\n")
        print(f"{len(cands)} candidates in seeded order")
    else:
        data = json.loads(Path(args.tasks).read_text())
        plan(Path(args.tasks), dict(a.split("=", 1) for a in args.repos), data["per_repo"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
