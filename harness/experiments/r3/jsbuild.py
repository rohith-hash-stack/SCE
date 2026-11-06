"""R3's JavaScript/TypeScript environment builder (amendment R3-5, Revision 4: runs until the self-check passes on all
7 repositories, or a repository fails twice with a structural cause).

    python -m harness.experiments.r3.jsbuild selfcheck CLONE REV [REV ...] --cache DIR [--out OUT.json]

Per repository and revision (a fresh git worktree; the clone is never modified):
1. **detect** (`jsenv.detect`): package manager, Node version spec, test runner, build script, workspaces.
2. **Node:** the spec (.nvmrc / .node-version / volta / engines) resolved against nodejs.org's release index
   (`lts/*` and range forms included). The system Node is used when it satisfies the spec. Otherwise the newest
   satisfying release is downloaded once into the cache.
3. **Package manager:** a direct binary (`npm install pnpm@X` into a cached prefix), not corepack. X is
   `packageManager`'s version, else the major the lockfile format implies.
4. **Install:** `pnpm install --frozen-lockfile`, `npm ci`, `yarn install --frozen-lockfile`. With no lockfile:
   `npm install --before=<commit date>`, which resolves dependencies as of the revision.
5. **Build:** in setup, never inside the test call. Run when the test script chain builds first, or when a test run
   fails to resolve a workspace package's built output.
6. **Tests:** test files found by the runner's conventions. A named file is run with machine-readable output: vitest
   `--reporter=json`, jest `--json`, mocha `--reporter json`, node:test `--test-reporter=tap`, ava `--tap`.

**Self-check per revision:**
- it installs;
- it finds test files;
- a named test file runs and reports at least one passing test and no failures;
- the same file run 3 times gives the same result (the flake check).

**Failure classes** (Revision 4): only `structural` counts toward the stop rule.
- `structural`: no Node release satisfies the spec; no runner; the install or the build fails for a reason other
  than the network or the registry.
- `missing_package`: a registry or network failure, or a package that cannot be fetched.
- `flake`: a result that varies over the 3 runs.
- `no_passing_test`: none of the candidate files passes at this revision.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

from harness.experiments.r3.jsenv import detect

NODE_INDEX = "https://nodejs.org/dist/index.json"
TEST_FILE = re.compile(r"\.(test|spec)\.(m|c)?[jt]sx?$")
SKIP_DIRS = re.compile(r"(^|/)(node_modules|[^/]*fixtures?[^/]*|e2e|playwright|browser|examples?|docs?|bench(mark)?s?)/")
INSTALL_TIMEOUT, BUILD_TIMEOUT, TEST_TIMEOUT = 1800, 1800, 600
MAX_CANDIDATES = 6


# ------------------------------------------------------------------ semver (enough for engines/.nvmrc specs)
def _ver(s: str) -> tuple[int, int, int]:
    parts = [int(x) for x in re.findall(r"\d+", s)[:3]]
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


def _cmp_ok(v: tuple, comp: str) -> bool:
    comp = comp.strip()
    if not comp or comp in ("*", "x", "latest"):
        return True
    m = re.match(r"^(\^|~|>=|<=|>|<|=)?\s*v?(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?", comp)
    if not m:
        return False
    op, a, b, c = m.groups()
    nums = [int(x) for x in (a, b, c) if x not in (None, "x", "*")]
    t = tuple(nums + [0] * (3 - len(nums)))
    if op in (None, "="):                      # "24", "18.19.0", "22.x": match the given parts
        return list(v[:len(nums)]) == nums
    if op == "^":
        upper = (t[0] + 1, 0, 0) if t[0] > 0 else ((0, t[1] + 1, 0) if t[1] > 0 else (0, 0, t[2] + 1))
        return t <= v < upper
    if op == "~":
        upper = (t[0], t[1] + 1, 0) if len(nums) > 1 else (t[0] + 1, 0, 0)
        return t <= v < upper
    return {">=": v >= t, ">": v > t, "<=": v <= t, "<": v < t}[op]


def satisfies(version: str, spec: str | None) -> bool:
    if not spec:
        return True
    v = _ver(version)
    for alt in spec.split("||"):
        alt = re.sub(r"(>=|<=|>|<|\^|~|=)\s+", r"\1", alt.strip())
        if " - " in alt:
            lo, hi = alt.split(" - ", 1)
            if _ver(lo) <= v <= _ver(hi):
                return True
            continue
        if all(_cmp_ok(v, c) for c in alt.split()):
            return True
    return False


# ------------------------------------------------------------------ Node
def _node_index(cache: str) -> list[dict]:
    path = os.path.join(cache, "node-index.json")
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 86400:
        with urllib.request.urlopen(NODE_INDEX, timeout=60) as r:
            data = r.read()
        with open(path, "wb") as fh:
            fh.write(data)
    with open(path) as fh:
        return json.load(fh)


def resolve_node(spec: str | None, cache: str) -> tuple[str | None, str]:
    """(version without 'v', why). System Node first when it satisfies the spec."""
    system = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    s = (spec or "").strip()
    if s.startswith("lts/"):
        index = _node_index(cache)
        name = s.split("/", 1)[1].lower()
        lts = [x for x in index if x.get("lts") and (name in ("*", "latest") or str(x["lts"]).lower() == name)]
        return (lts[0]["version"].lstrip("v"), f"{s} -> latest matching LTS") if lts else (None, f"no LTS named {s}")
    if system and satisfies(system, s or None):
        return system, "system Node satisfies the spec"
    index = _node_index(cache)
    ok = [x["version"].lstrip("v") for x in index if satisfies(x["version"].lstrip("v"), s)]
    lts_ok = [v for v in ok if any(x["version"].lstrip("v") == v and x.get("lts") for x in index)]
    pick = (lts_ok or ok)[:1]
    return (pick[0], "newest satisfying release" + (" (LTS)" if lts_ok else "")) if pick else (None, f"no release satisfies {s!r}")


def ensure_node(version: str, cache: str) -> str:
    """The bin directory of Node `version` (system Node if it is that version)."""
    system = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    if system == version:
        return os.path.dirname(shutil.which("node"))
    root = os.path.join(cache, "node", version)
    bin_dir = os.path.join(root, f"node-v{version}-linux-x64", "bin")
    if not os.path.exists(os.path.join(bin_dir, "node")):
        os.makedirs(root, exist_ok=True)
        url = f"https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.xz"
        tmp = os.path.join(root, "node.tar.xz")
        urllib.request.urlretrieve(url, tmp)
        with tarfile.open(tmp) as tar:
            tar.extractall(root)
        os.remove(tmp)
    return bin_dir


# ------------------------------------------------------------------ package manager
def pnpm_version_for(d: dict, lockfile_text: str | None) -> str:
    """`packageManager`'s pnpm version; else the version the lockfile records itself
    (`importers['.'].packageManagerDependencies.pnpm.version`, written by pnpm 10.x+); else the major the lockfile
    format implies (format 9.0 is written by pnpm 9 to 12, so 9, with newer majors tried on a broken-lockfile
    error)."""
    if d.get("package_manager") == "pnpm" and d.get("pm_version"):
        return d["pm_version"]
    m = re.search(r"packageManagerDependencies:\s*\n\s+pnpm:\s*\n(?:\s+specifier:[^\n]*\n)?\s+version:\s*'?([\d.]+)",
                  lockfile_text or "")
    if m:
        return m.group(1)
    m = re.search(r"lockfileVersion:\s*'?(\d+)", lockfile_text or "")
    major = int(m.group(1)) if m else 9
    return {5: "7", 6: "8", 9: "9"}.get(major, "10")


#: Newer pnpm majors to try, in order, when the chosen one rejects a format-9.0 lockfile it cannot read.
PNPM_FALLBACK = ("10", "11", "12")


def ensure_pm(name: str, version: str, node_bin: str, cache: str) -> str:
    """The bin directory holding `name`@`version`, installed directly from npm into a cached prefix."""
    prefix = os.path.join(cache, "pm", f"{name}-{version}")
    bin_dir = os.path.join(prefix, "node_modules", ".bin")
    if not os.path.exists(os.path.join(bin_dir, name)):
        os.makedirs(prefix, exist_ok=True)
        env = dict(os.environ, PATH=node_bin + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["npm", "install", "--no-save", "--prefix", prefix, f"{name}@{version}"], env=env,
                           capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"installing {name}@{version} failed: {r.stderr[-800:]}")
    return bin_dir


# ------------------------------------------------------------------ setup and tests
NETWORK = re.compile(r"ETIMEDOUT|ECONNRESET|ENOTFOUND|EAI_AGAIN|ERR_PNPM_FETCH|ERR_PNPM_META_FETCH|socket hang up|"
                     r"getaddrinfo|network|403 Forbidden|E404|404 Not Found|ERR_PNPM_NO_MATCHING_VERSION|"
                     r"ERR_SOCKET|tarball", re.I)


#: NODE_OPTIONS flags Node's worker threads reject (ERR_WORKER_INVALID_EXEC_ARGV): test runners start workers, so
#: these are dropped from the child environment. The proxy's CA stays trusted through NODE_EXTRA_CA_CERTS.
_WORKER_UNSAFE = ("--use-openssl-ca", "--use-bundled-ca", "--openssl-config")


def _worker_safe_node_options() -> str:
    opts = os.environ.get("NODE_OPTIONS", "").split()
    return " ".join(o for o in opts if not o.startswith(_WORKER_UNSAFE))


NATIVE_BUILD = re.compile(r"gyp ERR!|node-gyp|prebuild-install.*(fail|err)|make: \*\*\*", re.I)


class SetupError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _run(cmd, cwd, env, timeout):
    t = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or ""), time.time() - t
    except subprocess.TimeoutExpired as exc:
        return 124, f"timeout after {timeout}s: {(exc.stdout or b'')[-500:]!r}", time.time() - t


class Env:
    """One revision's installed environment in a worktree."""

    def __init__(self, clone: str, rev: str, work: str, cache: str):
        self.clone, self.rev, self.work, self.cache = clone, rev, work, cache
        self.log: list[dict] = []
        self.d = detect(clone, rev)
        self.built = False

    def _step(self, name, rc, out, secs):
        self.log.append({"step": name, "rc": rc, "seconds": round(secs, 1), "tail": out[-1500:]})

    def setup(self) -> None:
        d = self.d
        if not d.get("package_manager"):
            raise SetupError("structural", "no package manager could be determined")
        if not d.get("runner"):
            raise SetupError("structural", "no test runner found")
        version, why = resolve_node(d.get("node"), self.cache)
        if version is None:
            raise SetupError("structural", f"Node: {why}")
        self.node_version, self.node_why = version, why
        self.pm_version = None
        node_bin = ensure_node(version, self.cache)
        self.env = dict(os.environ, PATH=node_bin + os.pathsep + os.environ["PATH"], CI="1", COREPACK_ENABLE_STRICT="0",
                        COREPACK_ENABLE_AUTO_PIN="0", NODE_OPTIONS=_worker_safe_node_options(),
                        npm_config_store_dir=os.path.join(self.cache, "pnpm-store"),
                        PNPM_HOME=os.path.join(self.cache, "pnpm-home"), FORCE_COLOR="0", NO_COLOR="1")
        pm = d["package_manager"]
        if pm == "pnpm":
            lock = os.path.join(self.work, "pnpm-lock.yaml")
            pv = pnpm_version_for(d, open(lock).read() if os.path.exists(lock) else None)
            try:
                self.env["PATH"] = ensure_pm("pnpm", pv, node_bin, self.cache) + os.pathsep + self.env["PATH"]
            except RuntimeError as exc:
                raise SetupError("missing_package", str(exc)) from exc
            self.pm_version = pv
            cmd = ["pnpm", "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"]
            base_path = self.env["PATH"].split(os.pathsep, 1)[1]
            rc, out, secs = _run(cmd, self.work, self.env, INSTALL_TIMEOUT)
            self._step(f"install (pnpm {pv})", rc, out, secs)
            for fallback in (PNPM_FALLBACK if rc != 0 and "ERR_PNPM_BROKEN_LOCKFILE" in out else ()):
                if _ver(fallback) <= _ver(pv):
                    continue
                try:
                    self.env["PATH"] = ensure_pm("pnpm", fallback, node_bin, self.cache) + os.pathsep + base_path
                except RuntimeError:
                    continue
                self.pm_version = fallback
                rc, out, secs = _run(cmd, self.work, self.env, INSTALL_TIMEOUT)
                self._step(f"install (pnpm {fallback}, after a broken-lockfile error)", rc, out, secs)
                if rc == 0 or "ERR_PNPM_BROKEN_LOCKFILE" not in out:
                    break
            self._installed = (rc, out)
        elif pm == "yarn":
            self.pm_version = d.get("pm_version") or "1"
            cmd = ["yarn", "install", "--immutable"] if d.get("yarn_berry") else ["yarn", "install", "--frozen-lockfile"]
        else:
            self.pm_version = subprocess.run(["npm", "--version"], env=self.env, capture_output=True, text=True).stdout.strip()
            if d.get("no_lockfile"):
                date = subprocess.run(["git", "-C", self.clone, "log", "-1", "--format=%cI", self.rev], capture_output=True,
                                      text=True).stdout.strip()
                cmd = ["npm", "install", f"--before={date}", "--no-audit", "--no-fund"]
            else:
                cmd = ["npm", "ci", "--no-audit", "--no-fund"]
        if getattr(self, "_installed", None) is not None:
            rc, out = self._installed
        else:
            rc, out, secs = _run(cmd, self.work, self.env, INSTALL_TIMEOUT)
            self._step("install", rc, out, secs)
        if rc != 0 and NATIVE_BUILD.search(out) and not re.fullmatch(r"v?\d+\.\d+\.\d+", (d.get("node") or "").strip()):
            rc, out = self._retry_older_node(cmd, rc, out)
        if rc != 0:
            raise SetupError("missing_package" if NETWORK.search(out) else "structural", f"install failed: {out[-600:]}")
        if self._needs_build_first():
            self.build()

    def _retry_older_node(self, cmd, rc, out):
        """A native module that does not compile against the chosen Node (no prebuilt binary for it): retry the install
        with the newest satisfying release of each older LTS major, newest first. The lockfile is unchanged; only the
        Node version moves back toward the revision's era."""
        index = _node_index(self.cache)
        chosen = _ver(self.node_version)[0]
        majors = sorted({_ver(x["version"])[0] for x in index if x.get("lts") and _ver(x["version"])[0] < chosen},
                        reverse=True)
        for major in majors[:3]:
            cands = [x["version"].lstrip("v") for x in index if x.get("lts") and _ver(x["version"])[0] == major
                     and satisfies(x["version"].lstrip("v"), self.d.get("node"))]
            if not cands:
                continue
            node_bin = ensure_node(cands[0], self.cache)
            pm_bins = [p for p in self.env["PATH"].split(os.pathsep) if os.sep + "pm" + os.sep in p]
            self.env["PATH"] = os.pathsep.join(pm_bins + [node_bin, os.environ["PATH"]])
            shutil.rmtree(os.path.join(self.work, "node_modules"), ignore_errors=True)
            rc, out, secs = _run(cmd, self.work, self.env, INSTALL_TIMEOUT)
            self._step(f"install (Node {cands[0]}, after a native-module build failure)", rc, out, secs)
            if rc == 0:
                self.node_version, self.node_why = cands[0], f"{self.node_why}; a native module did not build on " \
                    f"Node {chosen}, so the newest Node {major} LTS was used"
                return rc, out
        return rc, out

    def _needs_build_first(self) -> bool:
        scripts = json.load(open(os.path.join(self.work, "package.json"))).get("scripts") or {}
        chain = scripts.get("test", "")
        return bool(re.search(r"\b(?:npm run|pnpm(?: run)?|yarn|nr)\s+build\b", chain)) and "build" in scripts

    def build(self) -> None:
        if self.built:
            return
        pm = self.d["package_manager"]
        rc, out, secs = _run([pm, "run", "build"], self.work, self.env, BUILD_TIMEOUT)
        self._step("build", rc, out, secs)
        if rc != 0:
            raise SetupError("missing_package" if NETWORK.search(out) else "structural", f"build failed: {out[-600:]}")
        self.built = True

    def test_files(self) -> list[str]:
        files = subprocess.run(["git", "-C", self.work, "ls-files"], capture_output=True, text=True).stdout.split()
        runner = self.d["runner"]
        if runner in ("node:test", "ava"):
            out = [f for f in files if f.startswith("test/") and f.endswith((".js", ".mjs", ".ts"))
                   and not re.search(r"(^|/)(fixtures|helpers)/", f)]
        else:
            out = [f for f in files if TEST_FILE.search(f)]
        return sorted(f for f in out if not SKIP_DIRS.search(f) and not f.endswith(".d.ts"))

    CONFIG_NAMES = re.compile(r"^(vitest\.(config|workspace)|vite\.config|jest\.config)\.(m|c)?[jt]s$")

    def _config_dir(self, rel: str) -> str:
        """The nearest directory above `rel` (within the worktree) with its own vitest/vite/jest config, where the
        runner must start for that file's settings to apply (monorepo test folders); the root otherwise."""
        d = os.path.dirname(rel)
        while d:
            try:
                if any(self.CONFIG_NAMES.match(n) for n in os.listdir(os.path.join(self.work, d))):
                    return d
            except OSError:
                pass
            d = os.path.dirname(d)
        return ""

    def run_test(self, rel: str) -> dict:
        """Run one test file; `{passed, failed, skipped, rc, seconds, parsed}`."""
        runner, out_json = self.d["runner"], os.path.join(self.work, ".r3-test-result.json")
        cwd = os.path.join(self.work, self._config_dir(rel)) if runner in ("vitest", "jest") else self.work
        rel_run = os.path.relpath(os.path.join(self.work, rel), cwd)
        if os.path.exists(out_json):
            os.remove(out_json)
        if runner == "vitest":
            cmd = [self.d["package_manager"], "exec", "vitest", "run", rel_run, "--reporter=json",
                   f"--outputFile={out_json}", "--no-coverage"] if self.d["package_manager"] == "pnpm" else \
                  ["npx", "--no-install", "vitest", "run", rel_run, "--reporter=json", f"--outputFile={out_json}"]
        elif runner == "jest":
            cmd = ["npx", "--no-install", "jest", rel_run, "--json", f"--outputFile={out_json}"]
        elif runner == "mocha":
            cmd = ["npx", "--no-install", "mocha", rel, "--reporter", "json"]
        elif runner == "ava":
            cmd = ["npx", "--no-install", "ava", rel, "--tap"]
        else:
            cmd = ["node", "--test", "--test-reporter=tap", rel]
        rc, out, secs = _run(cmd, cwd, self.env, TEST_TIMEOUT)
        res = {"tests": {}, "file": rel, "cwd": os.path.relpath(cwd, self.work), "rc": rc, "seconds": round(secs, 1), "passed": 0, "failed": 0, "skipped": 0, "parsed": False}
        messages = []
        if runner in ("vitest", "jest") and os.path.exists(out_json):
            data = json.load(open(out_json))
            res.update(passed=data.get("numPassedTests", 0), failed=data.get("numFailedTests", 0),
                       skipped=data.get("numPendingTests", 0) + data.get("numTodoTests", 0), parsed=True)
            messages = [tr.get("message") or "" for tr in data.get("testResults", [])]
            for tr in data.get("testResults", []):           # per-test outcomes (vitest and jest share this shape)
                for a in tr.get("assertionResults", []):
                    st = a.get("status")
                    res["tests"][a.get("fullName") or a.get("title", "")] = \
                        "passed" if st == "passed" else "failed" if st == "failed" else "skipped"
        elif runner == "mocha":
            try:
                st = json.loads(out[out.index("{"):]).get("stats", {})
                res.update(passed=st.get("passes", 0), failed=st.get("failures", 0), skipped=st.get("pending", 0), parsed=True)
            except ValueError:
                pass
        elif runner in ("node:test", "ava"):
            for m in re.finditer(r"^\s*(not ok|ok) \d+ - (.+?)(?:\s+#\s*(SKIP|TODO).*)?$", out, re.M):
                res["tests"][m.group(2).strip()] = "skipped" if m.group(3) else \
                    ("failed" if m.group(1) == "not ok" else "passed")
            p = re.search(r"^# pass (\d+)", out, re.M)
            f = re.search(r"^# fail (\d+)", out, re.M)
            if p or f:
                res.update(passed=int(p.group(1)) if p else 0, failed=int(f.group(1)) if f else 0, parsed=True)
            else:   # ava's TAP: ok / not ok lines
                oks, nots = len(re.findall(r"^ok \d+", out, re.M)), len(re.findall(r"^not ok \d+", out, re.M))
                if oks or nots:
                    res.update(passed=oks, failed=nots, parsed=True)
        res["tail"] = out[-1200:]
        res["text"] = out + "\n" + "\n".join(messages)     # the whole output and the report's errors, for diagnosis
        return res


