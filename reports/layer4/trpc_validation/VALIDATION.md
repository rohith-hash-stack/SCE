# Layer 4 tRPC / Vitest validation: FAIL

SCE `57ac43976d`, tRPC `2ec29bfa2e`, Node v20.18.1, pnpm 8.5.1, Vitest 0.32.0; install 58s, server build 51s (ok); tRPC checkout clean afterwards: True.

## primary: `getTRPCErrorFromUnknown` (packages/server/src/error/TRPCError.ts:4) - INVALID

| check | result | detail |
|---|---|---|
| baseline passes | pass | {'total': 15, 'passed': 15, 'failed': 0, 'success': True, 'stub_seen': False, 'first_failure': None} in 1.7s |
| runner not corrupted (traced exit 0, same counts as baseline) | pass | exit 0, {'total': 15, 'passed': 15, 'failed': 0, 'success': True, 'stub_seen': False, 'first_failure': None} |
| focal function ran in a forked worker | pass | 7 calls in 1 process(es) |
| tests attributed (expect.getState) | pass | 7 tests, e.g. ['packages/tests/server/TRPCError.test.ts > tests/server/TRPCError.test.ts > allows fuzzy matching based on error name', 'packages/tests/server/TRPCError.test.ts > tests/server/TRPCError.test.ts > getTRPCEr |
| focal file restored byte-for-byte | pass |  |
| stack: focal frame is the original .ts file inside the definition | pass | ('packages/server/src/error/TRPCError.ts', 4, 'getTRPCErrorFromUnknown') vs lines (4, 24) |
| stack: caller frame in the test file points at a line calling the target | pass | packages/tests/server/TRPCError.test.ts:57 `const trpcError = getTRPCErrorFromUnknown(originalError);` |
| stack: no transpiled .js frames for repo sources | pass | [] |
| coverage: callees resolve to original .ts sources | pass | 4 callees, mapped 16 / unmapped 20; .js: [] |
| coverage: expected callee in packages/server/src/shared/getCauseFromUnknown.ts | pass | ['packages/server/src/shared/getCauseFromUnknown.ts:31:get', 'packages/server/src/shared/getCauseFromUnknown.ts:6:getCauseFromUnknown'] |
| coverage: packages/server/src/shared/getCauseFromUnknown.ts:31:get line holds `get` | **FAIL** | `}` |
| coverage: packages/server/src/shared/getCauseFromUnknown.ts:6:getCauseFromUnknown line holds `getCauseFromUnknown` | pass | `export function getCauseFromUnknown(cause: unknown): Error / undefined {` |
| Prism name: shared.getCauseFromUnknown.getCauseFromUnknown among descendants | pass | ['error.TRPCError.TRPCError.constructor', 'shared.getCauseFromUnknown.getCauseFromUnknown'] |
| Prism name: focal location maps to the focal symbol | pass |  |
| diagnostic: without --enable-source-maps | info | exit 0, top frame [('packages/server/src/error/TRPCError.ts', 4, 'getTRPCErrorFromUnknown')], descendants 4, same descendants as with the flag: True |
| negative gate: STUB_IMPLEMENTATION body fails the suite | pass | exit 1, {'total': 15, 'passed': 8, 'failed': 7, 'success': False, 'stub_seen': True, 'first_failure': 'STUB_IMPLEMENTATION'} |
| negative gate: failures carry STUB_IMPLEMENTATION | pass |  |
| positive gate: original passes with 0 failures | pass | exit 0 |
| gates valid | pass |  |
| splice: original definition as a candidate passes | pass | mode function, exit 0 |
| splice: wrong body fails | pass | exit 1 |
| focal file restored after gates and splices | pass |  |

Descendants (Prism names): error.TRPCError.TRPCError.constructor, shared.getCauseFromUnknown.getCauseFromUnknown
Resolved by name from dist/: -

## cross_package: `createChain` (packages/client/src/links/internals/createChain.ts:10) - valid

| check | result | detail |
|---|---|---|
| baseline passes | pass | {'total': 2, 'passed': 2, 'failed': 0, 'success': True, 'stub_seen': False, 'first_failure': None} in 1.4s |
| runner not corrupted (traced exit 0, same counts as baseline) | pass | exit 0, {'total': 2, 'passed': 2, 'failed': 0, 'success': True, 'stub_seen': False, 'first_failure': None} |
| focal function ran in a forked worker | pass | 2 calls in 1 process(es) |
| tests attributed (expect.getState) | pass | 2 tests, e.g. ['packages/client/src/links/internals/createChain.test.ts > client/src/links/internals/createChain.test.ts > chain > multiple responses', 'packages/client/src/links/internals/createChain.test.ts > client/sr |
| focal file restored byte-for-byte | pass |  |
| stack: focal frame is the original .ts file inside the definition | pass | ('packages/client/src/links/internals/createChain.ts', 17, 'createChain') vs lines (10, 40) |
| stack: caller frame in the test file points at a line calling the target | pass | packages/client/src/links/internals/createChain.test.ts:7 `const result$ = createChain<AnyRouter, unknown, unknown>({` |
| stack: no transpiled .js frames for repo sources | pass | [] |
| coverage: callees resolve to original .ts sources | pass | 3 callees, mapped 6 / unmapped 0; .js: [] |
| cross-package callee `observable` recorded (dist/ or mapped src/) | pass | ['packages/server/dist/observable-ade1bad8.mjs:21:observable'] |
| cross-package callee `observable` -> Prism observable.observable.observable | pass | resolved_by_name: {'dist/observable-ade1bad8.mjs:21:observable': 'observable.observable.observable'} |
| diagnostic: without --enable-source-maps | info | exit 0, top frame [('packages/client/src/links/internals/createChain.ts', 17, 'createChain')], descendants 3, same descendants as with the flag: True |
| negative gate: STUB_IMPLEMENTATION body fails the suite | pass | exit 1, {'total': 2, 'passed': 0, 'failed': 2, 'success': False, 'stub_seen': True, 'first_failure': 'STUB_IMPLEMENTATION'} |
| negative gate: failures carry STUB_IMPLEMENTATION | pass |  |
| positive gate: original passes with 0 failures | pass | exit 0 |
| gates valid | pass |  |
| splice: original definition as a candidate passes | pass | mode function, exit 0 |
| splice: wrong body fails | pass | exit 1 |
| focal file restored after gates and splices | pass |  |

Descendants (Prism names): observable.observable.observable
Resolved by name from dist/: {'dist/observable-ade1bad8.mjs:21:observable': 'observable.observable.observable'}

