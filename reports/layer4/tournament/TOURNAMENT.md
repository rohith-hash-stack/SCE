# Layer 4 tournament

Code `22e1c63f93`, model `qwen2.5-coder:14b-instruct-q8_0` (Ollama 0.34.0, num_ctx 24576), seeds 42,43,44,45,46,47,48,49,50,51. Resumed across the declared code change 'leak check v2' (fe7ed62603 -> 22e1c63f93): leak cells from the older check were re-evaluated, ok cells kept. Complete.

16 tasks, 7 arms, seeds [42, 43, 44, 45, 46, 47, 48, 49, 50, 51]; 1120 cells. Contamination-flagged tasks (arm0 >= threshold): none.

## Cell status

| arm | ok | leak | error | timeout | dry_run |
|---|---|---|---|---|---|
| arm0_no_retrieval | 160 | 0 | 0 | 0 | 0 |
| arm1_hybrid_rag | 160 | 0 | 0 | 0 | 0 |
| repomap_pagerank | 160 | 0 | 0 | 0 | 0 |
| prism_full | 140 | 20 | 0 | 0 | 0 |
| prism_ask | 160 | 0 | 0 | 0 | 0 |
| normal_oracle | 160 | 0 | 0 | 0 | 0 |
| prism_oracle | 160 | 0 | 0 | 0 | 0 |

## Leak cells (held back, never sent to the model)

| task | arm | cells | leak check | body lines | test files | trace keys | prompt | first body line |
|---|---|---|---|---|---|---|---|---|
| l4_bwd_django_01 | prism_full | 10 | v2 | 1 | - | - | - | `f"Cannot update when ordering by an aggregate: {annotation}"` |
| l4_bwd_fastapi_02 | prism_full | 10 | v2 | 1 | - | - | - | `path_params=dependant.path_params.copy(),` |

## Forward blast radius (callers within each task's max_depth)

| arm | view | n | precision | recall | F1 | parsed | Django tables exact | tables F1 | effects exact | effects F1 | context tok |
|---|---|---|---|---|---|---|---|---|---|---|---|
| arm0_no_retrieval | all | 80 | 0.052 | 0.133 | 0.071 | 1.000 | 1.000 | 1.000 | 0.500 | 0.797 | 17.000 |
| arm0_no_retrieval | unflagged | 80 | 0.052 | 0.133 | 0.071 | 1.000 | 1.000 | 1.000 | 0.500 | 0.797 | 17.000 |
| arm1_hybrid_rag | all | 80 | 0.359 | 0.296 | 0.296 | 1.000 | 1.000 | 1.000 | 0.350 | 0.835 | 6752 |
| arm1_hybrid_rag | unflagged | 80 | 0.359 | 0.296 | 0.296 | 1.000 | 1.000 | 1.000 | 0.350 | 0.835 | 6752 |
| repomap_pagerank | all | 80 | 0.099 | 0.135 | 0.096 | 1.000 | 1.000 | 1.000 | 0.250 | 0.702 | 3580 |
| repomap_pagerank | unflagged | 80 | 0.099 | 0.135 | 0.096 | 1.000 | 1.000 | 1.000 | 0.250 | 0.702 | 3580 |
| prism_full | all | 80 | 0.540 | 0.259 | 0.321 | 1.000 | 0.950 | 0.950 | 0.150 | 0.752 | 4973 |
| prism_full | unflagged | 80 | 0.540 | 0.259 | 0.321 | 1.000 | 0.950 | 0.950 | 0.150 | 0.752 | 4973 |
| prism_ask | all | 80 | 0.432 | 0.317 | 0.329 | 1.000 | 0.500 | 0.500 | 0.250 | 0.757 | 7746 |
| prism_ask | unflagged | 80 | 0.432 | 0.317 | 0.329 | 1.000 | 0.500 | 0.500 | 0.250 | 0.757 | 7746 |
| normal_oracle | all | 80 | 0.918 | 0.724 | 0.783 | 1.000 | 1.000 | 1.000 | 0.300 | 0.833 | 5479 |
| normal_oracle | unflagged | 80 | 0.918 | 0.724 | 0.783 | 1.000 | 1.000 | 1.000 | 0.300 | 0.833 | 5479 |
| prism_oracle | all | 80 | 0.918 | 0.724 | 0.783 | 1.000 | 1.000 | 1.000 | 0.300 | 0.825 | 5479 |
| prism_oracle | unflagged | 80 | 0.918 | 0.724 | 0.783 | 1.000 | 1.000 | 1.000 | 0.300 | 0.825 | 5479 |

