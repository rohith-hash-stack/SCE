# Purpose-enriched search

Summarizer: ollama/qwen2.5-coder:14b-instruct-q8_0. BASE is the shipped `NLQIndex`; +RES / +SUM rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + resolved callees of the top-8.

## Held-out set 2 (60 questions: fastapi_template, styleguide)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.833 | 0.983 | 0.983 | 0.678 |
| +RES | 0.833 | 0.983 | 0.983 | 0.678 |
| +SUM | 0.933 | 1.000 | 1.000 | 0.756 |
| +SUM+RES | 0.933 | 1.000 | 1.000 | 0.757 |
| FIELD w=0.5 | 0.933 | 1.000 | 1.000 | 0.748 |
| FIELD w=1.0 | 0.950 | 1.000 | 1.000 | 0.763 |

`fastapi_template` (30 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.800 | 1.000 | 1.000 | 0.681 |
| +RES | 0.800 | 1.000 | 1.000 | 0.681 |
| +SUM | 0.900 | 1.000 | 1.000 | 0.771 |
| +SUM+RES | 0.900 | 1.000 | 1.000 | 0.773 |
| FIELD w=0.5 | 0.933 | 1.000 | 1.000 | 0.764 |
| FIELD w=1.0 | 0.967 | 1.000 | 1.000 | 0.781 |

`styleguide` (30 questions)

| arm | recall@5 | recall@20 | pool_recall | mrr |
|---|---|---|---|---|
| BASE | 0.867 | 0.967 | 0.967 | 0.674 |
| +RES | 0.867 | 0.967 | 0.967 | 0.674 |
| +SUM | 0.967 | 1.000 | 1.000 | 0.741 |
| +SUM+RES | 0.967 | 1.000 | 1.000 | 0.741 |
| FIELD w=0.5 | 0.933 | 1.000 | 1.000 | 0.731 |
| FIELD w=1.0 | 0.933 | 1.000 | 1.000 | 0.746 |

## Q2E unseeded behavioural questions (q09-q14)

| question | subject | subject rank BASE / +SUM / FIELD w=0.5 / FIELD w=1.0 | pool recall BASE / +SUM / FIELD w=0.5 / FIELD w=1.0 |
|---|---|---|---|
| q09 | `fastapi.routing.APIRouter.include_router` | 3 / 8 / 4 / 5 | 1.00 / 1.00 / 1.00 / 1.00 |
| q10 | `fastapi.security.http.HTTPBasic.__call__` | 1 / 1 / 1 / 1 | 1.00 / 1.00 / 1.00 / 1.00 |
| q11 | `fastapi.openapi.utils.get_fields_from_routes` | 48 / 48 / 51 / 71 | 0.00 / 0.00 / 0.50 / 0.50 |

Every gold symbol's rank per arm (`-` = not ranked):

| question | gold symbol | BASE | +SUM | FIELD w=0.5 | FIELD w=1.0 |
|---|---|---|---|---|---|
| q09 | `fastapi.routing.APIRouter.include_router` | 3 | 8 | 4 | 5 |
| q09 | `fastapi.routing.APIRouter.add_api_route` | 202 | 228 | 189 | 187 |
| q09 | `fastapi.routing.APIRouter.add_api_websocket_route` | 5 | 2 | 2 | 2 |
| q10 | `fastapi.security.http.HTTPBasic.__call__` | 1 | 1 | 1 | 1 |
| q10 | `fastapi.security.utils.get_authorization_scheme_param` | 45 | 53 | 31 | 27 |
| q11 | `fastapi.openapi.utils.get_fields_from_routes` | 48 | 48 | 51 | 71 |
| q11 | `fastapi.dependencies.utils.get_flat_params` | 1629 | 854 | 989 | 918 |

## Summary lint (share of summaries flagged)

| repo | first pass: axis / filler / identifier / callee list / any | repaired | final: any |
|---|---|---|---|
| fastapi | 0.021 / 0.000 / 0.046 / 0.000 / 0.067 | 121 | 0.011 |
| fastapi_template | 0.000 / 0.000 / 0.085 / 0.000 / 0.085 | 4 | 0.000 |
| styleguide | 0.000 / 0.000 / 0.020 / 0.000 / 0.020 | 3 | 0.005 |

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| fastapi | 2186/2186 (full) | 0.850 (n=40) / 1.000 vs 1.000 (n=4) / 0.200 | 0.333 / 0.333 / 0.667 / 0.371 |
| fastapi_template | 71/71 (full) | 1.000 (n=37) / 1.000 vs 1.000 (n=20) / 0.200 | 0.467 / 0.833 / 0.967 / 0.633 |
| styleguide | 203/203 (full) | 0.972 (n=36) / - vs - (n=0) / 0.200 | 0.533 / 0.733 / 0.967 / 0.635 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| fastapi | 2186 | 396 (0) | 308095 / 64127 | 2611.7 |
| fastapi_template | 71 | 67 (0) | 17624 / 2210 | 76.6 |
| styleguide | 203 | 57 (0) | 31759 / 5611 | 230.1 |
