# prism_full forward recall funnel (Layer 4 tournament)

Per task: gold callers (traced, within max_depth) surviving each stage; stages 4-5 are means over the 10 seeds. `lost: Turn 1` / `lost: budget` split the V_cand -> packed loss by replay (module docstring).

| task | gold | in index | in static graph | in V_cand | packed | emitted | from context | without context | packed, not emitted | lost: Turn 1 | lost: budget | recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| l4_fwd_django_01 | 5 | 5 | 3 | 4 | 1.4 | 1.0 | 1.0 | 0.0 | 0.4 | 2.6 | 0.0 | 0.2 |
| l4_fwd_django_02 | 5 | 5 | 5 | 5 | 4.0 | 3.1 | 3.0 | 0.1 | 1.0 | 0.9 | 0.1 | 0.62 |
| l4_fwd_express_01 | 12 | 12 | 6 | 6 | 4.8 | 4.5 | 4.5 | 0.0 | 0.3 | 1.2 | 0.0 | 0.37 |
| l4_fwd_express_02 | 11 | 11 | 10 | 11 | 8.2 | 7.9 | 7.9 | 0.0 | 0.3 | 2.8 | 0.0 | 0.72 |
| l4_fwd_fastapi_01 | 10 | 10 | 9 | 8 | 2.8 | 1.7 | 1.7 | 0.0 | 1.1 | 3.1 | 2.1 | 0.17 |
| l4_fwd_fastapi_02 | 3 | 3 | 2 | 2 | 1.9 | 1.9 | 1.9 | 0.0 | 0.0 | 0.1 | 0.0 | 0.63 |
| l4_fwd_trpc_01 | 10 | 10 | 7 | 7 | 4.9 | 4.8 | 4.8 | 0.0 | 0.1 | 1.7 | 0.4 | 0.48 |
| l4_fwd_trpc_02 | 7 | 7 | 6 | 6 | 4.2 | 3.5 | 3.5 | 0.0 | 0.7 | 1.6 | 0.2 | 0.5 |
| **total** | 63 | 63 | 48 | 49 | 32.2 | 28.4 | 28.3 | 0.1 | 3.9 | 14.0 | 2.8 | 0.461 |

Funnel over all 8 tasks (share of gold): gold 63 -> in static graph 76.2% -> in V_cand 77.8% -> packed 51.1% -> emitted 45.1% (of which from context 44.9%, without it 0.2%).

## Why gold callers are not in V_cand

| task | gold caller | hop | reason |
|---|---|---|---|
| l4_fwd_django_01 | `django.contrib.sessions.middleware.SessionMiddleware.process_response` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.response.format` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.response.sendFile` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.response.sendfile` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.layer.handle` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.layer.handle_error` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.restore` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_01 | `fastapi.routing.APIRouter.add_api_route` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_01 | `fastapi.routing.get_request_handler` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_fastapi_02 | `fastapi.applications.FastAPI.setup` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_01 | `core.router.callProcedure` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_01 | `core.router.createCallerFactory` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_01 | `observable.observable.observable.error` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_02 | `observable.observable.observable.error` | None | no static caller path to the focal (call edge missing) |

Reasons, counted: no static caller path to the focal: 13; transitive caller: 1

## Turn 1 selection (proxy: packed candidates other than the anchor and its auto-included direct callees)

| task | caller rows: gold picked / offered | caller rows: other | downstream rows: gold | downstream rows: other |
|---|---|---|---|---|
| l4_fwd_django_01 | 4/30 | 13/180 | - | 46/280 |
| l4_fwd_django_02 | 40/50 | 1/80 | - | 15/130 |
| l4_fwd_express_01 | 38/50 | 3/70 | - | 0/10 |
| l4_fwd_express_02 | 82/110 | 26/90 | - | - |
| l4_fwd_fastapi_01 | 23/70 | 29/60 | - | 1/20 |
| l4_fwd_fastapi_02 | 19/20 | 16/20 | - | 0/60 |
| l4_fwd_trpc_01 | 49/70 | 55/140 | - | - |
| l4_fwd_trpc_02 | 42/60 | 31/90 | - | 0/10 |