## Backward contract adaptation (gating tests green)

| arm | view | n | pass rate | syntax ok | context tok | tokens / pass | passes / 1k tok |
|---|---|---|---|---|---|---|---|
| arm0_no_retrieval | all | 80 | 0.000 | 0.000 | 17.000 | - | 0.000 |
| arm0_no_retrieval | unflagged | 80 | 0.000 | 0.000 | 17.000 | - | 0.000 |
| arm1_hybrid_rag | all | 80 | 0.000 | 0.000 | 7041 | - | 0.000 |
| arm1_hybrid_rag | unflagged | 80 | 0.000 | 0.000 | 7041 | - | 0.000 |
| repomap_pagerank | all | 80 | 0.000 | 0.000 | 3422 | - | 0.000 |
| repomap_pagerank | unflagged | 80 | 0.000 | 0.000 | 3422 | - | 0.000 |
| prism_full | all | 60 | 0.000 | 0.000 | 2426 | - | 0.000 |
| prism_full | unflagged | 60 | 0.000 | 0.000 | 2426 | - | 0.000 |
| prism_ask | all | 80 | 0.000 | 0.000 | 8514 | - | 0.000 |
| prism_ask | unflagged | 80 | 0.000 | 0.000 | 8514 | - | 0.000 |
| normal_oracle | all | 80 | 0.000 | 0.000 | 3154 | - | 0.000 |
| normal_oracle | unflagged | 80 | 0.000 | 0.000 | 3154 | - | 0.000 |
| prism_oracle | all | 80 | 0.000 | 0.000 | 3154 | - | 0.000 |
| prism_oracle | unflagged | 80 | 0.000 | 0.000 | 3154 | - | 0.000 |

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
| l4_bwd_django_01 | 0.000 | 0.000 | 0.000 | - | 0.000 | 0.000 | 0.000 |
| l4_bwd_django_02 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| l4_bwd_express_01 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| l4_bwd_express_02 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| l4_bwd_fastapi_01 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| l4_bwd_fastapi_02 | 0.000 | 0.000 | 0.000 | - | 0.000 | 0.000 | 0.000 |
| l4_bwd_trpc_01 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| l4_bwd_trpc_02 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| l4_fwd_django_01 | 0.196 | 0.447 | 0.150 | 0.250 | 0.192 | 0.660 | 0.660 |
| l4_fwd_django_02 | 0.080 | 0.247 | 0.020 | 0.581 | 0.384 | 0.982 | 0.982 |
| l4_fwd_express_01 | 0.104 | 0.370 | 0.000 | 0.372 | 0.484 | 0.652 | 0.652 |
| l4_fwd_express_02 | 0.054 | 0.157 | 0.000 | 0.000 | 0.506 | 0.811 | 0.811 |
| l4_fwd_fastapi_01 | 0.024 | 0.000 | 0.000 | 0.326 | 0.154 | 0.510 | 0.510 |
| l4_fwd_fastapi_02 | 0.111 | 0.113 | 0.249 | 0.244 | 0.142 | 0.867 | 0.867 |
| l4_fwd_trpc_01 | 0.000 | 0.452 | 0.159 | 0.436 | 0.413 | 0.865 | 0.865 |
| l4_fwd_trpc_02 | 0.000 | 0.582 | 0.187 | 0.359 | 0.358 | 0.914 | 0.914 |
