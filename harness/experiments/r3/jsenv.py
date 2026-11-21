"""R3 JavaScript/TypeScript environment builder (pre-registration "Order and gates", step 4; the one-week rule).

    python -m harness.experiments.r3.jsenv detect CLONE [REV]          # what the builder would do, as JSON (no install)

Per repository and revision it decides, from the files at that revision only:
- the package manager, from the lockfile (no lockfile: npm, resolved with `npm install --before=<commit date>`): pnpm-lock.yaml -> pnpm, yarn.lock -> yarn (v1 or berry, from
  .yarnrc.yml / packageManager), package-lock.json or npm-shrinkwrap.json -> npm; `packageManager` in package.json
  wins when present (the version corepack would use);
- the Node version: .nvmrc, .node-version, then `volta.node`, then `engines.node`; default the installed major;
- the build step: a `build` script, run only if the tests import the built output (decided when installing);
- the test runner: from the `test` script first, then the dependencies (vitest, jest, mocha, node:test, tap, ava);
- workspaces (a monorepo): pnpm-workspace.yaml or `workspaces` in package.json.

Installing, selected-test runs with machine-readable output, flake detection and caching are the next steps of this
module (see BUILD_PLAN.md); `detect` comes first because every one of them depends on it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

RUNNERS = ("vitest", "jest", "mocha", "tap", "ava")


def _show(clone: str, rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", clone, "show", f"{rev}:{path}"], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def _files(clone: str, rev: str) -> set[str]:
    out = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", rev], capture_output=True, text=True).stdout
    return set(out.split())


def _runner_of(script: str) -> str | None:
    s = script or ""
    if re.search(r"\bnode\b[^&|;]*--test\b", s):
        return "node:test"
    for r in RUNNERS:
        if re.search(rf"(^|[\s/&|;(]){r}(\b|$)", s):
            return r
    return None


_CHAIN = re.compile(r"\b(?:npm run|pnpm(?: run)?|yarn(?: run)?|nr)\s+([\w:.-]+)")


def _follow(scripts: dict, name: str, seen=None) -> tuple[str | None, str | None]:
    """The runner a script reaches, following `npm run x`, `pnpm x`, `yarn x` and `nr x` into other scripts; Bun
    scripts are skipped (Bun is not part of the environment)."""
    seen = seen or set()
    body = scripts.get(name)
    if body is None or name in seen or "bun " in body:
        return None, None
    seen.add(name)
    for part in re.split(r"&&|\|\||;", body):
        r = _runner_of(part)
        if r:
            return r, f"scripts.{name}"
        m = _CHAIN.search(part)
        if m and m.group(1) in scripts:
            r, where = _follow(scripts, m.group(1), seen)
            if r:
                return r, where
    return None, None


def detect(clone: str, rev: str = "HEAD") -> dict:
    files = _files(clone, rev)
    pkg_text = _show(clone, rev, "package.json")
    if pkg_text is None:
        return {"ok": False, "reason": "no package.json at the root"}
    try:
        pkg = json.loads(pkg_text)
    except ValueError:
        return {"ok": False, "reason": "package.json does not parse"}
    scripts = pkg.get("scripts") or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}

    pm, pm_version = None, None
    if isinstance(pkg.get("packageManager"), str):
        pm, _, pm_version = pkg["packageManager"].partition("@")
        pm_version = pm_version.split("+")[0] or None
    if pm is None:
        if "pnpm-lock.yaml" in files:
            pm = "pnpm"
        elif "yarn.lock" in files:
            pm = "yarn"
        elif files & {"package-lock.json", "npm-shrinkwrap.json"}:
            pm = "npm"
    lockfile = next((f for f in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json", "npm-shrinkwrap.json", "bun.lockb")
                     if f in files), None)
    yarn_berry = pm == "yarn" and (".yarnrc.yml" in files or (pm_version or "1").split(".")[0] not in ("1", ""))

    node, node_from = None, None
    for f in (".nvmrc", ".node-version"):
        if f in files:
            node, node_from = (_show(clone, rev, f) or "").strip().lstrip("v") or None, f
            break
    if node is None and isinstance(pkg.get("volta"), dict) and pkg["volta"].get("node"):
        node, node_from = str(pkg["volta"]["node"]), "volta"
    if node is None and isinstance(pkg.get("engines"), dict) and pkg["engines"].get("node"):
        node, node_from = str(pkg["engines"]["node"]), "engines"

    test_script = scripts.get("test", "")
    runner, runner_from = _follow(scripts, "test")
    if runner is None:  # no runner reachable from `test`: a test-like script that does not need Bun
        for name, body in scripts.items():
            if name.startswith("test") and "bun " not in body and _runner_of(body):
                runner, runner_from = _runner_of(body), f"scripts.{name}"
                break
    if runner is None:
        runner, runner_from = next(((r, "dependencies") for r in RUNNERS if r in deps), (None, None))
    no_lock = lockfile is None
    if pm is None and no_lock:  # no lockfile: npm, resolved as of the commit date (`npm install --before`)
        pm = "npm"

    workspaces = "pnpm-workspace.yaml" in files or bool(pkg.get("workspaces"))
    return {"ok": pm is not None and runner is not None, "package_manager": pm, "pm_version": pm_version,
            "yarn_berry": yarn_berry, "lockfile": lockfile, "node": node, "node_from": node_from,
            "build_script": "build" in scripts, "test_script": test_script, "runner": runner,
            "runner_from": runner_from, "workspaces": workspaces, "no_lockfile": no_lock,
            "reason": None if pm and runner else ("no lockfile or packageManager" if not pm else "no test runner found")}


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["detect"] and len(args) in (2, 3):
        print(json.dumps(detect(args[1], *(args[2:3] or ["HEAD"])), indent=1))
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
