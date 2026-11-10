"""Build R4 sanity tasks (delete, feature, Family B) in R3's task format, so R3's runner and loop run them unchanged.

    python -B -m harness.experiments.r4.sanity_tasks SAMPLES.json OUT.json --cache-py DIR --cache-js DIR NAME=CLONE ...

The family templates and done criteria are `ROADMAP_R4_R6.md` §2a's (the R4 design contract). For each sampled
candidate, in a fresh worktree with R3's environment builders:

| Family | Start state | Visible tests | Scored by |
|---|---|---|---|
| delete | the parent | the gate's related test files that exist at the parent | the developer's test changes applied (hidden); every related test that passes in the developer's end state must pass (pass-to-pass), **and the target must be gone** (`must_be_absent`, `r3/runner.score`) |
| feature | the parent + the developer's test changes (the failing tests are visible) | the commit's runnable test files | fail-to-pass (fails at the start, passes at the commit) and pass-to-pass, as R3 |
| Family B | as R3a (the anchor change applied) | as R3a | as R3a; only the prompt changes to §2a's template |

The sets come from 3 runs per state (R3's gate: `RUNS`, flaky tests dropped). Every task is then checked as R3's gate
did:
- the developer's change scores resolved;
- the unchanged start does not.
A candidate failing either is reported and left out, never forced in.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from harness.experiments.r3.gate import classify, reset_to, run_states, runnable_tests, set_files_to
from harness.experiments.r3.pool_scan import SOURCE, TEST
from harness.experiments.r3.runner import apply_patch, changed_functions, score, snapshot_tree

TRAILER = re.compile(r"^(Co-authored-by|Signed-off-by|Reviewed-by|Change-Id|Fixes|Closes|Refs?)\b.*$", re.I | re.M)


def _git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", check=check)


def prompt_delete(target: str) -> str:
    f, q = target.split("::", 1)
    return (f"Remove `{q}` from `{f}`. Remaining tests must still pass.\n\n"
            "Update or remove whatever uses it so that the project still works without it. "
            "When your changes are complete, call done.")


def commit_spec(clone: str, commit: str) -> str:
    msg = _git("-C", clone, "log", "-1", "--format=%B", commit).stdout
    msg = TRAILER.sub("", msg)
    msg = re.sub(r"\(#\d+\)", "", msg).strip()
    return re.sub(r"\n{3,}", "\n\n", msg)[:1500]


def prompt_feature(spec: str, failing: dict[str, list[str]]) -> str:
    names = [f"- `{f}`: {', '.join(ids[:6])}{' …' if len(ids) > 6 else ''}" for f, ids in sorted(failing.items())]
    return (f"Implement the following feature.\n\n{spec}\n\n"
            "Tests for the feature must pass. They are already in the working tree and fail now:\n" + "\n".join(names) +
            "\n\nWhen your changes are complete, call done.")


def prompt_family_b(r3a_prompt: str) -> str:
    """§2a: 'Function [X] changed [diff]. Update callers. Hidden tests will verify.' The anchor and diff are R3a's."""
    head = r3a_prompt.split("\n\nUpdate the rest of the project", 1)[0]
    return head + ("\n\nUpdate its callers. Hidden tests will verify the update. The change to the function above is "
                   "already in the working tree. When your changes are complete, call done.")


def _gold(work: str, parent: str, commit: str, source: list[str], start_patch: str | None) -> tuple[list[str], list[str]]:
    reset_to(work, parent)
    apply_patch(work, start_patch)
    start_tree = snapshot_tree(work)
    set_files_to(work, commit, source)
    gold_tree = snapshot_tree(work)
    files = sorted(f for f in _git("diff", "--name-only", "--no-renames", start_tree, gold_tree, cwd=work).stdout.split("\n") if f)
    return files, sorted(changed_functions(work, start_tree, files))