Axis annotations Turn 1 saw on the gold candidates:

- l4_fwd_django_01 `django.contrib.sessions.backends.base.SessionBase.cycle_key`: role `caller`, `axes=S:NONE;F:LINEAR;O:COMMAND;R:BRIDGE`
- l4_fwd_django_01 `django.contrib.sessions.backends.cached_db.SessionStore.save`: role `caller`, `axes=S:NONE;F:LINEAR;O:COMMAND;R:ENTRYPOINT,PUBLIC_API`
- l4_fwd_django_01 `django.contrib.sessions.backends.db.SessionStore.create`: role `caller`, `axes=S:NONE;F:LINEAR,BATCH_LOOP;O:COMMAND;R:NONE`
- l4_fwd_django_01 `django.utils.deprecation.MiddlewareMixin.__call__`: role `caller`, `axes=S:PURE_COMPUTE;F:GUARD_EARLY_EXIT;O:QUERY;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor._migrate_all_forwards`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,BATCH_LOOP;O:TRANSFORMER;R:NONE`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor.apply_migration`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH;O:TRANSFORMER;R:NONE`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor.check_replacements`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP,ASYNC_CONCURRENT;O:COMMAND;R:ORCHESTRATOR`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor.migrate`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,ASYNC_CONCURRENT;O:TRANSFORMER;R:BRIDGE`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor.record_migration`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP;O:COMMAND;R:NONE`
- l4_fwd_express_01 `lib.response.json`: role `callee`, `axes=S:NONE;F:LINEAR;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_express_01 `lib.response.jsonp`: role `caller`, `axes=S:NONE;F:BRANCH_DISPATCH;O:QUERY;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_express_01 `lib.response.render`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,RECURSIVE;O:QUERY;R:ENTRYPOINT,PUBLIC_API`
- l4_fwd_express_01 `lib.response.sendStatus`: role `caller`, `axes=S:NONE;F:LINEAR;O:QUERY;R:ENTRYPOINT,PUBLIC_API`
- l4_fwd_express_01 `lib.router.handle`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_express_01 `lib.router.sendOptionsResponse`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:COMMAND;R:NONE`
- l4_fwd_express_02 `lib.response.append`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_express_02 `lib.response.attachment`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:FLUENT;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_express_02 `lib.response.contentType`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_express_02 `lib.response.format`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:FLUENT;R:ORCHESTRATOR`
- l4_fwd_express_02 `lib.response.json`: role `caller`, `axes=S:NONE;F:LINEAR;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_express_02 `lib.response.jsonp`: role `caller`, `axes=S:NONE;F:BRANCH_DISPATCH;O:QUERY;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_express_02 `lib.response.links`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:ENTRYPOINT,PUBLIC_API`
- l4_fwd_express_02 `lib.response.location`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_express_02 `lib.response.redirect`: role `caller`, `axes=S:NONE;F:LINEAR;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_express_02 `lib.response.send`: role `caller`, `axes=S:NONE;F:BRANCH_DISPATCH;O:FLUENT;R:BRIDGE`
- l4_fwd_express_02 `lib.router.sendOptionsResponse`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:COMMAND;R:NONE`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.get_param_sub_dependant`: role `callee`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.get_parameterless_sub_dependant`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.get_sub_dependant`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH;O:QUERY;R:NONE`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.solve_dependencies`: role `caller`, `axes=S:NONE;F:RETRY_LOOP,RECURSIVE,ASYNC_CONCURRENT;O:FACTORY,ASYNC_DEFERRED;R:NONE`
- l4_fwd_fastapi_01 `fastapi.routing.APIRoute.__init__`: role `caller`, `axes=S:NONE;F:BRANCH_DISPATCH,BATCH_LOOP;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_fastapi_01 `fastapi.routing.APIRouter.add_api_websocket_route`: role `caller`, `axes=S:NONE;F:PIPELINE;O:COMMAND;R:NONE`
- l4_fwd_fastapi_01 `fastapi.routing.APIWebSocketRoute.__init__`: role `caller`, `axes=S:NONE;F:LINEAR,BATCH_LOOP;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_fastapi_01 `fastapi.routing.get_websocket_app`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_fastapi_02 `fastapi.applications.FastAPI.openapi`: role `caller`, `axes=S:NONE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_fastapi_02 `fastapi.openapi.utils.get_openapi`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,BATCH_LOOP;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_trpc_01 `adapters.ws.applyWSSHandler`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP,ASYNC_CONCURRENT;O:AGGREGATOR;R:ORCHESTRATOR`
- l4_fwd_trpc_01 `adapters.ws.createContextAsync`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,ASYNC_CONCURRENT;O:COMMAND,ASYNC_DEFERRED;R:ORCHESTRATOR`
- l4_fwd_trpc_01 `adapters.ws.handleRequest`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,ASYNC_CONCURRENT;O:ASYNC_DEFERRED,GUARD;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_trpc_01 `adapters.ws.handleRequest.error`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_trpc_01 `core.internals.procedureBuilder.createProcedureCaller`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:AGGREGATOR;R:NONE`
- l4_fwd_trpc_01 `http.resolveHTTPResponse.inputToProcedureCall`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,ASYNC_CONCURRENT;O:AGGREGATOR,ASYNC_DEFERRED;R:ORCHESTRATOR`
- l4_fwd_trpc_01 `http.resolveHTTPResponse.resolveHTTPResponse`: role `caller`, `axes=S:PURE_COMPUTE;F:RETRY_LOOP,ASYNC_CONCURRENT;O:AGGREGATOR,ASYNC_DEFERRED;R:NONE`
- l4_fwd_trpc_02 `adapters.ws.applyWSSHandler`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP,ASYNC_CONCURRENT;O:AGGREGATOR;R:ORCHESTRATOR`
- l4_fwd_trpc_02 `adapters.ws.createContextAsync`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,ASYNC_CONCURRENT;O:COMMAND,ASYNC_DEFERRED;R:ORCHESTRATOR`
- l4_fwd_trpc_02 `adapters.ws.handleRequest`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,ASYNC_CONCURRENT;O:ASYNC_DEFERRED,GUARD;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_trpc_02 `adapters.ws.handleRequest.error`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_trpc_02 `http.resolveHTTPResponse.inputToProcedureCall`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,ASYNC_CONCURRENT;O:AGGREGATOR,ASYNC_DEFERRED;R:ORCHESTRATOR`
- l4_fwd_trpc_02 `http.resolveHTTPResponse.resolveHTTPResponse`: role `caller`, `axes=S:PURE_COMPUTE;F:RETRY_LOOP,ASYNC_CONCURRENT;O:AGGREGATOR,ASYNC_DEFERRED;R:NONE`

