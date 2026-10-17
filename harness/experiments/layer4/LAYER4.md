# Layer 4 ground-truth tooling

Two task families, each needing ground truth that does not come from Prism
(Prism's graph or `prism.blast_radius` must never define the gold, or the
benchmark grades Prism against itself).

| family | question to the model | gold | grading |
|---|---|---|---|
| **Forward blast radius** | "Focal symbol X changes as specified: which callers are affected, which tables are written, which side effects run?" | runtime trace of the corpus's own tests (`trace_py`, `trace_node`) | set precision / recall / F1 over qualified names (unknown names count against precision; answers capped at 15 names); tables and side-effect labels matched exactly |
| **Backward contract adaptation** | "Implement X's body so its existing callers' expectations hold." | the corpus's own tests that exercise X | execution: the spliced candidate passes the test subset (`splice`) |

## Shared layer: `source_edit.py`

Every Layer 4 tool edits one function on disk and puts the file back
byte-for-byte (`edited`, verified by hash). Functions are located by file +
line with Prism's tree-sitter grammars, so the same code covers Python,
JavaScript and TypeScript: replace the whole definition (Python decorators
kept), replace the body, stub the body (`raise NotImplementedError` /
`throw new Error`), or wrap the body in enter/exit hooks (JS/TS tracing).
Instrumentation keeps line numbers unchanged, so stack frames and coverage
offsets still map to the original source.

## Forward blast radius: runtime call tracer

### Python (FastAPI, Django): `trace_py.py`

A pytest plugin (`-p harness.experiments.layer4.trace_py`, configured by
`L4_ROOT`, `L4_FOCAL`, `L4_OUT`) installs `sys.setprofile` (and
`threading.setprofile`) and keeps a shadow stack of repo functions. For
every call of a focal function it records:

