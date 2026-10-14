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
  prism_bm25 Prism's own lexical leg alone (NB documents, repo-relative
             paths) - the triad's stems leg with no action/capability legs.
  prism_triad
             the same BM25 re-ranked by the triad (stems x action x
             capability, index-time capability profiles with dependency
             summaries); the intent comes from the question text by the
             deterministic `rule_intent` - no per-question annotation.
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
SYSTEMS = ("ripgrep", "bm25", "prism_bm25", "prism_triad", "embedding")
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


class PrismLexical:
    def __init__(self, c: Corpus):
        from harness.experiments.query_to_evidence.nlq.lexical import LexicalIndex, TriadContext
        self.ix = LexicalIndex(c.builder, c.root, relative_paths=True, triad=TriadContext(c.builder, c.root))

    def rank(self, question: str, triad: bool) -> tuple[list[str], dict | None]:
        from harness.experiments.query_to_evidence.nlq.triad_eval import rule_intent
        intent = rule_intent(question) if triad else None
        return self.ix.ranked(question, "NB", intent), intent


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
    args = ap.parse_args(argv)

    data = json.loads(DATASET.read_text())
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
        runners = {
            "ripgrep": lambda q, c=c: rank_ripgrep(c, q),
            "bm25": IndependentBM25(c).rank,
        }
        prism = PrismLexical(c)
        runners["prism_bm25"] = lambda q: prism.rank(q, triad=False)[0]
        runners["prism_triad"] = lambda q: prism.rank(q, triad=True)[0]
        try:
            runners["embedding"] = Embedding(c, args.embed_model).rank
        except Exception as exc:  # model weights unavailable offline
            results["notes"]["embedding"] = f"not run: {type(exc).__name__}: {str(exc)[:200]}"
        for q in qs:
            row = results["per_question"].setdefault(q["id"], {"repo": repo, "category": q["category"],
                                                                "intent": None, "systems": {}})
            row["intent"] = prism.rank(q["question"], triad=True)[1]
            for system, run in runners.items():
                t = time.perf_counter()
                ranking = run(q["question"])
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