## Budget

With Turn 1 requesting **every** V_cand candidate (maximum budget pressure):

| task | V_cand | packed | gold in V_cand kept | gold dropped |
|---|---|---|---|---|
| l4_fwd_django_01 | 55 | 28 | 2/4 | `django.contrib.sessions.backends.cached_db.SessionStore.save`, `django.contrib.sessions.backends.db.SessionStore.create` |
| l4_fwd_django_02 | 28 | 24 | 0/5 | `django.db.migrations.executor.MigrationExecutor._migrate_all_forwards`, `django.db.migrations.executor.MigrationExecutor.apply_migration`, `django.db.migrations.executor.MigrationExecutor.check_replacements`, `django.db.migrations.executor.MigrationExecutor.migrate`, `django.db.migrations.executor.MigrationExecutor.record_migration` |
| l4_fwd_express_01 | 20 | 18 | 6/6 | - |
| l4_fwd_express_02 | 21 | 21 | 11/11 | - |
| l4_fwd_fastapi_01 | 23 | 16 | 4/8 | `fastapi.dependencies.utils.get_param_sub_dependant`, `fastapi.dependencies.utils.get_sub_dependant`, `fastapi.routing.APIRoute.__init__`, `fastapi.routing.APIWebSocketRoute.__init__` |
| l4_fwd_fastapi_02 | 20 | 9 | 2/2 | - |
| l4_fwd_trpc_01 | 23 | 20 | 5/7 | `adapters.ws.applyWSSHandler`, `core.internals.procedureBuilder.createProcedureCaller` |
| l4_fwd_trpc_02 | 18 | 14 | 5/6 | `http.resolveHTTPResponse.inputToProcedureCall` |

