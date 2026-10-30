"""R1 model answers and scores (README, "Runs").

    python -m harness.experiments.r1.run calls                                 # the planned calls, per phase
    python -m harness.experiments.r1.run generate PHASE KIND REPO OUT [--base-url U] [--model M] [--threads N]
    python -m harness.experiments.r1.run score KIND REPO OUT                   # -> OUT/scores.json

Answers are keyed `<task>/<version>@<seed>`; the determinism replay is `<task>/<version>@42replay`. Seed 42 of the
unpadded versions is the suite's answers (`reports/suite/<repo>/<type>/answers.json`), read when scoring.

Phases, in the order the Kaggle cell runs them:
    1  the padded versions, seed 42
    2  the determinism replay: 5 tasks per type (seed 5501), base and PRISM versions, seed 42
    3  every version, seed 1001
    4  every version, seed 2002
"""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib
import json
import random
import sys
from pathlib import Path

from harness.experiments.change_history.run import MODEL, ollama
from harness.experiments.r1.pad import BASE, PADS, SUITE, TYPES, base_prompt, pad_prompt

KINDS = ("forward", "blast", "newfn")
BASE_VERSION = {"forward": "FILE", "blast": "GREP", "newfn": "FILE"}
SEEDS = (42, 1001, 2002)
REPLAY_PER_KIND = 5
SUITE_OUT = Path("reports/suite")
SCORERS = {"forward": "harness.experiments.change_history.run", "blast": "harness.experiments.suite.blast",
           "newfn": "harness.experiments.suite.newfn"}


def versions(kind: str) -> tuple[str, ...]:
    return (BASE_VERSION[kind], "PRISM", *TYPES[kind]["versions"])


def tasks_of(kind: str, repo: str) -> list[dict]:
    path = SUITE / repo / f"{kind}.json"
    return json.loads(path.read_text())["tasks"] if path.exists() else []


def repos_of(kind: str) -> list[str]:
    return sorted(p.parent.name for p in SUITE.glob(f"*/{kind}.json"))


def pads_of(kind: str, repo: str) -> dict:
    path = PADS / kind / f"{repo}.json"
    return json.loads(path.read_text())["tasks"] if path.exists() else {}


def replay_tasks(kind: str) -> set[str]:
    ids = sorted(t["id"] for r in repos_of(kind) for t in tasks_of(kind, r))
    return set(random.Random(f"5501:{kind}").sample(ids, min(REPLAY_PER_KIND, len(ids))))


def planned(phase: int, kind: str, repo: str) -> list[tuple[dict, str, int, str]]:
    """(task, version, seed, answer key) for one phase of one type in one repository."""
    out = []
    replay = replay_tasks(kind) if phase == 2 else set()
    for t in tasks_of(kind, repo):
        if phase == 1:
            out += [(t, v, 42, f"{t['id']}/{v}@42") for v in TYPES[kind]["versions"]]
        elif phase == 2 and t["id"] in replay:
            out += [(t, v, 42, f"{t['id']}/{v}@42replay") for v in (BASE_VERSION[kind], "PRISM")]
        elif phase in (3, 4):
            seed = SEEDS[phase - 2]
            out += [(t, v, seed, f"{t['id']}/{v}@{seed}") for v in versions(kind)]
    return out


def prompt(kind: str, task: dict, version: str, pads: dict) -> str:
    return pad_prompt(kind, task, version, pads) if version in BASE else base_prompt(kind, task, version)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def generate(phase: int, kind: str, repo: str, out: Path, base_url: str, model: str, threads: int = 1) -> None:
    path = out / "answers.json"
    answers = _load(path)
    pads = pads_of(kind, repo)
    todo = [c for c in planned(phase, kind, repo) if c[3] not in answers]
    print(f"phase {phase} {kind} {repo}: {len(todo)} model calls to make", flush=True)

    def one(item):
        t, version, seed, key = item
        try:
            stats: dict = {}
            text, secs = ollama(base_url, model, prompt(kind, t, version, pads), stats=stats, seed=seed)
            return key, {"raw": text, "seconds": round(secs, 1), "model": model, "seed": seed, **stats}
        except Exception as exc:  # recorded; scored as no answer
            return key, {"raw": "", "error": f"{type(exc).__name__}: {exc}", "model": model, "seed": seed}

    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        for key, value in pool.map(one, todo):
            answers[key] = value
            path.write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")
            print(f"  {key}: {value.get('seconds', '-')}s {value.get('done_reason', '')} {value.get('error', '')}",
                  flush=True)


