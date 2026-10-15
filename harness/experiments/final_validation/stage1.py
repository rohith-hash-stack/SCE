"""Stage 1 of the two-stage validation: a retrieval tournament over the
frozen held-out question sets (`harness/experiments/heldout_retrieval/`:
set 1 and set 2 in Python, set 3 in JavaScript/TypeScript).

Every engine ranks the same candidates - Prism's lexical pool, each indexed
non-test function, method or class - and is scored with the held-out
runner's metrics (top-1, recall@5, MRR, gold-over-decoy separation).

Engines:
  plain_bm25          BM25Okapi, identifier-split tokens, no stemming
  stemmed_bm25        the same with Prism's tokenizer (one-suffix stemming)
  dense_jina_code     jinaai/jina-embeddings-v2-base-code cosine similarity
  hybrid_rrf_rerank   BM25 (k1=1.5, b=0.75) + jina, RRF (k=60) over the top 100
                      of each, bge-reranker-base over the top 50 (harness Arm 1)
  prism_rule_intent   prism.query.nlq.NLQIndex.ranked(question), rule intent
  prism_slm_live      the same, the intent {stems, action, capability} read off
                      the question by a live SLM (schema-validated; invalid ->
                      stems-only); stems are appended to the query text
  prism_oracle        the same, the hand-labeled oracle intent
  prism_shipped_ask   NLQIndex.search(question) - the shipped payload with entry
                      breadcrumbs; ranks equal prism_rule_intent by construction
  normal_oracle       gold first - a sanity reference (1.00 by construction)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

ENGINES = (
    "plain_bm25", "stemmed_bm25", "dense_jina_code", "hybrid_rrf_rerank", "prism_rule_intent",
    "prism_slm_live", "prism_oracle", "prism_shipped_ask", "normal_oracle",
)
#: Paired comparisons reported with significance (a vs b).
CONTRASTS = (
    ("prism_slm_live", "hybrid_rrf_rerank"),
    ("prism_slm_live", "stemmed_bm25"),
    ("prism_rule_intent", "stemmed_bm25"),
    ("prism_rule_intent", "hybrid_rrf_rerank"),
    ("prism_oracle", "hybrid_rrf_rerank"),
    ("hybrid_rrf_rerank", "stemmed_bm25"),
    ("dense_jina_code", "stemmed_bm25"),
)
RRF_K, FUSION_DEPTH, RERANK_DEPTH = 60, 100, 50
HERE = Path(__file__).resolve().parent
HELDOUT = HERE.parent / "heldout_retrieval"
#: The frozen sets: (name, dataset, oracle).
QUESTION_SETS = (
    ("set1", HELDOUT / "dataset.json", HELDOUT / "intent_oracle.json"),
    ("set2", HELDOUT / "set2" / "dataset.json", HELDOUT / "set2" / "intent_oracle.json"),
    ("set3", HELDOUT / "set3" / "dataset.json", HELDOUT / "set3" / "intent_oracle.json"),
)


# --------------------------------------------------------------------- #
# Live SLM intent
# --------------------------------------------------------------------- #
SLM_SYSTEM = (
    "You turn a developer's question about a codebase into a search intent. Reply with one JSON object only:\n"
    '{"stems": [up to 8 short lowercase code words likely to appear in the answer\'s identifiers or code],\n'
    ' "action": one of READ, MUTATE, SEND, RECEIVE, EXECUTE, WAIT, CONNECT, or null when the code does nothing physical,\n'
    ' "capability": one of DATABASE_IO, PRIM_NET_CONNECT, PRIM_NET_LISTEN, PRIM_FS_READ, PRIM_FS_WRITE, '
    "PRIM_PROC_SPAWN, PRIM_TIME_WAIT, or null when no external resource is touched}"
)
_STEM = re.compile(r"^[a-z][a-z0-9_]{1,29}$")


def allowed_capabilities() -> frozenset[str]:
    from prism.semantics.bitmask import FeatureBit
    return frozenset({"DATABASE_IO"} | {f.name for f in FeatureBit if f.name.startswith("PRIM_")})


def validate_slm_intent(raw: str) -> tuple[dict | None, list[str]]:
    """`(intent, problems)` from an SLM reply. The intent is `{"stems": [...],
    "action"?: ..., "capability"?: ...}` with every invalid part dropped
    (and named in `problems`); `None` when the reply is not a JSON object."""
    from prism.semantics.triad import PHYSICAL_ACTIONS
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif "{" in text and "}" in text[text.index("{"):]:
        text = text[text.index("{"): text.rindex("}") + 1]
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None, ["not JSON"]
    if not isinstance(obj, dict):
        return None, ["not a JSON object"]
    problems, intent = [], {"stems": []}
    stems = obj.get("stems", [])
    if not isinstance(stems, list):
        problems.append("stems is not a list")
        stems = []
    for s in stems[:8]:
        if isinstance(s, str) and _STEM.match(s.strip().lower()):
            intent["stems"].append(s.strip().lower())
        else:
            problems.append(f"invalid stem {s!r}")
    if len(stems) > 8:
        problems.append("more than 8 stems")
    action, capability = obj.get("action"), obj.get("capability")
    if action not in (None, "", "null"):
        if isinstance(action, str) and action.upper() in PHYSICAL_ACTIONS:
            intent["action"] = action.upper()
        else:
            problems.append(f"invalid action {action!r}")
    if capability not in (None, "", "null"):
        if isinstance(capability, str) and capability.upper() in allowed_capabilities():
            intent["capability"] = capability.upper()
        else:
            problems.append(f"invalid capability {capability!r}")
    return intent, problems


class SLMIntentExtractor:
    """Asks `llm` (a `harness.llm.ChatLLM`-shaped callable) once per question,
    at temperature 0 with a fixed seed, and caches every raw reply in
    `cache_path` (JSONL) so a resumed run never re-asks."""

    def __init__(self, llm: Callable, model_label: str, cache_path: Path | None = None, seed: int = 0):
        self.llm, self.model_label, self.seed = llm, model_label, seed
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path is not None and cache_path.exists():
            for line in cache_path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["key"]] = row

    def key(self, question: str) -> str:
        return hashlib.sha256(f"{self.model_label}\0{SLM_SYSTEM}\0{question}".encode()).hexdigest()[:24]

    def extract(self, question: str) -> dict:
        """`{"intent", "problems", "raw", "latency_s"}` for one question."""
        k = self.key(question)
        if k not in self.cache:
            t = time.perf_counter()
            reply = self.llm(SLM_SYSTEM, question, max_tokens=200, seed=self.seed, purpose="stage1_intent")
            text = getattr(reply, "text", reply)
            row = {"key": k, "question": question, "raw": text, "latency_s": round(time.perf_counter() - t, 3)}
            self.cache[k] = row
            if self.cache_path is not None:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with self.cache_path.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
        row = self.cache[k]
        intent, problems = validate_slm_intent(row["raw"])
        return {"intent": intent, "problems": problems, "raw": row["raw"], "latency_s": row.get("latency_s")}


# --------------------------------------------------------------------- #
# Dense and hybrid rankers over the shared candidate pool
# --------------------------------------------------------------------- #
class DenseRanker:
    def __init__(self, corpus, encoder, cache_dir: Path | None = None):
        import numpy as np
        self.names, self.encoder = corpus.names, encoder
        docs = [f"{s.qualified_name}\n{corpus.source(s)}" for s in corpus.pool]
        path = None
        if cache_dir is not None:
            digest = hashlib.sha256(("\0".join(docs) + encoder.name).encode()).hexdigest()[:16]
            path = cache_dir / f"{corpus.name}-{encoder.name.replace('/', '_')}-{digest}.npy"
        if path is not None and path.exists():
            self.vectors = np.load(path)
        else:
            self.vectors = np.asarray(encoder.encode(docs), dtype=np.float32)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, self.vectors)

    def scores(self, question: str):
        return self.vectors @ self.encoder.encode([question])[0]

    def rank(self, question: str) -> list[str]:
        sims = self.scores(question)
        return [self.names[i] for i in sorted(range(len(sims)), key=lambda i: (-float(sims[i]), i))]


class HybridRanker:
    """Harness Arm 1's recipe on the shared pool: BM25 + dense, RRF, rerank."""

    def __init__(self, corpus, dense: DenseRanker, reranker):
        from rank_bm25 import BM25Okapi

        from harness.arms.arm1_rag import lexical_tokens
        self.corpus, self.dense, self.reranker, self.tokens = corpus, dense, reranker, lexical_tokens
        self.names = corpus.names
        self.docs = [f"{s.qualified_name}\n{corpus.source(s)}" for s in corpus.pool]
        self.bm25 = BM25Okapi([lexical_tokens(d) for d in self.docs], k1=1.5, b=0.75)

    def rank(self, question: str) -> list[str]:
        from harness.arms.arm1_rag import rrf
        sparse_scores = self.bm25.get_scores(self.tokens(question))
        sparse = sorted(range(len(self.names)), key=lambda i: (-sparse_scores[i], i))[:FUSION_DEPTH]
        dense_scores = self.dense.scores(question)
        dense = sorted(range(len(self.names)), key=lambda i: (-float(dense_scores[i]), i))[:FUSION_DEPTH]
        fused = [i for i, _ in rrf([sparse, dense], k=RRF_K)]
        head = fused[:RERANK_DEPTH]
        scores = self.reranker.score(question, [self.docs[i] for i in head])
        order = sorted(range(len(head)), key=lambda j: (-float(scores[j]), j))
        return [self.names[i] for i in [head[j] for j in order] + fused[RERANK_DEPTH:]]


