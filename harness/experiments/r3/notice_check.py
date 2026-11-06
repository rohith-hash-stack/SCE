"""The qwen notice check (amendment R3-5): does qwen2.5-coder:14b read a gated file when PRISM's notice tells it to?

    python -m harness.experiments.r3.notice_check run --clone WERKZEUG --out DIR --cache DIR --base-url URL
    python -m harness.experiments.r3.notice_check summarize DIR

**The task** (PRISM version only; 5 runs, seeds 42, 1001, 2002, 3003, 4004):
- werkzeug at the pinned revision;
- the prompt asks for a small change to `Headers.get` in `src/werkzeug/datastructures/headers.py`;
- right after the index is built, the same file gets one broken line in another method. This is what an earlier
  broken edit would leave behind. The file is then gated, and PRISM serves its last parsable version with the
  notice.

**Recorded per run, a known behaviour of the model, not a pass/fail condition:**
- whether a notice was shown;
- the turn it appeared on;
- whether the agent called `read_file` on the named file within the next 2 turns;
- the tools it called instead.

`summarize` writes the result that goes in R3-5's results table.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SEEDS = (42, 1001, 2002, 3003, 4004)
REV = "81c7d5b77a4cd3a8a7ac8ca6e1aa5ecb2c4cf8f6"   # replaced at build time by the clone's pinned revision
FILE = "src/werkzeug/datastructures/headers.py"
TARGET = "src.werkzeug.datastructures.headers.Headers.get"
WITHIN = 2


def make_task(clone: str, rev: str) -> dict:
    """The task, with the broken line placed inside `Headers.__repr__` (a method the request does not touch)."""
    src = subprocess.run(["git", "-C", clone, "show", f"{rev}:{FILE}"], capture_output=True, text=True,
                         check=True).stdout
    lines = src.splitlines(keepends=True)
    at = next(i for i, ln in enumerate(lines) if ln.strip().startswith("def __repr__"))
    ind = lines[at + 1][: len(lines[at + 1]) - len(lines[at + 1].lstrip())]
    broken = "".join(lines[:at + 1] + [f"{ind}return (((\n"] + lines[at + 1:])
    import difflib
    patch = "".join(difflib.unified_diff(src.splitlines(keepends=True), broken.splitlines(keepends=True),
                                         f"a/{FILE}", f"b/{FILE}"))
    prompt = (f"In this repository, change `{TARGET}` so that when `type` is given and the conversion raises "
              f"`ValueError`, it returns `default` (it already does) and also when it raises `TypeError`. Make the "
              f"change, then call done.")
    return {"id": "notice-check-werkzeug", "family": "notice", "repo": "werkzeug", "parent": rev, "start_patch": None,
            "after_index_patch": patch, "prompt": prompt, "visible_tests": ["tests/test_datastructures.py"],
            "hidden_patch": None, "fail_to_pass": {}, "pass_to_pass": {}}


def analyze(jsonl_path: str) -> dict:
    turns = [json.loads(ln) for ln in open(jsonl_path)]
    first = next((t for t in turns if t.get("notice")), None)
    rec = {"turns": len(turns), "notice_shown": first is not None}
    if first is None:
        rec["tools"] = [t.get("tool") for t in turns]
        return rec
    rec["notice_turn"] = first["turn"]
    after = [t for t in turns if first["turn"] < t["turn"] <= first["turn"] + WITHIN]
    rec["next_tools"] = [(t.get("tool"), (t.get("args") or {}).get("path")) for t in after]
    rec["read_named_file"] = any(t.get("tool") == "read_file" and (t.get("args") or {}).get("path", "").lstrip("./") == FILE
                                 for t in after)
    rec["read_named_file_ever"] = any(t.get("tool") == "read_file" and (t.get("args") or {}).get("path", "").lstrip("./") == FILE
                                      for t in turns if t["turn"] > first["turn"])
    return rec


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--clone", required=True)
    r.add_argument("--rev", default="HEAD")
    r.add_argument("--out", required=True)
    r.add_argument("--cache", required=True)
    r.add_argument("--base-url", default="http://127.0.0.1:11434")
    r.add_argument("--turn-cap", type=int, default=30)
    s = sub.add_parser("summarize")
    s.add_argument("out")
    a = p.parse_args(argv)
    if a.cmd == "run":
        from harness.experiments.r3.loop import OllamaChat
        from harness.experiments.r3.runner import run_task
        rev = subprocess.run(["git", "-C", a.clone, "rev-parse", a.rev], capture_output=True, text=True,
                             check=True).stdout.strip()
        task = make_task(a.clone, rev)
        os.makedirs(a.out, exist_ok=True)
        json.dump(task, open(os.path.join(a.out, "task.json"), "w"), indent=1)
        for seed in SEEDS:
            res = run_task(task, "PRISM", seed, os.path.abspath(a.clone), a.out, a.cache, OllamaChat(a.base_url),
                           a.turn_cap, score_run=False)
            print(json.dumps({k: res.get(k) for k in ("run", "turns", "end")}), flush=True)
        return 0
    runs = {}
    for seed in SEEDS:
        path = os.path.join(a.out, f"notice-check-werkzeug__PRISM__{seed}.jsonl")
        if os.path.exists(path):
            runs[seed] = analyze(path)
    shown = [r for r in runs.values() if r["notice_shown"]]
    out = {"runs": runs, "runs_done": len(runs), "notice_shown": len(shown),
           "read_named_file_within_2_turns": sum(r["read_named_file"] for r in shown),
           "read_named_file_at_all": sum(r["read_named_file_ever"] for r in shown)}
    json.dump(out, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
