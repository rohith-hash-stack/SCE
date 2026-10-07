# M4 supporting tables

_Source: `reports/harness_m4/<corpus>/cells.parquet` at `8353a24`. 3-seed (42/43/44) mean TSR. 95% CI: task-level cluster bootstrap, 10,000 resamples, percentile. Corpora are never pooled._

## Oracle ceiling (model extraction ceiling)

| corpus | type | Oracle mean [CI] | delivered_symbols_resolved (mean) | uniform_cpi | cells delivered < gold | cells TSR < 1 with full delivery | missed symbols (cells) |
|---|---|---|---|---|---|---|---|
| fastapi | T2 | 0.931 [0.833, 1.000] | 3.25 | 1.000 | 0 | 5 of 72 | `fastapi.openapi.utils.generate_operation_summary` (3); `fastapi.dependencies.utils.request_params_to_args` (1); `fastapi.dependencies.utils.request_body_to_args` (1); `fastapi.dependencies.utils.solve_generator` (1); `fastapi.dependencies.utils.get_flat_params` (1) |
| fastapi | T5 | 0.557 [0.415, 0.706] | 4.00 | 1.000 | 0 | 19 of 24 | 11 distinct gold symbols missed over 19 cells |
| django | T2 | 0.767 [0.600, 0.917] | 6.45 | 1.000 | 0 | 14 of 60 | `django.db.models.deletion.Collector.get_del_batches` (3); `django.utils.translation.trans_real.DjangoTranslation` (3); `django.http.response.ResponseHeaders` (3); `django.db.backends.base.base.BaseDatabaseWrapper.close_if_health_check_failed` (2); `django.http.cookie.SimpleCookie` (2); `django.core.handlers.base.BaseHandler.get_response` (1); `django.utils.http._urlparse` (1); `django.core.handlers.base.BaseHandler.check_response` (1) |
| django | T5 | 0.693 [0.475, 0.871] | 5.62 | 1.000 | 0 | 14 of 24 | 22 distinct gold symbols missed over 14 cells |
| express | T2 | 1.000 [1.000, 1.000] | 2.35 | 1.000 | 0 | 0 of 60 | — |
| express (n=2) | T5 | 0.833 [0.667, 1.000] | 2.50 | 1.000 | 0 | 3 of 6 | 1 distinct gold symbols missed over 3 cells |
| trpc | T2 | 0.907 [0.827, 0.973] | 3.48 | 1.000 | 0 | 7 of 75 | `core.internals.procedureBuilder.createNewBuilder` (3); `error.TRPCError.TRPCError` (1); `core.middleware.createInputMiddleware` (1); `core.router.createCallerFactory` (1); `http.getHTTPStatusCode.getStatusCodeFromKey` (1) |
| trpc | T5 | 0.570 [0.446, 0.689] | 6.86 | 1.000 | 0 | 35 of 42 | 22 distinct gold symbols missed over 35 cells |

Per-cell T5 misses are listed in `summary.json` → `oracle_ceiling.<corpus>.T5.missed_cell_detail`.

## Prompt-gold gap and extraction loss

Extraction loss = mean answer gold recall − mean TSR. On T2, TSR is strict (every gold named), so the loss is what the all-or-nothing rule costs. On T5, TSR **is** the fractional answer recall (`scorer._score_t5`), so the loss is 0 by construction and `answer_gold_recall` is not stored for T5 cells; the recall below is re-derived from the answers to confirm it.

