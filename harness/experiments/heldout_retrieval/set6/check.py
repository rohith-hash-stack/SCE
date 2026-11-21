"""Authoring checks for held-out set 6 (no search, ranking or summarizer runs).

- every gold and decoy is a candidate: in the natural-language search pool
  (`prism.query.nlq.candidate_pool`, test files excluded) - a name-only
  index;
- gold and decoys are library code, never test or type-test files;
- paraphrase rule: no question contains its gold symbol's name, or every
  word of it (`prism.query.nlq.tokenize` of the last name segment);
- ids are unique, categories known, decoys never gold;
- every repository has at least MIN_FUNCTIONS library functions in its pool
  (the set 6 selection criterion).

Usage: python -m harness.experiments.heldout_retrieval.set6.check APPS
APPS/<repo> is each repository checked out at its dataset.json commit.
Exit status 1 on any violation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from prism.cli import build_pipeline
from prism.query.nlq import candidate_pool, is_search_excluded_file, tokenize
from prism.summaries.index import function_symbols

HERE = Path(__file__).resolve().parent
CATEGORIES = {"feature_logic", "io_sink", "routing"}
MIN_FUNCTIONS = 300


def check(apps: str) -> tuple[list[str], dict]:
    ds = json.loads((HERE / "dataset.json").read_text())
    problems, ids, sizes = [], set(), {}
    for repo, spec in ds["repos"].items():
        problems += _check_repo(ds, repo, str(Path(apps) / repo / spec["root"]), ids, sizes)
    stats = {"questions": len(ds["questions"]), "gold": sum(len(q["gold"]) for q in ds["questions"]),
             "status": ds["status"], "pool": sizes,
             "per_repo": {r: sum(q["repo"] == r for q in ds["questions"]) for r in ds["repos"]},
             "categories": {c: sum(q["category"] == c for q in ds["questions"]) for c in sorted(CATEGORIES)}}
    return problems, stats


def _check_repo(ds: dict, repo: str, root: str, ids: set, sizes: dict) -> list[str]:
    builder = build_pipeline(root, use_cache=False)[0]
    syms = candidate_pool(builder)
    pool = {s.qualified_name for s in syms}
    sizes[repo] = {"candidates": len(pool), "functions": len(function_symbols(syms))}
    problems = []
    if sizes[repo]["functions"] < MIN_FUNCTIONS:
        problems.append(f"{repo}: {sizes[repo]['functions']} library functions, under {MIN_FUNCTIONS}")
    for q in (q for q in ds["questions"] if q["repo"] == repo):
        qid = q["id"]
        if qid in ids:
            problems.append(f"{qid}: duplicate id")
        ids.add(qid)
        if q["category"] not in CATEGORIES:
            problems.append(f"{qid}: unknown category {q['category']}")
        if set(q["gold"]) & set(q["decoys"]):
            problems.append(f"{qid}: a decoy is also gold")
        words = set(tokenize(q["question"]))
        for name in q["gold"] + q["decoys"]:
            sym = builder.symbol_table.get(name)
            if "#" in name:
                problems.append(f"{qid}: {name} is a property setter")
            if name not in pool:
                problems.append(f"{qid}: {name} is not a candidate")
            elif is_search_excluded_file(builder, sym.file):
                problems.append(f"{qid}: {name} is in a test file")
        for name in q["gold"]:
            leaf = name.rsplit(".", 1)[-1]
            if leaf.lower() in q["question"].lower():
                problems.append(f"{qid}: question contains the gold name {leaf!r}")
            parts = set(tokenize(leaf))
            if parts and parts <= words:
                problems.append(f"{qid}: question contains every word of {leaf!r} ({sorted(parts)})")
    return problems


def main(argv=None) -> int:
    problems, stats = check((argv or sys.argv[1:])[0])
    print(json.dumps(stats, indent=1))
    for p in problems:
        print("VIOLATION", p)
    print(f"{len(problems)} violations")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
