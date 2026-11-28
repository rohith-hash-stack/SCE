"""Shipped-path equivalence: `prism.ask`'s automatic routing reproduces the
evaluated FIELD w=0.5 arm on held-out sets 2-4.

Per repository (the pinned checkouts and dependency sites the harness runs
used):

1. the committed summary cache of the validated run is installed with the
   shipped `prism summarize` code (`pipeline.build_index(from_cache=...)`):
   every entry is checked against its card key, so a card built differently
   by the moved card builder would not install;
2. `NLQIndex(..., summaries="auto")` must choose w = 0.5 on its own (locked
   pipeline, coverage >= 0.95);
3. for every question its ranking must equal the harness's
   `fielded(nlq, summaries, 0.5)` ranking exactly, and the recomputed BASE
   and FIELD w=0.5 metrics must equal the committed results;
4. the served order (the exact-name pin applied, `NLQIndex.served`) is
   scored too - it differs only for `mixed` questions whose names match a
   symbol;
5. `prism.blast_radius` (MCP, in process) on each gold symbol is
   byte-identical with and without the summary index.

Usage: python -m harness.experiments.purpose_search.equivalence --apps-dir A --sites-dir S --out-dir OUT
  [--repos fastapi_template,styleguide,...] [--no-blast]
Writes OUT/equivalence.json and OUT/EQUIVALENCE.md; exit status 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
import time
from pathlib import Path

from harness.experiments.purpose_search.run import HELD_OUT_REPOS, METRICS, fielded, load_repo, score
from prism.query.nlq import NLQIndex, rule_intent
from prism.summaries.index import index_path
from prism.summaries.pipeline import build_index

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS = {**{r: "reports/purpose_search_py_14b_v2" for r in ("fastapi_template", "styleguide")},
           **{r: "reports/purpose_search_set3_14b_v2" for r in ("express", "trpc")},
           **{r: "reports/purpose_search_set4" for r in ("flask", "httpie", "fastify", "zod")}}
SETS = {"set 2": ("fastapi_template", "styleguide"), "set 3": ("express", "trpc"), "set 4": ("flask", "httpie", "fastify", "zod")}
ARM = "FIELD w=0.5"


def _blast(repo_path: str, seeds: list[str]) -> list[str]:
    from mcp.client import Client
    from prism.mcp import server as mcp_server

    async def go():
        out = []
        async with Client(mcp_server.server) as client:
            for seed in seeds:
                r = await client.call_tool("prism.blast_radius", {"repo_path": repo_path, "seed_symbol": seed})
                out.append(json.dumps([c.text for c in r.content]) + json.dumps(r.structured_content, sort_keys=True))
        return out
    return asyncio.run(go())


def _metrics(rows: list[dict]) -> dict:
    return {m: round(st.mean(r[m] for r in rows), 3) for m in METRICS}


def check_repo(repo: str, apps_dir: str, sites_dir: str, out_dir: Path, blast: bool) -> dict:
    t0 = time.time()
    reported = json.loads((REPO_ROOT / RESULTS[repo] / f"{repo}.json").read_text())
    cache = json.loads((REPO_ROOT / RESULTS[repo] / f"summary_cache_{repo}.json").read_text())
    builder, _masks, _entries, root, questions = load_repo(repo, apps_dir, sites_dir, out_dir)
    path = index_path(root)
    path.unlink(missing_ok=True)
    seeds = sorted({g for q in questions for g in q["gold"] if builder.symbol_table.get(g) is not None})
    blast_before = _blast(root, seeds) if blast else []

    counts = build_index(builder, root, from_cache=cache)  # the shipped installer, card keys checked
    base = NLQIndex(builder, root)
    prod = NLQIndex(builder, root, summaries="auto")
    harness = fielded(base, prod.summary_state.summaries, 0.5)
    rows = {"BASE": [], ARM: [], "served": []}
    identical, pinned_questions = 0, []
    for q in questions:
        p_rank, h_rank = prod.ranked(q["question"]), harness.ranked(q["question"])
        identical += p_rank == h_rank
        route, pins, served, _ = prod.served(q["question"], rule_intent(q["question"]))
        if pins:
            pinned_questions.append({"id": q["id"], "route": route, "pins": pins})
        for arm, ranking in (("BASE", base.ranked(q["question"])), (ARM, p_rank), ("served", served)):
            rows[arm].append({"id": q["id"], **score(builder, ranking, q["gold"])})
    recomputed = {arm: _metrics(r) for arm, r in rows.items()}
    matches_reported = {}
    for arm in ("BASE", ARM):
        want = {qq["id"]: qq["ranks"] for qq in reported["search"][arm]["per_question"]}
        matches_reported[arm] = {
            "per_question_ranks": all(r["ranks"] == want[r["id"]] for r in rows[arm]),
            "metrics": all(recomputed[arm][m] == reported["search"][arm][m] for m in METRICS)}
    blast_after = _blast(root, seeds) if blast else []
    path.unlink(missing_ok=True)  # leave the checkout as the harness found it
    return {
        "repo": repo, "questions": len(questions), "functions": counts["functions"],
        "cache_entries": len(cache), "installed": counts["imported"], "reported_summaries": reported["summaries"],
        "coverage": prod.summary_state.coverage, "auto_weight": prod.summary_weight,
        "ranking_identical": identical, "metrics": recomputed, "reported": {arm: {m: reported["search"][arm][m]
                                                                             for m in METRICS} for arm in ("BASE", ARM)},
        "matches_reported": matches_reported, "pinned_questions": pinned_questions,
        "blast_seeds": len(seeds), "blast_identical": sum(a == b for a, b in zip(blast_before, blast_after)) if blast else None,
        "seconds": round(time.time() - t0, 1),
    }


def ok(r: dict) -> bool:
    return (r["auto_weight"] == 0.5 and r["installed"] == r["reported_summaries"] and r["ranking_identical"] == r["questions"]
            and all(all(v.values()) for v in r["matches_reported"].values())
            and (r["blast_identical"] is None or r["blast_identical"] == r["blast_seeds"]))


def render(results: list[dict]) -> str:
    L = ["# Shipped routing reproduces the evaluated FIELD w=0.5 arm", "",
         f"**{'PASS' if all(ok(r) for r in results) else 'FAIL'}** - {len(results)} repositories, "
         f"{sum(r['questions'] for r in results)} questions.", "",
         "Each repository's committed summary cache was installed with `prism summarize --from-cache` code; "
         "`prism.ask`'s index then chose its summary weight by the shipped rule.", "",
         "| repo | functions | installed / reported summaries | coverage | auto w | ranking == harness FIELD | "
         "BASE == reported | FIELD w=0.5 == reported | blast-radius identical |",
         "|---|---|---|---|---|---|---|---|---|"]
    yes = lambda b: "yes" if b else "**no**"
    for r in results:
        mr = r["matches_reported"]
        blast = "-" if r["blast_identical"] is None else f"{r['blast_identical']}/{r['blast_seeds']}"
        L.append(f"| {r['repo']} | {r['functions']} | {r['installed']} / {r['reported_summaries']} | {r['coverage']:.3f} | "
                 f"{r['auto_weight']} | {r['ranking_identical']}/{r['questions']} | "
                 f"{yes(mr['BASE']['per_question_ranks'] and mr['BASE']['metrics'])} | "
                 f"{yes(mr[ARM]['per_question_ranks'] and mr[ARM]['metrics'])} | {blast} |")
    L += ["", "## Metrics per set (recomputed on the shipped path)", "",
          "| set | arm | recall@5 | recall@20 | pool recall | MRR |", "|---|---|---|---|---|---|"]
    by = {r["repo"]: r for r in results}
    for name, repos in SETS.items():
        rs = [by[x] for x in repos if x in by]
        if not rs:
            continue
        n = sum(r["questions"] for r in rs)
        for arm in ("BASE", ARM, "served"):
            vals = [sum(r["metrics"][arm][m] * r["questions"] for r in rs) / n for m in METRICS]
            label = "served (pin applied)" if arm == "served" else arm
            L.append(f"| {name} ({n}) | {label} | " + " | ".join(f"{v:.3f}" for v in vals) + " |")
    pins = [(r["repo"], p) for r in results for p in r["pinned_questions"]]
    L += ["", f"## Questions the exact-name pin touched: {len(pins)}", ""]
    L += [f"- {repo} {p['id']} ({p['route']}): pinned {', '.join(p['pins'])}" for repo, p in pins] or ["- none"]
    L += ["", "Metrics are means over each set's questions (repository means weighted by question count). "
          "`served` is what `prism.ask` returns: the FIELD w=0.5 ranking with exact name matches of a mixed or "
          "identifier query in front."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apps-dir", required=True)
    ap.add_argument("--sites-dir", required=True)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--repos", default=",".join(HELD_OUT_REPOS))
    ap.add_argument("--no-blast", action="store_true")
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for repo in [r for r in args.repos.split(",") if r]:
        r = check_repo(repo, args.apps_dir, args.sites_dir, args.out_dir, not args.no_blast)
        print(f"[{repo}] {'ok' if ok(r) else 'MISMATCH'}: " + json.dumps({k: r[k] for k in (
            "installed", "reported_summaries", "coverage", "auto_weight", "ranking_identical", "questions",
            "matches_reported", "blast_identical", "seconds")}), flush=True)
        results.append(r)
    (args.out_dir / "equivalence.json").write_text(json.dumps(results, indent=1) + "\n")
    (args.out_dir / "EQUIVALENCE.md").write_text(render(results))
    print(render(results))
    return 0 if all(ok(r) for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
