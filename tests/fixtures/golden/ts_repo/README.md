# Golden fixture `ts_repo` (TypeScript / JavaScript)

PRISM's golden tests index a temporary copy of this repository (`GOLDEN_TESTS_PLAN.md` §1), set up as for
`py_repo`: a copy, `git init`, everything except `worktree_overlay/` committed, then the overlay copied over as an
uncommitted edit. The tests are never run (no `node_modules`); PRISM reads them as test code.

| Element | Where | Exercises |
|---|---|---|
| `export function`, `export const f = (…) =>`, `export const g = async (…) =>` | `src/text.ts` (`clean`, `words`, `countWords`) | the arrow-function parse (the `async` bug); const-bound functions |
| a barrel (`export { … } from`, `export * from`, a default re-export) | `src/index.ts` | T5; T2 through barrels |
| `import * as ns` with `ns.f()`; a default export | `src/report.ts`, `src/greet.ts` | T2 through namespaces and defaults |
| a class hierarchy with an override; an interface implementation | `src/shapes.ts` (`Rect.area`, `Square.area`, `Shape`, `totalArea`) | T4 |
| a `.d.ts` declaration for one module | `types/legacy.d.ts` for `src/legacy.js` | T7 |
| a route-registration table | `src/router.ts` (`router.get("/", home)`) | T6 |
| `*.test.ts` files and one `test-d` type test | `test/text.test.ts`, `test/shapes.test.ts`, `test/math.test-d.ts` | T8; the backlog's `test-d` gap |
| **the D1 trap in JS** | `src/cache.ts` `Cache.setEntry`; `src/warm.ts` bare `setEntry(...)` with no import | Q1 |
| **the D2 trap in JS** | `src/race.ts`: `Promise.race([fetchOne(url), timeoutAfter(ms)])`, `String(undefinedThing(x))` | Q2 (pilot B-02's shape) |
| a working-tree edit: an object-destructured parameter replaces positional ones | `worktree_overlay/src/user.ts` over `src/user.ts`; caller `src/profile.ts` | V2 and the arrow-function parse |
| a raise and a callee chain | `src/math.ts` `mean` (throws `RangeError`, calls `sum`) | T1, T3 |
