# Layer 4 tournament

Step 1 (contamination) - code `fe7ed62603`, model `qwen2.5-coder:14b-instruct-q8_0`.

16 tasks, 7 arms, seeds [42, 43, 44, 45, 46, 47, 48, 49, 50, 51]; 160 cells. Contamination-flagged tasks (arm0 >= threshold): none.

## Cell status

| arm | ok | leak | error | timeout | dry_run |
|---|---|---|---|---|---|
| arm0_no_retrieval | 160 | 0 | 0 | 0 | 0 |
| arm1_hybrid_rag | 0 | 0 | 0 | 0 | 0 |
| repomap_pagerank | 0 | 0 | 0 | 0 | 0 |
| prism_full | 0 | 0 | 0 | 0 | 0 |
| prism_ask | 0 | 0 | 0 | 0 | 0 |
| normal_oracle | 0 | 0 | 0 | 0 | 0 |
| prism_oracle | 0 | 0 | 0 | 0 | 0 |

## Forward blast radius (callers within each task's max_depth)

| arm | view | n | precision | recall | F1 | parsed | Django tables exact | tables F1 | effects exact | effects F1 | context tok |
|---|---|---|---|---|---|---|---|---|---|---|---|
| arm0_no_retrieval | all | 80 | 0.052 | 0.133 | 0.071 | 1.000 | 1.000 | 1.000 | 0.500 | 0.797 | 17.000 |
| arm0_no_retrieval | unflagged | 80 | 0.052 | 0.133 | 0.071 | 1.000 | 1.000 | 1.000 | 0.500 | 0.797 | 17.000 |

## Backward contract adaptation (gating tests green)

| arm | view | n | pass rate | syntax ok | context tok | tokens / pass | passes / 1k tok |
|---|---|---|---|---|---|---|---|
| arm0_no_retrieval | all | 80 | 0.000 | 0.000 | 17.000 | - | 0.000 |
| arm0_no_retrieval | unflagged | 80 | 0.000 | 0.000 | 17.000 | - | 0.000 |

## Contamination (arm0_no_retrieval)

Flagged at backward pass rate >= 0.5 or forward mean F1 >= 0.5.

| task | focal | arm0 score | seeds | flagged |
|---|---|---|---|---|
| l4_bwd_django_01 | `django.db.models.query.QuerySet.update` | 0.000 | 10 | no |
| l4_bwd_django_02 | `django.core.cache.backends.locmem.LocMemCache.incr` | 0.000 | 10 | no |
| l4_bwd_express_01 | `lib.response.location` | 0.000 | 10 | no |
| l4_bwd_express_02 | `lib.response.format` | 0.000 | 10 | no |
| l4_bwd_fastapi_01 | `fastapi.utils.deep_dict_update` | 0.000 | 10 | no |
| l4_bwd_fastapi_02 | `fastapi.dependencies.utils.get_flat_dependant` | 0.000 | 10 | no |
| l4_bwd_trpc_01 | `core.internals.mergeRouters.mergeRouters` | 0.000 | 10 | no |
| l4_bwd_trpc_02 | `observable.operators.share.share` | 0.000 | 10 | no |
| l4_fwd_django_01 | `django.contrib.sessions.backends.db.SessionStore.save` | 0.196 | 10 | no |
| l4_fwd_django_02 | `django.db.migrations.recorder.MigrationRecorder.record_applied` | 0.080 | 10 | no |
| l4_fwd_express_01 | `lib.response.send` | 0.104 | 10 | no |
| l4_fwd_express_02 | `lib.response.header` | 0.054 | 10 | no |
| l4_fwd_fastapi_01 | `fastapi.dependencies.utils.get_dependant` | 0.024 | 10 | no |
| l4_fwd_fastapi_02 | `fastapi.openapi.utils.get_openapi_path` | 0.111 | 10 | no |
| l4_fwd_trpc_01 | `error.TRPCError.getTRPCErrorFromUnknown` | 0.000 | 10 | no |
| l4_fwd_trpc_02 | `shared.getErrorShape.getErrorShape` | 0.000 | 10 | no |

## Task x arm (mean F1 forward, pass rate backward)

| task | arm0_no_retrieval | arm1_hybrid_rag | repomap_pagerank | prism_full | prism_ask | normal_oracle | prism_oracle |
|---|---|---|---|---|---|---|---|
| l4_bwd_django_01 | 0.000 | - | - | - | - | - | - |
| l4_bwd_django_02 | 0.000 | - | - | - | - | - | - |
| l4_bwd_express_01 | 0.000 | - | - | - | - | - | - |
| l4_bwd_express_02 | 0.000 | - | - | - | - | - | - |
| l4_bwd_fastapi_01 | 0.000 | - | - | - | - | - | - |
| l4_bwd_fastapi_02 | 0.000 | - | - | - | - | - | - |
| l4_bwd_trpc_01 | 0.000 | - | - | - | - | - | - |
| l4_bwd_trpc_02 | 0.000 | - | - | - | - | - | - |
| l4_fwd_django_01 | 0.196 | - | - | - | - | - | - |
| l4_fwd_django_02 | 0.080 | - | - | - | - | - | - |
| l4_fwd_express_01 | 0.104 | - | - | - | - | - | - |
| l4_fwd_express_02 | 0.054 | - | - | - | - | - | - |
| l4_fwd_fastapi_01 | 0.024 | - | - | - | - | - | - |
| l4_fwd_fastapi_02 | 0.111 | - | - | - | - | - | - |
| l4_fwd_trpc_01 | 0.000 | - | - | - | - | - | - |
| l4_fwd_trpc_02 | 0.000 | - | - | - | - | - | - |