| corpus | type | arm | answer gold recall | TSR | extraction loss |
|---|---|---|---|---|---|
| fastapi | T2 | arm0 | 0.626 | 0.389 | 0.237 |
| fastapi | T2 | arm1 | 0.780 | 0.556 | 0.225 |
| fastapi | T2 | arm4 | 0.721 | 0.500 | 0.221 |
| fastapi | T2 | arm2 | 0.792 | 0.583 | 0.209 |
| fastapi | T2 | arm3 | 0.946 | 0.819 | 0.126 |
| fastapi | T2 | arm5 | 0.982 | 0.931 | 0.051 |
| fastapi | T2 | oracle | 0.974 | 0.931 | 0.044 |
| fastapi | T5 | arm0 | 0.006 | 0.006 | 0.000 |
| fastapi | T5 | arm1 | 0.495 | 0.495 | 0.000 |
| fastapi | T5 | arm2 | 0.418 | 0.418 | 0.000 |
| fastapi | T5 | arm3 | 0.262 | 0.262 | 0.000 |
| fastapi | T5 | arm4 | 0.042 | 0.042 | 0.000 |
| fastapi | T5 | arm5 | 0.281 | 0.281 | 0.000 |
| fastapi | T5 | oracle | 0.557 | 0.557 | 0.000 |
| django | T2 | arm4 | 0.689 | 0.233 | 0.456 |
| django | T2 | arm0 | 0.703 | 0.250 | 0.453 |
| django | T2 | arm3 | 0.792 | 0.383 | 0.409 |
| django | T2 | arm1 | 0.822 | 0.433 | 0.388 |
| django | T2 | arm2 | 0.811 | 0.433 | 0.377 |
| django | T2 | arm5 | 0.896 | 0.650 | 0.246 |
| django | T2 | oracle | 0.930 | 0.767 | 0.164 |
| django | T5 | arm0 | 0.005 | 0.005 | 0.000 |
| django | T5 | arm1 | 0.230 | 0.230 | 0.000 |
| django | T5 | arm2 | 0.279 | 0.279 | 0.000 |
| django | T5 | arm3 | 0.073 | 0.073 | 0.000 |
| django | T5 | arm4 | 0.011 | 0.011 | 0.000 |
| django | T5 | arm5 | 0.190 | 0.190 | 0.000 |
| django | T5 | oracle | 0.693 | 0.693 | 0.000 |
| express | T2 | arm0 | 0.849 | 0.600 | 0.249 |
| express | T2 | arm3 | 0.826 | 0.583 | 0.243 |
| express | T2 | arm4 | 0.839 | 0.617 | 0.222 |
| express | T2 | arm1 | 0.886 | 0.683 | 0.203 |
| express | T2 | arm2 | 0.935 | 0.833 | 0.101 |
| express | T2 | arm5 | 0.988 | 0.950 | 0.038 |
| express | T2 | oracle | 1.000 | 1.000 | 0.000 |
| express (n=2) | T5 | arm0 | 0.000 | 0.000 | 0.000 |
| express (n=2) | T5 | arm1 | 0.361 | 0.361 | 0.000 |
| express (n=2) | T5 | arm2 | 0.806 | 0.806 | 0.000 |
| express (n=2) | T5 | arm3 | 0.083 | 0.083 | 0.000 |
| express (n=2) | T5 | arm4 | 0.000 | 0.000 | 0.000 |
| express (n=2) | T5 | arm5 | 0.250 | 0.250 | 0.000 |
| express (n=2) | T5 | oracle | 0.833 | 0.833 | 0.000 |
| trpc | T2 | arm3 | 0.707 | 0.253 | 0.454 |
| trpc | T2 | arm0 | 0.625 | 0.200 | 0.425 |
| trpc | T2 | arm4 | 0.622 | 0.240 | 0.382 |
| trpc | T2 | arm2 | 0.786 | 0.413 | 0.373 |
| trpc | T2 | arm1 | 0.758 | 0.400 | 0.358 |
| trpc | T2 | arm5 | 0.953 | 0.840 | 0.113 |
| trpc | T2 | oracle | 0.977 | 0.907 | 0.070 |
| trpc | T5 | arm0 | 0.000 | 0.000 | 0.000 |
| trpc | T5 | arm1 | 0.252 | 0.252 | 0.000 |
| trpc | T5 | arm2 | 0.262 | 0.262 | 0.000 |
| trpc | T5 | arm3 | 0.019 | 0.019 | 0.000 |
| trpc | T5 | arm4 | 0.000 | 0.000 | 0.000 |
| trpc | T5 | arm5 | 0.151 | 0.151 | 0.000 |
| trpc | T5 | oracle | 0.570 | 0.570 | 0.000 |

### Oracle: ceiling gap

| corpus | type | Oracle recall | Oracle TSR | 1 − TSR | cells TSR < 1 | cells TSR < 1 and recall > 0.5 |
|---|---|---|---|---|---|---|
| fastapi | T2 | 0.974 | 0.931 | 0.069 | 0.069 | 0.042 |
| fastapi | T5 | 0.557 | 0.557 | 0.443 | 0.792 | 0.208 |
| django | T2 | 0.930 | 0.767 | 0.233 | 0.233 | 0.200 |
| django | T5 | 0.693 | 0.693 | 0.307 | 0.583 | 0.333 |
| express | T2 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| express (n=2) | T5 | 0.833 | 0.833 | 0.167 | 0.500 | 0.500 |
| trpc | T2 | 0.977 | 0.907 | 0.093 | 0.093 | 0.093 |
| trpc | T5 | 0.570 | 0.570 | 0.430 | 0.833 | 0.310 |