- `ancestors` - repo functions below it on the stack: the **upstream blast
  radius** (who is affected when X's contract changes);
- `descendants` - repo functions entered while it is on the stack: what X
  runs (the downstream side of the change);
- `tests` - the test ids during which X ran (the subset to use for the
  backward-adaptation task on the same symbol);
- `sql` - for Django, every statement executed while X is on the stack,
  by table and verb (`CursorWrapper.execute`/`executemany` patched), the
  database side effects.

Names are `module.Qualname`, identical to Prism's qualified names; nested
functions and closures fold into their enclosing function (FastAPI's
`/docs` route handler is a closure inside `FastAPI.setup`). Test files and
installed packages are not repo functions. The manifest is deterministic
(sorted, with counts).

Validated on the real FastAPI suite: focal `fastapi.openapi.docs.get_swagger_ui_html`,
3 test files, 10 tests -> 4 focal calls in 4 tests; ancestors
`FastAPI.setup` (direct calls and the `/docs` closure) and
`FastAPI.__call__`; descendants `jsonable_encoder`.

Limit: a coroutine resumed by the event loop sees the loop below it, not
its logical caller, so ancestors across `asyncio` task boundaries are
partial (same-task `await` chains are complete).

### Node (Express, tRPC): `trace_node.py` + `trace_node_runtime.cjs`

Node has no `settrace`, and tRPC is TypeScript ES modules whose exports
cannot be monkey-patched. So the focal function is instrumented on disk
(`instrument_js`: `const t = __l4.enter(); try { BODY } finally { __l4.exit(t) }`)
and a runtime is loaded into the test process:

- `ancestors` from `new Error().stack` at entry (repo frames only);
- `descendants` from V8 **precise coverage** with call counts, reset at
  entry and read at exit through a same-thread inspector session
  (synchronous), so only functions run while X is on the stack count;
- `tests` from Mocha root hooks.

Locations are `relpath:line:name`; `map_manifest` maps them to Prism
qualified names by line (innermost function or method), so anonymous
callbacks fold into their enclosing function.

Validated on the real Express suite: focal `res.json`
(`lib/response.js:250`) over `test/res.json.js` -> 14 calls in 14 tests;
ancestors `app.handle`, `router.handle`/`next`, `query`, `expressInit`;
descendants `send`, `stringify`, `setCharset`, `generateETag`, `header`;
the file restored after the run.

Limit: for an `async` focal function the `finally` runs when its promise
settles, so work the event loop interleaves meanwhile is counted
(`async_window: true` in the manifest - such tasks get their descendants
reviewed or are excluded from descendant-based gold).

## Backward contract adaptation: `splice.py`

`SpliceTask(root, file, line, command)`; `evaluate(task, reply)` extracts
the last fenced code block, splices it in (a full definition replaces the
function, anything else replaces the body), checks Python syntax, runs the
test subset, restores the file. Validity gates (`gates`), both required
before a task enters the benchmark:

- **positive** - the subset passes with the original code;
- **negative** - the subset fails with the body stubbed. A subset that
  still passes does not exercise the function and is rejected.

`contamination(task, generate, seeds)` runs the **no-context arm** (the
model with no repository context) and flags the task when its pass rate
is >= 0.5: the 14B model has very likely seen these four libraries'
source, so a no-context pass may be recall, not adaptation. Flagged tasks
are dropped or perturbed (consistent identifier renames in X and its
callers) before the run.

Validated on the real Express suite: `res.json` with `test/res.json.js` -
positive passes (0.5 s), stubbed fails -> valid.

## Per-corpus environment constraints

| corpus | test runner | what it needs | status |
|---|---|---|---|
| FastAPI | pytest | an isolated venv with the checkout installed editable plus its test pins; **pin `anyio` to the commit's era** (`anyio==4.6.2`): the repo turns warnings into errors and current anyio deprecates an import the pinned Starlette uses | validated here |
| Django | `tests/runtests.py <labels> --parallel=1` (its own runner, not pytest) | the checkout installed editable; SQLite default settings; SQL capture works through the runner because the tracer patches `CursorWrapper` at import. `trace_py` is a pytest plugin, so for Django it runs through a small runner shim that installs the same profiler before `runtests.py` (next step) | to build |
| Express | Mocha | `npm install` **with dev dependencies** (the Stage 2 cell installs `--omit=dev`) | validated here |
| tRPC | Vitest in a pnpm monorepo | see below | see below |

### tRPC / Vitest on Kaggle

**Not yet validated end to end.** In the development container the install
fails: the lockfile pins 48 tarballs to `registry.npmjs.com`, which that
container's network policy blocks (`registry.npmjs.org` is allowed, which is
why Express installed). Rewriting the host in the lockfile makes pnpm 8.5.1
reject it as outdated, and `--filter` installs hit the same check; forcing a
re-resolve (`--no-frozen-lockfile`) would drift versions, so it was not used.
Kaggle has open internet, so the first Layer 4 cell must install and run one
traced Vitest file before any tRPC task is authored. The workspace also pulls
every example app (Next.js, Prisma, Playwright): expect a few hundred MB and
several minutes, with `--ignore-scripts` to skip engine downloads.

- **Package manager**: the repo pins `pnpm@8.5.1` and `node ^20.10`;
  install with `npx pnpm@8.5.1 install --frozen-lockfile --ignore-scripts`
  (newer pnpm rejects or rewrites the lockfile). Kaggle's image does not
  guarantee Node 20 - install it explicitly (NodeSource or a static
  tarball) before pnpm; Node 22 only warns.
- **Build**: tests import workspace packages; Vitest resolves sources
  through the workspace aliases, so no full `tsup` build is needed for the
  server/client packages, but the install itself is the heavy step
  (minutes, hundreds of MB).
- **Tracing**: Vitest runs test files in worker threads by default; the
  same-thread inspector session needs `--pool=forks` (one process per
  file). The runtime goes in as a `setupFiles` entry. Stack frames point
  at transformed code; Vitest's inline source maps plus
  `--enable-source-maps` map them back to `.ts` lines, which
  `instrument_js` keeps unchanged.
- **Splicing**: TypeScript type errors do not fail Vitest (esbuild strips
  types), so a candidate is graded on behaviour only - acceptable, and
  stated in the task.

## Next steps

1. Django runner shim for `trace_py` and a validated Django manifest
   (SQL tables included).
2. tRPC: Node 20 + pnpm install + `vitest --pool=forks` with the runtime as
   a setup file, validated on one focal function.
3. Task authoring: 16 tasks (2 per family per corpus), each passing the
   gates, with the trace manifest and the test subset frozen before any
   model runs, and the contamination check applied.
4. Kaggle cell for the Layer 4 run.
