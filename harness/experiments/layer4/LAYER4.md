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
kept), replace the body, stub the body (`raise NotImplementedError("STUB_IMPLEMENTATION")` /
`throw new Error("STUB_IMPLEMENTATION")`), or wrap the body in enter/exit hooks (JS/TS tracing).
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

### Django: `trace_django.py`

Django tests run through `tests/runtests.py`, not pytest. The adapter
installs the same `Tracer`, hooks `unittest.TestCase.run` for test ids,
puts `tests/` on `sys.path` (as running the script would), and runs
`runtests.py <labels> --parallel=1` in-process - parallel workers would
escape the profiler. Labels are runtests labels: an app (`queries`), a
module or a single test. A crash is reported as `exit_code: 1`, never as a
pass.

SQL is captured at `CursorWrapper._execute_with_wrappers`, the one method
both cursor wrappers reach (`assertNumQueries` switches on
`CursorDebugWrapper`, whose `execute` calls the plain one - patching both
`execute`s would count those statements twice). Statements are grouped by
table and verb; transaction control (savepoints, BEGIN/COMMIT) goes under
`_transaction`.

Validated on the pinned Django (4.2.30):

- `Model.save_base` over `basic update` (88 tests, 1.5 s): 140 calls in 46
  tests; ancestors `Model.save` (140), `QuerySet.create` (100); SQL e.g.
  `basic_article` INSERT 50 / UPDATE 13 / SELECT 3, `basic_selfref` INSERT
  10 / UPDATE 3. Manager methods Django generates as closures
  (`objects.create`) fold into their factory,
  `BaseManager._get_queryset_methods` - consistent with the closure rule,
  noted for task authors.
- `SQLCompiler.execute_sql` over `update delete`: 1,112 calls in 73 tests;
  720 SELECT, 243 DELETE, 210 UPDATE, per table (`delete_a` SELECT 123 /
  UPDATE 151 / DELETE 32, ...). No INSERT: `SQLInsertCompiler` overrides
  `execute_sql` without calling the base one - correct for this focal.

### Node (Express, tRPC): `trace_node.py` + `trace_node_runtime.cjs`

Node has no `settrace`, and tRPC is TypeScript ES modules whose exports
cannot be monkey-patched. So the focal function is instrumented on disk
(`instrument_js`: `const t = __l4.enter(); try { BODY } finally { __l4.exit(t) }`)
and a runtime is loaded into the test process:

- `ancestors` from `new Error().stack` at entry (repo frames only);
- `descendants` from V8 **precise coverage** with call counts, reset at
  entry and read at exit through a same-thread inspector session
  (synchronous), so only functions run while X is on the stack count;
- `tests` from Mocha root hooks, or Vitest's `expect.getState()`.

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

Validated:

| corpus | target | test subset | original | stubbed | valid |
|---|---|---|---|---|---|
| Express | `res.json` (`lib/response.js:250`) | `test/res.json.js` | passes (0.5 s) | fails | yes |
| Django | `QuerySet.update` (`django/db/models/query.py:1180`) | `update` (27 tests) | passes (0.6 s) | 26 errors | yes |
| Django | `LocMemCache.incr` (`django/core/cache/backends/locmem.py:65`) | `cache.tests.LocMemCacheTests` (60 tests) | passes (13.7 s) | 11 errors | yes |

## Per-corpus environment constraints

| corpus | test runner | what it needs | status |
|---|---|---|---|
| FastAPI | pytest | an isolated venv with the checkout installed editable plus its test pins; **pin `anyio` to the commit's era** (`anyio==4.6.2`): the repo turns warnings into errors and current anyio deprecates an import the pinned Starlette uses | validated here |
| Django | `tests/runtests.py <labels> --parallel=1` (its own runner, not pytest) | a venv with the checkout installed editable plus `tblib`; the default SQLite settings (`tests/test_sqlite.py`) need nothing else for the model/ORM/cache apps. `trace_django.py` runs `runtests.py` in-process under the same profiler | validated here (Django 4.2.30) |
| Express | Mocha | `npm install` **with dev dependencies** (the Stage 2 cell installs `--omit=dev`) | validated here |
| tRPC | Vitest in a pnpm monorepo | see below | see below |

