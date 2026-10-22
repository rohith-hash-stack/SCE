"""Purpose-enriched search evaluation (diagnostic; the shipped NLQIndex is unchanged).

Per repository:
  1. a context card (`cards.build_card`) for every function/method in
     NLQIndex's candidate pool (or the top-N of the baseline ranking per
     question, `--scope top:N`), summarized in batches by an LLM with the
     prompt below;
  2. direct metrics on the summaries alone - identifiability (pick the
     function among 5 siblings of the same class/module from its summary;
     the docstring is the reference) and query matching (BM25 over the
     summaries only);
  3. search coverage: NLQIndex rebuilt with each document extended by
     +RES (static resources), +SUM (summary) or +SUM+RES, everything else
     in the ranking (triad legs, priors) unchanged, against BASE.

Usage:
  python -m harness.experiments.purpose_search.run --repo REPO --out-dir OUT
      [--backend ollama --model qwen2.5-coder:7b-instruct-q8_0 --base-urls http://127.0.0.1:11434,...]
      [--backend gemini --model gemini-3.5-flash-lite]   # key in $Prism_gemini_API_key
      [--apps-dir A --sites-dir S]   # held-out repos: A/<repo> checkout at the pinned commit, S/<repo> deps
      [--scope full|top:300] [--no-llm]
REPO: fastapi | django (Q2E unseeded behavioural questions q09-q14)
      fastapi_template | styleguide (held-out set 2, 60 questions)
      express | trpc (held-out set 3, 50 questions, JavaScript/TypeScript)
FIELD w=... arms (--field-weights, default 0.5,1.0) score BM25(code) + w * BM25(summary)
with the summary as an independent field.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import itertools
import json
import os
import random
import re
import statistics as st
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from harness.experiments.purpose_search.cards import build_card, resources
from harness.experiments.purpose_search.lint import rates as lint_rates
from prism.query.nlq import BM25, NLQIndex, tokenize

REPO_ROOT = Path(__file__).resolve().parents[3]
Q2E = REPO_ROOT / "harness/experiments/query_to_evidence/dataset"
HELD_OUT_SETS = tuple(REPO_ROOT / f"harness/experiments/heldout_retrieval/{s}/dataset.json" for s in ("set2", "set3"))
HELD_OUT_REPOS = ("fastapi_template", "styleguide", "express", "trpc")
POOL_TOP, POOL_EXPAND = 20, 8  # candidate pool: top-20 hits + resolved callees of the top-8

#: v1: the card with the Architectural Axes line + this prompt (the set-2 / Q2E / set-3 runs).
SUMMARY_PROMPT_V1 = (
    "You are an expert software architect analyzing a code context graph.\n"
    "Below are the structural profiles and neighborhoods of {n} functions, separated by '---'.\n\n{cards}\n\n"
    "Task: For EACH function, write a precise, high-density behavioral summary in 8 to 14 words.\n"
    "Format: [Action Verb] [Domain Entity / Target] via [Mechanism / Dependencies], [Effect / Return / Throws].\n"
    "Rule: Do NOT simply rephrase or repeat the function name. State its operational intent within the system "
    "architecture.\n"
    "Only reference entities, protocols, and mechanisms explicitly present in the context card or signature; do not "
    "assume specific business domains.\n"
    "Respond with a JSON object mapping each Qualified Name exactly as given to its summary.")
#: v2: the card without the axes line + this prompt. v1's summaries copied axis labels and code
#: identifiers, and dumped callee names (`lint.py`); v2 asks for plain-English behaviour in the
#: words a developer would use when asking about it.
SUMMARY_PROMPT_V2 = (
    "You are an expert software architect describing functions for a code search index.\n"
    "Below are the structural profiles of {n} functions, separated by '---'.\n\n{cards}\n\n"
    "For EACH function write one plain-English sentence of 8 to 14 words saying what it does and how, in the words "
    "a developer would use when asking about this behaviour.\n"
    "Rules:\n"
    "1. No code identifiers: no function, method, class, variable, module or file names, and no backticks, dots, "
    "underscores, camelCase or parentheses.\n"
    "2. Do not list the functions it calls; say in ordinary words what they achieve together.\n"
    "3. Do not simply restate the function's name; describe its behaviour.\n"
    "4. No internal analysis labels or filler, such as 'pure compute', 'no effect', 'leaf utility' or 'no return'.\n"
    "5. Only describe behaviour, protocols and data present in the profile; do not assume business domains.\n"
    "Example - bad: \"Loads config via os.environ.get, json.loads, no effect.\" "
    "good: \"Reads settings from environment variables and parses them as JSON.\"\n"
    "Respond with a JSON object mapping each Qualified Name exactly as given to its sentence.")
VARIANTS = {"v1": (True, SUMMARY_PROMPT_V1), "v2": (False, SUMMARY_PROMPT_V2)}  # variant -> (card axes, prompt)
IDENT_PROMPT = (
    "Below is a one-line {what} of exactly one of the listed functions. Which function does it describe?\n"
    "{what_cap}: {text}\n\nCandidates (same class or module):\n{cands}\n\n"
    "Respond with a JSON object {{\"answer\": <candidate number>}}.")


# --------------------------------------------------------------------- #
# LLM backends (same prompts, JSON replies)
# --------------------------------------------------------------------- #
class LLM:
    def __init__(self, backend: str, model: str, base_urls: list[str]):
        self.backend, self.model = backend, model
        self._urls = itertools.cycle(base_urls or ["http://127.0.0.1:11434"])
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}

    def _post(self, url, body, headers=None, timeout=600):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def json(self, prompt: str, max_out: int, seed: int = 42) -> dict | None:
        for attempt in range(6):
            t = time.time()
            try:
                if self.backend == "ollama":
                    with self._lock:
                        url = next(self._urls)
                    d = self._post(f"{url}/api/chat", {
                        "model": self.model, "messages": [{"role": "user", "content": prompt}], "format": "json",
                        "stream": False, "options": {"temperature": 0.4, "seed": seed, "num_ctx": 8192,
                                                     "num_predict": max_out}})
                    text, pin, pout = d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)
                else:
                    d = self._post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                        {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_out, "seed": seed,
                                              "responseMimeType": "application/json",
                                              "thinkingConfig": {"thinkingLevel": "low"}}},
                        {"x-goog-api-key": os.environ["Prism_gemini_API_key"]})
                    text = "".join(p.get("text", "") for p in d["candidates"][0]["content"].get("parts", []))
                    u = d.get("usageMetadata", {})
                    pin, pout = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
                with self._lock:
                    self.usage["calls"] += 1
                    self.usage["prompt_tokens"] += pin
                    self.usage["completion_tokens"] += pout
                    self.usage["seconds"] += time.time() - t
                return json.loads(text)
            except json.JSONDecodeError:
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as e:
                if getattr(e, "code", 503) in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(2 ** (attempt + 2))
                    continue
                break
        with self._lock:
            self.usage["failed"] += 1
        return None


# --------------------------------------------------------------------- #
# repositories and questions
# --------------------------------------------------------------------- #
def load_repo(repo: str, apps_dir: str | None, sites_dir: str | None, out_dir: Path):
    from prism.graph.entry_points import find_entry_points
    if repo in ("fastapi", "django"):
        from harness.experiments.layer4 import context as X
        corpus = X.Corpus.load(repo)
        builder, masks, root = corpus.state.builder, corpus.state.feature_masks, corpus.state.repo_path
        qs = {q["id"]: q for q in json.loads((Q2E / "questions.json").read_text())["questions"]}
        gold = json.loads((Q2E / "gold.json").read_text())["gold"]
        questions = [{"id": k, "question": q["query"],
                      "gold": [s for r in gold[k]["requirements"] for s in r["required_symbols"]],
                      "subject": gold[k]["requirements"][0].get("subject")}
                     for k, q in qs.items() if q["corpus"] == repo and q["category"] == "unseeded_behavioral"]
    else:
        from harness.experiments.heldout_retrieval.run import Corpus
        from prism.semantics.extractor import compute_feature_masks_cached
        ds = next(d for d in (json.loads(p.read_text()) for p in HELD_OUT_SETS) if repo in d["repos"])
        root = os.path.join(apps_dir, repo, ds["repos"][repo]["root"])
        store = out_dir / f"deps_summaries_{repo}.sqlite"
        site = os.path.join(sites_dir, repo)
        os.makedirs(site, exist_ok=True)  # JS/TS: dependency summaries are Python-only, the site stays empty
        corpus = Corpus(repo, root, site, str(store))
        builder = corpus.builder
        masks = compute_feature_masks_cached(builder, root)
        questions = [{"id": q["id"], "question": q["question"], "gold": q["gold"], "subject": q["gold"][0]}
                     for q in ds["questions"] if q["repo"] == repo]
    return builder, masks, find_entry_points(builder), root, questions


# --------------------------------------------------------------------- #
# search variants and metrics
# --------------------------------------------------------------------- #
def augmented(nlq: NLQIndex, base_docs: list[list[str]], extra: dict[str, str] | None) -> NLQIndex:
    """A copy of `nlq` whose BM25 documents carry `extra[name]` as well."""
    if extra is None:
        return nlq
    v = NLQIndex.__new__(NLQIndex)
    v.__dict__.update(nlq.__dict__)
    v.bm25 = BM25([d + tokenize(extra.get(n, "")) for d, n in zip(base_docs, nlq.names)])
    return v


class FieldedBM25:
    """`BM25.scores` over two independent fields: code + w * summary."""

    def __init__(self, code: BM25, summary: BM25, weight: float):
        self.code, self.summary, self.weight = code, summary, weight

    def scores(self, query: list[str]) -> dict[int, float]:
        out = dict(self.code.scores(query))
        if not self.weight:  # w = 0 must not add summary-only matches at score 0
            return out
        for i, s in self.summary.scores(query).items():
            out[i] = out.get(i, 0.0) + self.weight * s
        return out


def fielded(nlq: NLQIndex, summaries: dict[str, str], weight: float) -> NLQIndex:
    """A copy of `nlq` scoring `BM25(code) + weight * BM25(summary)`; the
    summary field has its own index (its own IDF and length norm)."""
    v = NLQIndex.__new__(NLQIndex)
    v.__dict__.update(nlq.__dict__)
    v.bm25 = FieldedBM25(nlq.bm25, BM25([tokenize(summaries.get(n, "")) for n in nlq.names]), weight)
    return v


def candidate_pool(builder, ranked: list[str]) -> list[str]:
    fn = lambda q: (s := builder.symbol_table.get(q)) is not None and s.kind in ("function", "method")
    pool = [h for h in ranked[:POOL_TOP] if fn(h)]
    for h in ranked[:POOL_EXPAND]:
        for _u, t, d in sorted(builder.graph.out_edges(h, data=True), key=lambda e: e[1]):
            if d.get("relation", "CALLS") == "CALLS" and fn(t) and t not in pool:
                pool.append(t)
    return pool


def score(builder, ranked: list[str], gold: list[str]) -> dict:
    ranks = {g: (ranked.index(g) + 1 if g in ranked else None) for g in gold}
    pool = candidate_pool(builder, ranked)
    return {"ranks": ranks, "recall@5": sum(1 for r in ranks.values() if r and r <= 5) / len(gold),
            "recall@20": sum(1 for r in ranks.values() if r and r <= 20) / len(gold),
            "pool_recall": sum(g in pool for g in gold) / len(gold),
            "mrr": max((1 / r for r in ranks.values() if r), default=0.0)}


METRICS = ("recall@5", "recall@20", "pool_recall", "mrr")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, choices=("fastapi", "django") + HELD_OUT_REPOS)
    ap.add_argument("--field-weights", default="0.5,1.0", type=lambda s: [float(x) for x in s.split(",") if x],
                    help="summary-field weights w for FIELD arms: BM25(code) + w * BM25(summary)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--backend", default="ollama", choices=("ollama", "gemini"))
    ap.add_argument("--model", default="qwen2.5-coder:7b-instruct-q8_0")
    ap.add_argument("--base-urls", default="http://127.0.0.1:11434")
    ap.add_argument("--apps-dir")
    ap.add_argument("--sites-dir")
    ap.add_argument("--scope", default="full", help="full, or top:N (union of each question's baseline top-N)")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--identify-sample", type=int, default=40)
    ap.add_argument("--variant", default="v2", choices=sorted(VARIANTS),
                    help="card + summary prompt version (v1: the earlier runs; v2: no axes line, plain-English prompt)")
    ap.add_argument("--only", help="comma-separated qualified names: summarize and lint just these, skip evaluation")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    llm = LLM(args.backend, args.model, [u for u in args.base_urls.split(",") if u])

    t0 = time.time()
    builder, masks, entries, root, questions = load_repo(args.repo, args.apps_dir, args.sites_dir, args.out_dir)
    nlq = NLQIndex(builder, root)
    load_s = time.time() - t0
    fns = [s.qualified_name for s in nlq.pool if s.kind in ("function", "method")]
    base_rank = {q["id"]: nlq.ranked(q["question"]) for q in questions}
    if args.scope.startswith("top:"):
        n = int(args.scope.split(":")[1])
        keep = {h for r in base_rank.values() for h in r[:n]}
        scope_fns = [q for q in fns if q in keep]
    else:
        scope_fns = fns
    print(f"[{args.repo}] pool {len(nlq.pool)}, functions {len(fns)}, in scope ({args.scope}) {len(scope_fns)}, "
          f"questions {len(questions)}, loaded in {load_s:.0f}s", flush=True)

    with_axes, prompt = VARIANTS[args.variant]
    if args.only:
        scope_fns = [q for q in args.only.split(",") if q]
    cards = {q: c for q in scope_fns if (c := build_card(builder, masks, entries, q, axes=with_axes))}
    res = {q: resources(builder, q) for q in fns}
    cache_path = args.out_dir / f"summary_cache_{args.repo}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    key = lambda q: hashlib.sha1(((args.model + "\n" + cards[q]["card"]) if args.variant == "v1" else  # v1 keys unchanged
                                  (args.model + "\n" + args.variant + "\n" + prompt + "\n" + cards[q]["card"])).encode()).hexdigest()[:16]
    todo = [q for q in cards if cache.get(q, {}).get("key") != key(q)]
    print(f"[{args.repo}] cards {len(cards)}; to summarize {len(todo)} (cached {len(cards) - len(todo)})", flush=True)

    def summarize(batch):
        got = llm.json(prompt.format(n=len(batch), cards="\n---\n".join(cards[q]["card"] for q in batch)),
                       max_out=60 * len(batch) + 200) or {}
        out = {q: re.sub(r"\s+", " ", str(got[q])).strip() for q in batch if isinstance(got, dict) and got.get(q)}
        for q in batch:  # a card the batch reply left out is retried on its own
            if q not in out:
                one = llm.json(prompt.format(n=1, cards=cards[q]["card"]), max_out=200) or {}
                v = one.get(q) or (next(iter(one.values())) if isinstance(one, dict) and len(one) == 1 else None)
                if v:
                    out[q] = re.sub(r"\s+", " ", str(v)).strip()
        return out

    synth_t = time.time()
    if todo and not args.no_llm:
        batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
        with cf.ThreadPoolExecutor(args.threads) as ex:
            for i, got in enumerate(ex.map(summarize, batches), 1):
                for q, s in got.items():
                    cache[q] = {"key": key(q), "summary": s}
                if i % 25 == 0 or i == len(batches):
                    cache_path.write_text(json.dumps(cache))
                    print(f"[{args.repo}] summarized {i}/{len(batches)} batches, {time.time() - synth_t:.0f}s, "
                          f"usage {llm.usage}", flush=True)
    synth_s = time.time() - synth_t
    summ = {q: cache[q]["summary"] for q in cards if cache.get(q, {}).get("key") == key(q)}
    lint = lint_rates((summ[q], cards[q]["card"]) for q in summ)
    print(f"[{args.repo}] summaries {len(summ)}/{len(cards)}; lint {lint}", flush=True)
    if args.only:
        (args.out_dir / f"{args.repo}_only.json").write_text(json.dumps(
            {"variant": args.variant, "model": args.model, "lint": lint,
             "summaries": {q: {"summary": summ.get(q), "card": cards[q]["card"]} for q in cards}}, indent=1) + "\n")
        return 0

    # ---- search coverage -------------------------------------------------
    base_docs = [tokenize(s.qualified_name) + tokenize(nlq._relative(s.file))
                 + tokenize("\n".join(nlq._source(s.file)[s.line_range[0] - 1:s.line_range[1]])) for s in nlq.pool]
    variants = {"BASE": None, "+RES": {q: " ".join(r) for q, r in res.items()}, "+SUM": summ,
                "+SUM+RES": {q: summ.get(q, "") + " " + " ".join(res.get(q, [])) for q in fns}}
    indexes = {name: augmented(nlq, base_docs, extra) for name, extra in variants.items()}
    for w in args.field_weights:  # frozen before the run
        indexes[f"FIELD w={w}"] = fielded(nlq, summ, w)
    search = {}
    for name, idx in indexes.items():
        rows = [{"id": q["id"], **score(builder, base_rank[q["id"]] if name == "BASE" else idx.ranked(q["question"]),
                                        q["gold"]), "subject": q["subject"]} for q in questions]
        search[name] = {"per_question": rows, **{m: round(st.mean(r[m] for r in rows), 3) for m in METRICS}}
        print(f"[{args.repo}] {name:13} " + " ".join(f"{m} {search[name][m]:.3f}" for m in METRICS), flush=True)

    # ---- direct metric: query matching on the summaries alone -------------
    names = [q for q in fns if q in summ]
    qm = []
    if names:
        bm = BM25([tokenize(summ[q]) for q in names])
        for q in questions:
            sc = bm.scores(tokenize(q["question"]))
            order = [names[i] for i in sorted(sc, key=lambda i: (-sc[i], i))]
            ranks = {g: (order.index(g) + 1 if g in order else None) for g in q["gold"]}
            qm.append({"id": q["id"], "ranks": ranks, "best": min((r for r in ranks.values() if r), default=None)})
    query_matching = {"candidates": len(names), "per_question": qm,
                      **({f"hit@{k}": round(st.mean(1.0 if r["best"] and r["best"] <= k else 0.0 for r in qm), 3)
                          for k in (1, 5, 20)} if qm else {}),
                      "mrr": round(st.mean(1 / r["best"] if r["best"] else 0.0 for r in qm), 3) if qm else None}

    # ---- direct metric: identifiability among 5 siblings -----------------
    groups: dict[str, list[str]] = {}
    for q in names:
        groups.setdefault(q.rpartition(".")[0], []).append(q)
    eligible = [q for q in names if len(groups[q.rpartition(".")[0]]) >= 5]
    sample = random.Random(0).sample(eligible, min(args.identify_sample, len(eligible)))
    seeded = lambda s: random.Random(int(hashlib.sha1(s.encode()).hexdigest()[:8], 16))

    def identify(q, text, what):
        cands = seeded(q).sample([s for s in groups[q.rpartition(".")[0]] if s != q], 4) + [q]
        seeded(q + "#").shuffle(cands)
        got = llm.json(IDENT_PROMPT.format(what=what, what_cap=what.capitalize(), text=text,
                                           cands="\n".join(f"{i + 1}. {c}" for i, c in enumerate(cands))), max_out=32)
        try:
            a = int((got or {}).get("answer"))
        except (TypeError, ValueError):
            return False
        return 1 <= a <= 5 and cands[a - 1] == q

    identifiability = {}
    if sample and not args.no_llm:
        with cf.ThreadPoolExecutor(args.threads) as ex:
            hits = list(ex.map(lambda q: identify(q, summ[q], "behavioral summary"), sample))
            with_doc = [q for q in sample if cards[q]["docstring"]]
            doc_hits = list(ex.map(lambda q: identify(q, cards[q]["docstring"], "docstring"), with_doc))
        on_doc = [h for q, h in zip(sample, hits) if cards[q]["docstring"]]
        identifiability = {"eligible": len(eligible), "sample": len(sample), "summary_acc": round(st.mean(hits), 3),
                           "with_docstring": len(with_doc),
                           "docstring_acc": round(st.mean(doc_hits), 3) if doc_hits else None,
                           "summary_acc_on_docstring_subset": round(st.mean(on_doc), 3) if on_doc else None,
                           "chance": 0.2}
    print(f"[{args.repo}] query matching {({k: v for k, v in query_matching.items() if k != 'per_question'})}; "
          f"identifiability {identifiability}", flush=True)

    out = {"repo": args.repo, "backend": args.backend, "model": args.model, "scope": args.scope,
           "variant": args.variant, "lint": lint,
           "pool": len(nlq.pool), "functions": len(fns), "cards": len(cards), "summaries": len(summ),
           "load_seconds": round(load_s, 1), "synth_seconds": round(synth_s, 1), "llm_usage": llm.usage,
           "summary_words_mean": round(st.mean(len(s.split()) for s in summ.values()), 1) if summ else None,
           "resources_nonempty": sum(1 for r in res.values() if r), "search": search,
           "query_matching": query_matching, "identifiability": identifiability, "questions": questions,
           "summary_samples": dict(list(summ.items())[:20])}
    (args.out_dir / f"{args.repo}.json").write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
