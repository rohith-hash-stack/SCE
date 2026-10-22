# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:14b-instruct-q8_0. BASE is the shipped `NLQIndex`; +RES / +SUM rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 3 (25 questions: express)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.920 | 1.000 | 1.000 | 0.633 |
| +RES | 0.920 | 1.000 | 1.000 | 0.631 |
| +SUM | 0.840 | 1.000 | 1.000 | 0.675 |
| +SUM+RES | 0.840 | 1.000 | 1.000 | 0.675 |
| FIELD w=0.5 | 0.920 | 1.000 | 1.000 | 0.611 |
| FIELD w=1.0 | 0.860 | 1.000 | 1.000 | 0.623 |

`express` (25 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.920 | 1.000 | 1.000 | 0.633 |
| +RES | 0.920 | 1.000 | 1.000 | 0.631 |
| +SUM | 0.840 | 1.000 | 1.000 | 0.675 |
| +SUM+RES | 0.840 | 1.000 | 1.000 | 0.675 |
| FIELD w=0.5 | 0.920 | 1.000 | 1.000 | 0.611 |
| FIELD w=1.0 | 0.860 | 1.000 | 1.000 | 0.623 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| express | 270/270 (full) | 0.700 (n=40) / 0.950 vs 1.000 (n=20) / 0.200 | 0.240 / 0.480 / 0.760 / 0.375 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| express | 270 | 83 (0) | 42645 / 6492 | 278.6 |