### tRPC / Vitest on Kaggle

**Validation cell: `kaggle/trpc_layer4_validation_cell.py`** (CPU, Internet
on, ~15-25 min; writes `reports/layer4/trpc_validation/`). It cannot run in
the development container: the lockfile pins 48 tarballs to
`registry.npmjs.com`, which that container's network policy blocks
(`registry.npmjs.org` is allowed, which is why Express installed), and
rewriting the host makes pnpm 8.5.1 reject the lockfile. Kaggle has open
internet. The cell's analysis and report code was exercised locally against
the real checkout with mocked Vitest runs; the tracer runtime's source-map
and fork handling is covered by `tests/test_layer4_harness.py`.

- **Environment**: Node 20 from the static nodejs.org tarball (the repo
  pins `node ^20.10`), `pnpm@8.5.1` (its `packageManager`),
  `pnpm install --frozen-lockfile --ignore-scripts` (no unfrozen fallback:
  it would drift versions), `pnpm rebuild esbuild @swc/core` for the
  native binaries only, then `pnpm --filter @trpc/server build`: client
  code imports `@trpc/server/observable`, whose `exports` point at
  `dist/`. Vitest resolves `@trpc/*/src/` through its aliases, so only the
  server needs building.
- **Runner**: Vitest 0.32 has no `--pool`; `--run --no-threads` runs each
  test file in a `child_process` fork. The runtime goes in through
  `NODE_OPTIONS="--require trace_node_runtime.cjs --enable-source-maps"`,
  which every fork inherits; with `L4_MULTI=1` each process that ran the
  focal function writes `<out>.<pid>.json` and `run_traced(multi=True)`
  merges them (`merge`). Vitest's root is `packages/` (`root: '../'` in
  `packages/tests/vitest.config.ts`), so test filters are
  `server/TRPCError.test.ts`, run from `packages/tests`; the cell calls
  `node node_modules/vitest/<bin>` directly so pnpm itself is not traced.
- **Tests attribution**: Vitest has no Mocha root hooks; the runtime reads
  `expect.getState().currentTestName` (globals are on).
- **TypeScript source maps**: stack frames are rewritten by vite-node's
  own source-map support. Coverage offsets are not, and esbuild does not
  keep lines, so the runtime maps every covered function's start offset
  through the script's source map: it fetches the executed source with
  `Debugger.getScriptSource`, takes the last `sourceMappingURL` (vite-node
  appends an inline one) or an external `.map` file, and decodes the VLQ
  mappings itself (no dependencies). `source_mapping` in the manifest
  counts mapped / unmapped positions. tRPC's rollup build emits **no
  source maps**, so a cross-package callee in `packages/server/dist/` keeps
  its dist location; `map_manifest` resolves it by name to the one
  same-named function under that package's `src/` (`LineMap.dist_name`,
  listed under `resolved_by_name` for review).
- **Index root**: Prism indexes tRPC at `packages/server/src`
  (`pinned_commits.json` `subdir`); the tracer records clone-relative
  paths. `rebase(raw, "packages/server/src")` strips the prefix, keeps
  the server's `dist/` for name resolution and moves other packages'
  locations to `outside_*`. tRPC tasks therefore target functions under
  `packages/server/src`: client functions are not retrievable by any arm.
- **Targets validated by the cell**: `getTRPCErrorFromUnknown`
  (`packages/server/src/error/TRPCError.ts:4`, in the index) over
  `server/TRPCError.test.ts` (callees `TRPCError` constructor,
  `getCauseFromUnknown`), and `createChain`
  (`packages/client/src/links/internals/createChain.ts:10`) over its own
  test for the cross-package `dist/` path (`observable`). Checks per
  target: baseline passes; traced run exits 0 with the baseline's test
  counts (runner not corrupted); focal calls in a forked worker; tests
  attributed; the focal frame is the `.ts` file inside the definition; the
  caller frame in the test file points at a line that calls the target;
  no transpiled `.js` frames or callees for repo sources; expected callees
  at lines holding their definitions; Prism names; negative gate (the
  `STUB_IMPLEMENTATION` stub fails the file, and the failures carry the
  message); positive gate (0 failures); splice grader (original
  definition passes, a wrong body fails); files restored and the checkout
  clean. A run without `--enable-source-maps` is reported as a diagnostic.
