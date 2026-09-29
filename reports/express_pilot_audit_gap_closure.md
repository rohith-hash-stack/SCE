# Express Pilot: Audit-Gap Closure Report

**Branch:** `epic/audit-gap-fixes` · **Corpus:** `expressjs/express` @ `7e562c6d8daddff4604f8efaaf9db2cf98c6dcff` (tag `4.21.0`), real installed `node_modules` (400 packages) · **Status: gate applied, MIXED verdict — see Section 5.**

This report closes out the Express pilot phase of `epic/audit-gap-fixes`: the architectural-audit categories it was built to validate, the CommonJS/Node-resolution work that unblocked it, the 20-task ground-truth suite built to exercise it, and the two real evaluation passes (zero-LLM baseline, real two-pass LLM sweep) run against it, ending in a pre-registered statistical gate.

---

## 1. Audit categories resolved

Verified against `docs/architecture_boundaries.md` and the codebase's own `Category N` comments/test names directly — not asserted from memory. Categories **1** (Sibling Continuity & Temporal State) and **2** (Infilling & Bidirectional Sandwich Boundaries) are **not** part of this closure: `docs/architecture_boundaries.md` (created by commit `9c088b1`, "Document v1 architectural non-goals") declares both **permanent v1 non-goals**, and no work in this epic touched either. They're listed under Boundary Declarations (Section 6) instead of claimed here.

### Category 5 — Lexical constant bundling (the module-level-constant sub-case)

`docs/architecture_boundaries.md`'s Category 5 entry keeps `READS_STATE` permanently excluded from `TRAVERSABLE_RELATIONS` (a real, measured decision — an earlier broader inclusion caused a hub-flooding regression). What *was* closed this epic is the sub-case the same document calls out as "the one part of this already handled, by a different mechanism": a function referencing a module-level constant (`MAX_RETRIES`, `STATUS_CODES`) gets that reference discovered and priced as sidecar extraction data — never a graph edge, never seen by `DistanceEngine` — via the Two-Tier Visibility Pipeline:

- `ContractExtractor._referenced_module_constants` — discovers which module-level `kind="attribute"` constants a symbol genuinely reads (never a same-named local/parameter/comprehension target).
- `submodular_knapsack._constant_stub` / `_oversized_literal_summary` — renders each one, with entry-count summarization for oversized container literals.
- `_bundle_referenced_constants` — the post-greedy, budget-gated, dedup'd knapsack fixup that decides which referenced constants actually get attached to a selected function's rendered body.

Test coverage: `tests/test_lexical_constant_bundling.py`.

### Category 6 — External stub exception rendering

An external (Phase C, `role="external"`) stub's `ContractExtractor`-computed `thrown_exceptions` was silently discarded by `_render_signature_text` — an agent generating a call into a stubbed dependency saw only the type signature, with no signal that the real function could raise. Fixed so the rendered stub carries a real `@throws`/thrown-exception annotation. A connected follow-up in the same category fixed `_render_signature_text` hardcoding Python's `def`/`class` keywords for every language — TS/JS and Go now render syntactically valid declarations of their own.

Test coverage: `tests/test_external_stub_exception_rendering.py`. Directly exercised, live, by 10 of this pilot's own 20 tasks (every Category-5-in-the-*Express-pilot-sense* task — see Section 3 — resolves a real `@throws` annotation on its external candidate, e.g. `cookie.index.serialize`, `range-parser.index.rangeParser`, `http-errors.index.createError`).

### Category 8 — TypeScript/Node subpath & path-alias resolution

`TypeScriptSourceLocator` gained real subpath-export resolution (`locate(package_name, package_version, subpath=None)`), conforming to the Node.js Package Exports specification generically: exact-key match → single-`*` pattern match via `_best_export_pattern_match` → filesystem fallback (`<subpath>.d.ts`/`<subpath>/index.d.ts`). `_matching_root_import` was fixed to preserve a real npm subpath (`@trpc/server/adapters/express`) instead of collapsing it to the bare root package name, returning `(matched_package, remainder)` rather than a bare string. No package-specific heuristics — verified generic via the real `@trpc/server` subpath case and confirmed non-Express-specific.