# --------------------------------------------------------------------- #
# The tournament
# --------------------------------------------------------------------- #
SHIPPED_TOP_K = 10


def shipped_ranking(nlq, question: str) -> list[str]:
    """The shipped `NLQIndex.search` payload's top `SHIPPED_TOP_K` hits,
    then the rest of `ranked()` (a breadcrumb walk per candidate would cost
    minutes per question and change no rank). Raises if the payload's order
    disagrees with `ranked()`."""
    head = [h["qname"] for h in nlq.search(question, top_k=SHIPPED_TOP_K)["hits"]]
    full = nlq.ranked(question)
    if full[:len(head)] != head:
        raise AssertionError(f"shipped payload order differs from ranked() for {question!r}")
    return head + full[len(head):]

def sign_test_p(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def stratified_bootstrap(deltas_by_repo: dict[str, list[float]], iters: int = 4000, seed: int = 0) -> tuple[float, float] | None:
    """95% interval of the mean paired delta, resampling questions within repo."""
    rng = random.Random(seed)
    pools = [v for v in deltas_by_repo.values() if v]
    if not pools:
        return None
    means = []
    for _ in range(iters):
        sample = [rng.choice(v) for v in pools for _ in v]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * iters)], means[int(0.975 * iters) - 1]


def contrast(rows: list[dict], a: str, b: str, metric: str = "top1") -> dict | None:
    pairs = [(r, r["systems"][a], r["systems"][b]) for r in rows if a in r["systems"] and b in r["systems"]]
    if not pairs:
        return None
    wins = sum(1 for _, x, y in pairs if x[metric] and not y[metric]) if metric in ("top1", "separation") else None
    losses = sum(1 for _, x, y in pairs if y[metric] and not x[metric]) if metric in ("top1", "separation") else None
    by_repo: dict[str, list[float]] = defaultdict(list)
    for r, x, y in pairs:
        by_repo[r["repo"]].append(float(x[metric]) - float(y[metric]))
    delta = sum(d for v in by_repo.values() for d in v) / len(pairs)
    out = {"a": a, "b": b, "metric": metric, "n": len(pairs), "delta": round(delta, 4),
           "ci95": stratified_bootstrap(by_repo)}
    if wins is not None:
        out.update(wins=wins, losses=losses, sign_p=round(sign_test_p(wins, losses), 4))
    return out


