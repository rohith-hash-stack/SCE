"""Authoring checks for held-out set 5 (no search, ranking or summarizer runs).

- every gold and decoy is a candidate: in the natural-language search pool
  (`prism.query.nlq.candidate_pool`, test files excluded) - a name-only
  index;
- gold and decoys are library code, never test or type-test files;
- paraphrase rule: no question contains its gold symbol's name, or every
  word of it (`prism.query.nlq.tokenize` of the last name segment);
- ids are unique, categories known, decoys never gold;
- reports how many gold symbols are const-bound functions (`const f = ...`,
  the shape indexed since the JS/TS parser fix).

Usage: python -m harness.experiments.heldout_retrieval.set5.check HONO_CHECKOUT
Exit status 1 on any violation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from prism.cli import build_pipeline
from prism.query.nlq import candidate_pool, is_search_excluded_file, tokenize

HERE = Path(__file__).resolve().parent
CATEGORIES = {"feature_logic", "io_sink", "routing"}


def check(checkout: str) -> tuple[list[str], dict]:
    ds = json.loads((HERE / "dataset.json").read_text())
    spec = ds["repos"]["hono"]
    root = str(Path(checkout) / spec["root"])
    builder = build_pipeline(root, use_cache=False)[0]
    pool = {s.qualified_name for s in candidate_pool(builder)}
    problems, ids, const_bound = [], set(), []
    for q in ds["questions"]:
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
            node = builder.def_node(name)
            if node is not None and node.parent is not None and node.parent.type == "variable_declarator":
                const_bound.append(name)
    stats = {"questions": len(ds["questions"]), "gold": sum(len(q["gold"]) for q in ds["questions"]),
             "const_bound_gold": len(const_bound), "pool": len(pool), "status": ds["status"],
             "categories": {c: sum(q["category"] == c for q in ds["questions"]) for c in sorted(CATEGORIES)}}
    return problems, stats


def main(argv=None) -> int:
    problems, stats = check((argv or sys.argv[1:])[0])
    print(json.dumps(stats, indent=1))
    for p in problems:
        print("VIOLATION", p)
    print(f"{len(problems)} violations")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
