"""Usage: python harness/experiments/retrieval_pivot/seed_expand.py old APPS OUT.pkl | new SET6_APPS OUT.pkl

Seed-then-expand diagnostic: V2 search seeds, 1-hop CALLS neighbourhood, and how its members rank
under (a) summary BM25, (b) code BM25 (the search score), (c) no text (closeness to the top seed)."""
import json, sys, pickle, collections as C
from pathlib import Path
from prism.cli import build_pipeline
from prism.query.nlq import NLQIndex, tokenize, BM25
from prism.summaries.index import read_index, search_text, SUMMARY_WEIGHT

def run(sets, apps, summ, out):
    rows = []
    for s, dsp in sets:
        ds = json.load(open(dsp))
        for repo, spec in ds["repos"].items():
            root = str(Path(apps) / repo / spec["root"]); b = build_pipeline(root, use_cache=False)[0]
            d = read_index(Path(summ) / repo / "v2.json"); texts = {q: search_text(e, d["pipeline"]) for q, e in d["functions"].items()}
            idx = NLQIndex(b, root, summaries=texts, summary_weight=SUMMARY_WEIGHT)
            G = b.graph
            calls = C.defaultdict(set)
            for u, v, a in G.edges(data=True):
                if a.get("relation") == "CALLS": calls[u].add(v); calls[v].add(u)
            names = list(idx.names); pos = {n: i for i, n in enumerate(names)}
            sbm = BM25([tokenize(texts.get(n, "")) for n in names])
            for q in (q for q in ds["questions"] if q["repo"] == repo):
                ranked = idx.ranked(q["question"]); rank = {n: i + 1 for i, n in enumerate(ranked)}
                seeds = ranked[:5]
                nb = set().union(*(calls[x] for x in seeds)) - set(seeds)
                nb = {n for n in nb if n in pos}
                qt = tokenize(q["question"]); ss = sbm.scores(qt)
                best_gold = min((rank.get(g, 10**6) for g in q["gold"]))
                gin = [g for g in q["gold"] if g in nb]
                def order(key): return sorted(nb, key=key)
                ords = {"summary": order(lambda n: -ss[pos[n]]), "code": order(lambda n: rank.get(n, 10**6)),
                        "structure": order(lambda n: (min((i for i, x in enumerate(seeds) if n in calls[x]), default=9), rank.get(n, 10**6)))}
                rows.append({"set": s, "id": q["id"], "best": best_gold, "nb": len(nb), "gold_in_nb": bool(gin),
                             "nb_rank": {k: (min(o.index(g) + 1 for g in gin) if gin else None) for k, o in ords.items()}})
    pickle.dump(rows, open(out, "wb"))

H = "harness/experiments/heldout_retrieval"
if sys.argv[1] == "old":
    run([(s, f"{H}/{s}/dataset.json") for s in ("set2", "set3", "set4", "set5")], sys.argv[2], "reports/summary_candidate_v5/stage1", sys.argv[3])
else:
    run([("set6", f"{H}/set6/dataset.json")], sys.argv[2], "reports/summary_candidate_v5/set6", sys.argv[3])