- **Splicing**: TypeScript type errors do not fail Vitest (esbuild strips
  types), so a candidate is graded on behaviour only - acceptable, and
  stated in the task.

Splice note (all corpora): Python validates a `.pyc` against the source's
mtime in whole seconds and its size, so a candidate written in the same
second as an equal-sized earlier version (the stub, another candidate)
could run stale bytecode. `splice._run` deletes the edited file's cached
bytecode before every run and sets `PYTHONDONTWRITEBYTECODE=1`.

## Layer 4 run: tasks, arms, scoring

### Task contract

`task.schema.json` (`layer4.task.v1`) defines one task; `tasks.py`
validates it (`validate_task`: schema + semantic rules, optionally against
the corpus's Prism names) and the suite (`validate_suite`). Tasks live in
`benchmarks/layer4/tasks/l4_<fwd|bwd>_<corpus>_<nn>.json`, frozen with their
trace manifests before any model runs.

| | forward blast radius (`l4_fwd_*`) | backward adaptation (`l4_bwd_*`) |
|---|---|---|
| count | 8: 2 per corpus | 8: 2 per corpus |
| question | the focal function changes as `change` says (signature, return / raise contract, side effect, semantics): which functions are affected, which side effects and tables are involved? | implement the focal function's body so its callers' expectations hold |
| gold source | runtime trace of the corpus's tests (`trace_py`, `trace_django`, `trace_node`); `gold_from_manifest` takes ancestors (`direction: upstream`, the default), descendants or both at >= `min_count` calls, restricted to names in the Prism index, at most 15 | the corpus's own tests through `splice` (gates passed and frozen) |
| side effects / tables | Django: `side_effects_from_sql` (`SELECT` -> `DB_READ`, `INSERT/UPDATE/DELETE` -> `DB_WRITE`, DDL -> `DB_SCHEMA`, savepoints/`BEGIN`/`COMMIT` -> `DB_TRANSACTION`) and `tables_from_sql`; other corpora `null` (no runtime source: not scored, and not invented) | - |
| answer | `forward_json_v1`: one JSON object `{affected_symbols (<= 15), side_effects, tables}` | `backward_code_v1`: one fenced code block (whole definition or body) |
| score | set P / R / F1 over affected symbols (`score_forward`: names resolved to index names exactly or by a unique >= 2-component suffix; unknown names count against precision; answers past 15 truncated); exact set match of tags and of tables where gold is non-null | binary pass (`score_backward` over `splice.evaluate`), with tokens per pass and passes per 1k tokens |
| contamination | `arm0_no_retrieval` mean F1 >= 0.5 -> flagged (reported, not dropped) | `arm0_no_retrieval` pass rate >= 0.5 over the seeds -> flagged |

### Arm compatibility (all 7 Stage 2 arms, unchanged)

`to_evaluation_task(task)` builds the `EvaluationTask` every arm already
consumes: `seed_symbol` = the focal qname (prism_full's taxonomy anchor and
the BFS seed), `prompt` (the retrieval arms' query: prism_ask, hybrid RAG,
repo map), `task_type` (`blast` / `debug`), and the oracle slice as
`pipeline_symbols` / `required_context`:

| arm | context | Layer 4 specifics |
|---|---|---|
| `normal_oracle` (`pragmatic_oracle`) | `oracle_symbols` | forward: focal + gold affected symbols - a sanity ceiling (it contains the answer); backward: focal (masked) + its callers + types |
| `prism_oracle` | the same + breadcrumbs | as above |
| `prism_full` | two-pass from the seed (Turn 1 selection + final; 2 LLM calls) | its extra call is counted in `tokens.prompt` / `llm_calls` |
| `prism_ask` | `NLQIndex.search(prompt)` + breadcrumbs | - |
| `arm1_hybrid_rag` | BM25 + dense + reranker over symbol chunks | the cached Django embeddings are reused (the bodies are masked at render time, not re-indexed) |
| `repomap_pagerank` | PageRank repo map, 4,000 tokens | - |
| `arm0_no_retrieval` | none | the contamination probe |

Leak control for backward tasks (the answer must not be in any context):

1. **Mask**: every rendered context passes through
   `mask_context(rendered, definition, masked_definition(...))`, which
   replaces the focal definition with its signature, docstring and a
   placeholder. Arms render indexed bodies verbatim, so this is exact.
   Retrieval rankings were computed on the unmasked corpus - the ranking
   may favour the focal, the content shown never has its body (stated).
2. **Exclude**: the runner drops test-file nodes from every arm's package
   before rendering (Prism's index already treats them as non-application
   code; the hybrid-RAG chunks and the repo map are filtered the same
   way), and the gating test files are listed in
   `arm_view.exclude_paths` and checked.
3. **Check**: `leak_check` counts distinctive original body lines (>= 20
   characters, not the signature, not comments) and gating-test paths in
   the final context; a non-zero result makes the cell `status: "leak"`,
   excluded and counted per arm.

Forward tasks are not masked: the focal body is legitimately needed to
reason about the change.

### Cells

`cell.schema.json` (`layer4.cell.v1`): one record per task x arm x seed,
appended to `cells.jsonl` - status (`ok`, `error`, `timeout`, `leak`),
tokens (context, prompt and completion over every LLM call, number of
calls), the leak check, the selected symbols, the raw reply and the
score. 16 tasks x 7 arms x 10 seeds = **1,120 cells**, same model,
quantization, context window, temperature and seeds as Stage 2.

### Execution plan

1. **tRPC tooling**: run `kaggle/trpc_layer4_validation_cell.py`; any
   failed check blocks tRPC task authoring.
2. **Runner** (to build): `benchmarks/layer4/runner.py` reusing the Stage 2
   arm code - per cell: `to_evaluation_task` -> the arm's package ->
   test-file filter -> render -> `mask_context` + `leak_check` (backward)
   -> prompt + `answer_contract` -> model -> `score_forward` against the
   corpus's index names, or `splice.evaluate` + `score_backward` (cells of
   one corpus run sequentially: splices edit the checkout in place) ->
   one `layer4.cell.v1` record.
