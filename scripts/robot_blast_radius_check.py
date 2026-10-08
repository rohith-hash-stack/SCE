"""Score Prism's blast radius against what a Robot Framework run executed.

Robot's `output.xml` records every keyword each test ran, nested to any
depth. For a keyword K, the tests whose tree contains K are exactly the
tests a change to K's implementation can affect - gold that does not
depend on Prism. This script compares that set with the test callers
Prism reports for K's implementation (`prism.blast_radius`'s caller walk).

    python scripts/robot_blast_radius_check.py --repo /path/to/api-tests \\
        --output /path/to/output.xml \\
        --pair "UserApi.Create User=api.libraries.UserApi.UserApi.create_user" \\
        --pair "common.Create Test User=api.resources.common.Create_Test_User"

`--pair KEYWORD=SEED` (repeatable) or `--pairs FILE` (one `KEYWORD=SEED`
per line). KEYWORD is `Owner.Name` as Robot logs it (library or resource
name, then keyword name) or a bare name to match any owner. SEED is the
Prism symbol implementing it (`prism index` / `find_symbols_by_tag` /
the MCP error's candidate list help find it).

Only tests that ran are gold, so run the suites the seeds belong to in full
(`robot --output output.xml tests/`). A test Prism lists that did not run
counts against precision only if its suite file was part of the run.
Needs only the standard library plus Prism; Robot itself is not imported.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

from prism.cli import build_pipeline
from prism.graph.robot_framework import normalize
from prism.graph.symbol_table import SymbolRole, path_to_module
from prism.packer.candidate_index import upstream_callers_by_hop

_SLUG = re.compile(r"\W+", re.UNICODE)


def _slug(text: str) -> str:
    return _SLUG.sub("_", text).strip("_") or "unnamed"


def _keyword_identity(kw: ET.Element) -> tuple[str, str]:
    """(owner, name), normalized, across output.xml versions: RF 7 `owner`,
    RF 4-6 `library`, RF < 4 `Owner.Name` in `name`."""
    name = kw.get("name", "")
    owner = kw.get("owner") or kw.get("library") or ""
    if not owner and "." in name:
        owner, name = name.rsplit(".", 1)
    return normalize(owner), normalize(name)


def executed_tests(output_xml: str, repo_root: str) -> tuple[dict[str, set[tuple[str, str]]], set[str]]:
    """{test symbol: {(owner, keyword)} executed}, and the suite files run."""
    tree = ET.parse(output_xml)
    tests: dict[str, set[tuple[str, str]]] = {}
    suite_files: set[str] = set()

    def walk_suite(suite: ET.Element) -> None:
        source = suite.get("source") or ""
        if source.endswith((".robot", ".resource")):
            suite_files.add(os.path.abspath(source))
        for child in suite:
            if child.tag == "suite":
                walk_suite(child)
            elif child.tag == "test" and source:
                symbol = f"{path_to_module(os.path.abspath(source), repo_root)}.{_slug(child.get('name', ''))}"
                tests[symbol] = {_keyword_identity(kw) for kw in child.iter("kw")}

    for suite in tree.getroot().iter("suite"):
        walk_suite(suite)
        break                                     # the root suite walks its children
    return tests, suite_files


def score(repo_root: str, output_xml: str, pairs: list[tuple[str, str]]) -> list[dict]:
    builder, _ = build_pipeline(repo_root)
    tests, suite_files = executed_tests(output_xml, repo_root)
    rows = []
    for keyword, seed in pairs:
        owner, _, name = keyword.rpartition(".")
        want = (normalize(owner), normalize(name))
        gold = {t for t, kws in tests.items() if any(k == want or (not owner and k[1] == want[1]) for k in kws)}
        if seed not in builder.symbol_table:
            rows.append({"keyword": keyword, "seed": seed, "error": "seed not indexed"})
            continue
        walked = upstream_callers_by_hop(builder, seed, include_tests=True)
        predicted = {
            q for q in walked
            if (info := builder.symbol_table.get(q)) is not None and info.language_id == "robot"
            and info.role == SymbolRole.VERIFICATION and os.path.abspath(info.file) in suite_files
            and not q.rsplit(".", 1)[-1].startswith("Suite_")
        }
        hit = gold & predicted
        rows.append({
            "keyword": keyword, "seed": seed, "gold": len(gold), "predicted": len(predicted),
            "recall": len(hit) / len(gold) if gold else None,
            "precision": len(hit) / len(predicted) if predicted else None,
            "missed": sorted(gold - predicted), "extra": sorted(predicted - gold),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output", required=True, help="Robot output.xml")
    parser.add_argument("--pair", action="append", default=[], help="KEYWORD=SEED")
    parser.add_argument("--pairs", help="file with one KEYWORD=SEED per line")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    raw = list(args.pair)
    if args.pairs:
        with open(args.pairs, encoding="utf-8") as handle:
            raw.extend(line.strip() for line in handle if line.strip() and not line.startswith("#"))
    pairs = [tuple(p.split("=", 1)) for p in raw if "=" in p]
    if not pairs:
        parser.error("give at least one --pair KEYWORD=SEED")
    rows = score(os.path.abspath(args.repo), args.output, pairs)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        if "error" in r:
            print(f"{r['keyword']}: {r['error']} ({r['seed']})")
            continue
        fmt = lambda x: "n/a" if x is None else f"{x:.2f}"
        print(f"{r['keyword']}  gold={r['gold']}  prism={r['predicted']}  "
              f"recall={fmt(r['recall'])}  precision={fmt(r['precision'])}")
        for m in r["missed"]:
            print(f"    missed: {m}")
        for e in r["extra"]:
            print(f"    extra:  {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
