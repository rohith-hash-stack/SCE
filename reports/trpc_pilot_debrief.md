# tRPC Pilot: Monorepo Locator Validation + Small-Pilot Real-LLM Sweep

**Status: small pilot (Section 3, Step 3 of `docs/roadmap_public_release.md`)
complete.** Not the full 20-25 task benchmark (Step 4) - this validates the
pipeline end-to-end on a small, real, structurally verified task set before
committing to that larger run.

## 1. Scope decision: `packages/server` only

tRPC (`v10.45.4`, pinned commit `2ec29bfa2e3a170901be5201962f30e9faf73f96`)
is a pnpm workspace monorepo (`packages/{server,client,react-query,next,...}`).
Monorepo locator validation (real `pnpm install` against the pinned
commit, `TypeScriptSourceLocator` tested directly) found:

- **Genuine external third-party dependencies resolve correctly** -
  `@tanstack/react-query` (imported from `packages/react-query`) resolves
  through pnpm's real content-addressable store to its actual published
  `.d.ts`, confirmed live. Category 8's existing locator needed zero
  changes for this case.
- **Monorepo-internal cross-package imports do not** - `@trpc/client`
  importing `@trpc/server` resolves through a real workspace symlink
  (`node_modules/@trpc/server -> ../../../server`), but `packages/server`'s
  own `package.json` declares `"types": "dist/index.d.ts"` and `dist/` is
  never built in a source checkout. The locator correctly finds the
  symlink and correctly returns `[]` - not a bug, but a different
  resolution problem than Category 8's contract covers. Documented as
  `docs/architecture_boundaries.md` Category 10.

`packages/server` (the core pipeline-logic surface - generics-heavy
procedure builders, router factories, exactly what this benchmark exists
to validate) has **zero runtime dependencies of its own**, so scoping the
pilot to it sidesteps Category 10 entirely rather than authoring tasks
against a known-unresolvable path.

## 2. Real, disclosed indexing gap found and fixed

Running Phase 3's own zero-LLM-cost graph-quality spike against
`packages/server/src` surfaced a real Turn-1 recall gap: `createRouterFactory`'s
own returned object (`{ createCaller(ctx) {...}, getErrorShape(opts) {...} }`)
registered its methods as flat, bare top-level symbols with no real
standalone identity, silently dropping their own real outgoing calls from
their own candidate manifests - `_register_definition`'s ancestor walk
only recognized an enclosing *class*, never a function that owns/returns
an object literal. Fixed (`fix/js-object-literal-method-scoping`, merged
to `develop`) by recognizing the owning function as a real scope boundary
when the walk passes through an object literal first - JS/TS/TSX-only,
zero behavior change for any case it can't resolve with real evidence
(a plain top-level object literal, an anonymous-IIFE owner). Verified via
4 new regression tests plus live re-verification against this exact repo.

## 3. Corpus resolver fix: monorepo `subdir` support

`benchmarks/runner.py`/`run_two_pass_benchmark.py` both resolve a corpus
via `benchmarks.corpora.resolver.resolve(name)`, which previously always
returned the bare clone root - correct for a single-package repo
(Django/Express/FastAPI/Gin), wrong for tRPC: indexing the whole monorepo
root would have produced qualified names prefixed by the full relative
path (`packages.server.src.core.router....`) instead of what every
ground-truth task here was authored against (`core.router....`).

Added `CorpusSpec.subdir` (optional, `None` for every existing corpus -
zero behavior change) and taught `resolve()` to join it onto the real
clone root, failing loud (`CorpusResolutionError`) rather than silently
falling back if the declared subdir doesn't exist. `pinned_commits.json`'s
`trpc` entry now declares `"subdir": "packages/server/src"`. Verified with
3 new regression tests (local git repos, no network) and confirmed live -
`resolve("trpc")` now returns `.../trpc/packages/server/src`, and a real
two-pass dry run indexes exactly that directory.

