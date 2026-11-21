"""The seeded draw of set 7's gold functions (README.md, "How the gold functions are chosen").

Eligible: each repository's candidate pool (`prism.query.nlq.candidate_pool`, test files excluded), functions
and methods only; no property setters (`#2`), no dunder methods, at least MIN_LINES lines, nothing under an
examples / docs / benchmarks directory. The eligible names are sorted, then shuffled with
`random.Random(SEED + repo index)` (repos in dataset.json order). Questions are written in this order.

Usage: python -m harness.experiments.heldout_retrieval.set7.select APPS
APPS/<repo> is each repository checked out at its dataset.json commit. Writes draw.json next to this file.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from prism.cli import build_pipeline
from prism.query.nlq import candidate_pool
from prism.summaries.index import function_symbols

HERE = Path(__file__).resolve().parent
SEED = 7007
MIN_LINES = 5
EXCLUDED_DIRS = {"examples", "example", "docs", "benchmarks", "bench"}


def eligible(builder, root: str) -> list[dict]:
    out = []
    for sym in function_symbols(candidate_pool(builder)):
        name = sym.qualified_name
        leaf = name.rsplit(".", 1)[-1]
        lo, hi = sym.line_range
        rel = Path(sym.file).resolve().relative_to(Path(root).resolve())
        if "#" in name or (leaf.startswith("__") and leaf.endswith("__")) or hi - lo + 1 < MIN_LINES \
                or EXCLUDED_DIRS & set(rel.parts[:-1]):
            continue
        out.append({"name": name, "file": str(rel), "lines": [lo, hi]})
    return sorted(out, key=lambda e: e["name"])


def draw(apps: str) -> dict:
    ds = json.loads((HERE / "dataset.json").read_text())
    result = {"seed": SEED, "min_lines": MIN_LINES, "excluded_dirs": sorted(EXCLUDED_DIRS), "repos": {}}
    for index, (repo, spec) in enumerate(ds["repos"].items()):
        root = str(Path(apps) / repo / spec["root"])
        pool = eligible(build_pipeline(root, use_cache=False)[0], root)
        random.Random(SEED + index).shuffle(pool)
        result["repos"][repo] = {"eligible": len(pool), "order": pool}
    return result


def main(argv=None) -> int:
    result = draw((argv or sys.argv[1:])[0])
    (HERE / "draw.json").write_text(json.dumps(result, indent=1) + "\n")
    print({r: v["eligible"] for r, v in result["repos"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
