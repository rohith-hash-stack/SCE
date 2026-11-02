"""R3 repository pool check: post-cutoff commits and task candidates per repository (no model, no test runs).

    python -m harness.experiments.r3.pool_scan OUT.json NAME=CLONE ...

For every non-merge commit on the default branch dated on or after CUTOFF that touches 1-15 non-test source files:
- `changed_30_50`: it changes 30-50 existing functions (A3's size measure, reported as asked);
- **Family B candidate:** a function's signature (its first code line) changes, at least one other changed function
  calls it by name, and a test file changes too;
- **Family N candidate:** it adds exactly one non-test function of 6-60 lines whose body calls at least 2 functions
  defined in the repository at the parent, and a test file it changes mentions the new function's name.

These are candidates before R3's validity gate (the tests must fail at the start state and pass at the commit),
which needs the environments. A repository qualifies for a family with at least MIN_CANDIDATES candidates.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

CUTOFF = "2024-12-01"
MIN_CANDIDATES = 5
SOURCE = (".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".tsx")
TEST = re.compile(r"(^|/)(tests?|testing|__tests__|spec|test-d)(/|$)|(^|/)test_[^/]*$|_test\.py$|\.(test|spec|test-d)\.[mc]?[jt]sx?$|conftest\.py$")
CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")


def git(clone: str, *args: str) -> str:
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, errors="replace").stdout


def show(clone: str, rev: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "-C", clone, "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def norm(code: str) -> str:
    return "\n".join(ln.strip() for ln in code.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//")))


def repo_functions(clone: str, rev: str) -> set[str]:
    """Leaf names of the functions defined in the repository's non-test source at `rev` (by pattern)."""
    out = git(clone, "grep", "-h", "-o", "-E", r"(def|function)\s+[A-Za-z_$][A-Za-z0-9_$]*", rev, "--",
              *[f"*{e}" for e in SOURCE])
    return {ln.split()[-1] for ln in out.splitlines() if ln.strip()}


def scan(name: str, clone: str) -> dict:
    from harness.experiments.change_history.mine import functions
    head = git(clone, "rev-parse", "HEAD").strip()
    commits = git(clone, "rev-list", "--no-merges", f"--since={CUTOFF}T00:00:00", head).split()
    res = {"repo": name, "commits_since_cutoff": len(commits), "changed_30_50": 0, "family_b": [], "family_n": []}
    for c in commits:
        parent = git(clone, "rev-parse", f"{c}^").strip()
        if not parent or parent.startswith("fatal") or len(parent) != 40:
            continue
        files = git(clone, "diff", "--name-only", parent, c).split()
        src = [f for f in files if f.endswith(SOURCE) and not TEST.search(f)]
        tests = [f for f in files if TEST.search(f)]
        if not 1 <= len(src) <= 15:
            continue
        changed, added, sig_changed = [], [], []
        for f in src:
            old, new = show(clone, parent, f), show(clone, c, f)
            fo = functions(f, old) if old else {}
            fn = functions(f, new) if new else {}
            for q in fo:
                if q in fn and norm(fo[q]["code"]) != norm(fn[q]["code"]):
                    changed.append((f, q, fn[q]["code"]))
                    first = lambda code: next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
                    if first(fo[q]["code"]) != first(fn[q]["code"]):
                        sig_changed.append(q.rsplit(".", 1)[-1])
            added += [(f, q, fn[q]) for q in fn if q not in fo]
        if 30 <= len(changed) <= 50:
            res["changed_30_50"] += 1
        if tests and sig_changed:
            callers = [q for f, q, code in changed if any(re.search(rf"\b{re.escape(s)}\s*\(", code) and
                                                          q.rsplit(".", 1)[-1] != s for s in sig_changed)]
            if callers:
                res["family_b"].append({"commit": c, "signature_changed": sorted(set(sig_changed)), "callers": callers[:10]})
        top = [(f, q, d) for f, q, d in added if 6 <= d["end"] - d["start"] + 1 <= 60 and "." not in q.split(".")[-1]]
        if tests and len(top) == 1:
            f, q, d = top[0]
            leaf = q.rsplit(".", 1)[-1]
            known = repo_functions(clone, parent)
            used = {m.group(1) for m in CALL.finditer(d["code"])} & known - {leaf}
            mentioned = any(leaf in (show(clone, c, t) or b"").decode("utf8", "replace") for t in tests)
            if len(used) >= 2 and mentioned:
                res["family_n"].append({"commit": c, "function": q, "file": f, "helpers": sorted(used)[:10]})
    res["b_candidates"], res["n_candidates"] = len(res["family_b"]), len(res["family_n"])
    res["qualifies_b"] = res["b_candidates"] >= MIN_CANDIDATES
    res["qualifies_n"] = res["n_candidates"] >= MIN_CANDIDATES
    return res


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    out, pairs = args[0], [a.split("=", 1) for a in args[1:]]
    results = []
    for name, clone in pairs:
        results.append(scan(name, clone))
        r = results[-1]
        print(f"{name:16} since-cutoff {r['commits_since_cutoff']:5}  30-50 {r['changed_30_50']:3}  "
              f"B {r['b_candidates']:3}{'' if r['qualifies_b'] else ' SHORT'}  N {r['n_candidates']:3}{'' if r['qualifies_n'] else ' SHORT'}",
              flush=True)
    with open(out, "w") as fh:
        json.dump({"cutoff": CUTOFF, "min_candidates": MIN_CANDIDATES, "repos": results}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