## 4. Ground truth: 6 tasks authored, all verified

`benchmarks/ground_truth/tasks/trpc/trpc_t02_00{1-6}*.yaml` - every
seed/pipeline symbol verified against the real indexed corpus, every
stage's transitive reachability verified via `networkx.has_path` over
`builder.calls_graph`, all 6 pass the harness's real agreement gate
(Cohen's kappa >= 0.80, computed by the loader itself, never hand-typed).
Two tasks (`t02_002`, `t02_005`) specifically exercise the object-literal-
method-scoping fix above, using real, independently-authored ground
truth rather than just the spike's own ad hoc check.

## 5. Real-LLM sweep results

6 tasks x 5 seeds (42-46), budget 4000, `gpt-4o-mini` (DeepSeek's own
endpoint remains network-blocked in this container; OpenAI's real API
used instead, same substitution as the Express pilot). Total real cost:
**$0.0214** for all 30 cells.

| Task | Mean TSR (n=5) | Mean CPI_turn1 | Mean CPI_e2e |
|---|---|---|---|
| t02_001 init_bootstrap | 0.733 | 1.000 | 0.900 |
| t02_002 error_shape_http_status | 1.000 | 1.000 | 1.000 |
| t02_003 procedure_caller_error_normalization | 1.000 | 1.000 | 1.000 |
| t02_004 merge_routers | 1.000 | 0.667 | 1.000 |
| t02_005 router_caller_dispatch | 1.000 | 1.000 | 1.000 |
| t02_006 router_factory_path_registration | 0.667 | 1.000 | 1.000 |
| **Overall** | **0.900** | **0.945** | **0.983** |

**Both sub-1.0-TSR tasks are reasoning gaps, not retrieval gaps** - CPI
stayed at or near 1.0 for both across every seed, meaning every real
pipeline symbol was actually retrieved and present in context every
time. Turn-2's own raw responses show the model:

- **t02_006** (every seed, identically): retrieved and named all 3 real
  symbols correctly, but reported `recursiveGetPaths` before
  `omitPrototype` - the reverse of the real source's own execution order
  (`omitPrototype` runs first, building the empty record
  `recursiveGetPaths` is then called to populate). The causal-order-
  sensitive scorer correctly penalizes this as a partial match. This is
  the exact "plausible reordering" failure mode `trpc_t02_006`'s own
  `annotation_b` was authored to anticipate.
- **t02_001** (seeds 44/45/46): retrieved and correctly ordered most of
  the 6-stage pipeline but omitted `createBuilder` (the `procedure:`
  factory call) from at least one response, and/or reordered
  `createMiddlewareFactory`/`createCallerFactory` relative to each
  other - a real, if minor, completeness gap on the pilot's own richest,
  widest-fan-out task.

No hallucinated symbols were requested in any cell (`skipped_hallucinated`
empty throughout); every miss is a real omission or reordering of a
genuinely retrieved, genuinely correct candidate.

## 6. Status and next steps

This is a clean, real, small-pilot result: retrieval-side Two-Pass
performance on tRPC's core package is excellent (mean CPI_e2e 0.983)
and the residual TSR gap is isolated to Turn-2 reasoning quality on
ordering/completeness for richer, wider pipelines - not a retrieval
defect this epic's own fixes need to chase further. Per the roadmap's own
staged plan (Section 3), the next real-cost step is the full 20-25 task,
5-seed benchmark plus gate + manual audit - not started here, pending
explicit direction on scope/timing for that larger real-LLM commitment.

Known, deferred follow-up (not part of this pilot's own scope): a
pre-existing Express ground-truth task
(`express_t02_013_param_registration.yaml`) documents a name-collision
limitation this epic's own Option C fix has since resolved -
`lib.router.param` now correctly resolves to the real public API rather
than the unrelated private closure that used to shadow it. Filed as a
separate follow-up task rather than churned here, per the decision to
keep the Express milestone's own closed, tagged history untouched.