3. **Authoring** (per corpus, 2 + 2 tasks): pick focal functions inside
   the Prism index that the corpus's tests exercise; trace them (Django
   with the SQL logger); derive forward gold with `gold_from_manifest`
   and review `*_unmapped` / `resolved_by_name`; prefer
   `async_window: false` for descendant-based gold; run the gates for
   backward tasks (`stub_seen` required); write the task JSON;
   `validate_task(task, known=<index names>)` and `validate_suite`.
4. **Freeze**: commit tasks, manifests and gate results before any model
   runs.
5. **Contamination pass**: `arm0_no_retrieval` x 16 tasks x 10 seeds
   first; flagged backward tasks are perturbed (consistent renames of the
   focal function and its callers, re-gated) or kept and reported
   separately - decided before the other arms run.
6. **Main run**: the other 6 arms x 16 tasks x 10 seeds, resumable cell
   by cell like Stage 2 (`cells.jsonl`, periodic pushes).
7. **Analysis**: `summarize(cells, tasks)` - per family and arm, over all
   tasks and over unflagged tasks: forward mean P / R / F1 and tag /
   table exact-match rates; backward pass rate, mean context tokens,
   tokens per pass, passes per 1k tokens; leak / error cells per arm.
   Pre-registered comparisons as in Stage 2 (prism_ask and prism_full
   against hybrid RAG and the repo map; the oracles as ceilings;
   arm0 as the floor).
