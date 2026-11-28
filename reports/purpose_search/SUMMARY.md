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

## Q2E unseeded behavioural questions (q09-q14)

| question | subject | subject rank BASE / +SUM / +SUM+RES | pool recall BASE / +SUM / +SUM+RES |
|---|---|---|---|
| q09 | `fastapi.routing.APIRouter.include_router` | 3 / 8 / 8 | 1.00 / 1.00 / 1.00 |
| q10 | `fastapi.security.http.HTTPBasic.__call__` | 1 / 1 / 1 | 1.00 / 1.00 / 1.00 |
| q11 | `fastapi.openapi.utils.get_fields_from_routes` | 48 / 50 / 50 | 0.00 / 0.00 / 0.00 |
| q12 | `django.core.cache.backends.locmem.LocMemCache.get` | 15 / 18 / 17 | 0.75 / 0.75 / 0.75 |
| q13 | `django.contrib.auth.models.PermissionsMixin.has_perm` | 30 / 33 / 34 | 0.00 / 0.33 / 0.33 |
| q14 | `django.http.request.HttpRequest.get_host` | 5 / 6 / 3 | 1.00 / 1.00 / 1.00 |

Full gold ranks per arm are in `<repo>.json` (`search.<arm>.per_question[].ranks`).

## Direct metrics (summaries alone)

| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | query matching hit@1 / hit@5 / hit@20 / mrr |
|---|---|---|---|
| django | 14878/14878 (full) | 0.950 (n=40) / 1.000 vs 1.000 (n=9) / 0.200 | 0.333 / 1.000 / 1.000 / 0.667 |
| fastapi | 2185/2185 (full) | 0.750 (n=40) / 1.000 vs 1.000 (n=5) / 0.200 | 0.333 / 0.333 / 0.667 / 0.370 |
| fastapi_template | 71/71 (full) | 0.919 (n=37) / 0.950 vs 0.950 (n=20) / 0.200 | 0.333 / 0.800 / 1.000 / 0.515 |
| styleguide | 203/203 (full) | 0.889 (n=36) / - vs - (n=0) / 0.200 | 0.533 / 0.833 / 0.933 / 0.649 |

## Cost

| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |
|---|---|---|---|---|
| django | 14878 | 1353 (1) | 2179534 / 393564 | 6521.6 |
| fastapi | 2185 | 229 (0) | 324882 / 60799 | 966.6 |
| fastapi_template | 71 | 63 (0) | 18665 / 2257 | 27.2 |
| styleguide | 203 | 53 (0) | 36118 / 5464 | 74.7 |