UNRESOLVED_BUILD = re.compile(r"Failed to resolve (entry|import)|Cannot find module .*?(dist|lib|build)/|"
                              r"ERR_MODULE_NOT_FOUND|Does the file exist|Failed to load url|"
                              r"Could not resolve '\.{0,2}/?(dist|lib|build)/|UNRESOLVED_IMPORT|failed to load config", re.I)


def self_check_revision(clone: str, rev: str, cache: str, scratch: str) -> dict:
    work = os.path.join(scratch, f"{os.path.basename(clone)}-{rev[:8]}")
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(["git", "-C", clone, "worktree", "prune"], capture_output=True)
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, rev], check=True, capture_output=True)
    env = Env(clone, rev, work, cache)
    rec = {"rev": rev, "detect": {k: env.d.get(k) for k in ("package_manager", "pm_version", "lockfile", "node", "runner",
                                                              "runner_from", "workspaces", "no_lockfile")}}
    t0 = time.time()
    try:
        try:
            env.setup()
        finally:
            rec.update(node=getattr(env, "node_version", None), node_why=getattr(env, "node_why", None),
                       pm_version=getattr(env, "pm_version", None))
        files = env.test_files()
        rec["test_files"] = len(files)
        if not files:
            raise SetupError("structural", "no test files found")
        sizes = sorted(files, key=lambda f: (os.path.getsize(os.path.join(work, f)), f))
        tried = []
        for f in sizes[:MAX_CANDIDATES]:
            r = env.run_test(f)
            if not env.built and r["passed"] == 0 and UNRESOLVED_BUILD.search(r["text"]):
                env.build()
                rec["built_after_resolve_error"] = True
                r = env.run_test(f)
            tried.append({k: r[k] for k in ("file", "cwd", "rc", "passed", "failed", "skipped", "parsed", "seconds")})
            if r["parsed"] and r["passed"] >= 1 and r["failed"] == 0:
                runs = [env.run_test(f) for _ in range(2)] + [r]
                same = len({(x["passed"], x["failed"]) for x in runs}) == 1
                rec.update(named_test=f, flake_runs=[(x["passed"], x["failed"], x["seconds"]) for x in runs],
                           flake_free=same)
                break
        rec["tried"] = tried
        if "named_test" not in rec:
            last = tried[-1] if tried else {}
            rec["status"], rec["kind"] = "fail", ("no_passing_test" if any(t["parsed"] for t in tried) else "structural")
            rec["reason"] = "no candidate test file passed" if rec["kind"] == "no_passing_test" else \
                f"no test run produced machine-readable results (last rc {last.get('rc')})"
            rec["last_tail"] = r["tail"][-800:] if tried else ""
        elif not rec["flake_free"]:
            rec["status"], rec["kind"], rec["reason"] = "fail", "flake", "the named test varied over 3 runs"
        else:
            rec["status"] = "pass"
    except SetupError as exc:
        rec.update(status="fail", kind=exc.kind, reason=str(exc)[:900])
    except Exception as exc:  # recorded, classified as structural only if it is not a network error
        msg = f"{type(exc).__name__}: {exc}"
        rec.update(status="fail", kind="missing_package" if NETWORK.search(msg) else "structural", reason=msg[:900])
    rec["built"] = env.built
    rec["seconds"] = round(time.time() - t0, 1)
    rec["log"] = env.log
    subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
    shutil.rmtree(work, ignore_errors=True)
    return rec


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("selfcheck")
    s.add_argument("clone")
    s.add_argument("revs", nargs="+")
    s.add_argument("--cache", required=True)
    s.add_argument("--scratch", required=True)
    s.add_argument("--out")
    a = p.parse_args(argv)
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(a.scratch, exist_ok=True)
    res = {"repo": os.path.basename(a.clone), "revisions": [self_check_revision(os.path.abspath(a.clone), r, a.cache,
                                                                               a.scratch) for r in a.revs]}
    res["pass"] = all(r["status"] == "pass" for r in res["revisions"])
    text = json.dumps(res, indent=1)
    if a.out:
        open(a.out, "w").write(text + "\n")
    for r in res["revisions"]:
        print(f"{res['repo']} {r['rev'][:8]}: {r['status']} {r.get('kind', '')} node={r.get('node')} "
              f"pm={r.get('pm_version')} files={r.get('test_files')} named={r.get('named_test')} built={r['built']} "
              f"{r.get('reason', '')[:200]}", flush=True)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
