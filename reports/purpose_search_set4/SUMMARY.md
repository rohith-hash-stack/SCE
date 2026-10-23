# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:14b-instruct-q8_0. BASE is the shipped `NLQIndex`; +RES / +SUM rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 4 (12 questions: flask)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.667 | 0.833 | 0.833 | 0.583 |
| +RES | 0.667 | 0.833 | 0.833 | 0.583 |
| +SUM | 0.750 | 0.917 | 0.917 | 0.634 |
| +SUM+RES | 0.750 | 0.917 | 0.917 | 0.676 |
| FIELD w=0.5 | 0.750 | 0.917 | 0.917 | 0.668 |
| FIELD w=1.0 | 0.750 | 0.917 | 0.917 | 0.647 |

`flask` (12 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.667 | 0.833 | 0.833 | 0.583 |
| +RES | 0.667 | 0.833 | 0.833 | 0.583 |
| +SUM | 0.750 | 0.917 | 0.917 | 0.634 |
| +SUM+RES | 0.750 | 0.917 | 0.917 | 0.676 |
| FIELD w=0.5 | 0.750 | 0.917 | 0.917 | 0.668 |
| FIELD w=1.0 | 0.750 | 0.917 | 0.917 | 0.647 |

## Summary lint (share of summaries flagged)

| repo | first pass: axis / filler / identifier / callee list / any | repaired | final: any |
|---|---|---|---|
| flask | 0.003 / 0.000 / 0.038 / 0.000 / 0.041 | 10 | 0.014 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| flask | 366/366 (full) | 0.925 (n=40) / 0.958 vs 0.958 (n=24) / 0.200 | 0.167 / 0.583 / 0.667 / 0.301 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| flask | 366 | 114 (0) | 61909 / 9698 | 402.7 |