def blast_hits(task: dict, raw: str) -> list[bool]:
    from harness.experiments.suite.blast import _match
    try:
        items = [x.get("function", "") for x in json.loads(raw).get("affected", []) if isinstance(x, dict)]
    except (ValueError, TypeError, AttributeError):
        items = []
    left, hits = set(task["gold"]), []
    for a in items:
        g = next((g for g in sorted(left) if _match(a, g)), None)
        hits.append(g is not None)
        left.discard(g)
    return hits


def score(kind: str, repo: str, out: Path) -> dict:
    """Every answer of every version and seed, with the suite's scorer for the type (and, for newfn, the outer-fence
    variant as `fence_*`)."""
    mod = importlib.import_module(SCORERS[kind])
    answers = dict(_load(out / "answers.json"))
    for key, value in _load(SUITE_OUT / repo / kind / "answers.json").items():  # seed 42 of the unpadded versions
        task, version = key.rsplit("/", 1)
        if version in versions(kind):
            answers.setdefault(f"{task}/{version}@42", value)
    by_id = {t["id"]: t for t in tasks_of(kind, repo)}
    rows = {}
    for key, a in sorted(answers.items()):
        task_id, rest = key.rsplit("/", 1)
        version, seed = rest.split("@", 1)
        t = by_id.get(task_id)
        if t is None:
            continue
        s = mod.score_task(t, a.get("raw", ""))
        if kind == "newfn":
            f = mod.score_task(t, a.get("raw", ""), outer_fence=True)
            s.update({"fence_parses": f["parses"]})
        if kind == "forward":
            s["changed"], s["tier"] = len(t["changed_callers"]), t["tier"]
        if kind == "blast":  # the answer's names in order: which ones are traced callers (each counted once)
            s["gold_n"], s["hits_in_order"] = len(t["gold"]), blast_hits(t, a.get("raw", ""))
        rows[key] = {"task": task_id, "repo": repo, "version": version, "seed": seed, **s,
                     "raw_sha": __import__("hashlib").sha256(a.get("raw", "").encode()).hexdigest()[:16],
                     "prompt_tokens": a.get("prompt_tokens"), "answer_tokens": a.get("answer_tokens"),
                     "done_reason": a.get("done_reason"), "seconds": a.get("seconds"), "error": a.get("error")}
    (out / "scores.json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    print(f"{len(rows)} answers scored -> {out / 'scores.json'}")
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calls")
    g = sub.add_parser("generate")
    g.add_argument("phase", type=int, choices=(1, 2, 3, 4))
    s = sub.add_parser("score")
    for q in (g, s):
        q.add_argument("kind", choices=KINDS)
        q.add_argument("repo")
        q.add_argument("out")
    g.add_argument("--base-url", default="http://127.0.0.1:11434")
    g.add_argument("--model", default=MODEL)
    g.add_argument("--threads", type=int, default=1)
    args = p.parse_args(argv)
    if args.cmd == "calls":
        plan = {phase: {kind: {r: len(planned(phase, kind, r)) for r in repos_of(kind)} for kind in KINDS}
                for phase in (1, 2, 3, 4)}
        print(json.dumps({"per_phase": {ph: sum(sum(v.values()) for v in d.values()) for ph, d in plan.items()},
                          "plan": plan}, indent=1))
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "generate":
        if not pads_of(args.kind, args.repo) and args.phase in (1, 3, 4):
            raise SystemExit(f"no pads for {args.kind}/{args.repo}")
        generate(args.phase, args.kind, args.repo, out, args.base_url, args.model, args.threads)
    else:
        score(args.kind, args.repo, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
