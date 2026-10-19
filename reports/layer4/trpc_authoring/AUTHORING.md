# Layer 4 tRPC task authoring: FAIL

SCE `76dda16b4e`, tRPC `2ec29bfa2e`, Node v20.18.1, pnpm 8.5.1, Vitest 0.32.0; install 60s, server build 61s, authoring 44s (exit 1).

Suite: 14 tasks; suite errors ['trpc/forward_blast: 0 tasks, expected 2']; task errors none; missing artifacts none; Layer 4 tests exit 1; tRPC checkout clean: True.

## l4_fwd_trpc_01: not authored

See authoring.log.

## l4_fwd_trpc_02: not authored

See authoring.log.

## l4_bwd_trpc_01: `shared.getCauseFromUnknown.getCauseFromUnknown` (packages/server/src/shared/getCauseFromUnknown.ts:6)

Rejected candidates: `core.internals.mergeRouters.mergeRouters` (l4_bwd_trpc_01: gates invalid: positive False negative False)

Gates: positive {'passed': True, 'seconds': 1.7}, negative {'passed': False, 'exit_code': 1, 'stub_seen': True}.
Oracle: `shared.getCauseFromUnknown.getCauseFromUnknown`, `error.TRPCError.TRPCError.constructor`, `error.utils.isObject`
Excluded from every arm: ['packages/tests/server/TRPCError.test.ts'].

## l4_bwd_trpc_02: `observable.operators.share.share` (packages/server/src/observable/operators/share.ts:8)

Gates: positive {'passed': True, 'seconds': 1.5}, negative {'passed': False, 'exit_code': 1, 'stub_seen': True}.
Oracle: `observable.operators.share.share`
Excluded from every arm: ['packages/server/src/observable/operators/share.test.ts', 'packages/server/src/observable/observable.test.ts'].

## Rejected candidates (traces in benchmarks/layer4/rejected/)

- `core.internals.mergeRouters.mergeRouters` for l4_bwd_trpc_01: l4_bwd_trpc_01: gates invalid: positive False negative False (exit None, focal calls None, tests None, depths {})
- `error.TRPCError.getTRPCErrorFromUnknown` for l4_fwd_trpc_01: l4_fwd_trpc_01: trace exited 1 (exit 1, focal calls 0, tests 0, depths {})
- `error.TRPCError.getTRPCErrorFromUnknown` for l4_fwd_trpc_01: l4_fwd_trpc_01: trace exited 1 (exit 1, focal calls 7, tests 7, depths {})
- `shared.transformTRPCResponse.transformTRPCResponse` for l4_fwd_trpc_01: l4_fwd_trpc_01: trace exited 1 (exit 1, focal calls 0, tests 0, depths {})
- `shared.getErrorShape.getErrorShape` for l4_fwd_trpc_02: l4_fwd_trpc_02: trace exited 1 (exit 1, focal calls 0, tests 0, depths {})
- `shared.getErrorShape.getErrorShape` for l4_fwd_trpc_02: l4_fwd_trpc_02: trace exited 1 (exit 1, focal calls 0, tests 0, depths {})
- `core.router.callProcedure` for l4_fwd_trpc_02: l4_fwd_trpc_02: trace exited 1 (exit 1, focal calls 0, tests 0, depths {})

