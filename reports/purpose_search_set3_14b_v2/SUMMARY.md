# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:14b-instruct-q8_0. BASE is the shipped `NLQIndex`; +RES / +SUM rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 3 (50 questions: express, trpc)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.740 | 0.890 | 0.900 | 0.503 |
| +RES | 0.740 | 0.890 | 0.900 | 0.502 |
| +SUM | 0.680 | 0.950 | 0.950 | 0.551 |
| +SUM+RES | 0.680 | 0.950 | 0.950 | 0.551 |
| FIELD w=0.5 | 0.770 | 0.920 | 0.930 | 0.549 |
| FIELD w=1.0 | 0.760 | 0.910 | 0.930 | 0.618 |

`express` (25 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.920 | 1.000 | 1.000 | 0.633 |
| +RES | 0.920 | 1.000 | 1.000 | 0.631 |
| +SUM | 0.720 | 1.000 | 1.000 | 0.626 |
| +SUM+RES | 0.720 | 1.000 | 1.000 | 0.626 |
| FIELD w=0.5 | 0.880 | 1.000 | 1.000 | 0.646 |
| FIELD w=1.0 | 0.840 | 1.000 | 1.000 | 0.661 |

`trpc` (25 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.560 | 0.780 | 0.800 | 0.372 |
| +RES | 0.560 | 0.780 | 0.800 | 0.372 |
| +SUM | 0.640 | 0.900 | 0.900 | 0.477 |
| +SUM+RES | 0.640 | 0.900 | 0.900 | 0.477 |
| FIELD w=0.5 | 0.660 | 0.840 | 0.860 | 0.452 |
| FIELD w=1.0 | 0.680 | 0.820 | 0.860 | 0.575 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| express | 270/270 (full) | 0.725 (n=40) / 0.950 vs 1.000 (n=20) / 0.200 | 0.320 / 0.400 / 0.640 / 0.401 |
| trpc | 1099/1099 (full) | 0.575 (n=40) / 0.000 vs 1.000 (n=2) / 0.200 | 0.240 / 0.520 / 0.680 / 0.343 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| express | 270 | 88 (0) | 36427 / 5517 | 218.7 |
| trpc | 1099 | 251 (0) | 161679 / 29839 | 1202.2 |
