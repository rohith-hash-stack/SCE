"""Entry-to-sink slice investigation (read-only; does not change Prism).

1. entry detection (framework-agnostic): a function with a non-builtin
   decorator and no in-repo CALLS/INSTANTIATES predecessor.
2. upstream traversal cost (index time, all functions) and per-query cost.
3. score-propagation simulation over Prism's own BM25 (NB, relative paths):
     down  score(f) = bm25(f) + a * max bm25(entry ancestors of f)
     up    score(e) = bm25(e) + a * max bm25(descendants of e within 3)
     path  score(f) = bm25(f) + a * max bm25(any caller ancestor of f within 3)
   on all 50 frozen questions.
"""
import json
import os
import sys
import time
from collections import deque

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from harness.experiments.heldout_retrieval import run as R  # noqa: E402
from prism.parser.lang_config import collect_decorator_texts  # noqa: E402

H = os.environ.get("HELDOUT_WORKDIR", os.path.dirname(os.path.abspath(__file__)))  # holds reposdir/, sitesdir/, out3/
DATA = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.json")))
BUILTIN_DECOS = ("property", "staticmethod", "classmethod", "cached_property", "abstractmethod", "wraps",
                 "functools.", "dataclass", "contextmanager", "asynccontextmanager", "overload", "lru_cache", "cache")
EDGE = ("CALLS", "INSTANTIATES")
MAX_UP = 3
OUT = {}


def callers(g, n, funcs):
    return [u for u, _, d in g.in_edges(n, data=True) if d.get("relation", "CALLS") in EDGE and u in funcs]


def callees(g, n, funcs):
    return [v for _, v, d in g.out_edges(n, data=True) if d.get("relation", "CALLS") in EDGE and v in funcs]


def decorators(c, q):
    node = c.builder.def_node(q)
    sym = c.builder.symbol_table.get(q)
    parsed = c.builder.parsed_file(sym.file) if sym else None
    if node is None or parsed is None:
        return []
    return [t.lstrip("@").strip() for t in collect_decorator_texts(node, parsed)]


def framework_decorated(decos):
    return [d for d in decos if not any(d.split("(")[0].endswith(b) or d.startswith(b) for b in BUILTIN_DECOS)]


def upstream(g, n, funcs, entries, limit=MAX_UP):
    """{ancestor: distance} within `limit` caller hops, and the entry ancestors among them."""
    seen, q = {n: 0}, deque([n])
    while q:
        x = q.popleft()
        if seen[x] >= limit:
            continue
        for u in callers(g, x, funcs):
            if u not in seen:
                seen[u] = seen[x] + 1
                q.append(u)
    seen.pop(n)
    return seen, {a: d for a, d in seen.items() if a in entries}


def downstream(g, n, funcs, limit=MAX_UP):
    seen, q = {n: 0}, deque([n])
    while q:
        x = q.popleft()
        if seen[x] >= limit:
            continue
        for v in callees(g, x, funcs):
            if v not in seen:
                seen[v] = seen[x] + 1
                q.append(v)
    seen.pop(n)
    return seen


for repo in ("microblog", "realworld"):
    c = R.Corpus(repo, f"{H}/reposdir/{repo}", f"{H}/sitesdir/{repo}", f"{H}/out3/summaries_{repo}.db")
    g = c.builder.graph
    funcs = {s.qualified_name for s in c.pool if s.kind in ("function", "method")}
    info = {"functions": len(funcs), "graph_nodes": g.number_of_nodes(), "graph_edges": g.number_of_edges()}
    t = time.perf_counter()
    decos = {q: decorators(c, q) for q in funcs}
    fw = {q: framework_decorated(d) for q, d in decos.items()}
    entries = {q for q in funcs if fw[q] and not callers(g, q, funcs)}
    roots = {q for q in funcs if not callers(g, q, funcs)}
    info["entry_detect_ms"] = round((time.perf_counter() - t) * 1000, 1)
    info["entries"] = len(entries)
    info["undecorated_roots"] = len(roots - entries)
    info["decorated_but_called"] = sorted(q for q in funcs if fw[q] and q not in entries)
    info["entry_decorator_heads"] = sorted({d.split("(")[0] for q in entries for d in fw[q]})
    t = time.perf_counter()
    up = {q: upstream(g, q, funcs, entries) for q in funcs}
    down = {q: downstream(g, q, funcs) for q in funcs}
    info["traverse_all_ms"] = round((time.perf_counter() - t) * 1000, 1)
    info["with_entry_ancestor"] = sum(1 for q in funcs if up[q][1])
    info["is_or_reaches_entry"] = sum(1 for q in funcs if q in entries or up[q][1])
    gold = {g_ for q in DATA["questions"] if q["repo"] == repo for g_ in q["gold"]}
    info["gold_entry_status"] = {
        g_.split("app.", 1)[-1]: ("ENTRY" if g_ in entries else
                                  {a.split("app.", 1)[-1]: d for a, d in sorted(up[g_][1].items(), key=lambda x: x[1])} or "none")
        for g_ in sorted(gold) if g_ in funcs
    }
    OUT[repo] = {"graph": info}

    # ---- simulation ----
    p = R.PrismLexical(c)
    ix = p.ix
    bm = ix.bm25["NB"]
    from harness.experiments.query_to_evidence.nlq.lexical import tokenize
    sims = {}
    for q in [q for q in DATA["questions"] if q["repo"] == repo]:
        sc = bm.scores(tokenize(q["question"]))
        base = {ix.names[i]: s for i, s in sc.items()}
        t = time.perf_counter()
        variants = {"base": dict(base)}
        for a in (0.25, 0.5, 1.0):
            down_v, up_v, path_v = dict(base), dict(base), dict(base)
            for n in funcs:
                ent = up[n][1]
                if ent:
                    down_v[n] = base.get(n, 0) + a * max(base.get(e, 0) for e in ent)
                anc = up[n][0]
                if anc:
                    path_v[n] = base.get(n, 0) + a * max(base.get(x, 0) for x in anc)
                if n in entries and down[n]:
                    up_v[n] = base.get(n, 0) + a * max(base.get(x, 0) for x in down[n])
            variants[f"down@{a}"], variants[f"up@{a}"], variants[f"path@{a}"] = down_v, up_v, path_v
        query_ms = (time.perf_counter() - t) * 1000 / (len(variants) - 1)
        order = {n: i for i, n in enumerate(ix.names)}
        for name, scores in variants.items():
            ranking = [n for n in sorted((n for n, s in scores.items() if s > 0), key=lambda n: (-scores[n], order[n]))]
            s = R.score(ranking, q["gold"], q["decoys"])
            s["ms"] = query_ms
            sims.setdefault(name, {})[q["id"]] = s
    OUT[repo]["sims"] = sims

summary = {}
for name in OUT["microblog"]["sims"]:
    rows = [OUT[r]["sims"][name][q] for r in OUT for q in OUT[r]["sims"][name]]
    summary[name] = R.aggregate(rows)
OUT["summary"] = summary
json.dump(OUT, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "slice_investigation_results.json"), "w"), indent=1, default=str)
for repo in ("microblog", "realworld"):
    gi = OUT[repo]["graph"]
    print(repo, {k: v for k, v in gi.items() if k not in ("gold_entry_status", "decorated_but_called")})
    print("  decorated_but_called:", gi["decorated_but_called"])
    for k, v in gi["gold_entry_status"].items():
        print("   ", k, "->", v)
for name, agg in summary.items():
    print(f"{name:10s}", agg)
