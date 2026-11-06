"""R3's validity gate and seeded draw (no model calls).

    python -m harness.experiments.r3.gate MINE_DIR OUT_DIR --family B --want 10 --seed 6601 \
        --cache-py DIR --cache-js DIR NAME=CLONE ...

**Draw** (pre-registration, "Size and selection" and "Pilot-only tasks"):
- the family's candidates, sorted by (repository, commit), shuffled with the seed;
- taken in that order, with at most `--per-repo` tasks from one repository (B: 10, N: 20);
- the "used" repositories never hold more than half of the target;
- gated one by one until `--want` tasks pass, or the candidates run out.

**Gate**, per candidate, in one worktree with the revision's builder (`make_env` at the parent):
1. **Start state:** the parent, plus the start patch (Family B: the developer's anchor change) and the hidden tests.
   Each runnable hidden test file runs 3 times.
2. **The commit:** every file the commit changed is set to its content at the commit. A JS/TS repository whose
   tests use built output is rebuilt. The same files run 3 times.
3. **Classification, per test:**
   - a test whose outcome differs between its 3 runs, in either state, is flaky and is dropped;
   - **fail-to-pass:** it fails or is missing at the start state, and passes at the commit;
   - **pass-to-pass:** it passes in both states.
   - At least 1 fail-to-pass test is needed.
4. **Scoring check** (pilot pass criterion 5), through the runner's own `score()`:
   - the developer's patch on the start state must score resolved;
   - the start state with no change must score unresolved.

`OUT_DIR/gate_<F>.jsonl` holds one line per gated candidate, with its outcome and reason; reruns resume from it.
`OUT_DIR/tasks_<F>.json` is frozen with the sha256 of its task list. `OUT_DIR/attrition_<F>.json` gives, per
repository, the counts mined, drawn, gated and passed, and the reasons for failing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict

from harness.experiments.r3.mine import USED_REPOS

RUNS = 3
PER_REPO = {"B": 10, "N": 20}
BAD = ("failed", "missing")


def _git(*args, cwd=None, input_text=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, input=input_text, capture_output=True, text=True, check=check)


def draw_order(cands: list[dict], seed: int) -> list[dict]:
    pool = sorted(cands, key=lambda t: (t["repo"], t["commit"]))
    random.Random(seed).shuffle(pool)
    return pool


def runnable_tests(env, task: dict) -> list[str]:
    """The hidden test files the runner can run: the builder's own test files, plus any changed file named as a test
    module. The builder's list leaves out fixture and example folders, which is right for picking a test to self-check
    on, but the developer's tests are named explicitly here (e.g. litestar's `tests/examples/.../test_*.py`)."""
    from harness.experiments.r3.jsbuild import TEST_FILE
    from harness.experiments.r3.pybuild import TEST_PATH
    known = set(env.test_files())
    named = TEST_FILE if getattr(env, "d", {}).get("runner") in ("vitest", "jest", "mocha") else \
        TEST_PATH if getattr(env, "d", {}).get("runner") == "pytest" else None
    return [f for f in task["test_files"] if f in known or (named is not None and named.search(f)
                                                             and os.path.isfile(os.path.join(env.work, f)))]


def run_states(env, files: list[str]) -> dict[str, list[str]]:
    """`{file::test: [outcome per run]}`; a test absent from a run's report is "missing" in that run."""
    seen: dict[str, list[str | None]] = defaultdict(lambda: [None] * RUNS)
    unparsed = []
    for i in range(RUNS):
        for f in files:
            r = env.run_test(f)
            if not r.get("parsed"):
                unparsed.append(f)
            for t, s in (r.get("tests") or {}).items():
                seen[f"{f}::{t}"][i] = s
    out = {k: [s or "missing" for s in v] for k, v in seen.items()}
    out["__unparsed__"] = sorted(set(unparsed))   # type: ignore[assignment]
    return out


def set_files_to(work: str, commit: str, files: list[str]) -> None:
    present = set(_git("ls-tree", "-r", "--name-only", commit, "--", *files, cwd=work).stdout.split("\n")) if files else set()
    for f in files:
        if f in present:
            _git("checkout", commit, "--", f, cwd=work)
        elif os.path.exists(os.path.join(work, f)):
            os.remove(os.path.join(work, f))


