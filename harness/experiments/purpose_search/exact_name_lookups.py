"""Exact-name lookups on the shipped `prism.ask` path: each set-4 gold name asked
bare (`save_session`) and as `Class.method`, 104 queries, ranked by BASE, by FIELD w=0.5
and as served (FIELD w=0.5 + the exact-name pin), with the committed set-4 summaries.

Usage: python -m harness.experiments.purpose_search.exact_name_lookups APPS_DIR SITES_DIR OUT_JSON
"""
import json
import statistics as st
import sys
from pathlib import Path
from harness.experiments.purpose_search.run import load_repo
from prism.query.nlq import NLQIndex, rule_intent
from prism.summaries.index import index_path
from prism.summaries.pipeline import build_index

APPS, SITES, OUT = sys.argv[1:4]
ds = json.load(open("harness/experiments/heldout_retrieval/set4/dataset.json"))
rows = []
for repo in ("flask", "httpie", "fastify", "zod"):
    b, _m, _e, root, _ = load_repo(repo, APPS, SITES, Path(OUT).parent)
    cache = json.load(open(f"reports/purpose_search_set4/summary_cache_{repo}.json"))
    path = index_path(root)
    build_index(b, root, from_cache=cache)
    base, prod = NLQIndex(b, root), NLQIndex(b, root, summaries="auto")
    path.unlink()
    for q in ds["questions"]:
        if q["repo"] != repo:
            continue
        for g in q["gold"]:
            for query in (g.split(".")[-1], ".".join(g.split(".")[-2:])):
                rb, rf = base.ranked(query), prod.ranked(query)
                route, pins, served, _ = prod.served(query, rule_intent(query))
                rank = lambda r: r.index(g) + 1 if g in r else None
                rows.append({"repo": repo, "query": query, "gold": g, "route": route, "pins": len(pins),
                             "base": rank(rb), "field": rank(rf), "served": rank(served)})
top = lambda k, n: sum(1 for r in rows if r[k] and r[k] <= n) / len(rows)
mrr = lambda k: st.mean(1 / r[k] if r[k] else 0 for r in rows)
out = {"n": len(rows), **{f"{k}": {"top1": round(top(k, 1), 3), "top5": round(top(k, 5), 3), "mrr": round(mrr(k), 3)}
                         for k in ("base", "field", "served")},
       "pins_mean": round(st.mean(r["pins"] for r in rows), 2), "pins_max": max(r["pins"] for r in rows),
       "served_not_top1": [r for r in rows if r["served"] != 1]}
print(json.dumps({k: v for k, v in out.items() if k != "served_not_top1"}, indent=1))
for r in out["served_not_top1"]:
    print("  not top-1:", r)
json.dump(out, open(OUT, "w"), indent=1)
