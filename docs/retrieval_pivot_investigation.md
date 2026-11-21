# Retrieval pivot: where summaries belong (investigation)

Status: investigation and proposal. Nothing here changes shipped behaviour.
Data: the frozen held-out sets 2-6 (set 6 used diagnostically after its one scored run),
the stage 1 / set 6 summary indexes, `harness/experiments/retrieval_pivot/seed_expand.py`
(raw rows: `reports/retrieval_pivot/seed_expand.json`).

## 1. Correcting the premise

Summary cards are not PRISM's primary search index, and they are not failing.

- **Primary retrieval is already deterministic** (`prism.query.nlq`): BM25 over each symbol's
  qualified name, path and source lines (identifier splitting, stemming), a capability rerank from
  static profiles (triad), structural priors, and for identifier or mixed queries an exact-name pin
  (`prism.query.route`). No model runs at query time.
- **The summary is a secondary field**: `BM25(code) + 0.5 * BM25(summary)`.
- **It helps on fresh data**: set 6 V2 vs BASE 37 better / 20 worse, p = 0.033 (recall@5
  0.700 -> 0.758). Set 4 shipped it at 22 / 7.
- **The ceiling we hit is narrower**: *rewriting* summaries (v3, v4, v5) did not beat v2, because
  the remaining misses are worded differently from the code and from any one-sentence summary
  (`reports/summary_candidate_v4_stage2/DIAGNOSIS.md`).
- **Summaries never reach the subgraph or the payload**: `prism.slice` / `prism.explain` pack
  skeletonized code with contract blocks; `prism.blast_radius` is byte-identical with or without a
  summary index.

So "decouple primary search from summaries" would remove a measured gain. The real question is
whether summaries are *also* useful downstream.

## 2. Deterministic seeding - what it can and cannot do

- **Exact names** are already handled: for identifier queries, the pin puts the named symbol at
  top-1 for 0.894 of 104 lookups (MRR 0.916; `docs/capabilities_and_routing.md`).
- **Natural-language questions carry no names** to match: 156 of 164 frozen questions route
  `natural_language`, 8 `mixed`, none `identifier`. AST paths, imports and FQNs cannot seed a query
  that names none of them. Code BM25 already uses names and paths as text.
- **Deterministic signals that could still help** a natural-language query are structural
  *priors*, not seeds - entry points, capability tags, file or module locality. Triad and the
  migration prior already do this.

## 3. Graph expansion as a recovery mechanism - measured

Seeds = V2's top 5; expansion = their 1-hop `CALLS` neighbours (both directions).

| | sets 2-5 (188) | set 6 (120, diagnostic) |
|---|---|---|
| gold in top 5 (search alone) | 152 | 91 |
| neighbourhood size, median (p90) | 6 (14) | 10 (24) |
| misses whose gold is a 1-hop neighbour | 9 of 36 | 8 of 29 |
| top 5 + whole neighbourhood | 161 | 99 |
| **control:** top (5 + same count) by search | **166** | **105** |
| top 5 + 3 neighbours picked by summary / code / structure | 160 / 161 / 159 | 96 / 94 / 93 |
| **control:** top 8 by search | **163** | **98** |

- Expansion reaches about a quarter of the misses.
- At an equal node budget, more search hits beat graph expansion for *finding* the answer, on
  both sets.
- Inside a neighbourhood, summaries order the reached gold best on set 6 (median rank 1.5, vs 4
  for code and 6 for structure; 8 cases) and tie on sets 2-5 - a weak hint, not a result.

**Conclusion.** Graph traversal should not be the recall mechanism for locating answers. Its job
is the one PRISM already gives it: completing the *context* around found seeds (callers,
callees, data flow, entry paths).

## 4. Where summaries should live

Ranked by how promising the evidence and cost look:

1. **Search field (keep, as shipped).** Confirmed on two fresh sets.
2. **Compressed payload for secondary nodes (untested, most promising new use).** In a
   `prism.explain` / `prism.slice` payload, nodes beyond the seed's immediate ring could be sent as
   `signature + contract + one-line summary` instead of a skeleton. That fits more of the subgraph
   into the same token budget. No model runs at query time - the summaries already exist.
3. **Frontier priority in the budgeted walk (weak hint).** Among frontier nodes with equal
   structural cost, prefer the one whose summary matches the question. Section 3 hints the
   ordering is good, on only 8 cases.
4. **Semantic bitmasks for pruning (not recommended yet).** The capability profiles (database,
   network, filesystem; triad) already are deterministic semantic bits, and they don't share the
   summaries' wording problem. A summary-derived mask would add noise without evidence it beats them.

## 5. Blueprint

```
query
  -> route (prism.query.route): identifier | mixed | natural_language          [unchanged]
  -> seeds: hybrid lexical rank                                                 [unchanged]
       BM25(name+path+code) x triad(capability, action) + priors
       + 0.5 * BM25(summary)     (locked v2 index, coverage >= 95%)
       + exact-name pin for identifier / mixed
  -> subgraph: budgeted walk from the top seeds over typed edges                 [existing]
       cost = causal edge weights (call, data flow, guard)
       NEW (P2): frontier tie-break by summary-question match
  -> payload (packer, token budget B)
       seeds: source / skeleton                                                 [existing]
       ring 1: skeleton + contract                                              [existing]
       NEW (P1): ring >= 2: signature + contract + summary line
       entry breadcrumbs                                                        [existing]
  -> host LLM
```

## 6. Prototype plan (lightweight, offline first)

Each step needs gold that Prism does not produce, and none needs a model until P3.

- **P1 - payload compression.**
  - Gold: Q2E's required evidence symbols (`harness/experiments/query_to_evidence`) and Layer 4
    runtime descendants / ancestors (`harness/experiments/layer4`).
  - Arms: the current packer vs ring >= 2 compressed to signature + contract + summary.
  - Budgets: 2k / 4k / 8k tokens.
  - Metrics: gold nodes present in the payload, and tokens used.
  - Needs v2 summaries for the Q2E / Layer 4 corpora (one Kaggle `prism summarize` run if missing).
- **P2 - frontier priority.** Same gold and budgets; the walk with and without the summary
  tie-break. Metric: gold recall at budget.
- **P3 - answer quality (only if P1 or P2 shows a gain).** A fixed host model answers from each
  arm's payload. Grading: Layer 4's execution grading (spliced implementations pass the corpus's
  own tests) and Q2E's grounding checks. Pre-register the rule on a fresh task set, as for sets 4-6.

What not to build: removing the summary field from search, symbol-match seeding for
natural-language queries, or graph expansion as a substitute for more search hits - each is
contradicted by sections 1-3.