def reset_to(work: str, rev: str) -> None:
    _git("checkout", "-q", "-f", "--detach", rev, cwd=work)
    _git("clean", "-fdq", "-e", ".r3-*", "-e", "node_modules", cwd=work)


def classify(start: dict, end: dict) -> dict:
    tests = sorted(k for k in set(start) | set(end) if k != "__unparsed__")
    f2p, p2p, flaky = defaultdict(list), defaultdict(list), []
    for k in tests:
        s, e = start.get(k, ["missing"] * RUNS), end.get(k, ["missing"] * RUNS)
        if len(set(s)) > 1 or len(set(e)) > 1:
            flaky.append(k)
            continue
        f, t = k.split("::", 1)
        if s[0] in BAD and e[0] == "passed":
            f2p[f].append(t)
        elif s[0] == "passed" and e[0] == "passed":
            p2p[f].append(t)
    return {"fail_to_pass": dict(f2p), "pass_to_pass": dict(p2p), "flaky": flaky}


def gate_one(task: dict, clone: str, cache: str, scratch: str) -> dict:
    from harness.experiments.r3.runner import apply_patch, changed_functions, score, snapshot_tree
    from harness.experiments.r3.shared_tools import make_env
    work = os.path.join(scratch, "w")
    shutil.rmtree(work, ignore_errors=True)
    _git("-C", clone, "worktree", "prune", check=False)
    _git("-C", clone, "worktree", "add", "-q", "--detach", work, task["parent"])
    rec = {"commit": task["commit"], "repo": task["repo"], "family": task["family"]}
    t0 = time.time()
    try:
        env = make_env(clone, task["parent"], work, cache)
        try:
            env.setup()
        except Exception as exc:
            rec.update(passed=False, reason=f"setup: {getattr(exc, 'kind', type(exc).__name__)}: {str(exc)[:300]}")
            return rec
        changed = [f for f in _git("diff", "--name-only", "--no-renames", task["parent"], task["commit"],
                                   cwd=work).stdout.split("\n") if f]
        apply_patch(work, task.get("start_patch"))
        apply_patch(work, task["hidden_patch"])
        files = runnable_tests(env, task)
        if not files:
            rec.update(passed=False, reason="no runnable hidden test file")
            return rec
        env.ensure_current_build()
        start = run_states(env, files)
        set_files_to(work, task["commit"], changed)
        env.ensure_current_build()
        end = run_states(env, files)
        cls = classify(start, end)
        rec.update(test_files_run=files, unparsed_start=start["__unparsed__"], unparsed_commit=end["__unparsed__"],
                   n_f2p=sum(map(len, cls["fail_to_pass"].values())), n_p2p=sum(map(len, cls["pass_to_pass"].values())),
                   n_flaky=len(cls["flaky"]), flaky=cls["flaky"][:20])
        if not cls["fail_to_pass"]:
            rec.update(passed=False, reason="no fail-to-pass test")
            return rec
        gated = {**task, "fail_to_pass": cls["fail_to_pass"], "pass_to_pass": cls["pass_to_pass"]}
        # the answer key for diff quality, by the same code that scores the agent: the developer's non-test changes,
        # diffed from the start state
        reset_to(work, task["parent"])
        apply_patch(work, task.get("start_patch"))
        start_tree = snapshot_tree(work)
        source = [f for f in changed if f not in set(task["test_files"])]
        set_files_to(work, task["commit"], source)
        gold_tree = snapshot_tree(work)
        gated["gold_files"] = sorted(f for f in _git("diff", "--name-only", "--no-renames", start_tree, gold_tree,
                                                     cwd=work).stdout.split("\n") if f)
        gated["gold_functions"] = sorted(changed_functions(work, start_tree, gated["gold_files"]))
        # scoring check: the developer's patch resolves (with diff-quality F1 1.0); no change does not
        gold = score(gated, work, env, start_tree)
        reset_to(work, task["parent"])
        apply_patch(work, task.get("start_patch"))
        empty = score(gated, work, env, start_tree)
        rec.update(gold_resolved=gold.get("resolved"), empty_resolved=empty.get("resolved"),
                   gold_file_f1=gold.get("file_f1"), gold_function_f1=gold.get("function_f1"),
                   empty_file_f1=empty.get("file_f1"))
        if not gold.get("resolved"):
            bad = {k: v for k, v in (gold.get("tests") or {}).items() if v != "passed"}
            rec.update(passed=False, reason=f"the developer's patch does not score resolved: {list(bad.items())[:5]}")
            return rec
        if empty.get("resolved"):
            rec.update(passed=False, reason="the unchanged start state scores resolved")
            return rec
        gated["scoring_check"] = {"gold": True, "empty": False, "gold_file_f1": gold.get("file_f1"),
                                  "gold_function_f1": gold.get("function_f1")}
        rec.update(passed=True, task=gated, env={k: getattr(env, k, None) for k in
                                                 ("python_version", "strategy", "node_version", "pm_version", "built")})
        return rec
    except Exception as exc:  # recorded; one bad candidate never stops the gate
        rec.update(passed=False, reason=f"error: {type(exc).__name__}: {str(exc)[:400]}")
        return rec
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
        _git("-C", clone, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("mine_dir")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--family", required=True, choices=("B", "N"))
    p.add_argument("--want", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--per-repo", type=int, default=None)
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--id-prefix", default="pilot")
    a = p.parse_args(argv)
    fam, clones = a.family, dict(s.split("=", 1) for s in a.repos)
    per_repo = a.per_repo or PER_REPO[fam]
    cands = json.load(open(os.path.join(a.mine_dir, f"candidates_{fam}.json")))
    order = draw_order(cands, a.seed)
    os.makedirs(a.out, exist_ok=True)
    log_path = os.path.join(a.out, f"gate_{fam}.jsonl")
    done = {json.loads(ln)["commit"]: json.loads(ln) for ln in open(log_path)} if os.path.exists(log_path) else {}
    scratch = tempfile.mkdtemp(prefix=f"r3gate_{fam}_")
    passed = [r for r in done.values() if r.get("passed")]
    drawn = []
    try:
        for t in order:
            if len(passed) >= a.want:
                break
            n_repo = sum(1 for r in passed if r["repo"] == t["repo"])
            n_used = sum(1 for r in passed if r["repo"] in USED_REPOS)
            if n_repo >= per_repo or (t["repo"] in USED_REPOS and n_used >= a.want // 2):
                continue
            drawn.append(t["commit"])
            if t["commit"] in done:
                continue
            cache = a.cache_js if t["language"] == "js" else a.cache_py
            rec = gate_one(t, clones[t["repo"]], cache, scratch)
            done[t["commit"]] = rec
            with open(log_path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            if rec.get("passed"):
                passed.append(rec)
            print(f"{t['repo']:10} {t['commit'][:10]} {'PASS' if rec.get('passed') else 'fail'} "
                  f"{rec.get('reason', '')[:150]} ({rec['seconds']:.0f}s; {len(passed)}/{a.want})", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    tasks = []
    for i, r in enumerate(sorted(passed, key=lambda r: order.index(next(t for t in order if t["commit"] == r["commit"]))), 1):
        tasks.append({**r["task"], "id": f"{a.id_prefix}-{fam}-{i:02d}", "env": r.get("env")})
    body = json.dumps(tasks, sort_keys=True)
    json.dump({"family": fam, "seed": a.seed, "want": a.want, "per_repo": per_repo, "status": "frozen"
               if len(tasks) >= a.want else "short", "tasks_sha256": hashlib.sha256(body.encode()).hexdigest(),
               "tasks": tasks}, open(os.path.join(a.out, f"tasks_{fam}.json"), "w"), indent=1)
    att = {}
    for repo in sorted({t["repo"] for t in cands}):
        recs = [done[c] for c in drawn if c in done and done[c]["repo"] == repo]
        att[repo] = {"mined": sum(1 for t in cands if t["repo"] == repo), "gated": len(recs),
                     "passed": sum(1 for r in recs if r.get("passed")),
                     "reasons": dict(Counter(r.get("reason", "").split(":")[0] for r in recs if not r.get("passed")))}
    json.dump({"family": fam, "candidates": len(cands), "drawn": len(drawn), "passed": len(tasks), "want": a.want,
               "per_repo": att}, open(os.path.join(a.out, f"attrition_{fam}.json"), "w"), indent=1)
    print(json.dumps({"family": fam, "passed": len(tasks), "want": a.want, "candidates": len(cands)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
