# prism_full forward recall funnel (Layer 4 tournament)

Per task: gold callers (traced, within max_depth) surviving each stage; stages 4-5 are means over the 10 seeds. `lost: Turn 1` / `lost: budget` split the V_cand -> packed loss by replay (module docstring).

| task | gold | in index | in static graph | in V_cand | packed | emitted | from context | without context | packed, not emitted | lost: Turn 1 | lost: budget | recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| l4_fwd_django_01 | 5 | 5 | 3 | 2 | 1.1 | 1.1 | 1.1 | 0.0 | 0.0 | 0.9 | 0.0 | 0.22 |
| l4_fwd_django_02 | 5 | 5 | 5 | 2 | 2.0 | 2.2 | 2.0 | 0.2 | 0.0 | 0.0 | 0.0 | 0.44 |
| l4_fwd_express_01 | 12 | 12 | 6 | 3 | 3.0 | 3.0 | 3.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.25 |
| l4_fwd_express_02 | 11 | 11 | 0 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| l4_fwd_fastapi_01 | 10 | 10 | 6 | 4 | 3.2 | 2.6 | 2.6 | 0.0 | 0.6 | 0.0 | 0.8 | 0.26 |
| l4_fwd_fastapi_02 | 3 | 3 | 2 | 1 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.33 |
| l4_fwd_trpc_01 | 10 | 10 | 7 | 3 | 2.8 | 2.8 | 2.8 | 0.0 | 0.0 | 0.2 | 0.0 | 0.28 |
| l4_fwd_trpc_02 | 7 | 7 | 6 | 2 | 2.0 | 2.0 | 2.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.29 |
| **total** | 63 | 63 | 35 | 17 | 15.1 | 14.7 | 14.5 | 0.2 | 0.6 | 1.1 | 0.8 | 0.259 |

Funnel over all 8 tasks (share of gold): gold 63 -> in static graph 55.6% -> in V_cand 27.0% -> packed 24.0% -> emitted 23.3% (of which from context 23.0%, without it 0.3%).

## Why gold callers are not in V_cand

| task | gold caller | hop | reason |
|---|---|---|---|
| l4_fwd_django_01 | `django.contrib.sessions.backends.base.SessionBase.cycle_key` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_django_01 | `django.contrib.sessions.middleware.SessionMiddleware.process_response` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_django_01 | `django.utils.deprecation.MiddlewareMixin.__call__` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_django_02 | `django.db.migrations.executor.MigrationExecutor._migrate_all_forwards` | 3 | transitive caller (hop 3): the downstream-mode manifest admits direct callers only |
| l4_fwd_django_02 | `django.db.migrations.executor.MigrationExecutor.apply_migration` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_django_02 | `django.db.migrations.executor.MigrationExecutor.migrate` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_express_01 | `lib.response.format` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.response.render` | 1 | direct caller ranked 4 of 5 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_express_01 | `lib.response.sendFile` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.response.sendfile` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.handle` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_express_01 | `lib.router.layer.handle` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.layer.handle_error` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.restore` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_01 | `lib.router.sendOptionsResponse` | 1 | direct caller ranked 5 of 5 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_express_02 | `lib.response.append` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.attachment` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.contentType` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.format` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.json` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.jsonp` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.links` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.location` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.redirect` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.response.send` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_express_02 | `lib.router.sendOptionsResponse` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_01 | `fastapi.dependencies.utils.get_parameterless_sub_dependant` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_fastapi_01 | `fastapi.routing.APIRoute.__init__` | 1 | direct caller ranked 4 of 4 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_fastapi_01 | `fastapi.routing.APIRouter.add_api_route` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_01 | `fastapi.routing.APIRouter.add_api_websocket_route` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_01 | `fastapi.routing.get_request_handler` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_01 | `fastapi.routing.get_websocket_app` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_fastapi_02 | `fastapi.applications.FastAPI.openapi` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_fastapi_02 | `fastapi.applications.FastAPI.setup` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_01 | `adapters.ws.applyWSSHandler` | 1 | direct caller ranked 6 of 10 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_trpc_01 | `core.internals.procedureBuilder.createProcedureCaller` | 1 | direct caller ranked 7 of 10 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_trpc_01 | `core.router.callProcedure` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_01 | `core.router.createCallerFactory` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_01 | `http.resolveHTTPResponse.inputToProcedureCall` | 1 | direct caller ranked 4 of 10 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_trpc_01 | `http.resolveHTTPResponse.resolveHTTPResponse` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_trpc_01 | `observable.observable.observable.error` | None | no static caller path to the focal (call edge missing) |
| l4_fwd_trpc_02 | `adapters.ws.createContextAsync` | 1 | direct caller ranked 5 of 7 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_trpc_02 | `adapters.ws.handleRequest.error` | 1 | direct caller ranked 4 of 7 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_trpc_02 | `http.resolveHTTPResponse.inputToProcedureCall` | 1 | direct caller ranked 6 of 7 by W_upstream: past UPSTREAM_FRONTIER_CAP=3 |
| l4_fwd_trpc_02 | `http.resolveHTTPResponse.resolveHTTPResponse` | 2 | transitive caller (hop 2): the downstream-mode manifest admits direct callers only |
| l4_fwd_trpc_02 | `observable.observable.observable.error` | None | no static caller path to the focal (call edge missing) |