def run_stage1(
    repos_dir: str, sites_dir: str, out_dir: str, engines: tuple[str, ...] = ENGINES,
    slm: SLMIntentExtractor | None = None, encoder_factory: Callable | None = None,
    reranker_factory: Callable | None = None, question_sets=QUESTION_SETS, payload_samples: int = 3,
) -> dict:
    """Run every engine on every frozen question; write `stage1_results.json`
    and return it. `repos_dir/<repo>` must be the pinned checkout (its
    dataset `root`, e.g. `backend/`, already applied); `sites_dir/<repo>`
    the dependency site (may be empty)."""
    from harness.experiments.heldout_retrieval import run as R
    from prism.query.nlq import NLQIndex, rule_intent

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    notes: dict = {}
    encoder = None
    reranker = None
    for set_name, dataset_path, oracle_path in question_sets:
        data = json.loads(Path(dataset_path).read_text())
        oracle = {k: {f: v for f, v in i.items() if v} or None
                  for k, i in json.loads(Path(oracle_path).read_text())["intents"].items()}
        for repo in sorted({q["repo"] for q in data["questions"]}):
            qs = [q for q in data["questions"] if q["repo"] == repo]
            c = R.Corpus(repo, os.path.join(repos_dir, repo), os.path.join(sites_dir, repo), str(out / f"summaries_{repo}.db"))
            missing = sorted({n for q in qs for n in q["gold"] + q["decoys"]} - set(c.names))
            if missing:
                raise SystemExit(f"{set_name}/{repo}: dataset names not in the candidate pool: {missing}")
            notes[f"{repo}_pool"] = len(c.names)
            nlq = NLQIndex(c.builder, c.root)
            runners: dict[str, Callable[[dict], list[str]]] = {}
            plain, stemmed = R.IndependentBM25(c), R.StemmedBM25(c)
            runners["plain_bm25"] = lambda q: plain.rank(q["question"])
            runners["stemmed_bm25"] = lambda q: stemmed.rank(q["question"])
            if {"dense_jina_code", "hybrid_rrf_rerank"} & set(engines):
                if encoder is None:
                    if encoder_factory is not None:
                        encoder = encoder_factory()
                    else:
                        from harness.arms.arm1_rag import JinaCodeEmbedder
                        encoder = JinaCodeEmbedder()
                dense = DenseRanker(c, encoder, out / "embeddings")
                runners["dense_jina_code"] = lambda q, d=dense: d.rank(q["question"])
                if "hybrid_rrf_rerank" in engines:
                    if reranker is None:
                        if reranker_factory is not None:
                            reranker = reranker_factory()
                        else:
                            from harness.arms.arm1_rag import OnnxCrossEncoder
                            reranker = OnnxCrossEncoder()
                    hybrid = HybridRanker(c, dense, reranker)
                    runners["hybrid_rrf_rerank"] = lambda q, h=hybrid: h.rank(q["question"])
            runners["prism_rule_intent"] = lambda q: nlq.ranked(q["question"])
            runners["prism_oracle"] = lambda q: nlq.ranked(q["question"], intent=oracle[q["id"]])
            runners["prism_shipped_ask"] = lambda q: shipped_ranking(nlq, q["question"])
            runners["normal_oracle"] = lambda q: list(q["gold"]) + [n for n in stemmed.rank(q["question"]) if n not in q["gold"]]
            for q in qs:
                row = {"set": set_name, "id": q["id"], "repo": repo, "category": q["category"], "systems": {},
                       "rule_intent": None, "oracle_intent": oracle[q["id"]]}
                row["rule_intent"] = rule_intent(q["question"])
                if "prism_slm_live" in engines and slm is not None:
                    got = slm.extract(q["question"])
                    row["slm_intent"], row["slm_problems"], row["slm_raw"] = got["intent"], got["problems"], got["raw"]
                    intent = got["intent"] or {}
                    query = q["question"] + (" " + " ".join(intent.get("stems", [])) if intent.get("stems") else "")
                    legs = {k: v for k, v in intent.items() if k in ("action", "capability")} or None
                    runners["prism_slm_live"] = lambda _q, query=query, legs=legs: nlq.ranked(query, intent=legs)
                for engine in engines:
                    if engine not in runners:
                        continue
                    t = time.perf_counter()
                    ranking = runners[engine](q)
                    s = R.score(ranking, q["gold"], q["decoys"])
                    s["ms"] = round((time.perf_counter() - t) * 1000, 1)
                    row["systems"][engine] = s
                if "prism_shipped_ask" in engines and len([r for r in rows if r["repo"] == repo]) < payload_samples:
                    row["shipped_payload"] = nlq.search(q["question"], top_k=3)
                rows.append(row)
    summary = {"engines": {}, "contrasts": [], "notes": notes}
    for engine in engines:
        rs = [r["systems"][engine] for r in rows if engine in r["systems"]]
        if not rs:
            continue
        agg = {"overall": R.aggregate(rs)}
        for key in ("set", "repo", "category"):
            for val in sorted({r[key] for r in rows}):
                sub = [r["systems"][engine] for r in rows if r[key] == val and engine in r["systems"]]
                if sub:
                    agg[f"{key}:{val}"] = R.aggregate(sub)
        summary["engines"][engine] = agg
    for a, b in CONTRASTS:
        for metric in ("top1", "rr", "separation"):
            res = contrast(rows, a, b, metric)
            if res:
                summary["contrasts"].append(res)
    slm_rows = [r for r in rows if "slm_problems" in r]
    if slm_rows:
        summary["slm"] = {
            "n": len(slm_rows),
            "valid_json": sum(1 for r in slm_rows if r["slm_intent"] is not None),
            "fully_valid": sum(1 for r in slm_rows if r["slm_intent"] is not None and not r["slm_problems"]),
            "with_action": sum(1 for r in slm_rows if (r["slm_intent"] or {}).get("action")),
            "with_capability": sum(1 for r in slm_rows if (r["slm_intent"] or {}).get("capability")),
            "action_matches_oracle": sum(1 for r in slm_rows if (r["slm_intent"] or {}).get("action") == (r["oracle_intent"] or {}).get("action")),
            "capability_matches_oracle": sum(1 for r in slm_rows if (r["slm_intent"] or {}).get("capability") == (r["oracle_intent"] or {}).get("capability")),
        }
    result = {"summary": summary, "per_question": rows}
    (out / "stage1_results.json").write_text(json.dumps(result, indent=1, default=str))
    return result