## Counterfactual: Design C manifest (`direction="both"`)

| task | gold | in V_cand now | both, 8000-token budget | both, no budget |
|---|---|---|---|---|
| l4_fwd_django_01 | 5 | 4 | 4 (55 cand.) | 4 (66 cand.) |
| l4_fwd_django_02 | 5 | 5 | 5 (28 cand.) | 5 (30 cand.) |
| l4_fwd_express_01 | 12 | 6 | 6 (20 cand.) | 6 (20 cand.) |
| l4_fwd_express_02 | 11 | 11 | 11 (21 cand.) | 11 (21 cand.) |
| l4_fwd_fastapi_01 | 10 | 8 | 8 (23 cand.) | 9 (50 cand.) |
| l4_fwd_fastapi_02 | 3 | 2 | 2 (20 cand.) | 2 (20 cand.) |
| l4_fwd_trpc_01 | 10 | 7 | 7 (23 cand.) | 7 (28 cand.) |
| l4_fwd_trpc_02 | 7 | 6 | 6 (18 cand.) | 6 (19 cand.) |

## Other arms (mean gold per cell: packed / emitted / emitted from context)

| task | normal_oracle | prism_ask | arm1_hybrid_rag | repomap_pagerank | arm0_no_retrieval |
|---|---|---|---|---|---|
| l4_fwd_django_01 | 5.0 / 3.0 / 3.0 | 1.0 / 1.0 / 1.0 | 3.0 / 2.0 / 2.0 | 0.0 / 1.4 / 0.0 | 0.0 / 1.8 / 0.0 |
| l4_fwd_django_02 | 5.0 / 5.0 / 5.0 | 1.0 / 2.0 / 1.0 | 1.0 / 1.0 / 1.0 | 0.0 / 0.2 / 0.0 | 0.0 / 0.8 / 0.0 |
| l4_fwd_express_01 | 12.0 / 6.0 / 6.0 | 6.0 / 4.3 / 4.3 | 5.0 / 3.2 / 3.2 | 0.0 / 0.0 / 0.0 | 0.0 / 1.4 / 0.0 |
| l4_fwd_express_02 | 11.0 / 8.4 / 8.4 | 6.0 / 5.7 / 5.7 | 5.0 / 1.2 / 1.2 | 0.0 / 0.0 / 0.0 | 0.0 / 0.7 / 0.0 |
| l4_fwd_fastapi_01 | 6.0 / 3.5 / 3.5 | 3.0 / 1.4 / 1.4 | 0.0 / 0.0 / 0.0 | 0.0 / 0.0 / 0.0 | 0.0 / 0.3 / 0.0 |
| l4_fwd_fastapi_02 | 3.0 / 2.9 / 2.9 | 0.0 / 1.0 / 0.0 | 0.0 / 1.0 / 0.0 | 0.0 / 1.4 / 0.0 | 0.0 / 1.0 / 0.0 |
| l4_fwd_trpc_01 | 10.0 / 7.7 / 7.7 | 4.0 / 3.0 / 3.0 | 5.0 / 4.9 / 4.9 | 2.0 / 1.5 / 1.5 | 0.0 / 0.0 / 0.0 |
| l4_fwd_trpc_02 | 7.0 / 5.9 / 5.9 | 2.0 / 2.0 / 2.0 | 4.0 / 4.0 / 4.0 | 2.0 / 1.0 / 1.0 | 0.0 / 0.0 / 0.0 |
