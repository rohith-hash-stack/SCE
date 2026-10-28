"""Run tests in a repository at a past revision, in its own environment (types 4 and 6).

An environment is a `uv` virtualenv per (repository, revision), cached. Into it go:
- the repository installed in editable mode;
- the test requirements its revision declares (`requirements/test*.txt`, `requirements-dev.txt`,
  `tests/requirements.txt`, ...);
- pytest, coverage and the plugins the repository needs (`EXTRA`).

`run_tests` writes a test file's new text into a worktree, runs the named tests under coverage, and reports
which ran, which passed, and which lines of a target file they executed.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PYTHON = "3.11"
EXTRA = {  # what each repository's test suite needs beyond its declared requirements
    "aiohttp": ["pytest-aiohttp", "pytest-mock", "re-assert", "freezegun", "trustme", "brotli", "aiodns",
                "pytest-asyncio", "proxy.py", "pytest-xdist"],
    "scrapy": ["testfixtures", "pytest-twisted", "sybil", "pexpect", "uvloop", "botocore", "Pillow", "zstandard",
               "brotli", "pyftpdlib", "bpython", "ipython", "passlib", "testfixtures"],
    "tornado": ["pycurl", "twisted"],
    "werkzeug": ["pytest-timeout", "pytest-xprocess", "cryptography", "watchdog", "greenlet", "ephemeral-port-reserve"],
    "marshmallow": ["simplejson", "pytz", "python-dateutil"],
    "rich": ["attrs"],
}
REQ_GLOBS = ("requirements/test*.txt", "requirements/tests*.txt", "requirements-dev.txt", "requirements_dev.txt",
             "tests/requirements*.txt", "test-requirements.txt", "requirements/dev.txt")
ENV_TIMEOUT = 900
TEST_TIMEOUT = 300


def _run(cmd, cwd=None, timeout=ENV_TIMEOUT, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def build_env(repo_name: str, work: str, cache: str) -> tuple[str | None, str]:
    """`(python, log)`: the environment's interpreter for the revision checked out in `work` (None if it
    could not be built)."""
    key = _run(["git", "-C", work, "rev-parse", "HEAD"]).stdout.strip()
    venv = os.path.join(cache, f"{repo_name}-{key[:12]}")
    py = os.path.join(venv, "bin", "python")
    if os.path.exists(os.path.join(venv, ".ok")):
        return py, "cached"
    shutil.rmtree(venv, ignore_errors=True)
    log = []
    r = _run(["uv", "venv", "-q", "-p", PYTHON, venv])
    log.append(r.stderr[-500:])
    if r.returncode:
        return None, "\n".join(log)
    env = dict(os.environ, AIOHTTP_NO_EXTENSIONS="1", SETUPTOOLS_SCM_PRETEND_VERSION="0.0.0")
    if os.path.exists(os.path.join(work, ".gitmodules")):  # e.g. aiohttp's vendored llhttp: needed to install
        r = _run(["git", "-C", work, "submodule", "update", "--init", "--depth", "1"])
        log.append(f"submodules: {r.returncode} {r.stderr[-300:]}")
    reqs = []
    for g in REQ_GLOBS:
        reqs += sorted(glob.glob(os.path.join(work, g)))
    base = ["uv", "pip", "install", "-q", "-p", py]
    r = _run(base + ["-e", work, "pytest", "coverage", *EXTRA.get(repo_name, [])], env=env)
    log.append(r.stderr[-1500:])
    if r.returncode:
        return None, "\n".join(log)
    for req in reqs:  # best effort: a pinned requirement that no longer installs must not stop the build
        r = _run(base + ["-r", req], env=env)
        if r.returncode:
            log.append(f"(ignored) {os.path.basename(req)}: {r.stderr[-300:]}")
        r = _run(base + ["-e", work], env=env)  # keep the checkout as the installed package
    Path(venv, ".ok").write_text("ok")
    return py, "\n".join(log)


def test_names(code: str) -> list[str]:
    """Test function names defined in `code` (top level or in test classes)."""
    return sorted(set(re.findall(r"(?m)^\s*(?:async\s+)?def\s+(test\w*)\s*\(", code)))


def run_tests(py: str, work: str, test_file: str, new_text: str, names: list[str], target_file: str | None = None,
              target_lines: tuple[int, int] | None = None) -> dict:
    """Write `new_text` as `test_file`, run the tests called `names` under coverage of `target_file`."""
    if not names:
        return {"ran": 0, "passed": 0, "failed": 0, "error": "no tests", "covered": []}
    path = os.path.join(work, test_file)
    original = Path(path).read_text(errors="replace") if os.path.exists(path) else None
    Path(path).write_text(new_text)
    report = tempfile.mktemp(suffix=".xml")
    cov_json = tempfile.mktemp(suffix=".json")
    data = os.path.join(tempfile.mkdtemp(), ".coverage")
    try:
        rc_empty = os.path.join(os.path.dirname(data), "coveragerc")
        Path(rc_empty).write_text("[run]\n")  # the repository's own coverage settings (parallel, source) are ignored
        cmd = [py, "-m", "coverage", "run", f"--rcfile={rc_empty}", f"--data-file={data}"]
        if target_file:
            cmd.append(f"--include={os.path.join(work, target_file)}")
        cmd += ["-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", test_file, "-k",
                " or ".join(names), f"--junitxml={report}", "-o", "addopts="]
        try:
            r = _run(cmd, cwd=work, timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"ran": 0, "passed": 0, "failed": 0, "error": "timeout", "covered": []}
        xml = Path(report).read_text() if os.path.exists(report) else ""
        cases = re.findall(r"<testcase [^>]*name=\"([^\"]+)\"[^>]*?(/>|>(.*?)</testcase>)", xml, re.S)
        ran = [(n, body) for n, _end, body in cases if n.split("[", 1)[0] in names]
        failed = sum(1 for _n, body in ran if "<failure" in body or "<error" in body)
        skipped = sum(1 for _n, body in ran if "<skipped" in body)
        covered = []
        if target_file and os.path.exists(data):
            _run([py, "-m", "coverage", "json", f"--rcfile={rc_empty}", f"--data-file={data}", "-o", cov_json], cwd=work)
            if os.path.exists(cov_json):
                files = json.loads(Path(cov_json).read_text()).get("files", {})
                for f, v in files.items():
                    if os.path.abspath(os.path.join(work, f)) == os.path.abspath(os.path.join(work, target_file)) or \
                            f.endswith(target_file):
                        lo, hi = target_lines or (1, 10 ** 9)
                        covered = sorted(x for x in v.get("executed_lines", []) if lo <= x <= hi)
        return {"ran": len(ran), "passed": len(ran) - failed - skipped, "failed": failed, "skipped": skipped,
                "covered": covered, "error": "" if ran else (r.stdout[-800:] + r.stderr[-400:])}
    finally:
        if original is None:
            os.remove(path)
        else:
            Path(path).write_text(original)
        for f in (report, cov_json):
            if os.path.exists(f):
                os.remove(f)
