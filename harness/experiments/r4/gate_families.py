"""R4's family-specific gates (the R4 design contract: `ROADMAP_R4_R6.md` §2a; the rules as implemented: `GATES.md`).

    python -B -m harness.experiments.r4.gate_families CANDIDATES.json OUT_DIR --family delete \
        --want 60 --max-attempts 150 --per-repo 8 --seed 5501 --cache-py DIR --cache-js DIR NAME=CLONE ...

No model calls. Each candidate is checked in a fresh worktree with R3's environment builders (`r3/shared_tools.make_env`),
in a seeded draw order, until `--want` pass, `--max-attempts` have been tried, or the candidates run out. A repository
contributes at most `--per-repo` passes. Every attempt is one line in `gate_<family>.jsonl` (resumable).

| Family | Signal gate (the contract) | Checked here |
|---|---|---|
| delete | tests pass at end, target absent | at the commit: the related tests run with 0 failed, 1+ passed; the anchor is not defined in its file |
| refactor | tests pass at start AND end; diff is structural | the related tests pass at the parent and with the commit's source; a function's normalized code differs (mining) |
| move | tests pass at end; old path gone, new path exists | at the commit: the related tests pass; the function is gone from the old file and defined in the new one |
| tests | score by fail-to-pass on the prior version | the commit's tests pass at the commit; 1+ of them fails with the target's file at its prior version |
| feature | visible failing test at start | the commit's test files, given at the start, have 1+ test failing at the parent and passing at the commit |

"Related tests" (delete, refactor, move): the commit's own changed test files, plus up to 4 of the repository's test
files that mention the changed functions' names or module, most mentions first. A run "passes" when every file
produced results, at least one test passed, and none failed. Each state is run once here; flakiness is checked when a
task is calibrated, not at this gate.
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
from collections import Counter

from harness.experiments.change_history.mine import _lang, _normalized, functions
from harness.experiments.r3.gate import draw_order, reset_to, runnable_tests, set_files_to
from harness.experiments.r3.pool_scan import SOURCE, TEST, show

RELATED_MAX = 4
TIME_LIMIT_S = 1500
MIN_FREE_GB = 2.0      # below this the gate stops (exit 3) instead of recording failures caused by a full disk


def _git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", check=check)


def _leaf(q: str) -> str:
    return q.rsplit(".", 1)[-1]


def _nameable(leaf: str) -> bool:
    return len(leaf) > 3 and not (leaf.startswith("__") and leaf.endswith("__")) and not leaf.startswith("<")


def module_keys(path: str) -> list[str]:
    """How a test file would name this module: Python `pkg.mod` (without a leading `src.`), JS `dir/name` import
    path tails."""
    stem = re.sub(r"\.[mc]?[jt]sx?$|\.py$", "", path)
    parts = [p for p in stem.split("/") if p not in ("src", "lib", "index", "__init__")]
    if not parts:
        return []
    if path.endswith(".py"):
        return [".".join(parts[-2:])] if len(parts) >= 2 else [parts[-1]] if len(parts[-1]) > 4 else []
    return ["/".join(parts[-2:])] if len(parts) >= 2 else []


def related_tests(env, names: set[str], modules: list[str], extra: list[str]) -> list[str]:
    known = set(env.test_files())
    out = [f for f in extra if f in known]
    pats = [re.compile(rf"(?<![\w$]){re.escape(n)}(?![\w$])") for n in sorted(names) if _nameable(n)]
    scored = []
    for t in sorted(known - set(out)):
        try:
            text = open(os.path.join(env.work, t), encoding="utf8", errors="replace").read()
        except OSError:
            continue
        n = sum(len(p.findall(text)) for p in pats) + sum(text.count(m) for m in modules)
        if n:
            scored.append((-n, t))
    out += [t for _n, t in sorted(scored)[:RELATED_MAX]]
    return out


def run_once(env, files: list[str]) -> dict:
    tests, unparsed = {}, []
    for f in files:
        r = env.run_test(f)
        if not r.get("parsed"):
            unparsed.append(f)
        for t, s in (r.get("tests") or {}).items():
            tests[f"{f}::{t}"] = s
    c = Counter(tests.values())
    return {"tests": tests, "unparsed": unparsed, "passed": c.get("passed", 0), "failed": c.get("failed", 0),
            "passes": bool(files) and not unparsed and c.get("passed", 0) > 0 and c.get("failed", 0) == 0}


def _summary(r: dict) -> dict:
    return {k: r[k] for k in ("passed", "failed", "unparsed", "passes")}


def defined_in(work: str, qname: str, path: str) -> bool:
    p = os.path.join(work, path)
    if not os.path.isfile(p):
        return False
    return qname in functions(path, open(p, "rb").read())


# ---------------------------------------------------------------------------------------------- per family
def gate_delete(c, work, env, rec):
    f, q = c["anchor"].split("::", 1)
    names = {_leaf(q)}
    files = related_tests(env, names, module_keys(f), [])
    all_changed = c["source_files"] + c["test_files"]
    set_files_to(work, c["commit"], all_changed)
    env.ensure_current_build()
    files = [t for t in dict.fromkeys(c["test_files"] + files) if os.path.isfile(os.path.join(work, t))]
    files = [t for t in files if t in set(env.test_files()) or TEST.search(t)]
    rec["tests_run"] = files
    if not files:
        return "no related test file"
    end = run_once(env, files)
    rec["end"] = _summary(end)
    if not end["passes"]:
        return "tests do not pass at the end"
    if defined_in(work, q, f):
        return "the target is still defined at the end"
    return None


def gate_move(c, work, env, rec):
    of, oq = c["from"].split("::", 1)
    nf, nq = c["to"].split("::", 1)
    files = related_tests(env, {_leaf(oq)}, module_keys(of), [])
    set_files_to(work, c["commit"], c["source_files"] + c["test_files"])
    env.ensure_current_build()
    files = [t for t in dict.fromkeys(c["test_files"] + files) if os.path.isfile(os.path.join(work, t))]
    rec["tests_run"] = files
    if not files:
        return "no related test file"
    end = run_once(env, files)
    rec["end"] = _summary(end)
    if not end["passes"]:
        return "tests do not pass at the end"
    if defined_in(work, oq, of):
        return "the old path still defines the function"
    if not defined_in(work, nq, nf):
        return "the new path does not define the function"
    return None


def gate_refactor(c, work, env, rec):
    names = {_leaf(x.split("::", 1)[1]) for x in c["changed_functions"]}
    mods = [m for f in c["source_files"] for m in module_keys(f)]
    files = related_tests(env, names, mods, [])
    rec["tests_run"] = files
    if not files:
        return "no related test file"
    env.ensure_current_build()
    start = run_once(env, files)
    rec["start"] = _summary(start)
    if not start["passes"]:
        return "tests do not pass at the start"
    set_files_to(work, c["commit"], c["source_files"])
    env.ensure_current_build()
    end = run_once(env, files)
    rec["end"] = _summary(end)
    if not end["passes"]:
        return "tests do not pass at the end"
    return None


DEF = r"(def|function|class|const|let)[[:space:]]+{}\b"


def pick_target(clone: str, parent: str, names: list[str], added: str) -> tuple[str, str] | None:
    """The named function the new tests mention most that is defined in exactly one non-test source file."""
    best = None
    for n in names:
        r = _git("-C", clone, "grep", "-l", "-E", DEF.format(re.escape(n)), parent, "--",
                 *[f"*{e}" for e in SOURCE], check=False)
        defs = [ln.split(":", 1)[1] for ln in r.stdout.splitlines() if ":" in ln]
        defs = [d for d in defs if not TEST.search(d) and not d.endswith(".d.ts")]
        if len(defs) != 1:
            continue
        k = len(re.findall(rf"(?<![\w$]){re.escape(n)}(?![\w$])", added))
        if best is None or k > best[0]:
            best = (k, n, defs[0])
    return (best[1], best[2]) if best else None


def gate_tests(c, work, env, rec):
    diff = _git("-C", work, "diff", "-U0", "--no-renames", c["parent"], c["commit"], "--", *c["test_files"]).stdout
    added = "\n".join(ln for ln in diff.splitlines() if ln.startswith("+"))
    tgt = pick_target(work, c["parent"], c.get("names") or [], added)
    if tgt is None:
        return "no named target defined in exactly one source file"
    name, path = tgt
    rec["target"] = f"{path}::{name}"
    prior = _git("-C", work, "log", "-1", "--format=%H", c["parent"], "--", path).stdout.strip()
    pp = _git("-C", work, "rev-parse", "--verify", "-q", prior + "^", check=False).stdout.strip() if prior else ""
    if not pp:
        return "no prior version of the target's file in the history"
    rec["prior_version"] = {"file": path, "rev": pp, "changed_by": prior}
    set_files_to(work, c["commit"], c["test_files"])
    files = runnable_tests(env, {"test_files": c["test_files"]})
    rec["tests_run"] = files
    if not files:
        return "no runnable test file"
    env.ensure_current_build()
    cur = run_once(env, files)
    rec["current"] = _summary(cur)
    if not cur["passes"]:
        return "the developer's tests do not pass on the current code"
    now_text = open(os.path.join(work, path), "rb").read()
    old_text = show(work, pp, path)
    fn_now = functions(path, now_text).get(name) or next((v for k, v in functions(path, now_text).items()
                                                           if _leaf(k) == name), None)
    fn_old = (functions(path, old_text).get(name) or next((v for k, v in functions(path, old_text).items()
                                                           if _leaf(k) == name), None)) if old_text else None
    lang = _lang(path)
    if fn_now and fn_old and _normalized(fn_now["code"], lang) == _normalized(fn_old["code"], lang):
        rec["prior_differs_in_target"] = False
    else:
        rec["prior_differs_in_target"] = True
    if old_text is None:
        os.remove(os.path.join(work, path))
    else:
        open(os.path.join(work, path), "wb").write(old_text)
    try:
        env.ensure_current_build()
    except Exception as exc:  # the prior version does not build: every test fails on it
        rec["prior"] = {"build_failed": str(exc)[-200:]}
        f2p = [t for t, s in cur["tests"].items() if s == "passed"]
        rec.update(n_f2p=len(f2p), f2p_kind="build")
        return None if f2p else "no test fails on the prior version"
    pri = run_once(env, files)
    rec["prior"] = _summary(pri)
    f2p = [t for t, s in cur["tests"].items() if s == "passed" and (pri["tests"].get(t) in ("failed", None)
                                                                    or t.split("::")[0] in pri["unparsed"])]
    rec["n_f2p"] = len(f2p)
    rec["f2p_kind"] = "collection" if f2p and all(t.split("::")[0] in pri["unparsed"] for t in f2p) else "assertion"
    rec["f2p"] = f2p[:20]
    return None if f2p else "no test fails on the prior version"


def gate_feature(c, work, env, rec):
    set_files_to(work, c["commit"], c["test_files"])
    files = runnable_tests(env, {"test_files": c["test_files"]})
    rec["tests_run"] = files
    if not files:
        return "no runnable test file"
    env.ensure_current_build()
    start = run_once(env, files)
    set_files_to(work, c["commit"], c["source_files"])
    env.ensure_current_build()
    end = run_once(env, files)
    f2p = [t for t, s in end["tests"].items() if s == "passed" and start["tests"].get(t) in ("failed", None)]
    p2p_broken = [t for t, s in start["tests"].items() if s == "passed" and end["tests"].get(t) != "passed"]
    rec.update(start=_summary(start), end=_summary(end), n_f2p=len(f2p), f2p=f2p[:20], n_p2p_broken=len(p2p_broken))
    if not f2p:
        return "no visible test fails at the start and passes at the commit"
    return None


GATES = {"delete": gate_delete, "move": gate_move, "refactor": gate_refactor, "tests": gate_tests,
         "feature": gate_feature}


def gate_one(c: dict, family: str, clone: str, cache: str, scratch: str) -> dict:
    from harness.experiments.r3.shared_tools import make_env
    work = os.path.join(scratch, "w")
    shutil.rmtree(work, ignore_errors=True)
    _git("-C", clone, "worktree", "prune", check=False)
    rec = {"family": family, "repo": c["repo"], "commit": c["commit"], "parent": c["parent"]}
    t0 = time.time()
    try:
        _git("-C", clone, "worktree", "add", "-q", "--detach", work, c["parent"])
        env = make_env(clone, c["parent"], work, cache)
        try:
            env.setup()
        except Exception as exc:
            rec.update(passed=False, reason=f"setup: {getattr(exc, 'kind', type(exc).__name__)}: {str(exc)[:200]}")
            return rec
        why = GATES[family](c, work, env, rec)
        rec.update(passed=why is None, reason=why or "")
        if why is None:
            rec["env"] = {k: getattr(env, k, None) for k in ("python_version", "strategy", "node_version", "pm_version",
                                                            "built")}
        return rec
    except Exception as exc:
        rec.update(passed=False, reason=f"error: {type(exc).__name__}: {str(exc)[:300]}")
        return rec
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
        _git("-C", clone, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("candidates")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    p.add_argument("--family", required=True, choices=sorted(GATES))
    p.add_argument("--want", type=int, required=True)
    p.add_argument("--max-attempts", type=int, required=True)
    p.add_argument("--per-repo", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--cache-py", required=True)
    p.add_argument("--cache-js", required=True)
    a = p.parse_args(argv)
    clones = dict(s.split("=", 1) for s in a.repos)
    cands = [c for c in json.load(open(a.candidates)) if c["repo"] in clones]
    os.makedirs(a.out, exist_ok=True)
    log = os.path.join(a.out, f"gate_{a.family}.jsonl")
    done = {json.loads(ln)["commit"]: json.loads(ln) for ln in open(log)} if os.path.exists(log) else {}
    scratch = tempfile.mkdtemp(prefix=f"r4gate_{a.family}_")
    try:
        for c in draw_order(cands, a.seed):
            if shutil.disk_usage(a.out).free < MIN_FREE_GB * 1e9:
                print(f"stopping: less than {MIN_FREE_GB} GB free", flush=True)
                return 3
            passed = [r for r in done.values() if r.get("passed")]
            if len(passed) >= a.want or len(done) >= a.max_attempts:
                break
            if c["commit"] in done:
                continue
            if sum(1 for r in passed if r["repo"] == c["repo"]) >= a.per_repo:
                continue
            from harness.experiments.r3.shared_tools import language_of
            cache = a.cache_js if language_of(clones[c["repo"]]) == "js" else a.cache_py   # mining labels new repos "python"
            rec = gate_one(c, a.family, clones[c["repo"]], cache, scratch)
            done[c["commit"]] = rec
            with open(log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(f"{a.family:8} {c['repo']:14} {c['commit'][:10]} {'PASS' if rec['passed'] else 'fail'} "
                  f"{rec['reason'][:110]} ({rec['seconds']:.0f}s; {len([r for r in done.values() if r.get('passed')])}"
                  f"/{a.want}, {len(done)} tried)", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
