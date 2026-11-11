"""R3 dry run: the real runner on gated tasks with a scripted model, end to end (no model calls).

    python -m harness.experiments.r3.dryrun TASKS.json OUT_DIR --cache-py DIR --cache-js DIR NAME=CLONE ... [--pad N] [--turn-cap N] \
        [--ids ID ...] [--seed 42]

Two scripted "models", both versions each (pilot pass criterion 5, "the scoring path, end to end on synthetic
answers"):
- `gold` replays the developer's change through the real tools:
  - in the PRISM version, one PRISM call first (`prism_change_context` on the anchor, or
    `prism_new_function_context` for the new function);
  - one `edit_file` per diff hunk, its text widened until it is unique;
  - then `run_tests` and `done`.
  It must end `resolved`.
- `empty` calls `done` at once. It must end `unresolved`.

The runs go through `runner.run_task`: the worktree, `make_env`, the WorkingIndex, the tool layer, the shared tools,
the loop, scoring and the outcome. Seeds name the scripts apart: gold runs use the seed, empty runs the seed + 1.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def _git(*args, cwd=None, input_text=None):
    return subprocess.run(["git", *args], cwd=cwd, input=input_text, capture_output=True, text=True, check=True)


def start_contents(clone: str, task: dict, files: list[str]) -> dict[str, str | None]:
    tmp = tempfile.mkdtemp(prefix="r3dry_")
    work = os.path.join(tmp, "w")
    try:
        _git("-C", clone, "worktree", "add", "-q", "--detach", work, task["parent"])
        if task.get("start_patch"):
            _git("apply", "-", cwd=work, input_text=task["start_patch"])
        out = {}
        for f in files:
            p = os.path.join(work, f)
            out[f] = open(p, encoding="utf8").read() if os.path.exists(p) else None
        return out
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def edits_for(old: str | None, new: str | None, path: str) -> list[dict]:
    """`edit_file` calls turning `old` into `new`, each span unique in the text as it is when the call runs."""
    if new is None:   # a deleted file: the tools cannot delete, so it is emptied (scored as changed, functions removed)
        return [{"path": path, "old": old, "new": ""}] if old else []
    if old is None:
        return [{"path": path, "old": "", "new": new}]
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    calls, cur, shift = [], old, 0
    for tag, i1, i2, j1, j2 in ops:
        lo, hi = i1 + shift, i2 + shift              # positions in the current text's lines
        lines = cur.splitlines(keepends=True)
        for ctx in range(0, len(lines) + 1):
            s, e = max(0, lo - ctx), min(len(lines), hi + ctx)
            span = "".join(lines[s:e])
            if span and cur.find(span) == cur.rfind(span):       # unique, overlapping matches included
                break
        repl = "".join(lines[s:lo]) + "".join(b[j1:j2]) + "".join(lines[hi:e])
        calls.append({"path": path, "old": span, "new": repl})
        cur = cur.replace(span, repl, 1)
        shift += (j2 - j1) - (i2 - i1)
    assert cur == new, f"replayed edits do not reproduce {path}"
    return calls


class Script:
    """A model that replies with a fixed list of tool calls, then `done`."""

    def __init__(self, calls: list[dict]):
        self.calls = list(calls)

    def __call__(self, msgs, seed):
        call = self.calls.pop(0) if self.calls else {"tool": "done", "args": {"summary": "scripted"}}
        return {"text": json.dumps(call), "prompt_tokens": 100, "answer_tokens": 20, "done_reason": "stop",
                "seconds": 0.0}


def pad_calls(task: dict, version: str, n: int) -> list[dict]:
    """`n` read-only calls, cycling the disk tools and the version's index-backed tool (the memory test's check that
    memory does not grow with the number of tool calls, amendment R3-7). They change nothing in the tree."""
    if n <= 0:
        return []
    path = task.get("anchor_file") or task["target"].split("::", 1)[0]
    symbol = task.get("anchor") or task["target"].split("::", 1)[1]
    leaf = symbol.rsplit(".", 1)[-1]
    index = ({"tool": "prism_change_context", "args": {"symbol": symbol}} if version == "PRISM"
             else {"tool": "related_code", "args": {"query": leaf}})
    cycle = [{"tool": "read_file", "args": {"path": path, "start": 1, "end": 200}},
             {"tool": "grep", "args": {"pattern": re.escape(leaf)}},
             {"tool": "list_dir", "args": {"path": os.path.dirname(path) or "."}},
             index]
    return [cycle[i % len(cycle)] for i in range(n)]


def gold_calls(task: dict, clone: str, version: str) -> list[dict]:
    raw = _git("-C", clone, "diff", "--raw", "--no-renames", task["parent"], task["commit"]).stdout.split("\n")
    gitlinks = {ln.split("\t", 1)[1] for ln in raw if "\t" in ln and "160000" in ln.split("\t", 1)[0].split()[:2]}
    files = [f for f in _git("-C", clone, "diff", "--name-only", "--no-renames", task["parent"], task["commit"])
             .stdout.split("\n") if f and (f not in set(task["test_files"]) or task.get("done_gate") and
                                            task["family"] == "delete") and f not in gitlinks]   # no submodule bumps
    # R4-SANITY-2: a gated delete task's gold also updates the tests that used the target (the developer's test changes),
    # as the model must; scoring restores those files and applies the hidden patch, so the score is unchanged
    before = start_contents(clone, task, files)
    calls = []
    if version == "PRISM":
        if task["family"] == "B":
            calls.append({"tool": "prism_change_context", "args": {"symbol": task["anchor"]}})
        elif task["family"] == "delete":      # R4: the anchor is FILE::QUAL; PRISM's name is the dotted module path
            f, q = task["anchor"].split("::", 1)
            calls.append({"tool": "prism_change_context",
                          "args": {"symbol": f.rsplit(".", 1)[0].replace("/", ".") + "." + q}})
        elif task["family"] == "feature":     # R4: no anchor; one question from the spec's first line
            calls.append({"tool": "prism_ask", "args": {"question": (task.get("subject") or task["prompt"])[:200]}})
        else:
            f, q = task["target"].split("::", 1)
            calls.append({"tool": "prism_new_function_context",
                          "args": {"file": f, "name": q.rsplit(".", 1)[-1], "signature": task["signature"],
                                   "purpose": task["docstring"], "cls": q.rsplit(".", 1)[0] if "." in q else None}})
    for f in files:
        after = subprocess.run(["git", "-C", clone, "show", f"{task['commit']}:{f}"], capture_output=True)
        new = after.stdout.decode("utf8") if after.returncode == 0 else None
        calls += [{"tool": "edit_file", "args": e} for e in edits_for(before[f], new, f)]
    if task.get("visible_tests"):
        calls.append({"tool": "run_tests", "args": {"files": task["visible_tests"][:1]}})
    calls.append({"tool": "done", "args": {"summary": "replayed the developer's change"}})
    return calls


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--ids", nargs="*")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--versions", nargs="*", default=["PRISM", "BASELINE"])
    p.add_argument("--scripts", nargs="*", default=["gold", "empty"])
    p.add_argument("--pad", type=int, default=0, help="read-only calls before each script's own calls")
    p.add_argument("--turn-cap", type=int, default=30)
    a = p.parse_args(argv)
    from harness.experiments.r3.runner import run_task
    clones = dict(s.split("=", 1) for s in a.repos)
    tasks = json.load(open(a.tasks))["tasks"]
    os.makedirs(a.out, exist_ok=True)
    bad = 0
    for t in tasks:
        if a.ids and t["id"] not in a.ids:
            continue
        cache = a.cache_js if t["language"] == "js" else a.cache_py
        for version in [v for v in ("PRISM", "BASELINE") if v in a.versions]:
            pad = pad_calls(t, version, a.pad)
            scripts = [("gold", a.seed, lambda: Script(pad + gold_calls(t, clones[t["repo"]], version))),
                       ("empty", a.seed + 1, lambda: Script(list(pad)))]
            for kind, seed, make in [x for x in scripts if x[0] in a.scripts]:
                model = make()
                res = run_task(t, version, seed, os.path.abspath(clones[t["repo"]]), a.out, cache, model, a.turn_cap)
                want = "resolved" if kind == "gold" else "unresolved"
                # with the verification gate (R4-SANITY-2), an immediate done is rejected 3 times: stuck_done
                ok = res.get("outcome") == want or (kind == "empty" and t.get("done_gate")
                                                     and res.get("outcome") == "stuck_done")
                bad += not ok
                print(json.dumps({"task": t["id"], "version": version, "script": kind, "outcome": res.get("outcome"),
                                  "want": want, "ok": ok, "turns": res.get("turns"),
                                  "file_f1": (res.get("score") or {}).get("file_f1"),
                                  "function_f1": (res.get("score") or {}).get("function_f1")}), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
