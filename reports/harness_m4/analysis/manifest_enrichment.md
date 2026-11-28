# Manifest enrichment: what PRISM could precompute for the Turn-1 candidate manifest

No code modified, no Kaggle run. All numbers are offline: M4 Turn-1 manifests regenerated with the
pre-change PRISM tree, the M4 Arm 5 bundles (Turn-1 selections, seeds 42–44), and per-candidate fields
computed for every symbol in each seed's 3-hop CALLS/INSTANTIATES neighbourhood, upstream and downstream.
Corpora: fastapi, django, express, trpc. Tasks: 32 T5 (blast radius), 89 T2 (localization).
Scripts and raw rows: session scratchpad `chg/mf_extract.py`, `mf_analyze.py`, `mf_analyze2.py`, `mf_null.py`.

## Summary

* **The hypothesis holds for T5 but not in the form stated.** In M4 the T5 LLM's Turn-1 picks inside the
  manifest are at chance (within-seed AUC 0.51). An engine-only ranking from hop + direction + caller weight
  scores 0.93–0.94 on the same rows. But the decisive fact is already in the manifest: rows labelled
  `caller` are 77% gold, `transitive` rows 0%. The LLM still picks only 57% of gold callers, while picking
  73% of non-gold callers and 65% of `callee` rows (1% gold). Selecting every `caller` row would raise
  recall from 0.57 to 0.98 and cut non-gold picks from 105 to 16. The LLM isn't missing the fact; it isn't
  acting on it. The Turn-1 system prompt asks it to "trace the complete causal execution path from the seed
  to termination", a downstream instruction, on every task type.
* **T2 has no headroom from metadata.** The LLM already separates gold from non-gold as well as the
  engine's own ranking does (0.88 vs 0.89 within the manifest). It selects 93% of in-manifest gold, or 98%
  with Turn 2's automatic direct-callee union. No field adds more than the measured noise floor beyond
  hop + direction.
* **Ranked predictive fields (beyond hop + direction).** Only one family clears the noise floor on T5:
  body size (lines/tokens; +0.04 to +0.08 AUC), with parameter count and annotations close behind. Gold
  callers are substantial functions, not thin wrappers. The 4-axis mask, centrality, fan-in, SCC, call-site
  flags, docstring and complexity add nothing once test code is removed. Their large single-field scores
  are proxies for direction or for "is test code".
* **One real information gap, unrelated to selection signal.** The `signature` field is cut to its first
  line when a declaration header spans lines without a trailing colon (every multi-line TS/JS signature) or
  runs past 10 lines (Python). Rows cut this way: 27% in trpc, 13% in fastapi, 1% in django, 0% in express.
  The LLM sees `export function getDataTransformer(` with no parameters or return type.
* **Recommendation.** Manifest-only enrichment (Configs A–D below) is expected to move T5 little, because
  M4 shows the LLM ignores the direction it already has. If one serializer config is run, run Config D
  (hop + explicit direction + body lines + full signatures, fields documented in the prompt). Pair it with
  a prompt-only arm that tells the model that for blast-radius tasks the callers are the answer (§6). That
  pairing separates "missing facts" from "ignored facts".

---

## 1. Current manifest schema

**Serializer.** `src/prism/packer/candidate_index.py:build_candidate_manifest` (lines 227–341 at HEAD; the row f-string is at the end).
Arm 5 calls it through `PrismEngine.build_candidate_manifest` (`harness/arms/arm5_prism.py:135–146`).
M4 used the default mode (`direction="downstream"`) for every task type; `PRISM_BLAST_MODE` came later.
`PRISM_MANIFEST_STRICT` and `PRISM_TURN1_STRICT_PROMPT` were off in M4.

**Structure the LLM sees.** One wrapper and one pipe-delimited line per candidate:

```
<candidate_index>
qualified_name|role|kind|signature|calls=[q1,q2,...]
qualified_name|caller|kind|signature|calls=[...]|binds_return=true/false|nontrivial_args=true/false
</candidate_index>
```

| field | content |
|---|---|
| `qualified_name` | dotted id (module path + class + name) |
| `role` | `seed` / `callee` (a direct successor of the seed in the causal graph) / `caller` (an upstream candidate) / `transitive` (anything else, all downstream) — `_classify_role` (submodular_knapsack.py:984) |
| `kind` | `function` / `method` / `class` (`SymbolInfo.kind`) |
| `signature` | raw declaration header from source, whitespace-collapsed; decorators included for Python (line ranges start at decorators) — `_declaration_line` → `_signature_stub` |
| `calls=[...]` | qualified ids of the symbol's own CALLS/INSTANTIATES successors in the structural graph, sorted, capped at 12 |
| `binds_return`, `nontrivial_args` | caller rows only (`UpstreamCaller.unpacks_return` / `.supplies_nontrivial_args`) |

**Sort order.** Alphabetical by qualified name (`for qname in sorted(candidates)`), not by distance or
relevance. The seed row sits wherever its name sorts.

**Turn-1 system prompt** (`benchmarks/run_two_pass_benchmark.py:325`, verbatim):

