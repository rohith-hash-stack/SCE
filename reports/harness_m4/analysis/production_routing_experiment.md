# Production routing experiment: deciding direction from the query

Design and offline test only. No repository code was modified and nothing was run on Kaggle. Every
number is offline: the M4 bundles and queries, the current (fixed) PRISM graph, and scratchpad prototypes
of the classifier and routing (`chg/pr_intent_v0.py`, `pr_intent_v1.py`, `pr_heldout.json`,
`pr_routing.py`, `pr_routing_analyze.py`).

## Summary

* **The framing needs one correction.** PRISM's production surface is the MCP server (`prism_slice`,
  `prism_explain`). It receives a seed symbol and an optional structured `task_type` from the calling
  agent ("chain"/"blast"/"redundancy"/"architecture"/"debug"), and no natural-language query at all. It
  packs both directions in one pass with no Turn-1 LLM. Query-text routing is needed only on the two-pass
  path (`PrismEngine.retrieve_two_pass`, which takes `task_prompt`), the one that has the Turn-1
  direction blind spot. The design below targets that path and uses query text only. An explicit
  direction hint, where a caller has one, should override it.
* **Intent classifier.**
  * The a-priori heuristic (v0), written before reading any T5 query, scored **77/121 (64%)** on M4. It
    missed 28/32 T5 queries, which are almost all one template ("What breaks if `X` changes?"). That fails
    your 15% threshold.
  * The stronger heuristic (v1) uses two grammatical principles: impact = a conditional change, or the
    seed's dependents as the object; mechanism = the seed as the subject, or a trace/debug verb. It scores
    **121/121** on M4, but that is in-sample (designed after seeing v0's misses).
  * On a 36-query held-out set written before v1, it scores **33/36**: 15/15 impact, 14/15 localization,
    4/6 ambiguous. It never returns the opposite direction for a directional query. Its weakness is
    compound questions ("…and who uses it"), which it calls blast_radius instead of mixed.
* **Routing, offline test 5B — the slot split as specified fails the gate.**
  * R2 slot split (80/20 by intent over the Design C walk): T5 caller fraction moves only +2.3 pp at the
    13k budget. The split changes the list for only 2 of 32 T5 seeds (the Django hubs `reverse` and
    `QuerySet.get`, where the budget binds). For the other 30, every candidate already fits, so there is
    nothing to choose between.
  * Row caps make it bite (+9 pp at 20 rows, +16 pp at 12), but the 12-row cap costs gold reach
    (0.888 → 0.809).
  * On T2, R2 moves the wrong way (+26 pp more caller rows): applying the bidirectional walk to
    localization queries adds upstream rows that today's T2 manifest (≤3 direct callers) does not have.
* **R3 pool shaping passes the gate.**
  * blast_radius → full upstream walk + downstream hop 1 only;
  * localization → today's downstream manifest;
  * mixed/unknown → Design C.

  T5 caller fraction rises **+24.3 pp** (0.51 → 0.76) with gold reach unchanged (0.888). T2 is unchanged.
* **Test 5C: the rule beats the estimated LLM in every routing config.** Selecting every caller row
  delivers 0.888 of T5 gold. Estimates of the LLM, from M4's per-row pick rates, give 0.37–0.54. Routing
  shifts the estimated LLM's precision (0.34 → 0.40–0.48 under R3), not its recall. The M4 model picks
  only ~60% of the caller rows it is shown, and removing downstream rows cannot change that. Only a real
  run can show whether the model's per-row behaviour changes with composition.
* **Recommendation.**
  * Run the Kaggle ablation with **R3 routing** as the routed config, not the R2 slot split; R2 fails the
    gate.
  * Land the three production-safe fixes first (hop-count admission, full signatures, body_lines).
  * With those fixes on the main branch, "flag off" reproduces R1, not M4. M4 itself stays reproducible
    only from the M4 commit, and B0 uses the stored M4 cells.

---

## 1. Experiment scope

**What production PRISM receives today** (code-verified):

| entry point | inputs | direction signal | Turn-1 LLM? |
|---|---|---|---|
| MCP `prism_slice` / `prism_explain` (`src/prism/mcp/server.py:507`, `:572`) | `repo_path`, `seed_symbol`, `budget_tokens`, `format`, `d_max`, optional `task_type` ∈ {chain, blast, redundancy, architecture, debug} | **structured, from the calling agent** (optional) | no: one-pass knapsack with both downstream and upstream callers |
| CLI `prism causal-query` (`src/prism/cli.py`) | symbol, budget, `--task-type` | structured (optional) | no |
| `PrismEngine.retrieve_two_pass(seed, budget, request_symbols, task_prompt, task_type)` | seed + NL task prompt + LLM callback | none beyond the text | **yes**; this is the path the harness's Arm 5 uses |

So the framing "only the query text is available" is correct for the two-pass path, and that is where
the direction blind spot lives. On the MCP path the caller is itself an agent that already states intent
as an API parameter. That is a legitimate production signal, not a benchmark label.

**Other signals production could have** (not used here; listed for the design):
* an explicit direction hint in the tool call (the MCP `task_type`);
* editor state: the open file, cursor symbol, uncommitted diff (a pending change to a symbol implies
  impact intent);
* a failing test or stack trace (implies localization);
* the conversation's previous queries;
* the seed's own shape (a hub with hundreds of callers makes "what breaks" broad).

**Design for the minimal case.** Query text + seed only. Precedence, if the other signals are added
later: explicit hint > query classifier (confidence ≥ threshold) > balanced fallback.

## 2. Intent detection

**Design.** Pure regex heuristic, no model, no labels. It returns
`(intent ∈ {blast_radius, localization, mixed, unknown}, confidence, evidence)`.
* Each cue has a weight; the sums are B (impact) and L (mechanism).
* `unknown` if B + L < 2; otherwise blast_radius if B ≥ L + 2, localization if L ≥ B + 2, else mixed.
* Confidence = |B − L| / (B + L).

**v0 (a-priori, frozen before reading T5 queries; sha256 `947385de…`).** Phrase lists for impact ("blast
radius", "callers", "usages", "if we change…", "would break") and mechanism ("trace", "how does",
"pipeline", "debug", …).

| set | result |
|---|---|
| M4, 121 queries | **77/121 = 64%**: T5 4/32 (28 unknown), T2 73/89 (15 unknown, 1 mixed) |
| held-out, 36 queries | 15/36 |

That fails the 15% threshold. The misses were all "unknown", never the wrong direction.
* 28 T5 queries use one template, "What breaks if `X` changes?", which v0 didn't cover.
* 15 T2 queries are Express/FastAPI phrasings ("identify the real third-party package function it
  delegates … to", "Starting from `X`, identify what it does") whose cues v0 matched too literally.

**v1 (stronger heuristic).** Two grammatical principles rather than phrase lists:
* **impact**:
  * a conditional change to the seed ("what breaks", "breaks if", "if `X` changes", "if we
    change/rename/remove", "safe to rename", "deprecate", "affected", "impact");
  * or the seed's dependents as the object ("who/which functions … call/use/rely on/reach", "callers",
    "usages", "is `X` used", "relies on").
* **mechanism**:
  * the seed as the subject ("how does `X` …", "what does `X` do/call", "identify what it does",
    "starting from", "delegates");
  * or a trace/debug/locate verb ("trace", "walk through", "execution path", "why does", "find where",
    "which helper").

| set | result | note |
|---|---|---|
| M4, 121 queries | **121/121** | in-sample: designed after inspecting v0's misses |
| held-out, 36 queries (sha256 `6cde0963…`, written before v1) | **33/36 = 92%** | impact 15/15, localization 14/15, ambiguous 4/6 |

Held-out misses:
* "Explain the steps `build_index` performs" → unknown;
* "Explain `retry` and who uses it" → blast_radius (should be mixed);
* "How does `Encoder.encode` work and what depends on it?" → blast_radius (should be mixed).

No directional held-out query is classified in the opposite direction.

Confidence on M4: every T5 query 1.0; T2 median 1.0, minimum 0.33.

**Honest limits.**
* M4 contains essentially two T5 phrasings (28 template queries + 4 "blast radius" briefs). M4 accuracy
  therefore says little about real-world query diversity.
* The held-out set is small and written by the same author as v1.
* Compound queries are the known weak spot. For routing, mixed → Design C (both directions) is the safe
  fallback, so the v1 bias toward blast_radius on compound queries should be fixed by widening the mixed
  margin before production use.

Per-query results for all 121 M4 queries and the 36 held-out queries are in the appendix.

## 3. Routing policy

Two policy families were tested over the Design C walk (fixed graph, hop-count admission on, 13k budget):

| policy | blast_radius | localization | mixed | unknown |
|---|---|---|---|---|
| **R2 slot split** (as specified) | Design C walk, admitted rows track 80% upstream / 20% downstream | 20% / 80% | 50/50 | R1 behaviour |
| **R3 pool shaping** (proposed) | full upstream walk + downstream hop 1 only | today's downstream manifest (scope-filtered 3-hop downstream + ≤3 direct callers) | Design C (50/50) | Design C |

The slot split uses a deficit rule: each slot goes to whichever direction is furthest below its share.
At 0.5 this reproduces Design C's alternation; the reproduction check is in §5. Variants with row caps of
30, 20 and 12 test whether the split bites when slots are scarce.

*Does an intent-directed manifest produce the right direction in the LLM's selection without the model
being told?* That needs the model. Offline we can measure the composition the model would see (§5B), the
exact outcome of a deterministic selector, and a bracketed estimate of the M4 model's picks (§5C).

## 4. Experiment isolation

**Layout** (proposed, not created):

```
harness/experiments/production_routing/
  __init__.py
  intent.py        # v1 classifier: classify(query) -> (intent, confidence, evidence)
  routing.py       # R3 (and R2 for comparison): build the routed candidate list from PRISM's public pieces
  engine_proxy.py  # wraps a PrismEngine; build_candidate_manifest(seed) routes by the current query
  arm.py           # ProductionRoutingArm5(Arm5Prism): sets the query on the proxy, calls super().retrieve
tests/test_production_routing.py
```

* `arm5_prism.py` and `src/prism/` are untouched by the routing experiment.
* The wrapper arm forces `PRISM_BLAST_MODE` off, so the label-driven Design C branch never runs, and
  hands Arm 5 an engine proxy. The proxy's `build_candidate_manifest(seed)` builds the routed list from
  the classifier's intent on the query text. The router never sees `task_type`.
* It records `routing_intent`, `routing_confidence` and `routing_policy` in `build_meta`. Turn-1 prompt,
  parse, Turn-2 hydration and answer are unchanged.

**Flag.** `PRISM_PRODUCTION_ROUTING` in `harness/config.py`, env `HARNESS_PRISM_PRODUCTION_ROUTING`,
default off. When on, the harness builds `ProductionRoutingArm5` in place of `Arm5Prism`.

**Production-safe fixes, flag-independent, to land in the main branch** (need approval; these touch
`src/prism/`):
1. Hop-count admission: admit a downstream candidate within 3 structural hops, or within weighted
   distance 3.0. Offline, this restores T2 gold reach from 0.971 to 1.000 (the Express regression).
2. Full signatures in the manifest: header to body start, string literals collapsed, 128-token cap with
   a `sig_truncated` flag.
3. `lines=N` (body lines) in the manifest.

Your brief says "these four changes" but lists three; the routing flag is the fourth item and stays
under the experiment directory.

**Consequence for "M4 numbers reproduce when off".** With fixes 1–3 in the main branch, flag-off
reproduces R1, not M4: the manifest text and T2 Express reach both change. B0 should therefore use the
stored M4 cells (as planned) or a checkout of the M4 commit. A bit-exact "off = M4" would require putting
the fixes behind a flag too, which contradicts "applied regardless of the flag".

**Size.**

| part | production LOC | tests |
|---|---|---|
| experiment package | ~200 | 8 tests: classifier on the 36 held-out queries; routed lists for each intent on a synthetic graph; flag off = R1 identical; router never reads `task_type`; `build_meta` fields recorded |
| three fixes | ~85 | 6 tests |

## 5. Offline test (load-bearing)

### A. Intent classifier accuracy on the 121 M4 queries

| classifier | T5 → blast_radius | T2 → localization | overall | held-out (36) |
|---|---|---|---|---|
| v0 (a-priori) | 4/32 | 73/89 | 77/121 = 64% | 15/36 |
| v1 | 32/32 | 89/89 | 121/121 = 100% (in-sample) | 33/36 = 92% |

### B. Directional bias achieved by each routing config

Caller fraction = upstream (`caller`) rows ÷ all non-seed rows, pooled over tasks. Gold reach = gold in
the list ÷ all gold (seed excluded).

Checks on the reconstruction:
* R0 reproduces today's real Design C list exactly in 31/32 T5 tasks. The exception is `reverse`
  (709 callers), the seed where the budget binds hardest: Design C alternates by turn, my reconstruction
  by admitted count, so mine admits fewer upstream rows there (19 vs ~33). The routed numbers are slightly
  conservative for that one task.
* B0 uses the regenerated M4 manifests (M4 graph).

Estimated-LLM columns bracket the M4 model's behaviour:
* **(a)** every row is picked at M4's per-row rate for its direction (T5: caller 0.60, downstream 0.36;
  T2: caller 0.05, downstream 0.43);
* **(b)** the model picks a fixed-ish number of rows, k = a + b·rows (M4 fit: T5 3.05 + 0.123·rows,
  T2 0.80 + 0.30·rows), split between directions in the same proportions.

They are models of the model, not results.

**T5_blast_radius** (intent from the query text; routing by true label gives identical rows because v1 is 121/121)

| config | mean rows | caller fraction | shift vs R1 | gold reach | rule 'all callers': gold / precision | est. LLM (a) recall / precision | est. LLM (b) recall / precision |
|---|---|---|---|---|---|---|---|
| B0 (M4 manifest, M4 graph) | 10.5 | 0.205 | -30.7 pp | 0.303 | 0.298 / 0.768 | 0.182 / 0.233 | 0.215 / 0.285 |
| R0 (today's code) | 17.1 | 0.542 | +3.0 pp | 0.888 | 0.888 / 0.534 | 0.536 / 0.354 | 0.372 / 0.407 |
| R1 (+ hop-count admission) | 17.9 | 0.512 | +0.0 pp | 0.888 | 0.888 / 0.539 | 0.536 / 0.343 | 0.371 / 0.398 |
| R2 slot split, budget only | 18.3 | 0.535 | +2.3 pp | 0.888 | 0.888 / 0.503 | 0.536 / 0.33 | 0.369 / 0.392 |
| R2 slot split, cap 30 rows | 15.7 | 0.575 | +6.3 pp | 0.882 | 0.882 / 0.545 | 0.533 / 0.377 | 0.371 / 0.42 |
| R2 slot split, cap 20 rows | 13.5 | 0.604 | +9.2 pp | 0.876 | 0.876 / 0.598 | 0.529 / 0.429 | 0.377 / 0.452 |
| R2 slot split, cap 12 rows | 9.9 | 0.676 | +16.4 pp | 0.809 | 0.809 / 0.67 | 0.489 / 0.52 | 0.392 / 0.519 |
| R3 pool shaping | 13.7 | 0.755 | +24.3 pp | 0.888 | 0.888 / 0.479 | 0.536 / 0.401 | 0.404 / 0.481 |

**T2_localization** (intent from the query text; routing by true label gives identical rows because v1 is 121/121)

| config | mean rows | caller fraction | shift vs R1 | gold reach | rule 'all callers': gold / precision | est. LLM (a) recall / precision | est. LLM (b) recall / precision |
|---|---|---|---|---|---|---|---|
| B0 (M4 manifest, M4 graph) | 9.5 | 0.125 | +0.2 pp | 1.0 | 0.017 / 0.028 | 0.428 / 0.228 | 0.491 / 0.266 |
| R0 (today's code) | 8.7 | 0.138 | +1.5 pp | 0.971 | 0.017 / 0.028 | 0.415 / 0.245 | 0.489 / 0.284 |
| R1 (+ hop-count admission) | 9.8 | 0.123 | +0.0 pp | 1.0 | 0.017 / 0.028 | 0.428 / 0.221 | 0.489 / 0.259 |
| R2 slot split, budget only | 13.8 | 0.386 | +26.3 pp | 1.0 | 0.034 / 0.013 | 0.421 / 0.209 | 0.611 / 0.243 |
| R2 slot split, cap 30 rows | 12.1 | 0.363 | +24.0 pp | 1.0 | 0.034 / 0.015 | 0.421 / 0.231 | 0.595 / 0.264 |
| R2 slot split, cap 20 rows | 10.3 | 0.34 | +21.7 pp | 0.994 | 0.029 / 0.016 | 0.421 / 0.264 | 0.574 / 0.292 |
| R2 slot split, cap 12 rows | 7.9 | 0.274 | +15.1 pp | 0.966 | 0.023 / 0.021 | 0.41 / 0.31 | 0.531 / 0.333 |
| R3 pool shaping | 9.8 | 0.123 | +0.0 pp | 1.0 | 0.017 / 0.028 | 0.428 / 0.221 | 0.489 / 0.259 |

**Gate (> 20 pp shift in caller fraction).**

| policy | T5 shift | T2 shift | verdict |
|---|---|---|---|
| R2 slot split, budget only (as specified) | +2.3 pp | +26.3 pp, the wrong direction for localization | **fails**: not ready for Kaggle |
| R2 with row caps | +6 to +16 pp | +15 to +24 pp, wrong direction | fails, and the 12-row cap costs 8 points of T5 gold reach |
| **R3 pool shaping** | **+24.3 pp** (0.51 → 0.76), gold reach unchanged | 0 pp (unchanged by design) | **passes** |

Why the slot split doesn't bite:
* Design C's budget (13k tokens of hydratable bodies) binds for only 2 of 32 T5 seeds (`reverse`,
  `QuerySet.get`). Everywhere else every candidate already fits, so re-dividing the slots changes
  nothing.
* For localization, routing the bidirectional walk at 20% upstream still adds 2–4 caller rows to
  manifests that today carry at most 3 direct callers.

Direction bias has to come from what enters the pool (shaping), not from how a non-binding budget is
divided.

### C. Would rule-based caller selection beat the LLM under each routing config?

The rule ("request every `caller` row") is exact; it is the C0 control from the previous plan, now with
routing:

| T5 config | rule: gold delivered / precision | est. LLM (a) recall / precision | est. LLM (b) recall / precision |
|---|---|---|---|
| B0 | 0.298 / 0.768 | 0.182 / 0.233 | 0.215 / 0.285 |
| R1 | 0.888 / 0.539 | 0.536 / 0.343 | 0.371 / 0.398 |
| R3 | 0.888 / 0.479 | 0.536 / 0.401 | 0.404 / 0.481 |

(Full rows in table B.)
* **Yes, under every config.** The rule reaches the full 0.888 reach ceiling. Either LLM estimate stays
  at 0.37–0.54 recall.
* Routing raises the estimated LLM's precision (fewer downstream rows to waste picks on) but leaves recall
  where it is. The M4 model picks about 60% of caller rows on T5 regardless of how many downstream rows
  sit beside them.
* Composition alone can close the recall gap only if a shorter, caller-heavy manifest changes that
  per-row behaviour. Only a real run can show that.

The rule's precision under R3 (0.48) is slightly below R1's (0.54). On three Django hub seeds
(`reverse`, `QuerySet.get`, `Options.get_field`), dropping deep downstream rows frees budget for more
upstream callers (`reverse`: 19 → 52), none of them gold. The rule takes them all.

T2: R1 and R3 are identical by design. The hop-count admission fix restores T2 gold reach to 1.000
(R0: 0.971).

**Verdict of the offline test.** Intent detection works on M4 and on the held-out set, with the
compound-query caveat. The slot-split routing as specified does not change the manifest materially and
is not ready. Pool-shaping routing (R3) does change it (+24 pp) without losing gold, and is ready for the
Kaggle comparison. The offline data cannot say whether the LLM's recall follows the composition; the
estimates say precision improves and recall barely moves.

## 6. Kaggle ablation design (for approval; not run)

| config | what | source |
|---|---|---|
| B0 | M4 baseline | existing M4 cells (no rerun) |
| R1 | fixes only: hop-count admission, full signatures, body_lines; routing flag off | new run |
| R2 | R1 + production routing with **R3 pool shaping** + v1 intent from the query text | new run |

**Scope.** Arm 5 only (`HARNESS_ACTIVE_ARMS=arm5`), all four corpora, seeds 42–44, T2 + T5.
* Per config: T2 267 cells (fastapi 72, django 60, express 60, trpc 75) + T5 96 (fastapi 24, django 24,
  express 6, trpc 42) = **363**.
* Two new configs = **726 cells**.

**Wall time** (M4 Arm 5 end-to-end means; T2 21.8 s, T5 23.3 s; T5 cells under Design C/R3 estimated at
~28 s for the larger manifest and context):

| corpus | cells per config | estimate per config |
|---|---|---|
| django | 84 | ~40 min + 5 min index |
| fastapi | 96 | ~41 min |
| trpc | 117 | ~41 min |
| express | 66 | ~18 min |
| **total** | 363 | **~2.4 h** |

Two configs ≈ **4.8–5.5 h** including indexing and model setup: one session, run as two sequential
processes with separate `--out` directories and env flags.

**Report from the run:**
* T5: `tsr` (answer recall of gold), `uniform_cpi`, `context_recall`; the share of Turn-1 picks that are
  callers; R1 vs R2 and both vs B0.
* T2: the same metrics; R2 must hold R1 (non-inferiority). R3 leaves T2 manifests unchanged when intent
  is classified correctly, so any T2 change measures classifier error at runtime.
* Intent detection at runtime: `routing_intent` / `routing_confidence` from `build_meta`, against the
  labels, per corpus. Expected 121/121; any miss is logged with the query.
* The C0 rule numbers (offline, exact) as the T5 ceiling for comparison.

## 7. What this settles

The question: can PRISM decide direction from the query alone, and does that produce production-grade
behaviour?

| sub-question | answered by | status |
|---|---|---|
| Can direction be read from the query? | §5A | **largely yes** offline: 121/121 on M4 (in-sample), 33/36 held-out, no opposite-direction errors; compound queries are the weak spot |
| Does routing change what the model sees? | §5B | **yes for pool shaping** (+24 pp caller share, no gold lost); **no for the slot split** as specified (+2 pp) |
| Does the model's selection follow the composition, with no prompt change? | Kaggle R2 vs R1 (T5) | open; offline estimates predict higher precision and flat recall |
| Does T2 hold? | Kaggle R2 vs R1 (T2) | expected yes: R3 leaves T2 manifests unchanged when intent is right |

How to read the Kaggle outcome:
* **If R2 beats R1 on T5 `tsr` and holds T2:** PRISM routes direction from the query, with no label and no
  prompt change. The T5 story is configurational retrieval (Design C + fixes) plus intent-aware pool
  shaping.
* **If R2 ≈ R1 on T5:** the routing works (composition shifts, offline) but the model's ~60% per-caller
  pick rate is the bottleneck. Composition alone cannot overcome the Turn-1 direction blind spot; the
  remaining levers are the instruction (excluded here) or engine-side selection (the rule, 0.888).

Either outcome is a finding.

---

## Appendix — per-query classifier results

**M4 queries (121)**

| task | label | v0 (a-priori) | v1 intent | v1 confidence | v1 correct |
|---|---|---|---|---|---|
| django_t02_001_request_middleware_chain | localization | localization | localization | 1.0 | yes |
| django_t02_002_queryset_delete_cascade_pipeline | localization | localization | localization | 1.0 | yes |
| django_t02_003_form_clean_validation | localization | localization | localization | 1.0 | yes |
| django_t02_004_url_resolve_traversal | localization | localization | localization | 1.0 | yes |
| django_t02_005_model_save_signals | localization | localization | localization | 1.0 | yes |
| django_t02_006_auth_get_user_resolution | localization | localization | localization | 1.0 | yes |
| django_t02_007_locmem_cache_get_eviction | localization | localization | localization | 1.0 | yes |
| django_t02_008_db_session_load_decode | localization | localization | localization | 1.0 | yes |
| django_t02_009_queryset_filter_clone | localization | localization | localization | 1.0 | yes |
| django_t02_010_i18n_catalog_translation | localization | localization | localization | 1.0 | yes |
| django_t02_011_permissions_backend_check | localization | localization | localization | 1.0 | yes |
| django_t02_012_wsgi_entrypoint_dispatch | localization | localization | localization | 1.0 | yes |
| django_t02_013_common_middleware_slash_redirect | localization | localization | localization | 1.0 | yes |
| django_t02_014_send_mail_pipeline | localization | localization | localization | 1.0 | yes |
| django_t02_015_admin_each_context | localization | localization | localization | 1.0 | yes |
| django_t02_016_db_cursor_connection | localization | localization | localization | 1.0 | yes |
| django_t02_017_redirect_url_safety_check | localization | localization | localization | 1.0 | yes |
| django_t02_018_response_init_headers_cookies | localization | localization | localization | 1.0 | yes |
| django_t02_019_request_get_host_validation | localization | localization | localization | 1.0 | yes |
| django_t02_020_storage_generate_filename | localization | localization | localization | 1.0 | yes |
| django_t13_001_blast_reverse | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| django_t13_002_blast_queryset_get | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| django_t13_003_blast_field_clean | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| django_t13_004_blast_options_get_field | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| django_t5_001_form_clean_validation | blast_radius | unknown | blast_radius | 1.0 | yes |
| django_t5_002_send_mail_pipeline | blast_radius | unknown | blast_radius | 1.0 | yes |
| django_t5_003_redirect_url_safety_check | blast_radius | unknown | blast_radius | 1.0 | yes |
| django_t5_004_response_init_headers_cookies | blast_radius | unknown | blast_radius | 1.0 | yes |
| express_t02_001_app_bootstrap_defaults | localization | localization | localization | 1.0 | yes |
| express_t02_002_middleware_mounting | localization | localization | localization | 1.0 | yes |
| express_t02_003_routing_dispatch_loop | localization | localization | localization | 1.0 | yes |
| express_t02_004_error_middleware_propagation | localization | localization | localization | 0.455 | yes |
| express_t02_005_layer_path_matching | localization | localization | localization | 1.0 | yes |
| express_t02_006_response_json_send | localization | localization | localization | 1.0 | yes |
| express_t02_007_etag_external_dependency | localization | unknown | localization | 1.0 | yes |
| express_t02_008_view_rendering | localization | localization | localization | 1.0 | yes |
| express_t02_009_content_negotiation | localization | localization | localization | 0.529 | yes |
| express_t02_010_redirection | localization | localization | localization | 1.0 | yes |
| express_t02_011_file_transmission | localization | localization | localization | 1.0 | yes |
| express_t02_012_cookie_signing | localization | unknown | localization | 1.0 | yes |
| express_t02_013_param_registration | localization | localization | localization | 1.0 | yes |
| express_t02_014_top_level_dispatch | localization | unknown | localization | 1.0 | yes |
| express_t02_015_path_to_regexp_alias_mismatch | localization | unknown | localization | 1.0 | yes |
| express_t02_016_send_file_streaming | localization | unknown | localization | 1.0 | yes |
| express_t02_017_content_disposition | localization | unknown | localization | 1.0 | yes |
| express_t02_018_type_is_alias_mismatch | localization | mixed | localization | 0.333 | yes |
| express_t02_019_range_parser | localization | unknown | localization | 1.0 | yes |
| express_t02_020_query_string_parsing | localization | unknown | localization | 1.0 | yes |
| express_t5_001_routing_dispatch_loop | blast_radius | unknown | blast_radius | 1.0 | yes |
| express_t5_002_path_to_regexp_alias_mismatch | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t02_001_dependant_tree_construction | localization | localization | localization | 0.368 | yes |
| fastapi_t02_002_solve_dependencies_runtime_resolution | localization | localization | localization | 1.0 | yes |
| fastapi_t02_003_request_params_coercion | localization | localization | localization | 1.0 | yes |
| fastapi_t02_004_route_registration_pipeline | localization | localization | localization | 1.0 | yes |
| fastapi_t02_006_include_router_type_dispatch | localization | localization | localization | 1.0 | yes |
| fastapi_t02_007_add_api_route_default_resolution | localization | localization | localization | 1.0 | yes |
| fastapi_t02_008_add_api_websocket_route | localization | localization | localization | 1.0 | yes |
| fastapi_t02_009_get_openapi_schema_and_route_iteration | localization | localization | localization | 1.0 | yes |
| fastapi_t02_010_get_openapi_path_operation_metadata | localization | localization | localization | 1.0 | yes |
| fastapi_t02_011_get_fields_from_routes_recursion | localization | localization | localization | 1.0 | yes |
| fastapi_t02_012_openapi_security_definitions_serialization | localization | localization | localization | 1.0 | yes |
| fastapi_t02_013_swagger_ui_html_generation | localization | unknown | localization | 1.0 | yes |
| fastapi_t02_014_http_basic_auth_extraction | localization | localization | localization | 1.0 | yes |
| fastapi_t02_015_http_bearer_auth_extraction | localization | localization | localization | 1.0 | yes |
| fastapi_t02_016_oauth2_password_bearer_extraction | localization | localization | localization | 1.0 | yes |
| fastapi_t02_017_api_key_header_validation | localization | localization | localization | 1.0 | yes |
| fastapi_t02_018_application_setup_docs_registration | localization | unknown | localization | 1.0 | yes |
| fastapi_t02_019_orjson_response_render | localization | unknown | localization | 1.0 | yes |
| fastapi_t02_020_ujson_response_render | localization | unknown | localization | 1.0 | yes |
| fastapi_t02_021_openid_connect_extraction | localization | unknown | localization | 1.0 | yes |
| fastapi_t02_022_http_digest_auth_extraction | localization | localization | localization | 1.0 | yes |
| fastapi_t02_023_redoc_html_generation | localization | unknown | localization | 1.0 | yes |
| fastapi_t02_024_get_value_or_default_utility | localization | unknown | localization | 0.579 | yes |
| fastapi_t02_025_api_key_cookie_validation | localization | localization | localization | 1.0 | yes |
| fastapi_t5_001_dependant_tree_construction | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_002_request_params_coercion | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_003_add_api_route_default_resolution | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_004_add_api_websocket_route | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_005_get_openapi_path_operation_metadata | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_006_get_fields_from_routes_recursion | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_007_openapi_security_definitions_serialization | blast_radius | unknown | blast_radius | 1.0 | yes |
| fastapi_t5_008_get_value_or_default_utility | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t02_001_init_bootstrap | localization | localization | localization | 1.0 | yes |
| trpc_t02_002_error_shape_http_status | localization | localization | localization | 1.0 | yes |
| trpc_t02_003_procedure_caller_error_normalization | localization | localization | localization | 1.0 | yes |
| trpc_t02_004_merge_routers | localization | localization | localization | 1.0 | yes |
| trpc_t02_005_router_caller_dispatch | localization | localization | localization | 1.0 | yes |
| trpc_t02_006_router_factory_path_registration | localization | localization | localization | 1.0 | yes |
| trpc_t02_007_procedure_builder_new_builder | localization | localization | localization | 1.0 | yes |
| trpc_t02_008_procedure_builder_input_validation | localization | localization | localization | 1.0 | yes |
| trpc_t02_009_procedure_builder_output_validation | localization | localization | localization | 1.0 | yes |
| trpc_t02_010_procedure_builder_use_middleware | localization | localization | localization | 1.0 | yes |
| trpc_t02_011_input_middleware_error_handling | localization | localization | localization | 1.0 | yes |
| trpc_t02_012_output_middleware_error_handling | localization | localization | localization | 1.0 | yes |
| trpc_t02_013_http_response_context_pipeline | localization | localization | localization | 1.0 | yes |
| trpc_t02_014_http_response_status_code | localization | localization | localization | 1.0 | yes |
| trpc_t02_015_input_to_procedure_call | localization | localization | localization | 1.0 | yes |
| trpc_t02_016_caught_error_to_data | localization | localization | localization | 1.0 | yes |
| trpc_t02_017_shared_error_shape | localization | localization | localization | 1.0 | yes |
| trpc_t02_018_trpc_error_cause_normalization | localization | localization | localization | 1.0 | yes |
| trpc_t02_019_procedure_builder_query_resolution | localization | localization | localization | 1.0 | yes |
| trpc_t02_020_procedure_resolver_finalization | localization | localization | localization | 1.0 | yes |
| trpc_t02_021_response_serialization | localization | localization | localization | 1.0 | yes |
| trpc_t02_022_deprecated_router_migration | localization | localization | localization | 1.0 | yes |
| trpc_t02_023_deprecated_procedure_migration | localization | localization | localization | 1.0 | yes |
| trpc_t02_024_subscription_pull_factory | localization | localization | localization | 1.0 | yes |
| trpc_t02_025_client_message_validation | localization | localization | localization | 1.0 | yes |
| trpc_t5_001_procedure_caller_error_normalization | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_002_router_factory_path_registration | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_003_procedure_builder_new_builder | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_004_input_middleware_error_handling | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_005_output_middleware_error_handling | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_006_http_response_context_pipeline | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_007_http_response_status_code | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_008_input_to_procedure_call | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_009_caught_error_to_data | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_010_shared_error_shape | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_011_procedure_resolver_finalization | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_012_response_serialization | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_013_deprecated_procedure_migration | blast_radius | unknown | blast_radius | 1.0 | yes |
| trpc_t5_014_client_message_validation | blast_radius | unknown | blast_radius | 1.0 | yes |

**Held-out set (36 queries, written before v1)**

| query | expected | v0 | v1 | v1 confidence | v1 correct |
|---|---|---|---|---|---|
| Which parts of the codebase depend on `parse_config`? | blast_radius | unknown | blast_radius | 1.0 | yes |
| If I change the return type of `get_user`, what needs updating? | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| Find all callers of `send_email` that use its return value. | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| What is the impact of removing the `timeout` parameter from `fetch`? | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| Where is `normalize_path` used? | blast_radius | unknown | blast_radius | 1.0 | yes |
| Is it safe to rename `TokenStore.refresh`? What would be affected? | blast_radius | unknown | blast_radius | 1.0 | yes |
| List everything that would need to change if `Serializer.dump` started raising instead of returning None. | blast_radius | unknown | blast_radius | 1.0 | yes |
| Who relies on `Cache.get` returning None for a miss? | blast_radius | unknown | blast_radius | 1.0 | yes |
| Before I deprecate `old_login`, show me its usages. | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| What code paths reach `charge_card`? | blast_radius | unknown | blast_radius | 1.0 | yes |
| If `compute_tax` changes its rounding, which reports are affected? | blast_radius | unknown | blast_radius | 1.0 | yes |
| Show me the consumers of `on_user_created`. | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| How widely is `utils.retry` used across the project? | blast_radius | unknown | blast_radius | 1.0 | yes |
| What will break when we change the signature of `Router.add_route`? | blast_radius | blast_radius | blast_radius | 1.0 | yes |
| Which functions call `validate_token` directly or indirectly? | blast_radius | unknown | blast_radius | 1.0 | yes |
| How does `Router.dispatch` pick the handler for a request? | localization | localization | localization | 1.0 | yes |
| Walk me through what happens when `Session.commit` is called. | localization | localization | localization | 1.0 | yes |
| Where does `render_template` load the template from? | localization | unknown | localization | 1.0 | yes |
| Why does `parse_date` return None for ISO strings with a Z suffix? | localization | unknown | localization | 1.0 | yes |
| Trace the request from `app.handle` to the response being written. | localization | localization | localization | 1.0 | yes |
| What does `compress` do with the input buffer? | localization | unknown | localization | 1.0 | yes |
| Explain the steps `build_index` performs. | localization | unknown | unknown | 0.0 | NO |
| Which helper does `upload_file` use to compute the checksum? | localization | unknown | localization | 1.0 | yes |
| Debug: `login` raises KeyError on missing headers; find the source. | localization | localization | localization | 1.0 | yes |
| How is the password hashed inside `create_user`? | localization | unknown | localization | 1.0 | yes |
| What third-party library does `to_json` delegate serialization to? | localization | unknown | localization | 1.0 | yes |
| Find where `retry` decides to give up. | localization | unknown | localization | 1.0 | yes |
| Show the call chain from `main` into the database layer. | localization | unknown | localization | 1.0 | yes |
| What does `Scheduler.tick` call on each iteration? | localization | unknown | localization | 1.0 | yes |
| How does `Config.load` resolve environment overrides? | localization | localization | localization | 1.0 | yes |
| Tell me about `Cache`. | ambiguous | unknown | unknown | 0.0 | yes |
| Refactor `parse_args` to use dataclasses; what do I need to know? | ambiguous | unknown | unknown | 0.0 | yes |
| Explain `retry` and who uses it. | ambiguous | blast_radius | blast_radius | 1.0 | NO |
| `process_order` is slow. | ambiguous | unknown | unknown | 0.0 | yes |
| Review `auth.py`. | ambiguous | unknown | unknown | 0.0 | yes |
| How does `Encoder.encode` work and what depends on it? | ambiguous | blast_radius | blast_radius | 0.333 | NO |
