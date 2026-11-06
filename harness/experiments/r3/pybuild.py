"""R3's Python environment builder: the same interface as `jsbuild.Env`, for R3's Python repositories.

    python -m harness.experiments.r3.pybuild selfcheck CLONE REV [REV ...] --cache DIR --scratch DIR [--out OUT.json]

Per repository and revision (a fresh git worktree; the clone is never modified):
1. **Python:** the newest of 3.12, 3.11, 3.13, 3.10 (in that order of preference) that satisfies the revision's
   `requires-python` (pyproject, then setup.cfg). It falls back to the next satisfying one when installing fails.
   uv provides it.
2. **Install,** the first strategy that applies, all into one uv virtualenv per revision:
   - `uv.lock`: `uv sync --frozen --all-groups --all-extras` (without `--all-extras` if that fails);
   - otherwise:
     - `uv pip install -e .`;
     - the PEP 735 dependency groups for tests (`test`, `tests`, `testing`, `dev`) and the extras of the same names;
     - the requirement files for tests (`requirements/test*.txt`, `requirements-dev.txt`, `tests/requirements*.txt`,
       `requirements.txt`, ...);
     - tox's `[testenv] deps`;
     - pytest.

     Requirement files and tox deps are best effort: a pin that no longer installs does not stop the build.
   - with no lockfile, every dependency is resolved as of the revision's commit date (`uv --exclude-newer`);
   - Poetry's test and dev groups (`[tool.poetry.group.*]`) are installed too;
   - C extensions are built; if that install fails, it is redone without them (`*_NO_EXTENSIONS=1`). Git submodules
     are initialised.
3. **Tests:** pytest on a named file (and optional test ids), with `--junitxml`, giving a per-test outcome.

**Self-check per revision:**
- it installs;
- it finds test files (`test_*.py` / `*_test.py` under the test directories);
- a named test file passes, with at least one test and no failures;
- 3 runs give the same result.

The failure classes are those of `jsbuild`: `structural`, `missing_package`, `flake`, `no_passing_test`.
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET

from harness.experiments.r3.jsbuild import NETWORK, SetupError, _run

PY_PREFERENCE = ("3.12", "3.11", "3.13", "3.10")
TEST_GROUPS = ("test", "tests", "testing", "dev")
REQ_FILES = ("requirements/test.txt", "requirements/tests.txt", "requirements/test-requirements.txt",
             "requirements/dev.txt", "requirements/ci.txt", "requirements-dev.txt", "requirements_dev.txt",
             "requirements-test.txt", "test-requirements.txt", "tests/requirements.txt", "requirements.txt")
NO_EXT = {"AIOHTTP_NO_EXTENSIONS": "1", "MULTIDICT_NO_EXTENSIONS": "1", "YARL_NO_EXTENSIONS": "1",
          "FROZENLIST_NO_EXTENSIONS": "1", "PROPCACHE_NO_EXTENSIONS": "1", "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"}
INSTALL_TIMEOUT, TEST_TIMEOUT = 1800, 600
TEST_PATH = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$")
SKIP = re.compile(r"(^|/)([^/]*fixtures?[^/]*|examples?|docs?|bench(mark)?s?|node_modules)/")
MAX_CANDIDATES = 6


def _spec_ok(version: str, spec: str | None) -> bool:
    if not spec:
        return True
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    try:
        return SpecifierSet(spec.replace(" ", "")).contains(version + ".0", prereleases=True)
    except InvalidSpecifier:
        return True


class PyEnv:
    """One revision's Python environment in a worktree (the same interface as `jsbuild.Env`)."""

    def __init__(self, clone: str, rev: str, work: str, cache: str):
        self.clone, self.rev, self.work, self.cache = clone, rev, work, cache
        self.log: list[dict] = []
        self.built = False
        self.d = {"runner": "pytest", "package_manager": "uv"}
        self.py = None

    def _step(self, name, rc, out, secs):
        self.log.append({"step": name, "rc": rc, "seconds": round(secs, 1), "tail": out[-1500:]})

    def _pyproject(self) -> dict:
        try:
            with open(os.path.join(self.work, "pyproject.toml"), "rb") as fh:
                return tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def _requires_python(self, pp: dict) -> str | None:
        rp = (pp.get("project") or {}).get("requires-python")
        if rp:
            return rp
        cfg = configparser.ConfigParser()
        try:
            cfg.read(os.path.join(self.work, "setup.cfg"))
            return cfg.get("options", "python_requires", fallback=None)
        except configparser.Error:
            return None

    def setup(self) -> None:
        pp = self._pyproject()
        spec = self._requires_python(pp)
        self.d["python_spec"] = spec
        versions = [v for v in PY_PREFERENCE if _spec_ok(v, spec)]
        if not versions:
            raise SetupError("structural", f"no supported Python satisfies requires-python {spec!r}")
        self.env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION="0.0.0",
                        UV_CACHE_DIR=os.path.join(self.cache, "uv-cache"), PYTHONDONTWRITEBYTECODE="1")
        self.no_extensions = False
        if os.path.exists(os.path.join(self.work, ".gitmodules")):
            rc, out, secs = _run(["git", "-C", self.work, "submodule", "update", "--init", "--depth", "1"], self.work,
                                 self.env, INSTALL_TIMEOUT)
            self._step("submodules", rc, out, secs)
        last = ""
        for v in versions[:2]:
            venv = os.path.join(self.work, ".r3-venv")
            shutil.rmtree(venv, ignore_errors=True)
            rc, out, secs = _run(["uv", "venv", "-q", "-p", v, venv], self.work, self.env, INSTALL_TIMEOUT)
            self._step(f"venv {v}", rc, out, secs)
            if rc != 0:
                last = out
                continue
            self.py, self.python_version = os.path.join(venv, "bin", "python"), v
            self.env["VIRTUAL_ENV"], self.env["UV_PROJECT_ENVIRONMENT"] = venv, venv
            ok, last = self._install(pp)
            if ok:
                return
        raise SetupError("missing_package" if NETWORK.search(last) else "structural", f"install failed: {last[-600:]}")

    def _commit_date(self) -> str:
        return subprocess.run(["git", "-C", self.work, "log", "-1", "--format=%cI", "HEAD"], capture_output=True,
                              text=True).stdout.strip()

    def _install(self, pp: dict) -> tuple[bool, str]:
        """With C extensions built first; if that install fails, again without them (`*_NO_EXTENSIONS=1`)."""
        ok, out = self._install_once(pp)
        if ok:
            return True, ""
        self._step("retry without C extensions", 0, out[-600:], 0)
        self.env.update(NO_EXT)
        self.no_extensions = True
        return self._install_once(pp)

    def _install_once(self, pp: dict) -> tuple[bool, str]:
        # no lockfile: resolve every dependency as of the revision's commit date (the counterpart of npm --before)
        pip = ["uv", "pip", "install", "-q", "-p", self.py, "--exclude-newer", self._commit_date()]
        if os.path.exists(os.path.join(self.work, "uv.lock")):
            for extra in (["--all-extras"], []):
                rc, out, secs = _run(["uv", "sync", "--frozen", "--all-groups", *extra], self.work, self.env,
                                     INSTALL_TIMEOUT)
                self._step(f"uv sync {' '.join(extra)}".strip(), rc, out, secs)
                if rc == 0:
                    rc2, out2, s2 = _run(pip + ["pytest"], self.work, self.env, INSTALL_TIMEOUT)
                    self._step("pytest", rc2, out2, s2)
                    self.strategy = "uv.lock"
                    return True, ""
            last = out
        extras = [e for e in TEST_GROUPS if e in ((pp.get("project") or {}).get("optional-dependencies") or {})]
        target = "." + (f"[{','.join(extras)}]" if extras else "")
        rc, out, secs = _run(pip + ["-e", target, "pytest"], self.work, self.env, INSTALL_TIMEOUT)
        self._step(f"install -e {target}", rc, out, secs)
        if rc != 0:
            return False, out
        groups = [g for g in TEST_GROUPS if g in (pp.get("dependency-groups") or {})]
        if groups:
            rc, out, secs = _run(pip + [x for g in groups for x in ("--group", g)], self.work, self.env, INSTALL_TIMEOUT)
            self._step(f"groups {groups}", rc, out, secs)
        for req in REQ_FILES:
            if os.path.exists(os.path.join(self.work, req)):
                rc, out, secs = _run(pip + ["-r", req], self.work, self.env, INSTALL_TIMEOUT)
                self._step(f"requirements {req} (best effort)", rc, out, secs)
        for req in self._poetry_git_deps(pp):
            rc, out, secs = _run(pip + [req], self.work, self.env, INSTALL_TIMEOUT)
            self._step(f"git dependency {req}", rc, out, secs)
        poetry = self._poetry_group_deps(pp)
        if poetry:
            rc, out, secs = _run(pip + poetry, self.work, self.env, INSTALL_TIMEOUT)
            self._step(f"poetry groups ({len(poetry)} packages)", rc, out, secs)
        deps = self._tox_deps()
        if deps:
            rc, out, secs = _run(pip + deps, self.work, self.env, INSTALL_TIMEOUT)
            self._step("tox deps (best effort)", rc, out, secs)
        # keep the checkout installed as the package; --no-deps so the pins above (git dependencies, groups) stay
        rc, out, secs = _run(pip + ["--no-deps", "-e", target], self.work, self.env, INSTALL_TIMEOUT)
        self._step("reinstall checkout", rc, out, secs)
        self.strategy = "pip"
        return rc == 0, out

    def _poetry_git_deps(self, pp: dict) -> list[str]:
        """Poetry dependencies on a git branch (`{git = URL, branch = B}`), pinned to the branch's commit as of the
        revision's commit date, the same date rule as `--exclude-newer`. An explicit `rev` or `tag` is used as given."""
        deps = ((pp.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
        date, out = self._commit_date(), []
        for name, spec in deps.items():
            if not isinstance(spec, dict) or "git" not in spec:
                continue
            url, ref = spec["git"], spec.get("rev") or spec.get("tag")
            if not ref:
                branch = spec.get("branch", "HEAD")
                bare = os.path.join(self.cache, "git", re.sub(r"\W+", "_", url))
                if not os.path.isdir(bare):
                    subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:none", url, bare], capture_output=True)
                subprocess.run(["git", "-C", bare, "fetch", "-q", "origin", "+refs/heads/*:refs/heads/*"], capture_output=True)
                ref = subprocess.run(["git", "-C", bare, "rev-list", "-1", f"--before={date}", branch],
                                     capture_output=True, text=True).stdout.strip()
                if not ref:
                    continue
            out.append(f"{name} @ git+{url}@{ref}")
        return out

    @staticmethod
    def _poetry_group_deps(pp: dict) -> list[str]:
        """`[tool.poetry.group.<test|tests|testing|dev>.dependencies]` (and the older `dev-dependencies`) as pip
        requirements. Poetry's `^x.y` and `~x.y` become `>=x.y`; the commit-date cutoff bounds the rest."""
        poetry = (pp.get("tool") or {}).get("poetry") or {}
        tables = [((poetry.get("group") or {}).get(g) or {}).get("dependencies") or {} for g in TEST_GROUPS]
        tables.append(poetry.get("dev-dependencies") or {})
        out = []
        for table in tables:
            for name, spec in table.items():
                if name.lower() == "python":
                    continue
                v = spec.get("version", "") if isinstance(spec, dict) else str(spec)
                if isinstance(spec, dict) and ("path" in spec or "git" in spec or "url" in spec):
                    continue
                v = re.sub(r"^[\^~]=?", ">=", v.strip()) if v.strip() not in ("", "*") else ""
                out.append(name + (v if v.startswith((">", "<", "=", "!")) else (f"=={v}" if v else "")))
        return sorted(set(out))

    def _tox_deps(self) -> list[str]:
        cfg = configparser.ConfigParser(interpolation=None)
        try:
            cfg.read(os.path.join(self.work, "tox.ini"))
            raw = cfg.get("testenv", "deps", fallback="")
        except configparser.Error:
            return []
        deps = []
        for line in raw.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(("-r", "-c", "{", "-e")) or ":" in line.split(" ", 1)[0]:
                continue
            deps.append(line)
        return deps

    def test_files(self) -> list[str]:
        files = subprocess.run(["git", "-C", self.work, "ls-files"], capture_output=True, text=True).stdout.split()
        return sorted(f for f in files if TEST_PATH.search(f) and not SKIP.search(f))

    #: Options dropped from the repository's own pytest `addopts`: coverage and parallelism (they need plugins and
    #: change nothing about pass/fail). Everything else is kept, e.g. `-p no:aiohttp` and `-m "not dev_mode"`.
    _DROP_FLAG = re.compile(r"^(--cov(-[\w-]+)?(=.*)?|--no-cov(-on-fail)?|--durations(=.*)?|--numprocesses(=.*)?|"
                            r"-n\S*|--dist(=.*)?|--maxprocesses(=.*)?)$")
    _DROP_WITH_VALUE = {"--cov", "--cov-config", "--cov-context", "--cov-report", "--durations", "--numprocesses",
                        "-n", "--dist", "--maxprocesses"}

    def _addopts(self) -> str:
        """The repository's pytest `addopts` (pytest.ini, pyproject, tox.ini or setup.cfg), minus coverage and
        parallelism options."""
        import shlex
        raw = None
        for f, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
            cfg = configparser.ConfigParser(interpolation=None)
            try:
                cfg.read(os.path.join(self.work, f))
                if cfg.has_option(section, "addopts"):
                    raw = cfg.get(section, "addopts")
                    break
            except configparser.Error:
                continue
        toks = None
        if raw is None:
            ini = ((self._pyproject().get("tool") or {}).get("pytest") or {}).get("ini_options") or {}
            raw = ini.get("addopts", "")
            if isinstance(raw, list):     # a TOML list: each element is already one argument ("-m", "not property")
                toks = [str(t) for t in raw]
        if toks is None:
            lines = [ln.split(" #", 1)[0] for ln in (raw or "").splitlines() if not ln.strip().startswith("#")]
            try:
                toks = shlex.split(" ".join(lines))
            except ValueError:
                return ""
        out, skip = [], False
        for i, t in enumerate(toks):
            if skip:
                skip = False
                continue
            if t in ("-p",) and i + 1 < len(toks) and toks[i + 1] in ("pytest_cov", "xdist", "xdist.plugin"):
                skip = True
                continue
            if t in self._DROP_WITH_VALUE and i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                skip = True
                continue
            if self._DROP_FLAG.match(t):
                continue
            out.append(t)
        return shlex.join(out)

    def ensure_current_build(self) -> None:
        """The same interface as `jsbuild.Env`: the install is editable, so the tests always see the working tree."""

    def run_test(self, rel: str, ids: list[str] | None = None) -> dict:
        """Run one test file (or the given test ids in it); per-test outcomes from pytest's JUnit XML."""
        report = os.path.join(self.work, ".r3-junit.xml")
        if os.path.exists(report):
            os.remove(report)
        target = [f"{rel}::{i}" for i in ids] if ids else [rel]
        cmd = [self.py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-o",
               f"addopts={self._addopts()}", f"--junitxml={report}", *target]
        rc, out, secs = _run(cmd, self.work, self.env, TEST_TIMEOUT)
        res = {"file": rel, "rc": rc, "seconds": round(secs, 1), "passed": 0, "failed": 0, "skipped": 0,
               "parsed": False, "tests": {}}
        if os.path.exists(report):
            try:
                root = ET.parse(report).getroot()
                for case in root.iter("testcase"):
                    name = f"{case.get('classname', '')}::{case.get('name', '')}"
                    status = "failed" if case.find("failure") is not None or case.find("error") is not None else \
                        "skipped" if case.find("skipped") is not None else "passed"
                    res["tests"][name] = status
                res.update(passed=sum(s == "passed" for s in res["tests"].values()),
                           failed=sum(s == "failed" for s in res["tests"].values()),
                           skipped=sum(s == "skipped" for s in res["tests"].values()), parsed=True)
            except ET.ParseError:
                pass
        res["tail"], res["text"] = out[-1200:], out
        return res


def self_check_revision(clone: str, rev: str, cache: str, scratch: str) -> dict:
    work = os.path.join(scratch, f"{os.path.basename(clone)}-{rev[:8]}")
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(["git", "-C", clone, "worktree", "prune"], capture_output=True)
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, rev], check=True, capture_output=True)
    env = PyEnv(clone, rev, work, cache)
    rec, t0 = {"rev": rev}, time.time()
    try:
        try:
            env.setup()
        finally:
            rec.update(python=getattr(env, "python_version", None), python_spec=env.d.get("python_spec"),
                       strategy=getattr(env, "strategy", None))
        files = env.test_files()
        rec["test_files"] = len(files)
        if not files:
            raise SetupError("structural", "no test files found")
        tried = []
        for f in sorted(files, key=lambda f: (os.path.getsize(os.path.join(work, f)), f))[:MAX_CANDIDATES]:
            r = env.run_test(f)
            tried.append({k: r[k] for k in ("file", "rc", "passed", "failed", "skipped", "parsed", "seconds")})
            if r["parsed"] and r["passed"] >= 1 and r["failed"] == 0:
                runs = [env.run_test(f) for _ in range(2)] + [r]
                same = len({(x["passed"], x["failed"]) for x in runs}) == 1
                rec.update(named_test=f, flake_runs=[(x["passed"], x["failed"], x["seconds"]) for x in runs],
                           flake_free=same)
                break
        rec["tried"] = tried
        if "named_test" not in rec:
            rec.update(status="fail", kind="no_passing_test" if any(t["parsed"] for t in tried) else "structural",
                       reason="no candidate test file passed", last_tail=r["tail"][-800:])
        elif not rec["flake_free"]:
            rec.update(status="fail", kind="flake", reason="the named test varied over 3 runs")
        else:
            rec["status"] = "pass"
    except SetupError as exc:
        rec.update(status="fail", kind=exc.kind, reason=str(exc)[:900])
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        rec.update(status="fail", kind="missing_package" if NETWORK.search(msg) else "structural", reason=msg[:900])
    rec["seconds"], rec["log"] = round(time.time() - t0, 1), env.log
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
    res = {"repo": os.path.basename(a.clone),
           "revisions": [self_check_revision(os.path.realpath(a.clone), r, a.cache, a.scratch) for r in a.revs]}
    res["pass"] = all(r["status"] == "pass" for r in res["revisions"])
    if a.out:
        open(a.out, "w").write(json.dumps(res, indent=1) + "\n")
    for r in res["revisions"]:
        print(f"{res['repo']} {r['rev'][:8]}: {r['status']} {r.get('kind', '')} py={r.get('python')} "
              f"via={r.get('strategy')} files={r.get('test_files')} named={r.get('named_test')} "
              f"{r.get('reason', '')[:200]}", flush=True)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