def render_stage1_markdown(result: dict) -> str:
    s = result["summary"]
    lines = ["## Stage 1 - retrieval tournament", "",
             f"{sum(1 for _ in result['per_question'])} frozen questions (sets 1-3). Every engine ranks the same "
             "candidates. `normal_oracle` is 1.00 by construction (sanity row); `prism_shipped_ask` ranks exactly "
             "as `prism_rule_intent` (it adds the payload).", "",
             "| engine | top-1 | recall@5 | MRR | separation | set1 top-1 | set2 top-1 | set3 top-1 |",
             "|---|---|---|---|---|---|---|---|"]
    for engine, agg in s["engines"].items():
        o = agg["overall"]
        per = [f"{agg[k]['top1']:.3f}" if k in agg else "-" for k in ("set:set1", "set:set2", "set:set3")]
        lines.append(f"| `{engine}` | {o['top1']:.3f} | {o['recall@5']:.3f} | {o['mrr']:.3f} | {o['separation']:.3f} | "
                     + " | ".join(per) + " |")
    lines += ["", "Paired contrasts (questions are the pairing unit; 95% CI: bootstrap over questions, stratified by repo):", "",
              "| a | b | metric | Δ | 95% CI | wins-losses | sign-test p |", "|---|---|---|---|---|---|---|"]
    for c in s["contrasts"]:
        ci = f"[{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]" if c.get("ci95") else "-"
        wl = f"{c['wins']}-{c['losses']}" if "wins" in c else "-"
        p = f"{c['sign_p']:.3f}" if "sign_p" in c else "-"
        lines.append(f"| `{c['a']}` | `{c['b']}` | {c['metric']} | {c['delta']:+.3f} | {ci} | {wl} | {p} |")
    if "slm" in s:
        m = s["slm"]
        lines += ["", f"SLM intents: {m['n']} questions, {m['valid_json']} parsed, {m['fully_valid']} fully valid; "
                  f"action on {m['with_action']}, capability on {m['with_capability']}; agreement with the oracle - "
                  f"action {m['action_matches_oracle']}/{m['n']}, capability {m['capability_matches_oracle']}/{m['n']}."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repos-dir", required=True, help="<dir>/<repo> = pinned checkout, dataset root applied")
    p.add_argument("--sites-dir", required=True, help="<dir>/<repo> = pip --target site of its requirements")
    p.add_argument("--out", required=True)
    p.add_argument("--engines", default=",".join(ENGINES))
    p.add_argument("--slm-base-url", default=None, help="OpenAI-compatible endpoint for prism_slm_live")
    p.add_argument("--slm-model", default=None)
    p.add_argument("--sets", default="set1,set2,set3")
    args = p.parse_args(argv)
    engines = tuple(e for e in args.engines.split(",") if e)
    unknown = set(engines) - set(ENGINES)
    if unknown:
        raise SystemExit(f"unknown engines: {sorted(unknown)}")
    slm = None
    if "prism_slm_live" in engines:
        if not (args.slm_base_url and args.slm_model):
            raise SystemExit("prism_slm_live needs --slm-base-url and --slm-model")
        from harness.llm import ChatLLM
        llm = ChatLLM(base_url=args.slm_base_url, model=args.slm_model, temperature=0.0, timeout=300, num_ctx=4096)
        slm = SLMIntentExtractor(llm, args.slm_model, Path(args.out) / "slm_cache.jsonl")
    wanted = set(args.sets.split(","))
    result = run_stage1(args.repos_dir, args.sites_dir, args.out, engines, slm=slm,
                        question_sets=tuple(s for s in QUESTION_SETS if s[0] in wanted))
    md = render_stage1_markdown(result)
    (Path(args.out) / "stage1.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
