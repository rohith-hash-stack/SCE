"""Build every task type for ONE repository, freezing and committing each as it finishes.

    python -m harness.experiments.suite.build_repo NAME WORK_DIR [--push]

WORK_DIR holds the clones (`repos/<name>`), the mined candidates (`mined/`, `suite/*.json`), the
environments (`envs/`) and the traced edges (`edges/`). Stages, each skipped when its frozen file exists in
`harness/experiments/suite/repos/<name>/`:

    forward   type 1: mine (resumable), select (`suite.forced`), plan the break context
    bugs      type 3: plan from the bug candidates (resumable)
    newfn     type 2: plan from the new-function candidates (resumable)
    tests     type 6 (Python): plan with the validity gate (resumable)
    blast     type 4 (Python): trace the test suite, plan the anchors
    concepts  type 5: this repository's planned questions

Selection and seeds are those of the pre-registration. They are per repository already (`f"{seed}:{repo}"`),
so building one repository at a time gives the same tasks as building all ten together. A frozen file holds
only the tasks (no candidates), with the sha256 of its task list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PY_REPOS = {"werkzeug", "marshmallow", "rich", "tornado", "aiohttp", "scrapy"}
OUT = Path("harness/experiments/suite/repos")
ENV = dict(os.environ, PYTHONPATH="src:.", PYTHONDONTWRITEBYTECODE="1")


def _sh(cmd: list[str], log: Path) -> int:
    with open(log, "a") as fh:
        fh.write(f"$ {' '.join(cmd)}\n")
        fh.flush()
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, env=ENV).returncode


def _py(*args: str) -> list[str]:
    return [sys.executable, "-B", "-m", *args]


def freeze(src: Path, dst: Path, kind: str, repo: str) -> int:
    data = json.loads(src.read_text())
    tasks = [t for t in data["tasks"] if t["repo"] == repo]
    for i, t in enumerate(tasks, 1):
        t["id"] = f"{repo}-{kind}-{i:03d}"
    body = {"type": kind, "repo": repo, "status": "frozen",
            **{k: v for k, v in data.items() if k in ("seed", "per_repo", "quota", "forced", "no_self_callers")},
            "skipped": len(data.get("skipped", [])) if isinstance(data.get("skipped"), list) else
            len([k for k in data.get("skipped", {}) if k]),
            "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest(), "tasks": tasks}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(body, indent=1) + "\n")
    return len(tasks)


def _subset(src: Path, dst: Path, repo: str) -> None:
    """A per-repository working file (the candidates, plus any tasks already planned or skipped) from a
    selection over all repositories, kept when it already exists (resumable)."""
    if dst.exists():
        return
    data = json.loads(src.read_text())
    keep = {k: v for k, v in data.items() if k not in ("candidates", "tasks", "skipped", "status", "tasks_sha256")}
    keep["candidates"] = [c for c in data["candidates"] if c["repo"] == repo]
    keep["tasks"] = [t for t in data.get("tasks", []) if t["repo"] == repo]
    sk = data.get("skipped", [])
    if isinstance(sk, dict):
        keys = {f"{c['commit']}:{c.get('test_file', c.get('name', ''))}" for c in keep["candidates"]}
        keep["skipped"] = {k: v for k, v in sk.items() if k in keys}
    else:
        commits = {c["commit"] for c in keep["candidates"]} | {f"{c['commit']}:{c.get('name', '')}" for c in keep["candidates"]}
        keep["skipped"] = [k for k in sk if k in commits]
    keep["status"] = "selected"
    dst.write_text(json.dumps(keep, indent=1) + "\n")


def commit(paths: list[Path], message: str, push: bool) -> None:
    subprocess.run(["git", "add", "-f", *map(str, paths)], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", message + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
                    "Claude-Session: https://claude.ai/code/session_01JWST68Fi5NgbZEhS3wvVEz"], check=True)
    if push:
        for _ in range(4):
            if subprocess.run(["git", "push", "-q", "origin", "HEAD:feature/option3-contract-axis"]).returncode == 0:
                break


def build(name: str, work: Path, push: bool) -> None:
    clone = work / "repos" / name
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    wd = work / "suite" / "per_repo" / name
    wd.mkdir(parents=True, exist_ok=True)
    log = wd / "build.log"
    spec = f"{name}={clone}"

    # type 1
    if not (out / "forward.json").exists():
        mined = work / "mined" / name
        if not (work / "mined" / f"{name}.ok").exists():
            rc = _sh(_py("harness.experiments.change_history.mine", str(mined), spec, "--forced", "--scan", "10000",
                         "--max-candidates", "100000"), log)
            if rc:
                raise SystemExit(f"{name}: mining failed (see {log})")
            (work / "mined" / f"{name}.ok").touch()
        sel = wd / "forward.json"
        if not sel.exists():
            if _sh(_py("harness.experiments.suite.forced", "select", str(sel), str(work / "mined"), name), log):
                raise SystemExit(f"{name}: type-1 selection failed")
        if _sh(_py("harness.experiments.change_history.plan", str(sel), spec, "--breaks", "--no-overview"), log):
            raise SystemExit(f"{name}: type-1 planning failed")
        n = freeze(sel, out / "forward.json", "forward", name)
        commit([out / "forward.json"], f"Suite {name}: type 1 frozen ({n} tasks)", push)

    # type 3
    if not (out / "bugs.json").exists():
        w = wd / "bugs.json"
        _subset(work / "suite" / "tasks" / "bugs.json", w, name)
        if _sh(_py("harness.experiments.suite.bugs", "plan", str(w), spec), log):
            raise SystemExit(f"{name}: type-3 planning failed")
        n = freeze(w, out / "bugs.json", "bugs", name)
        commit([out / "bugs.json"], f"Suite {name}: type 3 frozen ({n} tasks)", push)

    # type 2
    if not (out / "newfn.json").exists():
        sel = work / "suite" / "tasks" / "newfn.json"
        if not sel.exists():
            _sh(_py("harness.experiments.suite.newfn", "select", str(work / "suite" / "newfn.json"), str(sel),
                    "--per-repo", "20"), log)
        w = wd / "newfn.json"
        _subset(sel, w, name)
        if _sh(_py("harness.experiments.suite.newfn", "plan", str(w), spec), log):
            raise SystemExit(f"{name}: type-2 planning failed")
        n = freeze(w, out / "newfn.json", "newfn", name)
        commit([out / "newfn.json"], f"Suite {name}: type 2 frozen ({n} tasks)", push)

    if name in PY_REPOS:
        # type 6
        if not (out / "tests.json").exists():
            sel = work / "suite" / "tasks" / "tests.json"
            if not sel.exists():
                _sh(_py("harness.experiments.suite.testgen", "select", str(work / "suite" / "tests.json"), str(sel),
                        "--per-repo", "20"), log)
            w = wd / "tests.json"
            _subset(sel, w, name)
            if _sh(_py("harness.experiments.suite.testgen", "plan", str(w), str(work / "envs"), spec), log):
                raise SystemExit(f"{name}: type-6 planning failed")
            n = freeze(w, out / "tests.json", "tests", name)
            commit([out / "tests.json"], f"Suite {name}: type 6 frozen ({n} tasks)", push)
        # type 4
        if not (out / "blast.json").exists():
            edges = work / "edges" / f"{name}.json"
            if not edges.exists():
                _sh(_py("harness.experiments.suite.blast", "trace", name, str(clone), str(work / "envs"), str(edges)), log)
            w = wd / "blast.json"
            if _sh(_py("harness.experiments.suite.blast", "plan", str(w), str(work / "edges"), spec), log):
                raise SystemExit(f"{name}: type-4 planning failed")
            n = freeze(w, out / "blast.json", "blast", name)
            commit([out / "blast.json"], f"Suite {name}: type 4 frozen ({n} anchors)", push)

    # type 5 (already planned for all repositories)
    if not (out / "concepts.json").exists():
        n = freeze(Path("harness/experiments/suite/tasks/concepts.json"), out / "concepts.json", "concepts", name)
        commit([out / "concepts.json"], f"Suite {name}: type 5 frozen ({n} questions)", push)
    print(f"{name}: all types frozen", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("names", nargs="+")
    p.add_argument("--work", required=True)
    p.add_argument("--push", action="store_true")
    args = p.parse_args(argv)
    for name in args.names:
        build(name, Path(args.work), args.push)
    return 0


if __name__ == "__main__":
    sys.exit(main())