> You are a senior software engineer investigating a codebase. You will be given a compact <candidate_index> - every symbol reachable from a seed function, one per line as qualified_name|role|kind|signature|calls=[...] (role is one of seed/callee/caller/transitive; signature is the symbol's own raw declaration line; calls lists the names it directly invokes in its own body, deterministically extracted, never a docstring or comment) - followed by a real task. Examine the symbol signatures and their direct call targets to trace the complete causal execution path from the seed to termination. Request all necessary intermediate and helper symbols required to form an unbroken execution chain. Only name symbols that appear in the index - never invent one.

**Turn-1 user prompt** (`_turn1_user_prompt`, line 491):

```
{manifest}

Task:
{task prompt}

Respond with a JSON object: {"thought_process": "1-2 sentences on why", "requested_symbols": ["qualified.name", ...]} - requested_symbols ordered seed first, then the causal stages in execution order, using each symbol's own full qualified_name exactly as given in the index (never a bare name from a calls=[...] list). Respond with this JSON object and nothing else.
```

Note that the binds_return/nontrivial_args fields are not mentioned in the system prompt.

**Real M4 entries.** The bundles store `manifest_candidates`, not the manifest text, so the manifests were
regenerated with the pre-change tree. All 121 M4 Arm 5 tasks reproduce the recorded candidate counts exactly
(fastapi 32/32, django 28/28, express 22/22, trpc 39/39). One shape for all corpora; quoted verbatim:

```
fastapi  fastapi.dependencies.models.Dependant|callee|class|@dataclass class Dependant:|calls=[]
fastapi  fastapi.dependencies.utils.get_sub_dependant|caller|function|def get_sub_dependant( *, depends: params.Depends, dependency: Callable[..., Any], path: str, name: Optional[str] = None, security_scopes: Optional[List[str]] = None, ) -> Dependant:|calls=[fastapi.dependencies.models.SecurityRequirement,fastapi.dependencies.utils.get_dependant]|binds_return=true|nontrivial_args=true
django   django.core.handlers.base.BaseHandler.check_response|callee|method|def check_response(self, response, callback, name=None):|calls=[asyncio.iscoroutine]
django   django.contrib.admin.helpers.AdminReadonlyField.get_admin_url|caller|method|def get_admin_url(self, remote_field, remote_obj):|calls=[django.contrib.admin.utils.quote,django.urls.base.reverse,django.utils.html.format_html]|binds_return=true|nontrivial_args=true
express  lib.application.defaultConfiguration|callee|function|app.defaultConfiguration = function defaultConfiguration() {|calls=[lib.application.debug,lib.application.enable,lib.application.set,path.resolve,setprototypeof]
express  lib.router.trim_prefix|caller|function|function trim_prefix(layer, layerError, layerPath, path) {|calls=[lib.router.debug,lib.router.layer.handle_error,lib.router.next]|binds_return=false|nontrivial_args=true
trpc     transformer.getDataTransformer|callee|function|export function getDataTransformer(|calls=[]
trpc     core.internals.procedureBuilder.createResolver|caller|function|function createResolver(|calls=[core.internals.procedureBuilder.createNewBuilder,core.internals.procedureBuilder.createProcedureCaller]|binds_return=false|nontrivial_args=true
```

A complete small manifest (fastapi_t02_016):

```
<candidate_index>
fastapi.exceptions.HTTPException|callee|class|class HTTPException(StarletteHTTPException):|calls=[]
fastapi.security.oauth2.OAuth2.__call__|callee|method|async def __call__(self, request: Request) -> Optional[str]:|calls=[fastapi.exceptions.HTTPException]
fastapi.security.oauth2.OAuth2PasswordBearer.__call__|seed|method|async def __call__(self, request: Request) -> Optional[str]:|calls=[fastapi.exceptions.HTTPException,fastapi.security.utils.get_authorization_scheme_param]
fastapi.security.utils.get_authorization_scheme_param|callee|function|def get_authorization_scheme_param( authorization_header_value: Optional[str], ) -> Tuple[str, str]:|calls=[]
</candidate_index>
```

**Truncated signatures.** `_signature_stub` keeps header lines until one ends with `:`, up to 10 lines.
If no line does, it falls back to the first line only. Every multi-line TS/JS signature therefore loses
its parameters and return type (TS/JS headers end in `{`), and so does any Python header longer than
10 lines:

| corpus | manifest rows | signature ends at `(` |
|---|---|---|
| django | 465 | 5 (1%) |
| fastapi | 348 | 46 (13%) |
| express | 179 | 0 |
| trpc | 316 | 85 (27%) |

## 2. What PRISM computes before serialization

Status key: **shown** = in the manifest today; **implied** = derivable by the LLM from what is shown;
**in scope** = computed inside `build_candidate_manifest` but not written; **trivial** = one lookup on
`builder` (graph / symbol table / call-site edge attributes), no new analysis; **new** = needs new code.

| | field | status | where |
|---|---|---|---|
| A | hop distance (integer) | **in scope, discarded** (downstream: `_real_call_chain_reachable` inside `_combined_hop_scope_filtered`; upstream: `walked` in blast mode, implicitly 1 in default mode) | candidate_index.py:118, 302 |
| A | weighted downstream distance `dist_w` | **in scope** (`dist_w_map`, causal-graph Dijkstra, capped at 3.0) | :282 |
| A | direction | **implied by `role`**: `caller` = upstream, `callee`/`transitive` = downstream, in both modes | `_classify_role` |
| A | caller weight `W_upstream` | **in scope** (`upstream_callers[q].weight`); its two components are **shown** for direct callers | blast_radius.py |
| A | fan-in | **trivial** (`builder.graph.in_degree`, CALLS/INSTANTIATES) | — |
| A | fan-out | **implied** (`calls=[...]`, capped at 12) | — |
| A | is_hub / is_leaf | **trivial** / **implied** (`calls=[]`) | — |
| A | is_bridge (articulation point) | **new**, cheap per query (`nx.articulation_points` on the candidate subgraph) | — |
| B | kind | **shown** | — |
| B | is_dunder / is_private | **implied** by the name | — |
| B | decorators, property, staticmethod, classmethod | **implied** for Python (decorators are part of `signature`) | — |
| B | is_abstract | **new**, cheap (`abstract*` decorator or `raise NotImplementedError` in body) | — |
| C | body line count | **trivial** (`SymbolInfo.line_range`) | — |
| C | body token count | **trivial** (`_default_costs`; already computed in blast mode for the budget) | submodular_knapsack.py:1209 |
| C | docstring presence, cyclomatic estimate | **new**, cheap (walk of `builder.def_node(q)`) | — |
| C | parameter count, annotations, return type | **implied** by `signature` except on truncated rows (§1); `builder._param_count` exists | concrete_builder.py |
| D | is_test / SymbolRole | **trivial** (`SymbolInfo.role`); partly implied by module path (`tests.`) | symbol_table.py |
| D | module path, top-level namespace, same module as seed | **implied** by `qualified_name` | — |
| D | is_exported | **new** (`__all__` / `export` scan) | — |
| E | SCC membership | **new**, index-time (Tarjan on the call graph) | — |
| E | betweenness / PageRank | **new**, index-time (approximate betweenness, k-sample) | — |
| E | uncapped distance both directions | downstream capped at `max_hops`; upstream beyond hop 1 only in blast mode | — |
| F | 4-axis mask | computed and cached at index time (`compute_feature_masks_cached`); **not in scope** here, one call to fetch | semantics/extractor.py |
| G | caller binds the seed's return / passes non-trivial args | **shown** for direct callers (hop 1) only | blast_radius.py |
| G | call inside a loop / try / null guard, call kind, argument flow | **trivial**: already on every structural CALLS edge (`inside_loop`, `inside_try_catch`, `guarded_by_null_check`, `call_kind`, `argument_flow`, `is_return_bound`) | graph/call_site.py |
| — | edge reached only by a best-effort resolution (`kind=TENTATIVE_CALL`) | **trivial** (edge attribute; new graph only) | — |

## 3. Trivial additions

LOC = lines added to the serializer (plus a prompt sentence when the field needs explaining). "Signal"
is the measured result from §5 (increment over hop + direction, leave-one-task-out; noise floor ≈ ±0.03).

| rank | field | source | LOC | signal T5 | signal T2 | plausible LLM benefit |
|---|---|---|---|---|---|---|
| 1 | full signature (fix truncation) | `_signature_stub` header rule per language | ~6 | n/a (presentation) | n/a | yes: restores params and return type on 13–27% of fastapi/trpc rows |
| 2 | `hop=` integer | return the hop map from `_real_call_chain_reachable` / `walked` | ~6 | ≈0 within direction | carries most of T2's distance signal (down1 61% gold, down2 10%, down3 2%) | small: T2 `role` already splits hop 1 from the rest; LLM already prefers hop-2 over hop-3 transitive (37% vs 22% non-gold picks) |
| 3 | `lines=` / body tokens | `line_range`, `_default_costs` | 2 | **+0.04 to +0.08** (only field above noise) | ≈0 | moderate for T5: separates substantive consumers from thin wrappers |
| 4 | explicit `dir=up/down` | from the walks | 2 | redundant with `role` | redundant with `role` | only as emphasis |
| 5 | `w=` numeric W_upstream, flags on all upstream rows | `upstream_callers`; hop>1 needs `compute_upstream_callers` per walked node (~10 LOC) | 1–10 | ≈0 (`binds_return` callers 46% gold vs 46% without) | n/a | low |
| 6 | fan-in | graph degree | 3 | ≤ noise | ≤ noise | low |
| 7 | `is_test` | `SymbolInfo.role` | 1 | moot: Design C excludes test callers; was a proxy in M4 | ≤ noise | low |
| 8 | call-site flags (loop / try / guard / return-bound) | edge attributes | 4 | ≤ noise | ≤ noise | low |
| 9 | 4-axis mask | `compute_feature_masks_cached` + `describe_mask` | 4 | ≤ noise after removing tests | ≤ noise | none measured |

## 4. Moderate additions

| field | LOC | cost | when | measured signal |
|---|---|---|---|---|
| Betweenness (approx., k=256 samples) | ~10 | O(k·E) once per corpus; computed for Django (32k functions) inside this study's extraction, not separately timed | index time, cache | within-stratum AUC 0.56–0.58; increment ≤ noise |
| PageRank | ~5 | O(iter·E) once | index time | 0.50–0.71 within stratum; increment ≤ noise |
| SCC membership (`in_cycle`) | ~8 | O(V+E) once | index time | 0.49–0.51; nothing |
| Bridge / articulation point | ~6 | O(V+E) on the candidate subgraph | per query (cheap) | 0.57–0.68 within stratum; increment +0.01 to +0.02 (≤ noise) |
| Cross-module reference count | ~5 | graph degree filtered by module; no LSP needed | index or query | fan-in variants ≤ noise |
| Git recency / churn | ~30 + history refetch | git log walk per file | index time | **not measurable here**: all four pinned corpora are single-commit shallow clones |

## 5. Which fields predict gold (load-bearing)

**Method.** For each task, candidates = every symbol within 3 hops of the seed, upstream and downstream,
over structural CALLS/INSTANTIATES edges. Label = membership in the task's `pipeline_symbols`. Per field:

* **single-field within-seed AUC**: per task, then averaged (removes per-seed base rates). Categorical
  fields use leave-one-task-out target encoding. AUC < 0.5 means a lower value predicts gold.
* **within-stratum AUC**: the same inside each (direction, hop) cell, i.e. signal beyond hop and direction.
* **increment**: leave-one-task-out logistic model, hop + direction + field vs hop + direction, scored by
  within-seed AUC.
* **noise floor**: the same increment for a random field, 8 draws: −0.027 … +0.036 (largest +0.036).
  Increments inside that band are not evidence.

Panels: T5 on the M4 graph with test-code candidates removed (Design C removes them, and T5 gold excludes
them by construction); T5 on the fixed graph inside the actual Design C manifests; T2 on the M4 graph.

**Gold coverage of the neighbourhood.** T5: 162 of 178 non-seed gold within 3 hops; 54 in the M4
manifests; 158 in the Design C manifests. T2: 176 of 176 within 3 hops; 176 in the M4 manifests.

**Gold rate by direction × hop.** The gold sets are directional by definition:

| cell | T5 (M4, production) | T5 (Design C manifests) | T2 (M4) |
|---|---|---|---|
| up 1 | 43% (n=176) | 51% (138) | 0% (433) |
| up 2 | 32% (169) | 69% (74) | 0% (323) |
| up 3 | 28% (97) | 57% (47) | 0% (407) |
| down 1 | 0% (93) | 0% (107) | 61% (227) |
| down 2 | 0% (78) | 0% (89) | 10% (256) |
| down 3 | 0% (35) | 0% (21) | 2% (175) |

**Model AUCs (within seed, leave-one-task-out).**

| features | T5 M4 prod | T5 Design C | T2 |
|---|---|---|---|
| hop | 0.53 | 0.49 | 0.78 |
| hop + direction | **0.89** | **0.83** | **0.91** |
| + caller weight (W, binds_return, nontrivial_args) | 0.88 | 0.85 | 0.91 |
| + dist_w | 0.87 | 0.83 | 0.91 |
| + all fields | 0.93 | 0.93 | 0.93 |

**Ranked list: signal beyond hop + direction.**

| rank | field | T5 M4 prod: increment / stratum AUC | T5 Design C: increment / stratum AUC | T2: increment / stratum AUC | verdict |
|---|---|---|---|---|---|
| 1 | body tokens | +0.038 / 0.80 | **+0.080** / 0.84 | −0.013 / 0.57 | only consistent T5 signal |
| 2 | body lines | +0.036 / 0.80 | **+0.073** / 0.83 | −0.011 / 0.63 | same signal as 1 |
| 3 | parameter count | +0.035 / 0.70 | +0.063 / 0.66 | +0.032 / 0.56 | T5 only; visible in a full signature |
| 4 | annotated params | +0.024 / 0.63 | +0.058 / 0.65 | +0.040 / 0.63 | visible in a full signature |
| 5 | cyclomatic estimate | +0.018 / 0.66 | +0.046 / 0.68 | +0.041 / 0.67 | proxy for size; borderline |
| 6 | Form axis | −0.021 / 0.59 | +0.046 / 0.67 | +0.030 / 0.67 | inconsistent sign across panels |
| 7 | articulation point | +0.017 / 0.63 | +0.008 / 0.57 | +0.010 / 0.62 | noise |
| 8 | PageRank | +0.015 / 0.71 | +0.012 / 0.64 | +0.006 / 0.50 | noise |
| 9 | W_upstream | −0.002 / 0.61 | +0.022 / 0.62 | n/a | noise |
| 10 | binds_return | −0.005 / 0.66 | −0.003 / 0.60 | n/a | noise |
| — | fan-in, fan-out, hub, SCC, betweenness, call-site flags, Substance/Output/Role axes, mask popcount, docstring, decorators, kind, same module/file, export | all ≤ noise in every panel | | | no signal |

**Why single-field scores mislead.** On T5, `fan_out` (0.82), `is_leaf` (0.31), PageRank (0.27), the
Output/Form/Role axes (0.67–0.73) and mask popcount (0.74) look strong alone. Upstream callers have
callees and downstream leaves don't, so these re-encode direction. Inside a direction × hop cell they fall
to 0.42–0.71 and add nothing. On the M4 graph with test code included, `is_test`, `symbol_role`,
`same_top_package` and the 4-axis bits showed +0.12 to +0.17. All of that vanished with test candidates
removed: those fields were test-code detectors, and T5 gold excludes tests.

**Caveats.** T5 gold was derived from PRISM's original graph. Fields tied to that graph's resolution, such
as `edge_tentative` (66 Design C rows reached via best-effort edges, 3 gold), cannot be judged fairly
against it. T2 gold is curated and complete within the M4 manifests. Within-seed AUCs average over 31 T5
and 69 T2 tasks.

**Does the LLM need the help?** M4 Turn-1 picks inside the manifest (mean over seeds 42–44):

| | LLM within-manifest AUC | engine ranking, same rows | gold picked | non-gold picked |
|---|---|---|---|---|
| T5 | **0.51** | 0.94 | 30.7 / 54 (57%) | 104.7 / 225 (47%) |
| T2 | 0.88 | 0.89 | 161 / 174 (93%; 98% with Turn-2 direct-callee union) | 160 / 592 (27%) |

T5 by role: `caller` rows 77% gold, LLM picks 57% of gold and 73% of non-gold callers. `callee` rows 1%
gold, LLM picks 65%. `transitive` rows 0% gold, LLM picks 28%. Every gold row the LLM never picked is
upstream; every non-gold row it never picked is downstream (direction AUC 1.00 on missed rows, 8 tasks).

## 6. Manifest schema experiment

| config | serializer change | LOC | prompt update | expected effect (offline evidence) |
|---|---|---|---|---|
| Baseline | today's manifest (Design C list for T5) | 0 | — | T5 Design C: 158/178 gold reachable; M4 LLM behaviour on T5 is at chance inside the manifest |
| A | `|hop=N` | ~6 | one clause explaining `hop` | T2: ≈0 (role already marks hop 1; LLM already prefers hop 2 over 3). T5: ≈0 (gold rate doesn't fall with hop inside Design C) |
| B | A + `|dir=up/down` | +2 | one clause | redundant with `role`; only emphasis. M4 shows the LLM ignoring `caller` |
| C | B + `|w=` and caller flags on every upstream row | +1 (hop 1) / ~10 (all hops) | one clause | ≈0: caller weight adds nothing beyond hop + direction |
| D | C + `|lines=N` + full multi-line signatures | +2 / ~6 | one clause | T5: the only measured extra signal (+0.04 to +0.08); full signatures restore params on 13–27% of fastapi/trpc rows. T2: ≈0 |

**Ceilings.** On M4 T5 manifests, "every `caller` row" gives recall 0.98 with 16 non-gold, against the
LLM's 0.57 with 105. Inside Design C manifests, "every upstream row" gives recall 1.00 at precision 0.57,
against 0.31 for selecting everything. On T2 no rule beats the LLM: "callee + hop-2 transitive" gives
0.96 recall but 318 non-gold picks (LLM: 0.93 / 160); "callee only" gives 0.80 / 89.

**Which config to run.** If one serializer config goes to Kaggle, run **Config D on T5 only**, documenting
the new fields in the system prompt. A–C add information the LLM already has or that doesn't predict gold.
T2 has no measurable headroom. Because the M4 evidence points at the instruction, not the data, add one
**prompt-only arm** for T5: Baseline manifest plus one task-type-aware Turn-1 sentence (for blast radius,
select the callers that consume the seed, not the seed's downstream chain). That is outside the
"serializer only" rule, so it is a separate arm. D versus prompt-only shows whether facts or framing is the
bottleneck. A deterministic no-LLM control ("all upstream rows") is free to score offline and anchors the
ceiling.

## 7. Fields ruled out

| field | why |
|---|---|
| Docstring presence | Within-stratum AUC 0.68–0.72 on T5 but increment −0.03 to −0.05: a proxy for body size, which is the better field. T2: 0.52. |
| Cyclomatic complexity | Same story: correlates with size, adds ≤ +0.046 (borderline at the noise floor), sign unstable across T2 panels. |
| Author / commit history | Not measurable: corpora are single-commit shallow clones. No mechanism linking authorship to call-graph relevance; needs git integration and history refetch. |
| Doc coverage | Informational, not a selection signal; same evidence as docstring presence. |
| 4-axis mask | Substance is `PURE_COMPUTE` on 87–97% of functions; the other axes proxy direction or test code. All increments ≤ noise once tests are removed. |
| Centrality (betweenness, PageRank), SCC, hub, fan-in | Within-stratum AUC ≤ 0.71, increments ≤ noise; index-time cost for no gain. |
| Call-site flags (loop / try / null-guard / call kind / argument flow) | ≤ noise in every panel; upstream callers calling inside a loop are 32% gold vs 42%, n=34. |
| `is_test` | Moot for T5 under Design C (tests excluded); otherwise visible in the module path. |
| Caller weight (`W_upstream`, `binds_return`) beyond hop 1 | Measured flat: `binds_return` callers 46% gold either way. Keep the existing hop-1 flags; don't extend. |

## 8. Integration with Design C

Independent: Design C decides **which** rows enter the manifest (`_upstream_walk` + `_interleave_within_budget`);
enrichment changes only **how each row is written** (the f-string at the end of `build_candidate_manifest`).
They touch disjoint code and can be switched separately.

Interactions to note:

* Design C labels every upstream row `caller` (hops 1–6) but gives `binds_return`/`nontrivial_args` only
  to direct callers. A `hop=` field is the natural way to say how far each caller is. Inside Design C,
  however, gold rate doesn't fall with upstream hop (51% / 69% / 57%), so this is presentation, not signal.
* 45% of Design C T5 rows (217 of 482) are downstream and never gold. The rows are admitted by design, as
  context for the callers. Whether the LLM is told to skip them is a prompt question (§6), not a schema one.
* Token cost: `|hop=N|lines=N` adds ~6 tokens per row, ~200 tokens on a mean Django Design C manifest
  (32 rows, ~2,240 tokens). Turn-1 prompt tokens are separate from the 13,000-token budget that Design C spends on hydratable bodies.
* `PRISM_MANIFEST_STRICT` filters rows by downstream distance and keeps upstream rows; unaffected.
