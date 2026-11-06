"""R3's run driver and scorer: one task, one version, one seed.

    python -m harness.experiments.r3.runner TASK.json VERSION SEED --clone CLONE --out DIR --cache DIR \
        --base-url URL [--turn-cap N]

**A task** (one JSON object):
- `id`, `family` (`B` or `N`), `repo`, `parent` (the start revision);
- `start_patch`: what is applied to `parent` to make the start state (Family B: the developer's change to the anchor;
  the qwen notice check: none);
- `prompt`;
- `visible_tests`: the test files `run_tests` runs by default;
- `hidden_patch`: the developer's test changes, applied only for scoring, never present during the run;
- `fail_to_pass` and `pass_to_pass`: `{file: [test ids]}`;
- `gold_files` and `gold_functions` (`file::qualified name`), for diff quality.
- An optional `after_index_patch` is applied after the index is built at the start. The qwen notice check uses it to
  leave a file broken, as an earlier agent edit would.

**Run:** the start state in a fresh worktree, the revision's environment (`make_env`), the WorkingIndex and
ToolLayer, the shared tools, then the loop (`AgentRun`).

**Score** (pre-registration, "Answer key and scoring"):
- **resolved:** the hidden patch applied on top of the final tree; every fail-to-pass and pass-to-pass test passes.
- **secondary:** F1 of the changed files and of the changed functions against the developer's commit, and the
  process measures from the run's summary.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

TOKENIZER = os.environ.get("R3_TOKENIZER", "/home/user/models/qwen2-tokenizer/tokenizer.json")


def _git(*args, cwd=None, input_text=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, input=input_text, capture_output=True, text=True, check=check)


def apply_patch(work: str, patch: str | None) -> None:
    if patch:
        _git("apply", "--whitespace=nowarn", "-", cwd=work, input_text=patch)


def f1(got: set, gold: set) -> float | None:
    if not gold and not got:
        return None
    tp = len(got & gold)
    p = tp / len(got) if got else 0.0
    r = tp / len(gold) if gold else 0.0
    return round(2 * p * r / (p + r), 4) if p + r else 0.0


def changed_functions(work: str, base_rev: str, files: list[str]) -> set[str]:
    """`file::qualified name` of every function whose code differs between `base_rev` and the working tree."""
    from harness.experiments.change_history.mine import _normalized, _lang, functions
    out = set()
    for f in files:
        old = _git("show", f"{base_rev}:{f}", cwd=work, check=False)
        old_b = old.stdout.encode() if old.returncode == 0 else b""
        path = os.path.join(work, f)
        new_b = open(path, "rb").read() if os.path.exists(path) else b""
        fo, fn = functions(f, old_b) if old_b else {}, functions(f, new_b) if new_b else {}
        lang = _lang(f)
        for q in set(fo) | set(fn):
            a, b = fo.get(q, {}).get("code", ""), fn.get(q, {}).get("code", "")
            if _normalized(a, lang) != _normalized(b, lang):
                out.add(f"{f}::{q}")
    return out


def snapshot_tree(work: str) -> str:
    """A git tree of the working tree as it is now (tracked and untracked, ignored files and `.r3-*` left out), made
    with a temporary index, so nothing in the worktree changes."""
    fd, idx = tempfile.mkstemp(prefix="r3idx_")
    os.close(fd)
    os.remove(idx)
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        subprocess.run(["git", "read-tree", "HEAD"], cwd=work, env=env, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A", "--", ".", ":(glob,exclude)**/.r3-*"], cwd=work, env=env, check=True,
                       capture_output=True)
        return subprocess.run(["git", "write-tree"], cwd=work, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()
    finally:
        if os.path.exists(idx):
            os.remove(idx)


def score(task: dict, work: str, env, start_tree: str | None = None) -> dict:
    """Score the final tree in `work` (it is modified: the hidden patch is applied).

    `start_tree` is the start state (`snapshot_tree` after the start patch). The agent's files and functions are what
    differs from it; the gold ones (`gold_files`, `gold_functions`) are what the developer changed beyond the start
    state. Without `start_tree`, the parent is the base and the start patch's own files and functions are left out.
    """
    if start_tree:
        final = snapshot_tree(work)
        agent_files = {f for f in _git("diff", "--name-only", "--no-renames", start_tree, final, cwd=work).stdout
                       .split("\n") if f}
        res = {"agent_files": sorted(agent_files)}
        base, drop_fns = start_tree, set()
    else:
        changed = [ln for ln in _git("diff", "--name-only", "HEAD", cwd=work).stdout.split() if ln]
        untracked = _git("ls-files", "--others", "--exclude-standard", cwd=work).stdout.split()
        agent_files = {f for f in changed + untracked if not f.startswith(".r3-")} - set(task.get("start_files", []))
        res = {"agent_files": sorted(agent_files)}
        base, drop_fns = task["parent"], set(task.get("start_functions", []))
    res["file_f1"] = f1(set(res["agent_files"]), set(task.get("gold_files", [])))
    try:
        fns = changed_functions(work, base, sorted(agent_files))
        res["agent_functions"] = sorted(fns - drop_fns)
        res["function_f1"] = f1(fns - drop_fns, set(task.get("gold_functions", [])))
    except Exception as exc:  # diff quality is secondary: recorded, never fatal
        res["function_f1"], res["function_error"] = None, f"{type(exc).__name__}: {exc}"
    try:
        apply_patch(work, task.get("hidden_patch"))
        res["hidden_patch_applied"] = True
    except subprocess.CalledProcessError as exc:
        res.update(hidden_patch_applied=False, resolved=False, reason=f"hidden tests do not apply: {exc.stderr[-300:]}")
        return res
    outcomes = {}
    ensure = getattr(env, "ensure_current_build", None)
    if ensure is not None:
        try:
            ensure()          # tests that use built output score the final tree, not the start state's build
        except Exception as exc:
            res.update(resolved=False, reason=f"the final tree does not build: {str(exc)[-300:]}")
            return res
    for group in ("fail_to_pass", "pass_to_pass"):
        for f, ids in (task.get(group) or {}).items():
            r = env.run_test(f)
            for t in ids:
                outcomes[f"{f}::{t}"] = (group, (r.get("tests") or {}).get(t, "missing"))
    res["tests"] = {k: v[1] for k, v in outcomes.items()}
    res["resolved"] = bool(outcomes) and all(s == "passed" for _g, s in outcomes.values())
    res["fail_to_pass_passed"] = sum(1 for g, s in outcomes.values() if g == "fail_to_pass" and s == "passed")
    res["pass_to_pass_broken"] = sum(1 for g, s in outcomes.values() if g == "pass_to_pass" and s != "passed")
    return res


def outcome(result: dict) -> str:
    """The run's outcome, one of five (BUILD_PLAN section 4, criterion 6):
    - `resolved`: the agent called done, and the final tree passes every scored test;
    - `unresolved`: the agent called done, and the final tree does not pass (it tried and failed);
    - `stuck`: the turn cap was reached without done;
    - `budget`: the token budget ran out without done;
    - `error`: the model call failed (infrastructure).
    Only `resolved` counts as resolved: runs that stop at the cap or the budget count as unresolved
    (pre-registration, "Tests and decisions"), and they are reported apart."""
    stop = result.get("stop") or ("done" if result.get("end") == "done" else "stuck" if result.get("end") == "turn_cap"
                                  else "budget" if result.get("end") == "budget" else "error")
    if stop != "done":
        return stop
    return "resolved" if (result.get("score") or {}).get("resolved") else "unresolved"


def run_task(task: dict, version: str, seed: int, clone: str, out: str, cache: str, model, turn_cap: int,
             score_run: bool = True) -> dict:
    from harness.experiments.r3.loop import AgentRun
    from harness.experiments.r3.shared_tools import SharedTools, make_env
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    run_id = f"{task['id']}__{version}__{seed}"
    final = os.path.join(out, run_id + ".score.json")
    if os.path.exists(final):
        return json.load(open(final))
    scratch = tempfile.mkdtemp(prefix="r3run_", dir=os.path.join(cache, "runs") if os.path.isdir(os.path.join(cache, "runs")) else None)
    work, shadow = os.path.join(scratch, "work"), os.path.join(scratch, "shadow")
    _git("-C", clone, "worktree", "add", "-q", "--detach", work, task["parent"])
    wi = layer = None
    stage = "start state"
    try:
        apply_patch(work, task.get("start_patch"))
        stage = "environment"
        env = make_env(clone, task["parent"], work, cache)
        env.setup()
        stage = "index"
        wi = WorkingIndex(work, shadow)
        apply_patch(work, task.get("after_index_patch"))
        start_tree = snapshot_tree(work)      # the start state, after setup: what the agent changes is diffed from it
        layer = ToolLayer(wi, TOKENIZER)
        shared = SharedTools(work, env, task.get("visible_tests", []), TOKENIZER)
        stage = "loop"
        summary = AgentRun(task, version, seed, model, shared, layer, TOKENIZER, turn_cap).run(out)
        result = {**summary}
        if score_run:
            stage = "scoring"
            result["score"] = score(task, work, env, start_tree)
        result["outcome"] = outcome(result)
        with open(final, "w") as fh:
            json.dump(result, fh, indent=1)
        err = os.path.join(out, run_id + ".error.json")
        if os.path.exists(err):
            os.remove(err)
        return result
    except Exception as exc:   # a harness or infrastructure death: recorded, never silently missing
        import traceback
        with open(os.path.join(out, run_id + ".error.json"), "w") as fh:
            json.dump({"run": run_id, "task": task["id"], "version": version, "seed": seed, "stage": stage,
                       "error": f"{type(exc).__name__}: {exc}"[:2000], "traceback": traceback.format_exc()[-4000:]},
                      fh, indent=1)
        raise
    finally:
        if layer is not None:
            layer.close()
        if wi is not None:
            wi.close()
        _git("-C", clone, "worktree", "remove", "--force", work, check=False)
        _git("-C", clone, "worktree", "prune", check=False)
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("task")
    p.add_argument("version", choices=("PRISM", "BASELINE"))
    p.add_argument("seed", type=int)
    p.add_argument("--clone", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:11434")
    p.add_argument("--turn-cap", type=int, default=30)
    a = p.parse_args(argv)
    from harness.experiments.r3.loop import OllamaChat
    task = json.load(open(a.task))
    res = run_task(task, a.version, a.seed, os.path.abspath(a.clone), a.out, a.cache, OllamaChat(a.base_url),
                   a.turn_cap)
    print(json.dumps({k: res.get(k) for k in ("run", "turns", "end", "used_tokens")}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