Reasons, counted: no static caller path to the focal: 28; transitive caller: 9; direct caller ranked 4 of 5 by W_upstream: 1; direct caller ranked 5 of 5 by W_upstream: 1; direct caller ranked 4 of 4 by W_upstream: 1; direct caller ranked 6 of 10 by W_upstream: 1; direct caller ranked 7 of 10 by W_upstream: 1; direct caller ranked 4 of 10 by W_upstream: 1; direct caller ranked 5 of 7 by W_upstream: 1; direct caller ranked 4 of 7 by W_upstream: 1; direct caller ranked 6 of 7 by W_upstream: 1

## Turn 1 selection (proxy: packed candidates other than the anchor and its auto-included direct callees)

| task | caller rows: gold picked / offered | caller rows: other | downstream rows: gold | downstream rows: other |
|---|---|---|---|---|
| l4_fwd_django_01 | 1/10 | 0/10 | - | 43/390 |
| l4_fwd_django_02 | 20/20 | 0/10 | - | 10/150 |
| l4_fwd_express_01 | 20/20 | - | - | 3/50 |
| l4_fwd_express_02 | - | - | - | 9/40 |
| l4_fwd_fastapi_01 | 22/30 | - | - | 0/230 |
| l4_fwd_fastapi_02 | 10/10 | - | - | 0/60 |
| l4_fwd_trpc_01 | 28/30 | - | - | - |
| l4_fwd_trpc_02 | 20/20 | 9/10 | - | 1/10 |

Axis annotations Turn 1 saw on the gold candidates:

- l4_fwd_django_01 `django.contrib.sessions.backends.cached_db.SessionStore.save`: role `caller`, `axes=S:NONE;F:LINEAR;O:COMMAND;R:ENTRYPOINT,PUBLIC_API`
- l4_fwd_django_01 `django.contrib.sessions.backends.db.SessionStore.create`: role `caller`, `axes=S:NONE;F:LINEAR,BATCH_LOOP;O:COMMAND;R:NONE`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor.check_replacements`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP,ASYNC_CONCURRENT;O:COMMAND;R:ORCHESTRATOR`
- l4_fwd_django_02 `django.db.migrations.executor.MigrationExecutor.record_migration`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP;O:COMMAND;R:NONE`
- l4_fwd_express_01 `lib.response.json`: role `callee`, `axes=S:NONE;F:LINEAR;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_express_01 `lib.response.jsonp`: role `caller`, `axes=S:NONE;F:BRANCH_DISPATCH;O:QUERY;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_express_01 `lib.response.sendStatus`: role `caller`, `axes=S:NONE;F:LINEAR;O:QUERY;R:ENTRYPOINT,PUBLIC_API`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.get_param_sub_dependant`: role `callee`, `axes=S:PURE_COMPUTE;F:LINEAR;O:QUERY;R:NONE`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.get_sub_dependant`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH;O:QUERY;R:NONE`
- l4_fwd_fastapi_01 `fastapi.dependencies.utils.solve_dependencies`: role `caller`, `axes=S:NONE;F:RETRY_LOOP,RECURSIVE,ASYNC_CONCURRENT;O:FACTORY,ASYNC_DEFERRED;R:NONE`
- l4_fwd_fastapi_01 `fastapi.routing.APIWebSocketRoute.__init__`: role `caller`, `axes=S:NONE;F:LINEAR,BATCH_LOOP;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_fastapi_02 `fastapi.openapi.utils.get_openapi`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,BATCH_LOOP;O:QUERY;R:ORCHESTRATOR`
- l4_fwd_trpc_01 `adapters.ws.createContextAsync`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,ASYNC_CONCURRENT;O:COMMAND,ASYNC_DEFERRED;R:ORCHESTRATOR`
- l4_fwd_trpc_01 `adapters.ws.handleRequest`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,ASYNC_CONCURRENT;O:ASYNC_DEFERRED,GUARD;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_trpc_01 `adapters.ws.handleRequest.error`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR;O:COMMAND;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`
- l4_fwd_trpc_02 `adapters.ws.applyWSSHandler`: role `caller`, `axes=S:PURE_COMPUTE;F:LINEAR,BATCH_LOOP,ASYNC_CONCURRENT;O:AGGREGATOR;R:ORCHESTRATOR`
- l4_fwd_trpc_02 `adapters.ws.handleRequest`: role `caller`, `axes=S:PURE_COMPUTE;F:BRANCH_DISPATCH,ASYNC_CONCURRENT;O:ASYNC_DEFERRED,GUARD;R:ENTRYPOINT,ORCHESTRATOR,PUBLIC_API`

## Budget

With Turn 1 requesting **every** V_cand candidate (maximum budget pressure):

| task | V_cand | packed | gold in V_cand kept | gold dropped |
|---|---|---|---|---|
| l4_fwd_django_01 | 47 | 20 | 2/2 | - |
| l4_fwd_django_02 | 20 | 22 | 0/2 | `django.db.migrations.executor.MigrationExecutor.check_replacements`, `django.db.migrations.executor.MigrationExecutor.record_migration` |
| l4_fwd_express_01 | 13 | 13 | 3/3 | - |
| l4_fwd_express_02 | 6 | 6 | 0/0 | - |
| l4_fwd_fastapi_01 | 34 | 9 | 2/4 | `fastapi.dependencies.utils.get_param_sub_dependant`, `fastapi.dependencies.utils.get_sub_dependant` |
| l4_fwd_fastapi_02 | 17 | 7 | 1/1 | - |
| l4_fwd_trpc_01 | 5 | 5 | 3/3 | - |
| l4_fwd_trpc_02 | 6 | 6 | 2/2 | - |

## Counterfactual: Design C manifest (`direction="both"`)

| task | gold | in V_cand now | both, 8000-token budget | both, no budget |
|---|---|---|---|---|
| l4_fwd_django_01 | 5 | 2 | 3 (54 cand.) | 3 (61 cand.) |
| l4_fwd_django_02 | 5 | 2 | 5 (28 cand.) | 5 (30 cand.) |
| l4_fwd_express_01 | 12 | 3 | 6 (23 cand.) | 6 (23 cand.) |
| l4_fwd_express_02 | 11 | 0 | 0 (6 cand.) | 0 (6 cand.) |
| l4_fwd_fastapi_01 | 10 | 4 | 6 (28 cand.) | 6 (38 cand.) |
| l4_fwd_fastapi_02 | 3 | 1 | 2 (20 cand.) | 2 (20 cand.) |
| l4_fwd_trpc_01 | 10 | 3 | 7 (23 cand.) | 7 (28 cand.) |
| l4_fwd_trpc_02 | 7 | 2 | 6 (18 cand.) | 6 (19 cand.) |

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