## Arm 3 (LSP) deep dive — T2

| corpus | TSR [CI] | context precision | context recall | delivered tokens | e2e latency (s) | index (s) | Δ vs A0 (p_holm) | Δ vs A5 (p_holm) |
|---|---|---|---|---|---|---|---|---|
| fastapi | 0.819 [0.667, 0.944] | 0.131 | 0.990 | 1317 | 11.7 | 7.3 | +0.431 (0.0000, sig) | -0.111 (0.4812) |
| django | 0.383 [0.200, 0.583] | 0.156 | 0.848 | 1228 | 11.8 | 21.8 | +0.133 (0.5278) | -0.267 (0.1900) |
| express | 0.583 [0.383, 0.783] | 0.053 | 0.602 | 590 | 9.1 | 5.2 | -0.017 (1.0000) | -0.367 (0.0000, sig) |
| trpc | 0.253 [0.120, 0.413] | 0.093 | 0.924 | 1095 | 11.3 | 4.2 | +0.053 (1.0000) | -0.587 (0.0000, sig) |

## Arm 4 metric gap

| corpus | Arm 4 items | items with symbols | context_recall mean | uniform_cpi mean | cleanliness null cells | delivered-symbol metrics usable |
|---|---|---|---|---|---|---|
| fastapi | 422 | 215 | 0.424 | 0.477 | 18 of 96 | yes |
| django | 387 | 140 | 0.264 | 0.298 | 36 of 84 | yes |
| express | 217 | 0 | 0.000 | 0.000 | 66 of 66 | **no** |
| trpc | 335 | 0 | 0.000 | 0.000 | 117 of 117 | **no** |

On Express and tRPC, Arm 4's grep/read results carry no symbol labels, so context recall and uniform_cpi read 0 and cleanliness is undefined (null) for every Arm 4 cell. TSR is unaffected: T2/T5 scoring reads only the answer. Not fixed; documented for methods.

## Latency and token efficiency

Latency = per-cell `l_e2e_ms` (retrieve + generate), indexing excluded. Sorted by corpus, task type, mean latency.

