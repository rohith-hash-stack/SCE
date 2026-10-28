"""Type 6 (write tests for a function): select, plan with the validity gate, prompt and score by running.

    python -m harness.experiments.suite.testgen select CANDIDATES.json OUT.json [--per-repo 20] [--seed 5501]
    python -m harness.experiments.suite.testgen plan TASKS.json ENVS_DIR NAME=CLONE ...
    python -m harness.experiments.suite.testgen score TASKS.json ANSWERS.json OUT.json ENVS_DIR NAME=CLONE ...

Planning, per candidate in the seeded order:
1. **Target:** index the parent revision and choose the target, the production function or class the new
   tests call most (`choose_target`).
2. **Gate:** build the environment and run the developer's new tests on the parent's code. Keep the task
   only if they all run and pass (README, type 6, validity gate).
3. **Coverage:** record the target lines those tests execute.

Scoring writes the AI's tests at the end of the test file as it was at the parent, and runs them the same way.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.experiments.suite.execute import build_env, run_tests, test_names
from harness.experiments.suite.newfn import GENERIC

ARMS = ("FILE", "PRISM")
TARGET_WINDOW = 120
TEST_HEAD, TEST_TAIL = 60, 140
BATCH = 8      # candidates planned per process (memory: every revision's index is kept until exit)
MORE = 3       # exit code: more candidates to plan

INSTRUCTIONS = """You are a software engineer working in the repository `{repo}`.

Write new pytest tests for `{target}` (in `{target_file}`). They will be added at the end of `{test_file}`.
Test its behaviour thoroughly, including edge cases and errors, in the style of the existing tests.

Respond with a JSON object: {{"code": "<the new test functions, with any imports they need>"}}
"""

FILES_BLOCK = """`{target_file}` (lines {lo}-{hi}):
```python
{target_text}
```

`{test_file}` as it is now ({head_note}):
```python
{test_text}
```
"""

PRISM_BLOCK = """PRISM test context:

{context}
"""


def select(cands: list[dict], seed: int) -> list[dict]:
    by_repo: dict[str, list[dict]] = {}
    for c in sorted(cands, key=lambda c: (c["repo"], c["commit"], c["test_file"])):
        by_repo.setdefault(c["repo"], []).append(c)
    out = []
    for repo in sorted(by_repo):
        pool = by_repo[repo]
        random.Random(f"{seed}:{repo}").shuffle(pool)
        out += [{**c, "order": i} for i, c in enumerate(pool)]
    return out


def choose_target(builder, tests: list[dict], test_file: str = "") -> str | None:
    """The subject of the new tests. Candidates: production functions and classes called by at least half of them,
    their names unique in the repository. The target is the candidate defined in the file the test file is
    named after (`test_tree.py` -> `tree.py`, `test_downloadermiddleware_useragent.py` -> `useragent.py`), or
    else the only candidate when it is a function. Otherwise there is no clear target and the task is skipped:
    a class the tests merely build as input (`Request` in a middleware's tests) is not their subject."""
    by_leaf: dict[str, list[str]] = collections.defaultdict(list)
    for q in builder._def_nodes:
        s = builder.symbol_table.get(q)
        if s is not None and s.kind in ("function", "method", "class") and not builder._in_test_file(s.file):
            by_leaf[q.rsplit(".", 1)[-1]].append(q)
    counts = collections.Counter()
    for t in tests:
        for name in set(t["calls"]):
            if len(by_leaf.get(name, [])) == 1 and name not in GENERIC and not name.startswith("_"):
                counts[name] += 1
    stem = os.path.splitext(os.path.basename(test_file))[0].removeprefix("test_").removesuffix("_test")

    def named_after(name: str) -> bool:
        f = os.path.splitext(os.path.basename(builder.symbol_table.get(by_leaf[name][0]).file))[0]
        return bool(stem) and f not in ("__init__", "utils", "misc") and (stem == f or stem.endswith("_" + f))
    eligible = [n for n, c in counts.items() if c * 2 >= len(tests)]
    named = [n for n in eligible if named_after(n)]
    if named:
        return by_leaf[sorted(named, key=lambda n: (-counts[n], n))[0]][0]
    if len(eligible) == 1 and builder.symbol_table.get(by_leaf[eligible[0]][0]).kind in ("function", "method"):
        return by_leaf[eligible[0]][0]
    return None  # no clear subject: the tests may be about something they only construct through a helper


def _excerpt(text: str, head: int, tail: int) -> tuple[str, str]:
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text, "whole file"
    return "\n".join(lines[:head] + [f"# ... ({len(lines) - head - tail} lines not shown) ..."] + lines[-tail:]), \
        f"first {head} and last {tail} of {len(lines)} lines"


def plan_one(task: dict, work: str, repo: str, envs: str) -> dict | None:
    from prism.cli import build_pipeline
    from prism.impact.testcontext import TestContextBuilder, render
    b = build_pipeline(work, use_cache=False)[0]
    target = choose_target(b, task["tests"], task["test_file"])
    if target is None:
        return {"skip": "no single target"}
    sym = b.symbol_table.get(target)
    target_file = os.path.relpath(sym.file, work)
    lo, hi = sym.line_range
    py, log = build_env(task["repo"], work, envs)
    if py is None:
        return {"skip": "environment", "log": log[-600:]}
    new_text = subprocess.run(["git", "-C", repo, "show", f"{task['commit']}:{task['test_file']}"],
                              capture_output=True, text=True).stdout
    names = [t["name"].rsplit(".", 1)[-1] for t in task["tests"]]
    gate = run_tests(py, work, task["test_file"], new_text, names, target_file, (lo, hi))
    if gate["ran"] == 0 or gate["failed"] or gate["passed"] == 0 or not gate["covered"]:
        return {"skip": "gate", "gate": {k: v for k, v in gate.items() if k != "covered"}}
    src = Path(sym.file).read_text(errors="replace").splitlines()
    a = max(1, lo - (TARGET_WINDOW - (hi - lo + 1)) // 2)
    z = min(len(src), a + max(TARGET_WINDOW, hi - lo + 1) - 1)
    test_now = Path(work, task["test_file"]).read_text(errors="replace")
    test_text, note = _excerpt(test_now, TEST_HEAD, TEST_TAIL)
    ctx = TestContextBuilder(b, work).build(target, task["test_file"])
    return {**task, "target": target, "target_file": target_file, "target_lines": [lo, hi],
            "target_window": "\n".join(src[a - 1:z]), "target_window_lines": [a, z],
            "test_excerpt": test_text, "test_excerpt_note": note, "existing_test_names": test_names(test_now),
            "dev_gate": {k: v for k, v in gate.items() if k != "covered"}, "dev_covered": gate["covered"],
            "prism_context": render(ctx)}


def plan(path: Path, envs: str, repos: dict[str, str], per_repo: int) -> None:
    data = json.loads(path.read_text())
    planned = {f"{t['commit']}:{t['test_file']}": t for t in data.get("tasks", [])}
    skipped = data.get("skipped") or {}
    if isinstance(skipped, list):  # a per-repository working file starts with an empty list
        skipped = {k: {"skip": "earlier"} for k in skipped}
    handled = 0
    for repo in sorted({c["repo"] for c in data["candidates"]}):
        for c in sorted((c for c in data["candidates"] if c["repo"] == repo), key=lambda c: c["order"]):
            key = f"{c['commit']}:{c['test_file']}"
            if sum(1 for t in planned.values() if t["repo"] == repo) >= per_repo:
                break
            if key in planned or key in skipped:
                continue
            if handled >= BATCH:  # each index stays in memory: exit and let the caller start a fresh process
                print(f"batch of {BATCH} done; run again to continue", flush=True)
                raise SystemExit(MORE)
            handled += 1
            work = tempfile.mkdtemp(prefix="tg_")
            subprocess.run(["git", "-C", repos[repo], "worktree", "add", "-q", "--detach", work, c["parent"]],
                           check=True, capture_output=True)
            try:
                got = plan_one(c, work, repos[repo], envs)
            except Exception as exc:
                got = {"skip": f"error {type(exc).__name__}: {exc}"}
            finally:
                subprocess.run(["git", "-C", repos[repo], "worktree", "remove", "--force", work], capture_output=True)
            if "skip" in got:
                skipped[key] = got
                print(f"  {repo} {c['commit'][:10]}: skipped ({got['skip']})", flush=True)
            else:
                planned[key] = got
                print(f"  {repo} {c['commit'][:10]}: target {got['target']}, developer tests cover "
                      f"{len(got['dev_covered'])} target lines", flush=True)
            data["tasks"], data["skipped"] = list(planned.values()), skipped
            path.write_text(json.dumps(data, indent=1) + "\n")
    tasks = sorted(planned.values(), key=lambda t: (t["repo"], t["order"]))
    for i, t in enumerate(tasks, 1):
        t["id"] = f"tg{i:03d}"
    data["tasks"], data["status"] = tasks, "frozen"
    data["tasks_sha256"] = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(data, indent=1) + "\n")
    print(f"planned {len(tasks)} tasks ({len(skipped)} skipped); sha256 {data['tasks_sha256']}")


def prompt(task: dict, arm: str) -> str:
    lo, hi = task["target_window_lines"]
    parts = [INSTRUCTIONS.format(repo=task["repo"], target=task["target"], target_file=task["target_file"],
                                 test_file=task["test_file"]),
             FILES_BLOCK.format(target_file=task["target_file"], lo=lo, hi=hi, target_text=task["target_window"],
                                test_file=task["test_file"], head_note=task["test_excerpt_note"],
                                test_text=task["test_excerpt"])]
    if arm == "PRISM":
        parts.append(PRISM_BLOCK.format(context=task["prism_context"]))
    return "\n".join(parts)


def answer_code(raw: str) -> str:
    try:
        got = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    code = got.get("code", "") if isinstance(got, dict) else ""
    return code if isinstance(code, str) else ""


def score_one(task: dict, raw: str, py: str, work: str) -> dict:
    code = answer_code(raw)
    names = test_names(code)
    if not names:
        return {"answered": bool(code.strip()), "tests": 0, "ran": 0, "passed": 0, "all_pass": False,
                "calls_target": False, "covered": 0, "coverage_vs_developer": 0.0}
    clash = [n for n in names if n in task["existing_test_names"]]
    for n in clash:  # a name already in the file would shadow (or be shadowed by) the existing test
        code = re.sub(rf"\bdef\s+{n}\s*\(", f"def {n}_ai(", code)
    names = test_names(code)
    base = Path(work, task["test_file"]).read_text(errors="replace")
    got = run_tests(py, work, task["test_file"], base.rstrip("\n") + "\n\n\n" + code + "\n", names,
                    task["target_file"], tuple(task["target_lines"]))
    leaf = task["target"].rsplit(".", 1)[-1]
    dev = len(task["dev_covered"]) or 1
    return {"answered": True, "tests": len(names), "ran": got["ran"], "passed": got["passed"],
            "all_pass": got["ran"] > 0 and got["failed"] == 0 and got["passed"] > 0,
            "calls_target": bool(re.search(rf"\b{re.escape(leaf)}\b", code)), "covered": len(got["covered"]),
            "coverage_vs_developer": len(got["covered"]) / dev, "error": got.get("error", "")[-400:]}


def score(tasks_path: Path, answers_path: Path, out_path: Path, envs: str, repos: dict[str, str]) -> None:
    tasks = {t["id"]: t for t in json.loads(tasks_path.read_text())["tasks"]}
    answers = json.loads(answers_path.read_text())
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    for tid, t in sorted(tasks.items()):
        keys = [k for k in answers if k.startswith(tid + "/") and k not in done]
        if not keys:
            continue
        work = tempfile.mkdtemp(prefix="tgs_")
        subprocess.run(["git", "-C", repos[t["repo"]], "worktree", "add", "-q", "--detach", work, t["parent"]],
                       check=True, capture_output=True)
        try:
            py, _log = build_env(t["repo"], work, envs)
            for k in keys:
                done[k] = score_one(t, answers[k].get("raw", ""), py, work) if py else {"error": "environment"}
                out_path.write_text(json.dumps(done, indent=1, sort_keys=True) + "\n")
                print(f"  {k}: {done[k].get('all_pass')} covered {done[k].get('covered')}", flush=True)
        finally:
            subprocess.run(["git", "-C", repos[t["repo"]], "worktree", "remove", "--force", work], capture_output=True)


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
    pl.add_argument("envs")
    pl.add_argument("repos", nargs="+")
    sc = sub.add_parser("score")
    sc.add_argument("tasks")
    sc.add_argument("answers")
    sc.add_argument("out")
    sc.add_argument("envs")
    sc.add_argument("repos", nargs="+")
    args = p.parse_args(argv)
    if args.cmd == "select":
        cands = select(json.loads(Path(args.candidates).read_text()), args.seed)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"type": "write tests", "status": "selected", "seed": args.seed,
                                              "per_repo": args.per_repo, "candidates": cands}, indent=1) + "\n")
        print(f"{len(cands)} candidates in seeded order")
    elif args.cmd == "plan":
        data = json.loads(Path(args.tasks).read_text())
        plan(Path(args.tasks), args.envs, dict(a.split("=", 1) for a in args.repos), data["per_repo"])
    else:
        score(Path(args.tasks), Path(args.answers), Path(args.out), args.envs, dict(a.split("=", 1) for a in args.repos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