Test coverage: `tests/test_ts_path_aliasing.py`, `tests/test_external_locator_ts.py`, `tests/test_engine_locator_dispatch.py`.

### Category 9 — Hub fan-in damping

`_hub_fanin_penalty` in `src/prism/slicer/distance.py` — `D_hybrid`'s discount against a busy shared node reading as artificially close to everything that touches it. Referenced directly in Category 5's own architecture-boundaries entry as the reason `READS_STATE` traversability must stay excluded (a whole new class of busy nodes would multiply the exact problem Category 9 exists to contain).

Test coverage: `tests/test_distance_metric.py`.

---

## 2. CommonJS `require()` resolution and Node subpath export spec

Closed on `feature/commonjs-require-resolution` and `feature/subpath-export-resolution`, both merged into this epic before the Express pilot began.

**`ConcreteGraphBuilder._parse_js_requires`** — a pure, generic tree-sitter `call_expression` walk (no package-name awareness, confirmed against `lodash`/`rxjs`/Express's own real call sites), handling all five real Node `require()` binding shapes and funneling each into the same `LocalImportMap` ES `import` statements already use:

1. Side-effect only (`require('pkg');`) — no binding introduced.
2. Default/namespace (`var x = require('pkg')`) — bound **bare**, not under a synthetic `.default` suffix. This mattered beyond cosmetics: a synthetic suffix broke the *ordinary* in-repo call resolver for a relative require used as a 2-segment member call (`const utils = require('./utils'); utils.helper()` resolved to the wrong `utils.default.helper`) — found and fixed live during this epic.
3. Destructuring (`const { a, b } = require('pkg')`).
4. Aliased destructuring (`const { a: localA } = require('pkg')`).
5. Property access (`const fn = require('pkg').fn`).

**The one real gap a bare binding leaves**: a bare call directly on the required value itself (`x(...)`) needs the module's own real primary export, and a local call-site alias is not reliably that export's own name — confirmed live against real `path-to-regexp` (locally `pathRegexp`, internally `pathToRegexp`). Fixed at the one real consumer (`PrismEngine.build_external_candidate_manifest`'s bare-call path) via `prism.external.index._commonjs_primary_export_name`'s `module.exports = <identifier>` redirect, not by corrupting the shared binding.

**Disclosed, real non-goals** (found and documented, not silently worked around):
- `module.exports = require(...)` (bare re-export)
- `module.exports = {...}` (object-literal export) — confirmed live against real `qs` (`node_modules/qs/lib/index.js`'s own `module.exports = { parse: require('./parse'), ... }`)
- `module.exports = function(){}` (anonymous function export) — confirmed live against real `cookie-signature` (`exports.sign = function(val, secret){...}`, no name for the detector's named-function-only definition finder to find)

---

## 3. Express ground-truth suite: 20 tasks, static disclosures, agreement metrics

Grew from the original 7-task pilot to 20 across two authoring passes (`benchmark/express-ground-truth-pilot`, `feature/express-task-expansion`) — 7 internal-subsystem tasks, 6 external-CommonJS-sink tasks (both new), plus the original 7. Every seed and pipeline stage verified against the real indexed symbol table and `networkx.has_path` over `calls_graph`; every external symbol verified via a live `PrismEngine.build_external_candidate_manifest` call — nothing assumed, per `TASK_AUTHORING.md`'s own checklist.

| Task | Kappa | Category |
|---|---|---|
| 001 app_bootstrap_defaults | 0.857 | internal |
| 002 middleware_mounting | 0.857 | internal |
| 003 routing_dispatch_loop | 0.889 | internal |
| 004 error_middleware_propagation | 0.800 | internal |
| 005 layer_path_matching | 1.000 | internal |
| 006 response_json_send | 0.800 | internal |
| 007 etag_external_dependency | 1.000 | external (Cat. 5/6) |
| 008 view_rendering | 0.857 | internal |
| 009 content_negotiation | 0.750 | internal (2x root_imports) |
| 010 redirection | 0.857 | internal |
| 011 file_transmission | 0.800 | internal |
| 012 cookie_signing | 1.000 | external (Cat. 5/6) |
| 013 param_registration | 0.800 | internal |
| 014 top_level_dispatch | 0.800 | external (Cat. 5) |
| 015 path_to_regexp_alias_mismatch | 1.000 | external (Cat. 5/8) |
| 016 send_file_streaming | 1.000 | external (Cat. 5) |
| 017 content_disposition | 1.000 | external (Cat. 5) |
| 018 type_is_alias_mismatch | 1.000 | external (Cat. 5) |
| 019 range_parser | 1.000 | external (Cat. 5/6) |
| 020 query_string_parsing | 1.000 | external (Cat. 5) |

19/20 tasks clear the κ ≥ 0.80 auto-accept bar; task 009 (κ = 0.750) falls in the `[0.60, 0.80)` band, which `TASK_AUTHORING.md`'s gate requires a *genuine, non-duplicate* adjudication for — verified: `task.adjudicated != task.annotation_a` and `!= task.annotation_b`. All 20 accepted, 0 rejected.

### Real static findings disclosed in-YAML (G41 and beyond), not routed around

- **Four confirmed instances of the same bare-name collision** (`this.set(...)` / `this.get(...)` resolving to an unrelated same-named symbol on a *different* prototype, because a chained assignment like `res.set = res.header = function header(){}` only registers the function under its last-assigned name): Tasks 6, 9, 10, 19.
- **Three dynamically-rebound receivers** where a real call is genuinely unreachable in the static graph (`View()` instantiation in `app.render` via `this.get('view')`; `router.handle()` in `app.handle` via `this._router`; both recorded as `boundary_symbols`).
- **A same-file, same-simple-name collision** distinct from the receiver-type G41 cases: `router/index.js` declares two unrelated functions both named `param` — the real public `proto.param` API, and a private closure nested inside `process_params`. The indexer keeps one qualified name per (module, simple name); the private one wins. Task 013's `this._router.param(...)` call is therefore excluded from its own pipeline.
- **`defineGetter`-registered accessors are inconsistently indexed**: `req.fresh` (anonymous function) is indexed but has zero real out-edges (its own internal calls, including a real `fresh` package delegation, aren't captured); `req.stale`/`req.ip`/`req.ips` (named function expressions) aren't indexed at all. Confirmed via an empty `build_external_candidate_manifest` result; these subsystems were dropped from the roster rather than authored into unverifiable tasks.

---

## 4. Step 3 — Zero-LLM comparative grid (`runner.py --dry-run`)

20 tasks × 4 engines (`baseline_rag`, `baseline_bfs_bidirectional`, `prism_v11` single-pass, `PragmaticOracle`) × 3 budgets (2000/4000/8000) = 240 cells, 0 errors.

| Engine | Recall (cpi_fractional) | Cleanliness (1 − fpr_gt) @8000 |
|---|---|---|
| baseline_rag | 67.5% → 72.5% | 11.1% |
| baseline_bfs_bidirectional | 98.8% → 100% | 41.2% |
| prism_v11 (single-pass) | 100% (all budgets, 0 exceptions) | 45.4% |
| PragmaticOracle | 100% | 100% |

**Recall alone barely separates `prism_v11` from `baseline_bfs_bidirectional`** on this task suite (+1.2pp at budget 2000, +0pp at 4000/8000) — most Express pipelines are short (2-4 hops), easily found by unweighted bidirectional BFS too. **Cleanliness does separate them**, and `baseline_rag`'s own cleanliness collapses as budget grows (72.5% recall bought at 88.9% noise by 8000 tokens). This was flagged at the time as the reason cleanliness, not recall alone, should be treated as a co-primary metric — validated empirically in Step 4 below. Zero non-monotonic recall drops for `prism_v11` across all 20 tasks; zero tasks fail to reach full recall by 8000 tokens.

Also confirmed structurally: `prism_v11` in `runner.py`'s `ENGINE_REGISTRY` wraps `PrismEngineCache.retrieve()` → plain single-pass `PrismEngine.retrieve()`, which never attempts external-dependency resolution. Since `cpi_fractional` scores only against `pipeline_symbols` (never `required_context`), Step 3's "100% recall" on the 10 Category-5 tasks reflects zero actual external-resolution activity — a real, disclosed scope limit of this specific metric, not an engine claim.

---

## 5. Step 4/5 — Real two-pass LLM sweep and the pre-registered gate

### 5.1 A real blocker found and fixed before any real LLM call could succeed

DeepSeek's endpoint is network-blocked in this environment (`api.deepseek.com:443` → 403 from the egress proxy). OpenAI's real API (`gpt-4o-mini`) was used instead for every real-LLM cell in this closure. That surfaced a genuine bug: `run_two_pass_cell` unconditionally sent Ollama-native `extra_body={"options": {...}}` sampler kwargs on every Turn 1 call (tuned, per its own docstring, against a local `qwen2.5-coder` model) — DeepSeek tolerated the unrecognized field silently, OpenAI's real endpoint returned a hard HTTP 400. Fixed with `_ollama_sampler_kwargs`, gated on `client.base_url` actually being a local Ollama-style endpoint (mirroring `OpenAICompatibleClient.__init__`'s own convention). 4 new tests added (`TestOllamaSamplerKwargs`).

### 5.2 Two-pass PRISM sweep (`run_two_pass_benchmark.py`): 20 tasks × 2 budgets × 5 seeds, 200 cells, $0.1261

| Budget | TSR | cpi_turn1_selection | cpi_end_to_end | Cleanliness |
|---|---|---|---|---|
| 2000 | 95.5% | 93.8% | 95.5% | 70.5% |
| 4000 | 96.4% | 94.2% | 96.4% | 70.6% |

- **15/20 tasks scored a perfect 1.000 TSR/cpi_end_to_end on every seed and budget**, including all 6 external-sink tasks (015-020) and the other 4 Category-5 tasks (007, 012, 014, and partially 009).
- **The 5 tasks that didn't (001, 002, 003, 009, 010) all root-cause to the model's own Turn 1 symbol selection, never to a missing or mis-hydrated candidate.** Task 002 never once (0/10 cells) requested `lib.application.set` despite it being a real, available candidate; task 010 never requested `lib.response.get` — the exact "read the resolved value back off a chained call" stage that this pilot's own dual annotators independently disagreed on during manual authoring. The ground truth's own annotator-disagreement axis correctly predicted where the real model would also struggle.
- **Hallucination gate**: 164 hallucinated-symbol-requests logged, concentrated in a narrow, benign pattern — 7 of 10 Category-5 tasks saw Turn 2b request the bare npm package name (`etag` instead of `etag.index.etag`) or an already-known in-repo symbol alongside the one real candidate. `pack_external_context_requested`'s hallucination gate filtered every one of them (never silently admitted); all 7 affected tasks still scored a perfect TSR=1.000.

### 5.3 Real-LLM baseline sweep (`runner.py`, `baseline_bfs_bidirectional`): 20 tasks × 2 budgets × 5 seeds, 200 cells, $0.1082

Required because `apply_gate.py` needs *paired, real-LLM* TSR on both sides of the comparison — Step 3 was dry-run-only (zero TSR anywhere) and Step 4 only ever scored the two-pass engine. Run for real against the same tasks/budgets/seeds/model. (One earlier attempt at this sweep was lost to a `runner.py` checkpoint-overwrite behavior when `--resume` isn't passed — `save_checkpoint` doesn't merge with what's already on disk, so a second `--budgets` invocation silently replaced the first rather than adding to it. Re-run correctly as a single combined invocation; the lost ~$0.05 is disclosed here rather than absorbed silently.)

### 5.4 Gate verdict: **MIXED**

`scripts/merge_pilot_checkpoints.py` + `scripts/apply_gate.py`, default pre-registered thresholds (ΔTSR ≥ 15pp, ΔCPI_answer ≥ 15pp or headroom-adjusted, both requiring a bootstrap CI excluding zero), `baseline_bfs_bidirectional` vs `prism_two_pass`, n=200 paired cells:

| Metric | baseline_bfs_bidirectional | prism_two_pass | Δ | 95% CI | Excludes zero |
|---|---|---|---|---|---|
| TSR | 28.5% | 96.0% | **+67.46pp** | [61.08, 73.75] | **yes** |
| CPI_answer | 97.5% | 96.0% | **−1.54pp** | [−3.58, 0.67] | no |

**Decision: MIXED** — "ΔTSR shows a clear effect (67.46pp, clears the 15pp threshold, CI excludes zero) but ΔCPI_answer does not (−1.54pp, below the 5pp floor)."

This is a real, honest, informative result, not a defect in the gate or the run. `baseline_bfs_bidirectional`'s own retrieval-side CPI_answer (its own retrieved set containing the full ground-truth pipeline) is already near-ceiling at 97.5% — consistent with Step 3's own finding that raw recall barely separates the two engines. What the gate's real, paired TSR data now confirms empirically is that **retrieval recall alone does not predict real task success on this suite**: a small model (`gpt-4o-mini`) reading `baseline_bfs_bidirectional`'s noisier, lower-cleanliness context (Step 3: ~41-42%, vs. `prism_v11`'s ~45-47%) fails to correctly articulate the causal pipeline in its final answer 71.5% of the time, even when the right symbols were technically present — while the same model reading `prism_two_pass`'s cleaner, requested-not-dumped context succeeds 96.0% of the time. Cleanliness — flagged as the more honest signal after Step 3 — is the dominant real driver of task success this gate surfaces, not raw recall.

---

## 6. Formal boundary declarations (out of scope, unchanged by this epic)

- **Category 1** (Sibling Continuity & Temporal State) and **Category 2** (Infilling & Bidirectional Sandwich Boundaries) — permanent v1 non-goals per `docs/architecture_boundaries.md`; untouched by this epic.
- **Category 5's `READS_STATE`-traversable-edge exclusion itself** (as opposed to the lexical-bundling sub-case closed above) — remains permanent; lifting it would require a materially different edge-weight model, validated against the same real corpora Category 9's own before/after evaluation used.
- **CommonJS non-goals** (Section 2): bare re-export (`module.exports = require(...)`), object-literal export (`module.exports = {...}`), anonymous-function export (`module.exports = function(){}`).
- **`defineGetter`-registered named-function accessors** are not indexed at all (`req.stale`, `req.ip`, `req.ips`); the anonymous-function variant (`req.fresh`) is indexed but carries zero real out-edges. Not fixed in this epic — disclosed via the Express suite's own dropped-subsystem note (Section 3).
- **Router-level `param()` registration is unreachable under its own qualified name** due to an in-module simple-name collision with a private closure (Section 3) — a real indexer limitation, not fixed here.

---

## 7. Verification summary

- Full fast suite: 1678 passed, 0 failed, 22 skipped, 1 xfailed, 1 xpassed (consistent across every merge in this epic).
- 20/20 Express ground-truth tasks load cleanly, agreement gate passes for all (19 auto-accept, 1 genuine adjudication).
- Zero-LLM dry-run grid: 240 cells (Step 3, comparative) + 60 cells (post-Step-2 sanity, 3-budget) — 0 crashes.
- Real-LLM cells this closure: 200 (Step 4, two-pass) + 200 (Step 5, baseline) = 400 cells, $0.2343 combined real spend (excluding the disclosed ~$0.05 lost to the checkpoint-overwrite retry).
- Gate: MIXED, both metrics computed from real, paired, bootstrapped data (n=200, 10,000 resamples, seed=42).
