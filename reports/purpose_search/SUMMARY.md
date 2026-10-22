# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:7b-instruct-q8_0. BASE is the shipped `NLQIndex`; the other arms rebuild its BM25 documents with the extra text, nothing else changed. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 2 (60 questions: fastapi_template, styleguide)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.833 | 0.983 | 0.983 | 0.678 |
| +RES | 0.833 | 0.983 | 0.983 | 0.678 |
| +SUM | 0.917 | 1.000 | 1.000 | 0.721 |
| +SUM+RES | 0.917 | 1.000 | 1.000 | 0.721 |

`fastapi_template` (30 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.800 | 1.000 | 1.000 | 0.681 |
| +RES | 0.800 | 1.000 | 1.000 | 0.681 |
| +SUM | 0.833 | 1.000 | 1.000 | 0.700 |
| +SUM+RES | 0.833 | 1.000 | 1.000 | 0.700 |

`styleguide` (30 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.867 | 0.967 | 0.967 | 0.674 |
| +RES | 0.867 | 0.967 | 0.967 | 0.674 |
| +SUM | 1.000 | 1.000 | 1.000 | 0.742 |
| +SUM+RES | 1.000 | 1.000 | 1.000 | 0.742 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| fastapi_template | 71/71 (full) | 0.919 (n=37) / 0.950 vs 0.950 (n=20) / 0.200 | 0.333 / 0.800 / 1.000 / 0.515 |
| styleguide | 203/203 (full) | 0.889 (n=36) / - vs - (n=0) / 0.200 | 0.533 / 0.833 / 0.933 / 0.649 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| fastapi_template | 71 | 63 (0) | 18665 / 2257 | 27.2 |
| styleguide | 203 | 53 (0) | 36118 / 5464 | 74.7 |
