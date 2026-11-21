# Layer 4 tRPC task authoring: PASS

SCE `f614474515`, tRPC `2ec29bfa2e`, Node v20.18.1, pnpm 8.5.1, Vitest 0.32.0; install 60s, workspace build 83s, authoring 47s (exit 0).

Suite: 16 tasks; suite errors none; task errors none; missing artifacts none; Layer 4 tests exit 0; tRPC checkout clean: True.

## l4_fwd_trpc_01: `error.TRPCError.getTRPCErrorFromUnknown` (packages/server/src/error/TRPCError.ts:4)

Gold (max_depth 2): `http.resolveHTTPResponse.resolveHTTPResponse`, `core.internals.procedureBuilder.createProcedureCaller`, `http.resolveHTTPResponse.inputToProcedureCall`, `adapters.ws.applyWSSHandler`, `adapters.ws.handleRequest`, `adapters.ws.createContextAsync`, `adapters.ws.handleRequest.error`, `core.router.callProcedure`, `core.router.createCallerFactory`, `observable.observable.observable.error`
Focal calls 35, tests 23, trace attempts 1.

## l4_fwd_trpc_02: `shared.getErrorShape.getErrorShape` (packages/server/src/shared/getErrorShape.ts:10)

Gold (max_depth 2): `http.resolveHTTPResponse.resolveHTTPResponse`, `http.resolveHTTPResponse.inputToProcedureCall`, `adapters.ws.applyWSSHandler`, `adapters.ws.handleRequest`, `adapters.ws.createContextAsync`, `adapters.ws.handleRequest.error`, `observable.observable.observable.error`
Focal calls 18, tests 17, trace attempts 1.

## l4_bwd_trpc_01: `core.internals.mergeRouters.mergeRouters` (packages/server/src/core/internals/mergeRouters.ts:37)

Gates: positive {'passed': True, 'seconds': 4.1}, negative {'passed': False, 'exit_code': 1, 'stub_seen': True}.
Excluded from every arm: ['packages/tests/server/index.test.ts', 'packages/tests/server/smoke.test.ts'].

## l4_bwd_trpc_02: `observable.operators.share.share` (packages/server/src/observable/operators/share.ts:8)

Gates: positive {'passed': True, 'seconds': 1.6}, negative {'passed': False, 'exit_code': 1, 'stub_seen': True}.
Excluded from every arm: ['packages/server/src/observable/operators/share.test.ts', 'packages/server/src/observable/observable.test.ts'].

