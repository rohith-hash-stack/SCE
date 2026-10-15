"""Held-out retrieval benchmark: Prism triad vs. lexical and embedding
baselines on two repositories Prism was never tuned on.

Usage:
  python -m harness.experiments.heldout_retrieval.run --repos-dir R --sites-dir S --out OUT [--check-names]
                                                      [--embed-model NAME]

`R/<repo>` is a checkout of each dataset repo at its pinned commit; `S/<repo>`
a `pip install --target` directory holding that repo's dependencies (for
Prism's depth-1 dependency summaries). Every system ranks the same candidate
unit: each indexed non-test function, method or class (Prism's lexical pool),
named `module.Class.function`.

Systems:
  ripgrep    real `rg -i -F -w` per question keyword; a line counts for the
             innermost candidate containing it; rank by distinct keywords
             matched, then total matches.
  bm25       BM25Okapi (rank_bm25) over each candidate's source, plain
             identifier-split tokens, no stemming - independent of Prism.
  bm25_stem  post-hoc ablation, added after the first run: the same BM25Okapi
             with Prism's tokenizer (one-suffix stemming, Prism's stop words)
             over qualified name + source - what a stemmed BM25 alone does.
  prism_bm25 Prism's own lexical leg alone (NB documents, repo-relative
             paths) - the triad's stems leg with no action/capability legs.
  prism_triad
             the same BM25 re-ranked by the triad (stems x action x
             capability, index-time capability profiles with dependency
             summaries); the intent comes from the question text by the
             deterministic `rule_intent` - no per-question annotation.
  prism_triad_att
             prism_triad with internal-hop attenuation of the capability
             leg (`hop_gamma` = HOP_GAMMA): the function making the I/O call
             scores in full, each caller above it HOP_GAMMA times less.
  prism_triad_oracle / prism_triad_oracle_att
             the triad without / with attenuation, the intent read from
             `intent_oracle.json` (annotated per question, frozen before
             these runs) instead of `rule_intent`.
  The arms above keep the former OPAQUE/PROXY treatment (capability weight
  moved onto the action leg, `unknown_to_action=True`) so round 1-2 results
  reproduce. Round 3 (fixes designed after reading this set's failures, so
  in-sample, not held-out):
  prism_triad_neutral     prism_triad with neutral unknown capability
  prism_triad_classprior  prism_triad + a class never outranks its own best member
  prism_triad_nomigr      prism_triad + migration files below every other hit
  prism_triad_fixed       all three; prism_triad_oracle_fixed the same with the oracle intent
  embedding  sentence-transformers cosine similarity (question vs. candidate
             source); recorded as not run when the model cannot be loaded.

Metrics (per system, overall / per repo / per category):
  top1        top-ranked candidate is a gold function
  recall@5    mean fraction of gold functions in the top 5
  mrr         mean reciprocal rank of the best-ranked gold (0 if unranked)
  separation  best gold ranked above every decoy (unranked = last)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.json"
SYSTEMS = ("ripgrep", "bm25", "bm25_stem", "prism_bm25", "prism_triad", "prism_triad_att", "prism_triad_oracle",
           "prism_triad_oracle_att", "prism_triad_neutral", "prism_triad_classprior", "prism_triad_nomigr",
           "prism_triad_fixed", "prism_triad_oracle_fixed", "embedding")
INTENT_ORACLE = HERE / "intent_oracle.json"
ORACLE_PATH = [INTENT_ORACLE]  # `--oracle` overrides
HOP_GAMMA = 0.5
_STOP = set(
    "a an the of in on to and or is are be by for from with what which where how when does do did its it if "
    "i my our we that this those these into out up as at whose has have there their them they who app code "
    "function functions method".split()
)


def words(text: str) -> list[str]:
    """Identifier-split, lower-cased tokens (snake_case and camelCase split), no stemming."""
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        out += [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", w) or [w]]
    return out


def keywords(question: str) -> list[str]:
    seen = []
    for w in words(question):
        if w not in _STOP and len(w) > 2 and w not in seen:
            seen.append(w)
    return seen


# --------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------- #
class Corpus:
    def __init__(self, name: str, root: str, site: str, store: str):
        sys.path.insert(0, str(HERE.parents[2]))
        from harness.experiments.query_to_evidence.run import build_engine
        from harness.experiments.query_to_evidence.nlq.lexical import candidate_pool
        from prism.deps import DependencyIndex, SummaryStore

        self.name, self.root = name, root
        t = time.perf_counter()
        self.engine = build_engine(root, dependencies=DependencyIndex(SummaryStore(store), paths=[site]))
        self.build_s = time.perf_counter() - t
        self.builder = self.engine.builder
        self.pool = candidate_pool(self.builder)
        self.names = [s.qualified_name for s in self.pool]
        self._lines: dict[str, list[str]] = {}

    def path(self, sym) -> str:
        return sym.file if os.path.isabs(sym.file) else os.path.join(self.root, sym.file)

    def source(self, sym) -> str:
        p = self.path(sym)
        if p not in self._lines:
            self._lines[p] = open(p, encoding="utf-8", errors="replace").read().splitlines()
        return "\n".join(self._lines[p][sym.line_range[0] - 1:sym.line_range[1]])

    def innermost(self) -> dict[str, list[tuple[int, int, str]]]:
        """{absolute file: [(start, end, qname)], smallest span first}."""
        spans: dict[str, list] = {}
        for s in self.pool:
            spans.setdefault(os.path.realpath(self.path(s)), []).append((s.line_range[0], s.line_range[1], s.qualified_name))
        for v in spans.values():
            v.sort(key=lambda x: x[1] - x[0])
        return spans


# --------------------------------------------------------------------- #
# Systems
# --------------------------------------------------------------------- #
def rank_ripgrep(c: Corpus, question: str) -> list[str]:
    spans = c.innermost()
    distinct: dict[str, set] = {}
    total: dict[str, int] = {}
    for kw in keywords(question):
        proc = subprocess.run(
            ["rg", "-i", "-F", "-w", "--no-heading", "--line-number", "--with-filename", "-g", "*.py", kw, c.root],
            capture_output=True, text=True,
        )
        for line in proc.stdout.splitlines():
            m = re.match(r"(.+?):(\d+):", line)
            if not m:
                continue
            f, n = os.path.realpath(m.group(1)), int(m.group(2))
            owner = next((q for a, b, q in spans.get(f, ()) if a <= n <= b), None)
            if owner is not None:
                distinct.setdefault(owner, set()).add(kw)
                total[owner] = total.get(owner, 0) + 1
    order = {n: i for i, n in enumerate(c.names)}
    return sorted(distinct, key=lambda q: (-len(distinct[q]), -total[q], order[q]))


class IndependentBM25:
    def __init__(self, c: Corpus):
        from rank_bm25 import BM25Okapi
        self.c = c
        self.bm25 = BM25Okapi([words(s.qualified_name) + words(c.source(s)) for s in c.pool])

    def rank(self, question: str) -> list[str]:
        q = keywords(question)
        scores = self.bm25.get_scores(q)
        idx = [i for i in range(len(scores)) if scores[i] > 0]
        return [self.c.names[i] for i in sorted(idx, key=lambda i: (-scores[i], i))]


class StemmedBM25:
    def __init__(self, c: Corpus):
        from harness.experiments.query_to_evidence.nlq.lexical import tokenize
        from rank_bm25 import BM25Okapi
        self.c, self.tokenize = c, tokenize
        self.bm25 = BM25Okapi([tokenize(s.qualified_name) + tokenize(c.source(s)) for s in c.pool])

    def rank(self, question: str) -> list[str]:
        scores = self.bm25.get_scores(self.tokenize(question))
        idx = [i for i in range(len(scores)) if scores[i] > 0]
        return [self.c.names[i] for i in sorted(idx, key=lambda i: (-scores[i], i))]


class PrismLexical:
    def __init__(self, c: Corpus):
        from harness.experiments.query_to_evidence.nlq.lexical import LexicalIndex, TriadContext
        self.ix = LexicalIndex(c.builder, c.root, relative_paths=True, triad=TriadContext(c.builder, c.root),
                               member_over_class=True, demote_migrations=True)
        self.plain, self.attenuated = self.ix.triad, TriadContext(c.builder, c.root, hop_gamma=HOP_GAMMA)
        self.oracle = {k: {f: v for f, v in i.items() if v} or None
                       for k, i in json.loads(Path(ORACLE_PATH[0]).read_text())["intents"].items()}

    def intent(self, q: dict, source: str) -> dict | None:
        from harness.experiments.query_to_evidence.nlq.triad_eval import rule_intent
        return {"none": lambda: None, "rule": lambda: rule_intent(q["question"]), "oracle": lambda: self.oracle[q["id"]]}[source]()

    def rank(self, q: dict, source: str = "none", attenuate: bool = False, fixes: frozenset = frozenset(),
             hits: int = 0):
        """`fixes` (round 3): "neutral" (unknown capability neutral), "class"
        (member over class), "migrations" (demote migration files). Without
        "neutral" the former unknown-to-action shift applies."""
        ctx = self.attenuated if attenuate else self.plain
        saved = (ctx.unknown_to_action, self.ix.member_over_class, self.ix.demote_migrations)
        ctx.unknown_to_action = "neutral" not in fixes
        self.ix.member_over_class, self.ix.demote_migrations = "class" in fixes, "migrations" in fixes
        self.ix.triad = ctx
        try:
            if hits:
                return self.ix.hits(q["question"], "NB", hits, self.intent(q, source))
            return self.ix.ranked(q["question"], "NB", self.intent(q, source))
        finally:
            ctx.unknown_to_action, self.ix.member_over_class, self.ix.demote_migrations = saved
            self.ix.triad = self.plain


class Embedding:
    def __init__(self, c: Corpus, model_name: str):
        from sentence_transformers import SentenceTransformer
        self.c, self.model = c, SentenceTransformer(model_name, device="cpu")
        docs = [f"{s.qualified_name}\n{c.source(s)}" for s in c.pool]
        self.emb = self.model.encode(docs, normalize_embeddings=True, batch_size=16)

    def rank(self, question: str) -> list[str]:
        qv = self.model.encode([question], normalize_embeddings=True)[0]
        sims = self.emb @ qv
        return [self.c.names[i] for i in sorted(range(len(sims)), key=lambda i: (-sims[i], i))]


# --------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------- #
def score(ranking: list[str], gold: list[str], decoys: list[str]) -> dict:
    pos = {n: i + 1 for i, n in enumerate(ranking)}
    gold_ranks = [pos.get(g) for g in gold]
    best_gold = min((r for r in gold_ranks if r), default=math.inf)
    best_decoy = min((pos[d] for d in decoys if d in pos), default=math.inf)
    return {
        "top1": bool(ranking) and ranking[0] in gold,
        "recall@5": sum(1 for r in gold_ranks if r and r <= 5) / len(gold),
        "rr": 0.0 if best_gold == math.inf else 1.0 / best_gold,
        "separation": best_gold < best_decoy,
        "gold_ranks": gold_ranks,
        "best_decoy_rank": None if best_decoy == math.inf else best_decoy,
        "top5": ranking[:5],
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n": n,
        "top1": round(sum(r["top1"] for r in rows) / n, 3),
        "recall@5": round(sum(r["recall@5"] for r in rows) / n, 3),
        "mrr": round(sum(r["rr"] for r in rows) / n, 3),
        "separation": round(sum(r["separation"] for r in rows) / n, 3),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", required=True)
    ap.add_argument("--sites-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--check-names", action="store_true", help="only verify every gold/decoy name is a candidate")
    ap.add_argument("--embed-model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--dataset", default=str(DATASET), help="question set (default: set 1)")
    ap.add_argument("--oracle", default=str(INTENT_ORACLE), help="intent oracle for --dataset")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.dataset).read_text())
    ORACLE_PATH[0] = args.oracle
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {"dataset_version": data["version"], "systems": {}, "per_question": {}, "notes": {}}
    for repo in sorted({q["repo"] for q in data["questions"]}):
        qs = [q for q in data["questions"] if q["repo"] == repo]
        c = Corpus(repo, os.path.join(args.repos_dir, repo), os.path.join(args.sites_dir, repo),
                   str(out / f"summaries_{repo}.db"))
        results["notes"][f"{repo}_pool"] = len(c.names)
        results["notes"][f"{repo}_build_s"] = round(c.build_s, 1)
        missing = sorted({n for q in qs for n in q["gold"] + q["decoys"]} - set(c.names))
        if args.check_names:
            print(repo, "pool", len(c.names), "missing", missing)
            continue
        if missing:
            raise SystemExit(f"{repo}: dataset names not in the candidate pool: {missing}")
        bm25, bm25_stem = IndependentBM25(c), StemmedBM25(c)
        runners = {
            "ripgrep": lambda q, c=c: rank_ripgrep(c, q["question"]),
            "bm25": lambda q: bm25.rank(q["question"]),
            "bm25_stem": lambda q: bm25_stem.rank(q["question"]),
        }
        prism = PrismLexical(c)
        runners["prism_bm25"] = lambda q: prism.rank(q)
        runners["prism_triad"] = lambda q: prism.rank(q, "rule")
        runners["prism_triad_att"] = lambda q: prism.rank(q, "rule", attenuate=True)
        runners["prism_triad_oracle"] = lambda q: prism.rank(q, "oracle")
        runners["prism_triad_oracle_att"] = lambda q: prism.rank(q, "oracle", attenuate=True)
        ALL = frozenset({"neutral", "class", "migrations"})
        runners["prism_triad_neutral"] = lambda q: prism.rank(q, "rule", fixes=frozenset({"neutral"}))
        runners["prism_triad_classprior"] = lambda q: prism.rank(q, "rule", fixes=frozenset({"class"}))
        runners["prism_triad_nomigr"] = lambda q: prism.rank(q, "rule", fixes=frozenset({"migrations"}))
        runners["prism_triad_fixed"] = lambda q: prism.rank(q, "rule", fixes=ALL)
        runners["prism_triad_oracle_fixed"] = lambda q: prism.rank(q, "oracle", fixes=ALL)
        try:
            embedding = Embedding(c, args.embed_model)
            runners["embedding"] = lambda q: embedding.rank(q["question"])
        except Exception as exc:  # model weights unavailable offline
            results["notes"]["embedding"] = f"not run: {type(exc).__name__}: {str(exc)[:200]}"
        for q in qs:
            row = results["per_question"].setdefault(q["id"], {"repo": repo, "category": q["category"],
                                                                "intent": None, "systems": {}})
            row["intent"] = prism.intent(q, "rule")
            row["oracle_intent"] = prism.intent(q, "oracle")
            row["fixed_hits"] = prism.rank(q, "rule", fixes=ALL, hits=5)
            for system, run in runners.items():
                t = time.perf_counter()
                ranking = run(q)
                s = score(ranking, q["gold"], q["decoys"])
                s["ms"] = round((time.perf_counter() - t) * 1000, 1)
                row["systems"][system] = s
    if args.check_names:
        return 0
    for system in SYSTEMS:
        rows = [(r, r["systems"][system]) for r in results["per_question"].values() if system in r["systems"]]
        if not rows:
            continue
        agg = {"overall": aggregate([s for _, s in rows])}
        for key in ("repo", "category"):
            for val in sorted({r[key] for r, _ in rows}):
                agg[f"{key}:{val}"] = aggregate([s for r, s in rows if r[key] == val])
        results["systems"][system] = agg
    (out / "results.json").write_text(json.dumps(results, indent=1))
    for system, agg in results["systems"].items():
        o = agg["overall"]
        print(f"{system:12s} top1 {o['top1']:.3f}  recall@5 {o['recall@5']:.3f}  mrr {o['mrr']:.3f}  separation {o['separation']:.3f}")
    for k, v in results["notes"].items():
        print(f"note {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
