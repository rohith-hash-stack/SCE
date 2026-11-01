"""Post-hoc blast diagnostics (secondary): P@9, R@20, matched length, and what every named function is."""
import json, re, statistics, sys
from collections import Counter, defaultdict
sys.path[:0] = ["/home/user/SCE", "/home/user/SCE/src"]
from load import answers, items, tasks
from harness.experiments.suite.blast import _match
from harness.experiments.suite import stats
A, T = answers("blast"), tasks("blast")
FUN = json.load(open("blast_functions.json"))
leafidx = {r: defaultdict(list) for r in FUN}
for r, qs in FUN.items():
    for q in qs: leafidx[r][q.rsplit(".", 1)[-1]].append(q)
def resolve(repo, name):
    a = re.sub(r"\(.*$", "", (name or "").strip().strip("`")).replace("::", ".")
    leaf = a.rsplit(".", 1)[-1]
    return [q for q in leafidx[repo].get(leaf, []) if _match(a, q)]
def kind_of(t, name):
    if any(_match(name, g) for g in t["gold"]): return "traced"
    if any(_match(name, c) for c in t["prism_callers"]): return "static_caller"
    if resolve(t["repo"], name): return "exists_not_caller"
    return "does_not_exist"
def hits(t, names):
    left, out = set(t["gold"]), []
    for a in names:
        g = next((g for g in sorted(left) if _match(a, g)), None); out.append(g is not None); left.discard(g)
    return out
SEEDS = ("42", "1001", "2002")
cut = {tid for (tid, v, s), a in A.items() if v in ("GREP_PAD", "PRISM") and s in SEEDS and a.get("done_reason") == "length"}
keep = sorted(t for t in T if t not in cut)
def metric_table(fn):
    res = {}
    for s in SEEDS + ("all",):
        ss = SEEDS if s == "all" else (s,)
        va, vb = [], []
        for tid in keep:
            t = T[tid]
            pa = [fn(t, [i.get("function", "") for i in items(A[(tid, "GREP_PAD", x)]["raw"])]) for x in ss]
            pb = [fn(t, [i.get("function", "") for i in items(A[(tid, "PRISM", x)]["raw"])]) for x in ss]
            pa, pb = [x for x in pa if x is not None], [x for x in pb if x is not None]
            if len(pa) == len(ss) and len(pb) == len(ss):
                va.append(statistics.mean(pa)); vb.append(statistics.mean(pb))
        w = stats.wilcoxon(va, vb); b = stats.bootstrap_diff(va, vb)
        res[s] = (len(va), round(statistics.mean(va), 3), round(statistics.mean(vb), 3), round(b["mean"], 3), round(b["low"], 3), round(b["high"], 3), w["p"] if w else 1.0)
    return res
def p_at(k):
    def f(t, names):
        h = hits(t, names)[:k]
        return sum(h) / len(h) if h else None  # precision of what was named, among the first k
    return f
def r_at(k):
    return lambda t, names: sum(hits(t, names)[:k]) / len(t["gold"])
out = {"tasks_used": len(keep), "tasks_cut": len(cut)}
for name, fn in (("P@9 (traced)", p_at(9)), ("R@20", r_at(20)), ("R@9", r_at(9)), ("recall (all names)", r_at(10**6))):
    out[name] = metric_table(fn)
# what every named function is, per version (seed-pooled, all 76 tasks)
cls = {v: Counter() for v in ("GREP", "GREP_PAD", "PRISM")}
for (tid, v, s), a in A.items():
    if v in cls and s in SEEDS:
        for i in items(a.get("raw", "")): cls[v][kind_of(T[tid], i.get("function", ""))] += 1
out["classes"] = {v: dict(c) for v, c in cls.items()}
# PRISM's names beyond the padded answer's length, per task and seed
extra = Counter(); per_task = []
for tid in sorted(T):
    t = T[tid]
    for s in SEEDS:
        pn = [i.get("function", "") for i in items(A[(tid, "PRISM", s)]["raw"])]
        k = len(items(A[(tid, "GREP_PAD", s)]["raw"]))
        c = Counter(kind_of(t, x) for x in pn[k:])
        extra.update(c)
        if s == "42": per_task.append((tid, len(pn), k, dict(c)))
out["prism_extra_names"] = dict(extra)
# how closely PRISM's answer follows its own list: share of PRISM's named functions that are on its list, in order
follow = []
for tid in sorted(T):
    pn = [i.get("function", "") for i in items(A[(tid, "PRISM", "42")]["raw"])]
    if pn:
        follow.append(sum(any(_match(x, c) for c in T[tid]["prism_callers"]) for x in pn) / len(pn))
out["prism_names_from_its_list"] = round(statistics.mean(follow), 3)
out["named_median"] = {v: statistics.median(len(items(A[(tid, v, "42")]["raw"])) for tid in T) for v in ("GREP_PAD", "PRISM")}
out["named_mean"] = {v: round(statistics.mean(len(items(A[(tid, v, "42")]["raw"])) for tid in T), 1) for v in ("GREP_PAD", "PRISM")}
json.dump({**out, "audit_sample": per_task}, open("blast_diag.json", "w"), indent=1)
for k, v in out.items(): print(k, v)
