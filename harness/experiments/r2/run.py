"""R2 model answers and scores (A6; R2-1: seeds 42 and 1001).

    python -m harness.experiments.r2.run calls                                  # planned calls per phase
    python -m harness.experiments.r2.run generate PHASE SPLIT REPO OUT [--base-url U] [--model M] [--threads N]
    python -m harness.experiments.r2.run score SPLIT REPO OUT                   # -> OUT/scores.json

Answers are keyed `<task>/<version>@<seed>`. Phases, in the order the Kaggle cell runs them (A6, "Order"):
    1  development: FULL, NO4, NO6        seeds 42, 1001     192 x 3 x 2 = 1,152 calls
    2  development: NO7, NO47             seeds 42, 1001     192 x 2 x 2 =   768
    3  held-out: all five versions        seeds 42, 1001      75 x 5 x 2 =   750
Within a phase, seed 42 comes before seed 1001.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
from pathlib import Path

from harness.experiments.change_history.run import MODEL, ollama, score_task
from harness.experiments.r2.build import SPLITS, VERSIONS, pads_of, prompt, tasks_of

SEEDS = (42, 1001)
FILE_LANG = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript"}
PHASES = {1: ("dev", ("FULL", "NO4", "NO6")), 2: ("dev", ("NO7", "NO47")), 3: ("heldout", tuple(VERSIONS))}


def repos_of(split: str) -> list[str]:
    return sorted({t["repo"] for t in tasks_of(split)})


def planned(phase: int, repo: str) -> list[tuple[dict, str, int, str]]:
    split, versions = PHASES[phase]
    tasks = tasks_of(split, repo)
    return [(t, v, s, f"{t['id']}/{v}@{s}") for s in SEEDS for t in tasks for v in versions]


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def generate(phase: int, split: str, repo: str, out: Path, base_url: str, model: str, threads: int = 1) -> None:
    if PHASES[phase][0] != split:
        raise SystemExit(f"phase {phase} is the {PHASES[phase][0]} split")
    path = out / "answers.json"
    answers = _load(path)
    pads = pads_of(split, repo)
    todo = [c for c in planned(phase, repo) if c[3] not in answers]
    print(f"phase {phase} {split} {repo}: {len(todo)} model calls to make", flush=True)

    def one(item):
        t, version, seed, key = item
        text = prompt(t, version, pads)
        sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        try:
            stats: dict = {}
            raw, secs = ollama(base_url, model, text, stats=stats, seed=seed)
            return key, {"raw": raw, "seconds": round(secs, 1), "model": model, "seed": seed, "prompt_sha": sha,
                         **stats}
        except Exception as exc:  # recorded; scored as no answer
            return key, {"raw": "", "error": f"{type(exc).__name__}: {exc}", "model": model, "seed": seed,
                         "prompt_sha": sha}

    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        for key, value in pool.map(one, todo):
            answers[key] = value
            path.write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")
            print(f"  {key}: {value.get('seconds', '-')}s {value.get('done_reason', '')} {value.get('error', '')}",
                  flush=True)


def score(split: str, repo: str, out: Path) -> dict:
    """Every answer with the forward scorer (`change_history.run.score_task`), unchanged from R1."""
    answers = _load(out / "answers.json")
    by_id = {t["id"]: t for t in tasks_of(split, repo)}
    rows = {}
    for key, a in sorted(answers.items()):
        task_id, rest = key.rsplit("/", 1)
        version, seed = rest.split("@", 1)
        t = by_id.get(task_id)
        if t is None:
            continue
        s = score_task(t, a.get("raw", ""))
        rows[key] = {"task": task_id, "repo": repo, "split": split, "language": t["language"],
                     "file_language": FILE_LANG.get(Path(t["anchor_file"]).suffix, "other"), "tier": t["tier"],
                     "version": version, "seed": seed, "changed": len(t["changed_callers"]), **s,
                     "raw_sha": hashlib.sha256(a.get("raw", "").encode()).hexdigest()[:16],
                     "prompt_sha": a.get("prompt_sha"), "prompt_tokens": a.get("prompt_tokens"),
                     "answer_tokens": a.get("answer_tokens"), "done_reason": a.get("done_reason"),
                     "seconds": a.get("seconds"), "error": a.get("error")}
    (out / "scores.json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    print(f"{len(rows)} answers scored -> {out / 'scores.json'}")
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calls")
    g = sub.add_parser("generate")
    g.add_argument("phase", type=int, choices=tuple(PHASES))
    s = sub.add_parser("score")
    for q in (g, s):
        q.add_argument("split", choices=SPLITS)
        q.add_argument("repo")
        q.add_argument("out")
    g.add_argument("--base-url", default="http://127.0.0.1:11434")
    g.add_argument("--model", default=MODEL)
    g.add_argument("--threads", type=int, default=1)
    a = p.parse_args(argv)
    if a.cmd == "calls":
        plan = {ph: {r: len(planned(ph, r)) for r in repos_of(PHASES[ph][0])} for ph in PHASES}
        print(json.dumps({"per_phase": {ph: sum(d.values()) for ph, d in plan.items()},
                          "total": sum(sum(d.values()) for d in plan.values()), "plan": plan}, indent=1))
        return 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "generate":
        if not pads_of(a.split, a.repo):
            raise SystemExit(f"no pads for {a.split}/{a.repo}")
        generate(a.phase, a.split, a.repo, out, a.base_url, a.model, a.threads)
    else:
        score(a.split, a.repo, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
