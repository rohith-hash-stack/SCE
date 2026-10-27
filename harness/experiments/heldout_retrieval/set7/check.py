"""Authoring checks for held-out set 7 (no search, ranking or summarizer runs).

Set 6's checks (`set6.check._check_repo`: gold and decoys in the name-only pool, library code, paraphrase
rule, unique ids, known categories, >= MIN_FUNCTIONS library functions), plus the draw rule of README.md:
per repository, the drawn functions taken so far (each question's first gold, each skip) are exactly a prefix
of `draw.json`'s order, every skip has a known reason, and there are QUESTIONS_PER_REPO questions.

Usage: python -m harness.experiments.heldout_retrieval.set7.check APPS [--partial]
`--partial` allows fewer than QUESTIONS_PER_REPO questions (while drafting). Exit status 1 on any violation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from harness.experiments.heldout_retrieval.set6.check import CATEGORIES, _check_repo

HERE = Path(__file__).resolve().parent
QUESTIONS_PER_REPO = 30
SKIP_REASONS = {"trivial", "unaskable", "duplicate"}


def draw_problems(ds: dict, draw: dict, partial: bool) -> list[str]:
    problems = []
    for repo in ds["repos"]:
        order = [e["name"] for e in draw["repos"][repo]["order"]]
        questions = [q for q in ds["questions"] if q["repo"] == repo]
        skips = [s for s in ds["skips"] if s["repo"] == repo]
        for s in skips:
            if s.get("reason") not in SKIP_REASONS:
                problems.append(f"{repo}: skip {s.get('name')} has reason {s.get('reason')!r}")
        taken = {q["gold"][0]: q["id"] for q in questions}
        taken_skips = {s["name"] for s in skips}
        if set(taken) & taken_skips:
            problems.append(f"{repo}: drawn both as a question and a skip: {sorted(set(taken) & taken_skips)}")
        used = set(taken) | taken_skips
        prefix = order[:len(used)]
        if set(prefix) != used:
            missing = [n for n in prefix if n not in used][:5]
            extra = sorted(used - set(prefix))[:5]
            problems.append(f"{repo}: not a prefix of the draw (not taken in order: {missing}; out of order: {extra})")
        if not partial and len(questions) != QUESTIONS_PER_REPO:
            problems.append(f"{repo}: {len(questions)} questions, not {QUESTIONS_PER_REPO}")
    return problems


def check(apps: str, partial: bool = False) -> tuple[list[str], dict]:
    ds = json.loads((HERE / "dataset.json").read_text())
    draw = json.loads((HERE / "draw.json").read_text())
    problems, ids, sizes = [], set(), {}
    for repo, spec in ds["repos"].items():
        problems += _check_repo(ds, repo, str(Path(apps) / repo / spec["root"]), ids, sizes)
    problems += draw_problems(ds, draw, partial)
    stats = {"questions": len(ds["questions"]), "gold": sum(len(q["gold"]) for q in ds["questions"]),
             "skips": {r: sum(s["repo"] == r for s in ds["skips"]) for r in ds["repos"]},
             "status": ds["status"], "pool": sizes,
             "per_repo": {r: sum(q["repo"] == r for q in ds["questions"]) for r in ds["repos"]},
             "categories": {c: sum(q["category"] == c for q in ds["questions"]) for c in sorted(CATEGORIES)}}
    return problems, stats


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    partial = "--partial" in args
    args = [a for a in args if a != "--partial"]
    problems, stats = check(args[0], partial)
    print(json.dumps(stats, indent=1))
    for p in problems:
        print("VIOLATION", p)
    print(f"{len(problems)} violations")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