def build_one(family: str, cand: dict, gate: dict, clone: str, cache: str, scratch: str) -> dict:
    from harness.experiments.r3.shared_tools import make_env
    work = os.path.join(scratch, "w")
    shutil.rmtree(work, ignore_errors=True)
    _git("-C", clone, "worktree", "prune", check=False)
    _git("-C", clone, "worktree", "add", "-q", "--detach", work, cand["parent"])
    rec = {"family": family, "repo": cand["repo"], "commit": cand["commit"]}
    t0 = time.time()
    try:
        env = make_env(clone, cand["parent"], work, cache)
        env.setup()
        changed = [f for f in _git("diff", "--name-only", "--no-renames", cand["parent"], cand["commit"], cwd=work).stdout.split("\n") if f]
        tests = [f for f in changed if TEST.search(f)]
        source = [f for f in changed if f.endswith(SOURCE) and f not in tests]
        test_patch = _git("diff", "--binary", "--no-renames", cand["parent"], cand["commit"], "--", *tests, cwd=work).stdout if tests else ""
        task = {"family": family, "repo": cand["repo"], "language": "js" if cand["language"] == "js" else "python",
                "commit": cand["commit"], "parent": cand["parent"], "subject": cand.get("subject", ""),
                "after_index_patch": None, "start_files": [], "start_functions": [], "test_files": tests}
        if family == "delete":
            parent_files = set(_git("ls-tree", "-r", "--name-only", cand["parent"], cwd=work).stdout.split("\n"))
            visible = sorted(f for f in (gate.get("tests_run") or []) if f in parent_files)
            run = sorted(set(gate.get("tests_run") or []))
            set_files_to(work, cand["commit"], changed)
            env.ensure_current_build()
            end = run_states(env, run)
            cls = classify(end, end)
            task.update(start_patch=None, hidden_patch=test_patch or None, visible_tests=visible, tests_run=run,
                        fail_to_pass={}, pass_to_pass=cls["pass_to_pass"], must_be_absent=cand["anchor"],
                        prompt=prompt_delete(cand["anchor"]), anchor=cand["anchor"])
        elif family == "feature":
            apply_patch(work, test_patch)
            files = runnable_tests(env, {"test_files": tests})
            env.ensure_current_build()
            start = run_states(env, files)
            set_files_to(work, cand["commit"], source)
            env.ensure_current_build()
            end = run_states(env, files)
            cls = classify(start, end)
            if not cls["fail_to_pass"]:
                rec.update(built=False, reason="no fail-to-pass test on re-run (3 runs per state)")
                return rec
            task.update(start_patch=test_patch, hidden_patch=None, visible_tests=files, tests_run=files,
                        fail_to_pass=cls["fail_to_pass"], pass_to_pass=cls["pass_to_pass"],
                        prompt=prompt_feature(commit_spec(work, cand["commit"]), cls["fail_to_pass"]),
                        new_functions=cand.get("new_functions", []))
        else:
            raise ValueError(family)
        task["gold_files"], task["gold_functions"] = _gold(work, cand["parent"], cand["commit"], source, task["start_patch"])
        # the scoring check (as R3's gate): the developer's change resolves; the unchanged start does not
        reset_to(work, cand["parent"]); apply_patch(work, task["start_patch"]); st = snapshot_tree(work)
        set_files_to(work, cand["commit"], source)
        gold = score(task, work, env, st)
        reset_to(work, cand["parent"]); apply_patch(work, task["start_patch"]); st = snapshot_tree(work)
        empty = score(task, work, env, st)
        rec.update(gold_resolved=gold.get("resolved"), empty_resolved=empty.get("resolved"),
                   n_f2p=sum(map(len, task["fail_to_pass"].values())), n_p2p=sum(map(len, task["pass_to_pass"].values())))
        if not gold.get("resolved") or empty.get("resolved"):
            rec.update(built=False, reason=f"scoring check failed: gold {gold.get('resolved')}, empty {empty.get('resolved')}",
                       gold_not_passed=[k for k, v in (gold.get("tests") or {}).items() if v != "passed"][:5])
            return rec
        if not task["pass_to_pass"] and not task["fail_to_pass"]:
            rec.update(built=False, reason="no scored test")
            return rec
        task["scoring_check"] = {"gold": True, "empty": False}
        task["env"] = {k: getattr(env, k, None) for k in ("python_version", "strategy", "node_version", "pm_version", "built")}
        rec.update(built=True, task=task)
        return rec
    except Exception as exc:  # recorded, never fatal for the other candidates
        rec.update(built=False, reason=f"error: {type(exc).__name__}: {str(exc)[:300]}")
        return rec
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
        _git("-C", clone, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(work, ignore_errors=True)


def family_b_task(r3a_task: dict) -> dict:
    t = dict(r3a_task)
    t["prompt"] = prompt_family_b(r3a_task["prompt"])
    t["family"] = "B"
    return t


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("samples", help="JSON: {family: [commit or R3a task id, ...]}")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    p.add_argument("--gate-dir", required=True, help="the gating logs (gate_<family>.jsonl)")
    a = p.parse_args(argv)
    from harness.experiments.r3.shared_tools import language_of
    clones = dict(s.split("=", 1) for s in a.repos)
    samples = json.load(open(a.samples))
    here = os.path.dirname(os.path.abspath(__file__))
    log_path = a.out + ".log.jsonl"
    done = {json.loads(l)["commit"]: json.loads(l) for l in open(log_path)} if os.path.exists(log_path) else {}
    scratch = tempfile.mkdtemp(prefix="r4sanity_")
    try:
        for fam in ("delete", "feature"):
            cands = {c["commit"]: c for c in json.load(open(os.path.join(here, "candidates", f"candidates_{fam}.json")))}
            gates = {json.loads(l)["commit"]: json.loads(l) for l in open(os.path.join(a.gate_dir, fam, f"gate_{fam}.jsonl"))}
            for commit in samples.get(fam, []):
                if commit in done:
                    continue
                c = cands[commit]
                cache = a.cache_js if language_of(clones[c["repo"]]) == "js" else a.cache_py
                rec = build_one(fam, c, gates.get(commit, {}), clones[c["repo"]], cache, scratch)
                done[commit] = rec
                with open(log_path, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                print(f"{fam:8} {c['repo']:12} {commit[:10]} {'BUILT' if rec.get('built') else 'skip'} "
                      f"{rec.get('reason', '')[:100]} ({rec['seconds']:.0f}s)", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    tasks = []
    for fam in ("delete", "feature"):
        for i, commit in enumerate(c for c in samples.get(fam, []) if done.get(c, {}).get("built")):
            tasks.append({**done[commit]["task"], "id": f"r4s-{fam}-{i + 1:02d}"})
    for i, tid in enumerate(samples.get("B", [])):
        tasks.append({**family_b_task(json.load(open(f"reports/r3/r3a/tasks/{tid}.json"))), "id": f"r4s-B-{i + 1:02d}",
                      "r3a_id": tid})
    json.dump({"tasks": tasks}, open(a.out, "w"), indent=1)
    print(f"{len(tasks)} tasks -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
