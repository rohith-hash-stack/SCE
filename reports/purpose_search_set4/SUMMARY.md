# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:14b-instruct-q8_0. BASE is the shipped `NLQIndex`; +RES / +SUM rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 4 (48 questions: flask, httpie, fastify, zod)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.521 | 0.750 | 0.823 | 0.442 |
| +RES | 0.521 | 0.750 | 0.823 | 0.442 |
| +SUM | 0.635 | 0.812 | 0.875 | 0.466 |
| +SUM+RES | 0.635 | 0.812 | 0.875 | 0.476 |
| FIELD w=0.5 | 0.615 | 0.844 | 0.865 | 0.486 |
| FIELD w=1.0 | 0.625 | 0.865 | 0.885 | 0.503 |

`flask` (12 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.667 | 0.833 | 0.833 | 0.583 |
| +RES | 0.667 | 0.833 | 0.833 | 0.583 |
| +SUM | 0.750 | 0.917 | 0.917 | 0.634 |
| +SUM+RES | 0.750 | 0.917 | 0.917 | 0.676 |
| FIELD w=0.5 | 0.750 | 0.917 | 0.917 | 0.668 |
| FIELD w=1.0 | 0.750 | 0.917 | 0.917 | 0.647 |

`httpie` (12 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.417 | 0.750 | 0.833 | 0.357 |
| +RES | 0.417 | 0.750 | 0.833 | 0.357 |
| +SUM | 0.500 | 0.667 | 0.833 | 0.389 |
| +SUM+RES | 0.500 | 0.667 | 0.833 | 0.388 |
| FIELD w=0.5 | 0.583 | 0.750 | 0.833 | 0.397 |
| FIELD w=1.0 | 0.583 | 0.833 | 0.917 | 0.345 |

`fastify` (12 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.542 | 0.750 | 0.917 | 0.440 |
| +RES | 0.542 | 0.750 | 0.917 | 0.440 |
| +SUM | 0.625 | 0.833 | 0.917 | 0.439 |
| +SUM+RES | 0.625 | 0.833 | 0.917 | 0.439 |
| FIELD w=0.5 | 0.542 | 0.958 | 0.958 | 0.409 |
| FIELD w=1.0 | 0.542 | 0.958 | 0.958 | 0.519 |

`zod` (12 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.458 | 0.667 | 0.708 | 0.386 |
| +RES | 0.458 | 0.667 | 0.708 | 0.386 |
| +SUM | 0.667 | 0.833 | 0.833 | 0.400 |
| +SUM+RES | 0.667 | 0.833 | 0.833 | 0.400 |
| FIELD w=0.5 | 0.583 | 0.750 | 0.750 | 0.471 |
| FIELD w=1.0 | 0.625 | 0.750 | 0.750 | 0.501 |

## Summary lint (share of summaries flagged)

| repo | first pass: axis / filler / identifier / callee list / any | repaired | final: any |
|---|---|---|---|
| fastify | 0.043 / 0.014 / 0.018 / 0.005 / 0.076 | 52 | 0.010 |
| flask | 0.003 / 0.000 / 0.038 / 0.000 / 0.041 | 10 | 0.014 |
| httpie | 0.000 / 0.000 / 0.016 / 0.000 / 0.016 | 8 | 0.003 |
| zod | 0.000 / 0.000 / 0.011 / 0.000 / 0.011 | 3 | 0.000 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| fastify | 800/800 (full) | 0.700 (n=40) / 1.000 vs 1.000 (n=1) / 0.200 | 0.250 / 0.333 / 0.583 / 0.323 |
| flask | 366/366 (full) | 0.925 (n=40) / 0.958 vs 0.958 (n=24) / 0.200 | 0.167 / 0.583 / 0.667 / 0.301 |
| httpie | 615/615 (full) | 0.950 (n=40) / 0.833 vs 1.000 (n=6) / 0.200 | 0.083 / 0.250 / 0.583 / 0.186 |
| zod | 274/274 (full) | 0.925 (n=40) / 0.500 vs 1.000 (n=2) / 0.200 | 0.333 / 0.333 / 0.667 / 0.366 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| fastify | 800 | 175 (0) | 100463 / 18019 | 733.2 |
| flask | 366 | 114 (0) | 61909 / 9698 | 402.7 |
| httpie | 615 | 109 (0) | 83570 / 14573 | 635.1 |
| zod | 274 | 67 (0) | 34047 / 5913 | 243.7 |
