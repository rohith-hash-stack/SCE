# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:14b-instruct-q8_0. BASE is the shipped `NLQIndex`; +RES / +SUM rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 3 (25 questions: express)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.920 | 1.000 | 1.000 | 0.633 |
| +RES | 0.920 | 1.000 | 1.000 | 0.631 |
| +SUM | 0.720 | 1.000 | 1.000 | 0.626 |
| +SUM+RES | 0.720 | 1.000 | 1.000 | 0.626 |
| FIELD w=0.5 | 0.880 | 1.000 | 1.000 | 0.646 |
| FIELD w=1.0 | 0.840 | 1.000 | 1.000 | 0.661 |

`express` (25 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.920 | 1.000 | 1.000 | 0.633 |
| +RES | 0.920 | 1.000 | 1.000 | 0.631 |
| +SUM | 0.720 | 1.000 | 1.000 | 0.626 |
| +SUM+RES | 0.720 | 1.000 | 1.000 | 0.626 |
| FIELD w=0.5 | 0.880 | 1.000 | 1.000 | 0.646 |
| FIELD w=1.0 | 0.840 | 1.000 | 1.000 | 0.661 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| express | 270/270 (full) | 0.725 (n=40) / 0.950 vs 1.000 (n=20) / 0.200 | 0.320 / 0.400 / 0.640 / 0.401 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| express | 270 | 88 (0) | 36427 / 5517 | 218.7 |
