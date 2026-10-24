"""Freeze the P3b task set, and verify it before any P3b cell runs (P3.md, "Task authoring and freezing").

`freeze` writes `benchmarks/layer4_p3b/FROZEN.json`:
- the seed;
- the sha256 of the draw (`draw.json`) and of the specs (`specs_p3b.py`);
- per task: the sha256 of its task file and of its trace manifest, plus corpus, focal, draw rank and
  gold count;
- the eligibility rule in force (`min_gold`, amendment 1 of P3.md);
- per corpus: kept, skipped and environment-fault counts;
- every candidate not kept: draw skips (`draw.json`: no test mention, no runnable test), trace skips
  (`authoring_log*.json`, with reason codes) and environment faults (the run failed with nothing
  traced too, or timed out - set aside, not counted as ineligible).

It refuses when:
- a kept decision has no task file, or a task file no kept decision;
- the total is below MIN_TASKS (24): extend the specs from the same draw (`p3b_draw --n`) and author
  again - never a new draw.

A corpus below 8 keeps its natural yield (approved: no backfill from another corpus).

`verify(tasks_dir)` is the runner's guard: the task files in `tasks_dir` must be exactly the frozen
ones, byte for byte, or it raises.

Usage:
  python -m harness.experiments.retrieval_pivot.p3b_freeze freeze [--out benchmarks/layer4_p3b]
  python -m harness.experiments.retrieval_pivot.p3b_freeze verify [--out benchmarks/layer4_p3b]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCE = Path(__file__).resolve().parents[3]
OUT = SCE / "benchmarks" / "layer4_p3b"
SPECS_FILE = SCE / "benchmarks" / "layer4" / "specs_p3b.py"
MIN_TASKS = 24
PER_CORPUS = 8
AMENDMENT = 1  # P3.md "Amendment 1": gold 2..15, unrunnable Django apps excluded, control runs, tRPC as source
FROZEN = "FROZEN.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(out: Path = OUT) -> dict:
    from benchmarks.layer4 import specs_p3b
    draw_path = out / "draw.json"
    log_paths = sorted(out.glob("authoring_log*.json"))  # one per corpus when authored in parallel lanes
    if (out / FROZEN).exists():
        raise SystemExit(f"{out / FROZEN} exists: a frozen set is never re-frozen")
    if not log_paths:
        raise SystemExit(f"no authoring_log*.json in {out}")
    draw = json.loads(draw_path.read_text())
    if draw["seed"] != specs_p3b.SEED:
        raise SystemExit(f"draw seed {draw['seed']} != specs seed {specs_p3b.SEED}")
    decisions = {}
    for path in log_paths:
        for tid, d in json.loads(path.read_text())["decisions"].items():
            if tid in decisions:
                raise SystemExit(f"{tid} decided in more than one authoring log")
            decisions[tid] = d
    kept = {tid: d for tid, d in decisions.items() if d["status"] == "kept"}
    files = {p.stem: p for p in sorted((out / "tasks").glob("p3b_*.json"))}
    if set(kept) != set(files):
        raise SystemExit(f"kept decisions and task files differ: only kept {sorted(set(kept) - set(files))}, "
                         f"only files {sorted(set(files) - set(kept))}")
    if len(kept) < MIN_TASKS:
        raise SystemExit(f"{len(kept)} tasks < {MIN_TASKS}: extend the specs from the same draw "
                         "(p3b_draw --n) and author again before freezing")
    tasks = {}
    for tid, path in files.items():
        task = json.loads(path.read_text())
        manifest = SCE / task["gold"]["trace_manifest"]
        tasks[tid] = {"task_sha256": sha256(path), "manifest_sha256": sha256(manifest), "corpus": task["corpus"],
                      "qname": task["focal"]["qname"], "draw_rank": kept[tid].get("draw_rank"),
                      "gold": len(task["gold"]["affected_symbols"])}
    corpora = sorted({d["corpus"] for d in decisions.values()})
    per_corpus = {c: {"kept": sum(1 for t in tasks.values() if t["corpus"] == c),
                      "trace_skipped": sum(1 for d in decisions.values() if d["corpus"] == c and d["status"] == "skipped"),
                      "env_faults": sum(1 for d in decisions.values() if d["corpus"] == c and d["status"] == "env_fault"),
                      "draw_skipped": len(draw["corpora"].get(c, {}).get("skipped_before_specs", []))} for c in corpora}
    from harness.experiments.layer4.author import MAX_GOLD, P3B_MIN_GOLD
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SCE, capture_output=True, text=True).stdout.strip()
    frozen = {
        "seed": draw["seed"], "draw_sha256": sha256(draw_path), "specs_sha256": sha256(SPECS_FILE),
        "authoring_logs_sha256": {p.name: sha256(p) for p in log_paths}, "sce_commit": head,
        "frozen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "amendment": AMENDMENT, "eligibility": {"min_gold": P3B_MIN_GOLD, "max_gold": MAX_GOLD, "max_depth": 2},
        "per_corpus_target": PER_CORPUS, "min_tasks": MIN_TASKS, "total": len(tasks), "per_corpus": per_corpus,
        "short_corpora": [c for c, v in per_corpus.items() if v["kept"] < PER_CORPUS],
        "tasks": tasks,
        "skipped": {
            "draw": {c: r.get("skipped_before_specs", []) for c, r in draw["corpora"].items()},
            "trace": [{"task_id": tid, **{k: d.get(k) for k in ("corpus", "qname", "draw_rank", "reason")}}
                      for tid, d in sorted(decisions.items()) if d["status"] == "skipped"],
            "environment": [{"task_id": tid, **{k: d.get(k) for k in ("corpus", "qname", "draw_rank", "reason", "detail")}}
                            for tid, d in sorted(decisions.items()) if d["status"] == "env_fault"],
        },
    }
    (out / FROZEN).write_text(json.dumps(frozen, indent=1) + "\n")
    return frozen


def verify(tasks_dir: Path, frozen_path: Path | None = None) -> dict:
    """The runner's guard: the task files of `tasks_dir` are exactly FROZEN.json's, byte for byte."""
    frozen_path = frozen_path or tasks_dir.parent / FROZEN
    if not frozen_path.exists():
        raise SystemExit(f"refusing to run: {frozen_path} not found - the P3b task set is not frozen")
    frozen = json.loads(frozen_path.read_text())
    files = {p.stem: p for p in sorted(tasks_dir.glob("p3b_*.json"))}
    problems = [f"missing {t}" for t in sorted(set(frozen["tasks"]) - set(files))]
    problems += [f"not frozen: {t}" for t in sorted(set(files) - set(frozen["tasks"]))]
    for tid, path in files.items():
        want = frozen["tasks"].get(tid)
        if want and sha256(path) != want["task_sha256"]:
            problems.append(f"changed since the freeze: {tid}")
        if want:
            manifest = SCE / json.loads(path.read_text())["gold"]["trace_manifest"]
            if not manifest.exists() or sha256(manifest) != want["manifest_sha256"]:
                problems.append(f"manifest changed or missing: {tid}")
    if problems:
        raise SystemExit("refusing to run: the P3b tasks differ from FROZEN.json:\n  " + "\n  ".join(problems))
    return frozen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("freeze", "verify"))
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    if a.command == "freeze":
        f = freeze(a.out)
        print(f"frozen {f['total']} tasks: {f['per_corpus']}; short corpora {f['short_corpora']}")
    else:
        f = verify(a.out / "tasks", a.out / FROZEN)
        print(f"verified {f['total']} frozen tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