| corpus | type | arm | mean (s) | median (s) | p95 (s) | prompt tokens | delivered tokens | relevant token density |
|---|---|---|---|---|---|---|---|---|
| fastapi | T2 | oracle | 11.6 | 10.6 | 22.0 | 1476 | 1246 | 1.000 |
| fastapi | T2 | arm3 | 11.7 | 10.7 | 17.4 | 1549 | 1317 | 0.717 |
| fastapi | T2 | arm0 | 22.2 | 7.0 | 157.5 | 224 | 0 | — |
| fastapi | T2 | arm5 | 24.1 | 19.1 | 54.3 | 2268 | 2038 | 0.553 |
| fastapi | T2 | arm2 | 31.3 | 26.1 | 44.1 | 11892 | 11656 | 0.082 |
| fastapi | T2 | arm4 | 35.5 | 24.8 | 83.3 | 1950 | 942 | 0.622 |
| fastapi | T2 | arm1 | 69.1 | 58.1 | 98.8 | 11362 | 11127 | 0.048 |
| fastapi | T5 | arm0 | 5.9 | 5.7 | 7.1 | 135 | 0 | — |
| fastapi | T5 | oracle | 11.0 | 10.2 | 15.2 | 3008 | 2867 | 1.000 |
| fastapi | T5 | arm3 | 15.2 | 12.2 | 20.5 | 1771 | 1627 | 0.430 |
| fastapi | T5 | arm5 | 26.5 | 21.4 | 44.6 | 2922 | 2780 | 0.572 |
| fastapi | T5 | arm4 | 26.7 | 24.9 | 47.7 | 1461 | 841 | 0.375 |
| fastapi | T5 | arm2 | 29.8 | 27.8 | 44.0 | 12409 | 12257 | 0.159 |
| fastapi | T5 | arm1 | 58.8 | 58.8 | 63.3 | 12458 | 12306 | 0.254 |
| django | T2 | arm0 | 8.0 | 7.8 | 10.5 | 309 | 0 | — |
| django | T2 | arm3 | 11.8 | 11.4 | 14.7 | 1542 | 1228 | 0.951 |
| django | T2 | oracle | 13.2 | 13.0 | 20.7 | 3400 | 3084 | 1.000 |
| django | T2 | arm4 | 25.2 | 22.2 | 45.7 | 1686 | 636 | 0.435 |
| django | T2 | arm5 | 26.8 | 23.8 | 38.8 | 3285 | 2969 | 0.830 |
| django | T2 | arm2 | 27.8 | 27.8 | 34.4 | 12248 | 11914 | 0.122 |
| django | T2 | arm1 | 51.5 | 48.0 | 54.6 | 9740 | 9410 | 0.176 |
| django | T5 | arm0 | 8.9 | 6.0 | 10.7 | 190 | 0 | — |
| django | T5 | oracle | 11.0 | 10.6 | 14.6 | 1915 | 1718 | 1.000 |
| django | T5 | arm3 | 11.6 | 10.6 | 19.2 | 1833 | 1637 | 0.712 |
| django | T5 | arm5 | 26.5 | 26.2 | 33.6 | 4836 | 4639 | 0.221 |
| django | T5 | arm2 | 67.3 | 41.1 | 283.8 | 12299 | 12083 | 0.038 |
| django | T5 | arm1 | 68.2 | 56.1 | 70.9 | 10794 | 10587 | 0.055 |
| django | T5 | arm4 | 108.7 | 28.2 | 809.5 | 2464 | 1648 | 0.294 |
| express | T2 | arm0 | 5.6 | 5.3 | 7.4 | 265 | 0 | — |
| express | T2 | oracle | 7.1 | 6.8 | 9.9 | 793 | 524 | 1.000 |
| express | T2 | arm3 | 9.1 | 8.8 | 11.3 | 861 | 590 | 0.779 |
| express | T2 | arm5 | 15.6 | 14.3 | 24.9 | 1106 | 837 | 0.712 |
| express | T2 | arm4 | 26.1 | 19.6 | 60.9 | 1443 | 488 | 0.000 |
| express | T2 | arm2 | 30.7 | 25.6 | 29.2 | 13038 | 12769 | 0.034 |
| express | T2 | arm1 | 49.7 | 49.5 | 52.9 | 13223 | 12953 | 0.021 |
| express | T5 | arm0 | 5.6 | 5.1 | 8.3 | 130 | 0 | — |
| express | T5 | oracle | 7.1 | 6.9 | 8.6 | 1162 | 1027 | 1.000 |
| express | T5 | arm3 | 9.1 | 8.9 | 10.5 | 612 | 473 | 0.420 |
| express | T5 | arm5 | 18.0 | 17.6 | 25.1 | 1441 | 1306 | 0.618 |
| express | T5 | arm4 | 29.0 | 27.8 | 38.6 | 527 | 110 | 0.000 |
| express | T5 | arm1 | 54.1 | 51.3 | 66.9 | 12785 | 12650 | 0.076 |
| express | T5 | arm2 | 74.7 | 27.6 | 243.2 | 12773 | 12638 | 0.077 |
| trpc | T2 | arm0 | 6.4 | 6.4 | 8.1 | 266 | 0 | — |
| trpc | T2 | oracle | 8.8 | 8.5 | 12.6 | 1104 | 834 | 1.000 |
| trpc | T2 | arm3 | 11.3 | 10.7 | 15.1 | 1371 | 1095 | 0.514 |
| trpc | T2 | arm5 | 20.4 | 20.1 | 28.8 | 1597 | 1327 | 0.752 |
| trpc | T2 | arm4 | 25.7 | 18.7 | 59.5 | 1613 | 657 | 0.000 |
| trpc | T2 | arm2 | 37.0 | 28.3 | 36.2 | 12969 | 12698 | 0.051 |
| trpc | T2 | arm1 | 58.9 | 54.4 | 60.5 | 13241 | 12971 | 0.049 |
| trpc | T5 | arm0 | 5.5 | 5.4 | 7.0 | 134 | 0 | — |
| trpc | T5 | oracle | 11.2 | 9.2 | 21.1 | 2869 | 2731 | 1.000 |
| trpc | T5 | arm3 | 12.5 | 11.9 | 17.0 | 1289 | 1144 | 0.288 |
| trpc | T5 | arm5 | 20.3 | 19.3 | 26.4 | 1817 | 1678 | 0.488 |
| trpc | T5 | arm4 | 25.0 | 27.2 | 41.2 | 436 | 62 | 0.000 |
| trpc | T5 | arm2 | 29.2 | 29.1 | 32.7 | 12868 | 12730 | 0.200 |
| trpc | T5 | arm1 | 67.6 | 53.2 | 57.3 | 13113 | 12975 | 0.160 |

Relevant token density is null for cells with no delivered context (Arm 0 always; Arm 4 on TS corpora).

