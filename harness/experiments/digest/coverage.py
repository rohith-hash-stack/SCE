"""Whole-repository checks for the PRISM digest (`prism.summaries.digest`), and the random samples used to judge
its accuracy by hand. No model calls.

    python -m harness.experiments.digest.coverage check OUT NAME=PATH [NAME=PATH ...]
    python -m harness.experiments.digest.coverage sample OUT --seed N [--exclude IDS.tsv] NAME=PATH [...]

`check` digests every function and method of each repository (test files included), twice, and records:
crashes, invariant violations (a fact outside its function's lines, an empty fact, a `certain` call target
that is neither an index symbol nor a labelled builtin / library / standard-library name), runs whose two
renders differ, fact kinds, certainty levels, and the digest's size against the function's source.

`sample` draws, per repository, one production function from each length band (under 10 lines, 10-40,
over 40) with a fixed seed, and writes each function's numbered source next to its digest for review. The
60 functions of the earlier summary-accuracy audit and any `--exclude` (repo<TAB>qualified name) are skipped.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
import sys
import time
import traceback
from pathlib import Path

from prism.cli import build_pipeline
from prism.graph.entry_points import find_entry_points
from prism.summaries.digest import CERTAIN, DigestBuilder

AUDITED = Path(__file__).resolve().parents[1] / "summary_grounding" / "accuracy_sample_functions.json"


def _functions(builder) -> list[str]:
    return sorted(q for q in builder._def_nodes
                  if (s := builder.symbol_table.get(q)) is not None and s.kind in ("function", "method"))


def _source_lines(builder, qname: str) -> list[str]:
    sym = builder.symbol_table.get(qname)
    return builder.parsed_file(sym.file).source.decode("utf8", "replace").splitlines()


def check(name: str, root: str) -> tuple[dict, list[str]]:
    t0 = time.time()
    b = build_pipeline(root, use_cache=False)[0]
    build_s = time.time() - t0
    entries = find_entry_points(b)
    fns = _functions(b)
    stats, kinds, certainty = collections.Counter(), collections.Counter(), collections.Counter()
    crashes, violations, sizes, ratios, rendered = [], [], [], [], {}
    t1 = time.time()
    db = DigestBuilder(b, entries)
    for q in fns:
        try:
            d = db.digest(q)
        except Exception:  # recorded, never fatal: the point is to count them
            crashes.append((q, traceback.format_exc(limit=3)))
            continue
        text = rendered[q] = d.render()
        stats["digested"] += 1
        stats["with_facts"] += bool(d.facts)
        stats["over_budget"] += bool(d.omitted)
        if d.note:
            stats[f"note: {d.note}"] += 1
        lo, hi = d.lines
        source_chars = sum(len(line) for line in _source_lines(b, q)[lo - 1:hi])
        sizes.append(len(text))
        if source_chars:
            ratios.append(len(text) / source_chars)
        for f in d.facts:
            kinds[f.kind] += 1
            certainty[f.certainty] += 1
            if not lo <= f.lines[0] <= f.lines[1] <= hi:
                violations.append((q, "fact outside its function", f.kind, f.lines, d.lines))
            if not f.text.strip():
                violations.append((q, "empty fact", f.kind, f.lines))
            if f.kind in ("call", "calls") and f.certainty == CERTAIN:
                violations.extend((q, "certain target not in the index", t) for t in f.targets
                                  if " " not in t and t not in b.symbol_table)
    digest_s = time.time() - t1
    again = DigestBuilder(b, entries)
    nondeterministic = sum(1 for q in rendered if again.digest(q).render() != rendered[q])
    result = {
        "repo": name, "functions": len(fns), "build_s": round(build_s, 1), "digest_s": round(digest_s, 1),
        "crashes": len(crashes), "violations": len(violations), "nondeterministic": nondeterministic,
        "stats": dict(stats), "kinds": dict(kinds.most_common()), "certainty": dict(certainty),
        "render_chars_median": statistics.median(sizes) if sizes else 0,
        "render_chars_p95": sorted(sizes)[int(len(sizes) * 0.95)] if sizes else 0,
        "digest_to_source_ratio_median": round(statistics.median(ratios), 2) if ratios else 0,
        "crash_list": crashes[:20], "violation_list": [list(map(str, v)) for v in violations[:50]],
    }
    return result, [rendered.get(q, f"(crashed) {q}") for q in fns]


def sample(name: str, root: str, rng: random.Random, exclude: set[tuple[str, str]]) -> list[str]:
    b = build_pipeline(root, use_cache=False)[0]
    db = DigestBuilder(b, find_entry_points(b))
    bands: dict[str, list[str]] = {"short": [], "mid": [], "long": []}
    for q in _functions(b):
        sym = b.symbol_table.get(q)
        if b._in_test_file(sym.file) or (name, q) in exclude:
            continue
        lo, hi = sym.line_range
        n = hi - lo + 1
        bands["short" if n < 10 else "mid" if n <= 40 else "long"].append(q)
    out = []
    for band, pool in bands.items():
        if not pool:
            continue
        q = rng.choice(pool)
        d = db.digest(q)
        source = _source_lines(b, q)[d.lines[0] - 1:d.lines[1]]
        out.append(f"########## {name} [{band}] {q}\n"
                   + "\n".join(f"{i:5} {line}" for i, line in enumerate(source, d.lines[0]))
                   + "\n---- digest\n" + d.render() + "\n")
    return out


def _repos(specs: list[str]) -> list[tuple[str, str]]:
    repos = []
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep or not Path(path).is_dir():
            raise SystemExit(f"expected NAME=PATH to a checkout, got {spec!r}")
        repos.append((name, path))
    return repos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("out")
    c.add_argument("repos", nargs="+")
    s = sub.add_parser("sample")
    s.add_argument("out")
    s.add_argument("repos", nargs="+")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--exclude")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    repos = _repos(args.repos)
    if args.command == "check":
        summary = []
        for name, root in repos:
            result, rendered = check(name, root)
            (out / f"{name}.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
            (out / f"{name}.digests.txt").write_text("\n\n".join(rendered) + "\n")
            summary.append({k: v for k, v in result.items() if not k.endswith("_list")})
            print(json.dumps(summary[-1]), flush=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        bad = sum(r["crashes"] + r["violations"] + r["nondeterministic"] for r in summary)
        return 1 if bad else 0
    exclude = {tuple(pair) for pair in json.loads(AUDITED.read_text())}
    if args.exclude:
        exclude |= {tuple(line.split("\t")[:2]) for line in Path(args.exclude).read_text().splitlines() if line}
    rng = random.Random(args.seed)
    blocks = [block for name, root in repos for block in sample(name, root, rng, exclude)]
    (out / f"sample_{args.seed}.txt").write_text("\n".join(blocks))
    print(f"{len(blocks)} functions -> {out / f'sample_{args.seed}.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
