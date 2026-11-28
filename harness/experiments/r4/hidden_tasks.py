"""Phase 3's hidden-dependency tasks (the user's decision, 2026-11-22): rename a method whose name other objects share,
at exactly the places PRISM resolves to it (`hidden_scout.py`), validated in the repository's own test environment.

    python -B -m harness.experiments.r4.hidden_tasks SCOUT.json OUT.json --cache DIR --scratch DIR --want 20 \
        --per-repo 3 NAME=CLONE ...

No model calls. **The answer key** renames the definition and each place, at its line and column, and nothing else:
a token-matching rename (every `name` in the files that contain it, the way Phase 2's gold was made, and the way a
`grep`/`sed` change goes) would also rename the look-alikes. **The static check** (`derived_checks.py`, kind
`rename_sites`) counts the uses of the old name per file, scope and receiver against the answer key: a place left
over and a look-alike renamed both fail.

**A candidate becomes a task only if every check passes** (`derive_tasks.validate`, Phase 2's checks: the tests pass
at the start, 3 runs; the answer key passes them, a generated hidden test and the static check, 3 runs, nothing
flaky; the start fails the check and a test) **and the places are live:** renaming the definition alone makes at
least one scored test fail; **and two readers agree:** jedi's Find All References (`r3/lsp_tools.py`) names exactly
PRISM's places (by file and line). Recorded for each task besides (`probes`): what the definition-only rename and the
token-matching rename do to the scored tests and to the static check.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from harness.experiments.r4 import derive_tasks as D
from harness.experiments.r4 import derived_checks as DC

MAX_TEST_FILES = 4
INERT_BASES = {"ABC", "Generic", "Protocol"}


def _imports(tree) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module)
            out |= {f"{n.module}.{a.name}" for a in n.names}
    return out


#: per worktree: {test file: whether every test in it passes at the start}
_START_OK: dict[tuple[str, str], bool] = {}
MAX_TRIED_TEST_FILES = 10


def scored_tests(work: str, c: dict, known: set, env=None) -> list[str]:
    """The test files with a place, then the test files naming the method's class, then those importing the target's
    module or a place's module; with `env`, only files whose every test passes at the start (a file failing for
    reasons of its own, such as the network, is skipped rather than rejecting the candidate)."""
    first = [f for f in c["test_files"] if f in known]
    mods = {D.importable(work, f) for f in {c["file"], *c["files"]} if not f.startswith("tests")}
    cls = D._qual(c).rsplit(".", 1)[0].split(".")[-1]
    named, rest = [], []
    for f in sorted(known - set(first)):
        if not f.endswith(".py"):
            continue
        try:
            text = D._read(work, f)
            imps = _imports(ast.parse(text))
        except (SyntaxError, ValueError, OSError):
            continue
        if any(i == m or i.startswith(m + ".") or ("." in i and m.startswith(i + ".")) for i in imps for m in mods):
            (named if re.search(rf"\b{re.escape(cls)}\b", text) else rest).append(f)
    ordered = first + named + rest
    if env is None:
        return ordered[:MAX_TEST_FILES]
    out = []
    for f in ordered[:MAX_TRIED_TEST_FILES]:
        key = (work, f)
        if key not in _START_OK:
            st = D._run_states(env, [f])
            _START_OK[key] = not st.get("__unparsed__") and bool(st) and all(
                v[0] == "passed" for k, v in st.items() if k != "__unparsed__")
        if _START_OK[key]:
            out.append(f)
        if len(out) >= MAX_TEST_FILES:
            break
    return out


def _rename_at(text: str, edits: list[tuple[int, int]], old: str, new: str) -> str | None:
    """`old` replaced by `new` at each (line, byte column); None if one of them does not hold `old`."""
    lines = text.encode("utf8").split(b"\n")
    o, n = old.encode(), new.encode()
    for line, col in sorted(set(edits), reverse=True):
        ln = lines[line - 1]
        if ln[col:col + len(o)] != o:
            return None
        lines[line - 1] = ln[:col] + n + ln[col + len(o):]
    return b"\n".join(lines).decode("utf8")


def def_position(work: str, c: dict) -> tuple[int, int] | None:
    """The (line, byte column) of the definition's name in its `def`/`class`; None when the name is defined twice in
    its container (a property with a setter, `@overload`s, a conditional redefinition) or a method's class uses the
    bare name."""
    tree = ast.parse(D._read(work, c["file"]))
    qual = D._qual(c)
    node = DC.find_def(tree, qual)
    if node is None:
        return None
    parent = DC.find_def(tree, qual.rsplit(".", 1)[0]) if "." in qual else tree
    if parent is None:
        return None
    defs = [x for x in ast.walk(parent) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and x.name == c["leaf"]]
    if defs != [node]:
        return None
    if parent is not tree and any(isinstance(x, ast.Name) and x.id == c["leaf"] for x in ast.walk(parent)):
        return None
    ln = D._read(work, c["file"]).encode("utf8").split(b"\n")[node.lineno - 1]
    m = re.search(rb"\b(?:def|class)\s+(" + re.escape(c["leaf"].encode()) + rb")\b", ln)
    return (node.lineno, m.start(1)) if m else None


def jedi_agreement(work: str, c: dict) -> dict:
    """PRISM's places against jedi's Find All References on the definition (`r3/lsp_tools.py`), by (file, line)."""
    from harness.experiments.r3.lsp_tools import LspError, LspReferences
    try:
        text = LspReferences(work).find_references(f"{c['file']}:{c['lines'][0]}")
    except LspError as exc:
        return {"agree": False, "error": str(exc)}
    got = set()
    for ln in text.splitlines()[1:]:
        p = ln.split(":")
        if len(p) > 3 and p[1].isdigit() and "(definition)" not in p[2]:
            got.add((p[0], int(p[1])))
    want = {(p["file"], p["line"]) for p in c["places"] if p["how"] != "string"}     # `__all__`: not a reference
    return {"agree": got == want, "jedi_only": sorted(got - want)[:5], "prism_only": sorted(want - got)[:5]}


def gold_sites(work: str, c: dict, new: str) -> dict | None:
    pos = def_position(work, c)
    if pos is None:
        return None
    by_file: dict[str, list] = {c["file"]: [pos]}
    for p in c["places"]:
        by_file.setdefault(p["file"], []).append((p["line"], p["col"]))
    out = {}
    for f, edits in by_file.items():
        text = _rename_at(D._read(work, f), edits, c["leaf"], new)
        if text is None:
            return None
        out[f] = text
    return out


def gold_tokens(work: str, c: dict, new: str) -> dict:
    """The grep-and-replace change: every `leaf` token renamed in every file `git grep -w` finds it in."""
    files = D._git(work, "grep", "-l", "-w", "-I", c["leaf"], "--", "*.py").stdout.split()
    return {f: D._replace_tokens(D._read(work, f), c["leaf"], new) for f in files}


def _probe(work, env, rev, changed, derived, tests) -> dict:
    from harness.experiments.r3.gate import reset_to
    reset_to(work, rev)
    for f, text in changed.items():
        D._write(work, f, text)
    st = D._run_states(env, tests)
    chk = DC.check(derived, work)
    reset_to(work, rev)
    return {"failing_tests": sorted(k for k, v in st.items() if k != "__unparsed__" and v[0] != "passed")[:20],
            "unparsed": bool(st.get("__unparsed__")), "static_check_ok": chk["ok"], "static_problems": chk["problems"]}


def build(c: dict, work: str, env, rev: str) -> dict:
    from harness.experiments.r3.gate import reset_to
    reset_to(work, rev)
    known = set(env.test_files())
    tests = scored_tests(work, c, known, env)
    if not tests:
        return {"rejected": "no test file that uses it passes at the start"}
    qual = D._qual(c)
    # `ABC`, `Generic` and `Protocol` define no method a rename could stop overriding
    outside = [b for b in D._outside_bases(work, c["file"], qual) if b not in INERT_BASES] \
        if c.get("kind") == "method" else []
    if outside:
        return {"rejected": f"method of a class with an outside base (may override it): {outside}"}
    taken = lambda name: bool(D._git(work, "grep", "-l", "-w", name, "--").stdout.strip())
    new = D.new_name(c["leaf"], taken)
    if new is None:
        return {"rejected": "no free new name"}
    jedi = jedi_agreement(work, c)                  # the second reader: a language server's references
    if not jedi["agree"]:
        return {"rejected": f"PRISM's places and jedi's references differ: {jedi}"}
    changed = gold_sites(work, c, new)
    if changed is None:
        return {"rejected": "a place or the definition does not hold the name (or a property setter)"}
    derived = {"kind": "rename_sites", "file": c["file"], "qualname": qual, "old": c["leaf"], "new": new}
    details = {"new": new}
    gen_rel = os.path.join(os.path.dirname(tests[0]), f"test_phase3_rename_{c['leaf']}.py")
    gen = D.generated_test("rename", D.importable(work, c["file"]), qual, details).replace("test_phase2_", "test_phase3_")
    r = D.validate(changed, derived, details, tests, gen_rel, gen, work, env, rev)
    if "rejected" in r:
        return r
    pos = def_position(work, c)
    def_only = {c["file"]: _rename_at(D._read(work, c["file"]), [pos], c["leaf"], new)}
    probes = {"definition_only": _probe(work, env, rev, def_only, r["derived"], tests),
              "token_rename": _probe(work, env, rev, gold_tokens(work, c, new), r["derived"], tests)}
    if not probes["definition_only"]["failing_tests"] and not probes["definition_only"]["unparsed"]:
        return {"rejected": "renaming the definition alone fails no scored test (the places are not exercised)"}
    r["probes"] = probes
    return r


def prompt_for(c: dict, r: dict, turns: int) -> tuple[str, str]:
    qual = D._qual(c)
    new, leaf = r["details"]["new"], c["leaf"]
    display = f"the method `{qual}`"
    tests = ", ".join(f"`{t}`" for t in r["tests"])
    goal = (f"`{qual}` is renamed to `{new}`, every use of it uses the new name, and the uses of the name `{leaf}` "
            f"that belong to other objects are unchanged")
    check = (f"When you call done, the harness checks your work: {goal}, and the tests in {tests} that passed at the "
             f"start still pass (a test you delete is not counted). If the check fails, done is rejected, you are "
             f"told what is still wrong, and you continue. Run `run_tests` yourself before calling done. You have "
             f"{turns} turns in total; a rejected done uses one.")
    text = (f"Rename {display} in `{c['file']}` (it is defined at line {c['lines'][0]}) to `{new}`. Update every use "
            f"of this method across the repository so nothing refers to it by the old name: calls, references and "
            f"tests. Other functions, methods and attributes named `{leaf}` belong to other objects: leave them "
            f"unchanged. Do not keep an alias under the old name. Remaining tests must still pass.")
    return text + "\n\nWhen your changes are complete, call done.\n\n" + check, display


def ranked(scout: dict) -> list[dict]:
    out = [c for rep in scout.values() for c in rep["candidates"]]
    return sorted(out, key=lambda c: (c["grep_precision"] if c["grep_precision"] is not None else 1.0,
                                      -c["lookalikes"], c["repo"], c["qname"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("scout")
    p.add_argument("out")
    p.add_argument("--cache", required=True)
    p.add_argument("--scratch", required=True)
    p.add_argument("--want", type=int, default=20)
    p.add_argument("--per-repo", type=int, default=3)
    p.add_argument("--max-attempts", type=int, default=15, help="per repository")
    p.add_argument("--turns", type=int, default=28)
    p.add_argument("--repos", default=None, help="comma-separated, in this order")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("clones", nargs="+")
    a = p.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    scout = json.load(open(a.scout))
    clones = dict(x.split("=", 1) for x in a.clones)
    state = json.load(open(a.out)) if os.path.exists(a.out) else {"tasks": [], "attempts": []}
    from harness.experiments.r3.shared_tools import make_env
    for repo in (a.repos.split(",") if a.repos else sorted(scout)):
        if len(state["tasks"]) >= a.want or repo not in clones:
            continue
        if sum(1 for t in state["tasks"] if t["repo"] == repo) >= a.per_repo:
            continue
        rev = scout[repo]["rev"]
        work = os.path.join(a.scratch, f"hidden_{repo}")
        if not os.path.isdir(work):
            D._git(clones[repo], "worktree", "prune")
            subprocess.run(["git", "-C", clones[repo], "worktree", "add", "-q", "--detach", work, rev], check=True)
        env = make_env(clones[repo], rev, work, a.cache)
        try:
            env.setup()
        except Exception as exc:
            state["attempts"].append({"repo": repo, "setup_failed": str(exc)[:300]})
            json.dump(state, open(a.out, "w"), indent=1)
            continue
        tried = 0
        for c in ranked({repo: scout[repo]}):
            if len(state["tasks"]) >= a.want or tried >= a.max_attempts or \
                    sum(1 for t in state["tasks"] if t["repo"] == repo) >= a.per_repo:
                break
            if any(x.get("qname") == c["qname"] for x in state["attempts"]):
                continue
            tried += 1
            try:
                r = build(c, work, env, rev)
            except Exception as exc:
                r = {"rejected": f"{type(exc).__name__}: {str(exc)[:200]}"}
            state["attempts"].append({"repo": repo, "qname": c["qname"],
                                      **({"rejected": r["rejected"]} if "rejected" in r else {"ok": True})})
            if "rejected" not in r:
                prompt, display = prompt_for(c, r, a.turns)
                tid = f"p3-hidden-{len(state['tasks']) + 1:02d}"
                state["tasks"].append({
                    "id": tid, "family": "rename_hidden", "repo": repo, "language": "python", "commit": rev,
                    "parent": rev, "subject": f"hidden-dependency rename: {c['qname']}", "start_patch": None,
                    "after_index_patch": None, "start_files": [], "start_functions": [], "test_files": [],
                    "hidden_patch": r["hidden_patch"], "visible_tests": r["tests"],
                    "tests_run": r["tests"] + [r["generated_test"]], "fail_to_pass": r["fail_to_pass"],
                    "pass_to_pass": r["pass_to_pass"], "visible_start_pass": r["visible_start_pass"],
                    "gold_files": r["gold_files"], "gold_functions": r["gold_functions"], "prompt": prompt,
                    "anchor": f"{c['file']}::{D._qual(c)}", "anchor_qname": c["qname"], "target_display": display,
                    "target_line": c["lines"][0], "derived": r["derived"], "done_gate": True,
                    "explicit_tools": True, "restore_scored_tests": True,
                    "hidden": {k: c[k] for k in ("n_places", "files", "test_files", "lookalikes", "lookalike_files",
                                                 "word_files", "grep_precision", "same_name_defs")},
                    "places": c["places"], "probes": r["probes"],
                    "gold_patch_sha": hashlib.sha256(r["gold_patch"].encode()).hexdigest(),
                })
                D._write(os.path.dirname(os.path.abspath(a.out)), f"gold/{tid}.patch", r["gold_patch"])
            json.dump(state, open(a.out, "w"), indent=1)
            print(repo, c["qname"], r.get("rejected", "OK"), flush=True)
        if a.cleanup:
            D._git(clones[repo], "worktree", "remove", "--force", work)
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
