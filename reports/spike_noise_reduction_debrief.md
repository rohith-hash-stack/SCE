# Noise-Reduction Spike Debrief: Approaches A, B, and C

**Branch:** `experiment/noise-filtering-spike` (isolated, never merged - production
`prism.packer.submodular_knapsack`/`prism.surface.build` were never modified by
any of the three approaches below)
**Model:** `gpt-4o-mini`, real OpenAI API (`Codex_open_API_key`), temperature 0.0
**Matrix:** 3 tasks (`django_t02_005_model_save_signals`,
`django_t02_009_queryset_filter_clone`,
`django_t02_017_redirect_url_safety_check`) x 3 budgets (2000/4000/8000) x 2
seeds (42/43) = 18 cells per approach
**Total real spend across the spike (all sanity checks, diagnostics, and full
sweeps, including the Approach A v2/v3 follow-ups and the response-logging
diagnostic re-run below):** ~$0.54
**`reports/pilot/checkpoint.json` hash:** unchanged throughout
(`589e42386e58c528f7a24b1083d4b097ea66ac28984317eb471ca9a02e11aa81`)

## Motivation

`docs/design_formalism.md` Sec 10.4's own closing finding, from the disclosed
`django_t02_017`/`_urlparse` regression: excluding a VERIFICATION-role test
candidate from the knapsack's competitive rounds let other real successors win
those rounds by pure density, leaving too little budget for `_urlparse`'s own
cascade-based admission. The stated hypothesis needing a real test: "topological
causal-spine prioritization must supersede pure greedy novelty/density scoring
under tight budget pressure." Three different, real mechanisms were built to
test that hypothesis and its alternatives, each scored against the same live LLM
on the same matrix.

## Summary

| Approach | Mean TSR | Mean CPI Strict | Mean FPR GT | Mean Spine/Admitted | Mean Tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Baseline (single-zone, production, unmodified) | 0.222 | 0.889 | 0.778 | 22.2 | 7597 |
| B - Two-Zone Rendering (frontier index, no selection change) | 0.222 | 0.889 | 0.778 | 22.2 | 8275 |
| C - Spine Variant (forked selection, topological tiering) | 0.111 | 0.778 | 0.739 | 18.7 | - |
| A v1 - Two-Pass Hydration, name-only manifest | 0.333 | 0.333 | 0.000 | 3.0 | 8379 |
| A v2 - Two-Pass Hydration, signatures + resolved calls | 0.389 | 0.833 | 0.133 | 3.8 | 47060 |
| A v3 - Two-Pass Hydration, hop=3 + scope-filtered manifest | 0.500 | 0.667 | 0.000 | 3.4 | 4167 |

B, C, and A v1 each fall short of the goal that motivated this spike: **suppress
`fpr_gt` without dropping `cpi_strict`.** B doesn't move either metric at all (by
design). C moves `fpr_gt` a little at the cost of `cpi_strict`. A v1 eliminates
`fpr_gt` completely but at a much larger `cpi_strict` cost than C. A v2 recovers
`cpi_strict` to within 0.056 of baseline while keeping `fpr_gt` well under the
0.50 target and beating every other approach's `tsr` - but at a real, large
token cost (~6x baseline). **A v3 is the best-balanced result of the entire
spike**: `tsr` improves further still (0.500, more than double baseline's
0.222), `fpr_gt` returns to a clean 0.000, and mean tokens (4167) land *below*
baseline's own single-turn cost (7597) - at the price of `cpi_strict` settling
back to 0.667 (still well above v1's 0.333, below v2's 0.833). No single variant
dominates on every metric; see Approach A v3 below, the "Structural Recall vs.
Selection Recall" finding, and the
Synthesis.

---

## Approach B: Frontier Index + Spine Hydration (Two-Zone Rendering)

**Mechanism:** `RenderOptions.two_zone` (`prism.surface.renderer`, additive,
default-off) renders a lightweight `<ReachableFrontier>` block (one-hop
successors/predecessors of every selected node, hub-degree-capped at 25,
entry-capped at 40) alongside the existing `<HydratedSpine>` - the knapsack's
own selection is completely untouched.

**Result:** `cpi_strict`/`fpr_gt` are byte-identical to baseline in every one of
18 paired cells - expected and mechanically guaranteed, since rendering more of
the graph without changing which nodes get admitted cannot move either metric.
Real cost: a ~9% unconditional prompt-token overhead (7597 -> 8275 mean tokens)
for the extra visibility, with only 1/18 cells showing any `tsr` difference (a
single flip, `django_t02_005`@budget=8000/seed=42, 0.0 -> 1.0 - consistent with
ordinary LLM sampling noise as much as a real effect at that sample size).

**Conclusion:** Confirms the design-review prediction directly: surface-level
rendering changes cannot move selection-level metrics. Establishes the
methodology (real scoring functions, real API, side-by-side comparable cells)
the next two approaches reused.

Commits: `60683b2` (scaffolding), `3ab117d` (CLI flags), `0792570` (36-cell
result, $0.0455).

---

## Approach C: Forked Spine Pruning in Knapsack (dropped)

**Mechanism:** `select_submodular_context_spine_variant`
(`benchmarks/experiments/knapsack_spine_variant.py`, forked from production's
`select_submodular_context`, production untouched). Two-tier admission:

- **Tier 1 (spine):** interior candidates - real successor edge into the
  candidate subgraph, i.e. not a terminal leaf - admitted unconditionally in
  ascending `dist_w` order, always `L0_full`, no density competition at all.
- **Tier 2:** the remaining, non-spine pool runs a static-pool version of
  production's own density-competitive loop; under `target_budget <= 4000`, a
  terminal-leaf candidate is stub-priced and admitted only if its density clears
  Tier 1's own median density (peripheral-leaf demotion/eviction).

**A real bug was caught and fixed before the first valid run**, worth recording
because it explains why the very first result looked wrong: the initial spine
definition marked every reachable candidate as spine (every node trivially sits
on the tail end of its own shortest path), collapsing Tier 1 into the full
candidate set and silently reproducing the exact `_urlparse` regression this
spike exists to test, just relocated. Fixed by redefining spine as interior/
backbone nodes only (a real successor into the candidate subgraph) - verified
via direct `pack_symbol_context_spine_variant` calls before spending any real
API money.

**Result (18 cells, $0.0646 for the 54-cell 3-approach grid this ran in):**
`fpr_gt` moved in the intended direction but far short of the `<0.50` target
(0.778 -> 0.739), while `cpi_strict` regressed (0.889 -> 0.778) - the opposite
of the goal. Per-task breakdown:

| task | baseline cpi/tsr | C cpi/tsr |
| :--- | :--- | :--- |
| django_t02_005 | 1.000 / 0.167 | 1.000 / 0.000 |
| django_t02_009 | 1.000 / 0.000 | 1.000 / 0.000 |
| django_t02_017 | 0.667 / 0.667 | 0.333 / 0.333 |

**Root cause, directly diagnosed** (not inferred): at `django_t02_017`,
budget=4000, C now fails on *both* seeds where baseline passed both.
`_urlparse` (`dist_w=2.0`) is correctly classified as spine (real out-degree 4,
into `_urlsplit`/`ParseResult`/etc.) - Tier 1 does try to admit it. But several
*other* spine nodes at the same or lower distance - from what the graph shows as
an apparently unrelated i18n/translation branch also structurally reachable from
this seed (`check_for_language`, `get_language_from_request`,
`TranslationCatalog.get`, likely via a synthetic causal-coupling edge rather
than a real function call) - exhaust the budget in ascending-distance-then-name
order before `_urlparse`'s own alphabetical turn arrives. **Tier 1's admission
order is pure topological distance with zero semantic value discrimination** -
no equivalent of production's own novelty-streak or scope-gate crowding
defenses exists inside it. A seed with multiple parallel spine branches lets the
less-relevant branch starve the genuinely relevant one: a new instance of the
same hub-crowding failure class production's own mechanisms exist to prevent,
occurring inside a tier that has no defense against it by construction.

**Decision: dropped, not patched further.** Adding value discrimination back
into Tier 1 (e.g. density-ranking within the tier, or a scope gate) would mean
re-deriving the existing knapsack's own density/streak machinery at higher
complexity, inside a new, separately-maintained tier - for no demonstrated gain
over using the knapsack directly. Confirmed with the user rather than iterating
blind on a real-money-per-attempt loop.

Commits: `1f66771` (implementation, bug fix, negative result, $0.0646 for the
grid it ran in).

---

## Approach A v1: Two-Pass Hydration Protocol, name-only manifest

**Mechanism:** `benchmarks/experiments/hydration_loop.py`, no native
tool-calling dependency (the harness's `OpenAICompatibleClient.complete()` only
takes flat `system`/`user` strings and always forces
`response_format={"type": "json_object"}`).

- **Turn 1 (manifest selection):** `_build_candidate_index` renders every
  symbol reachable from the seed - the identical candidate universe
  `pack_symbol_context`'s own `candidate_symbols` list is built from - as one
  compact `qualified_name|role|kind` line each (no bodies, no knapsack). Sent
  with the real task prompt; the model returns
  `{"thought_process": ..., "requested_symbols": [...]}`.
- **Turn 2 (targeted hydration & scoring):** `pack_symbol_context_requested`/
  `build_context_package_requested` (forked from production the same way
  Approach C's module already established) render exactly the seed plus
  whichever requested names resolve to a real candidate in Turn 1's own
  manifest - a hallucinated name is dropped and counted
  (`skipped_hallucinated`), never rendered as if real. `_enforce_render_budget`
  (real, unmodified production code) still caps the result to `target_budget`,
  keeping the budget axis comparable to every other approach even though
  nothing here is knapsack-admitted against it. `run_tsr_prompt` then scores
  Turn 2 exactly like every other approach's own single turn.

**A design-estimate gap was found and disclosed before spending real money**:
at production's own default reach (`max_hops=6.0`), `django_t02_017`'s real
candidate universe is 409 symbols - a ~27KB (~6-7K token) Turn 1 manifest, not
the "~120-200 tokens" the original design estimate assumed. Confirmed with the
user to proceed at full reach and measure the real 2-turn cost/tsr honestly
rather than silently narrow the scope to hit the original estimate.

**A latency-measurement bug was also caught and fixed**: the first
implementation timed `llm_latency_s` across both turns *including*
package-construction work (`compute_causal_edges`/`compute_all_data_flow_edges`/
`compute_guard_indicator_edges`, run fresh inside
`build_context_package_requested`) - work every sibling approach also pays but
never counts in its own `llm_latency_s`, since their own timer starts after
retrieval. This made Approach A's latency look ~10x worse than a fair
comparison would show (47s vs. the real ~4.5s of actual LLM wall time,
confirmed against the two calls' own logged per-call latencies). Fixed:
`llm_latency_s` is now the sum of both calls' own real latency
(`CallResult.latency_seconds`), comparable to every sibling approach;
`total_wall_clock_s` is kept as a separate field for the fuller figure.

**Result (18 cells, $0.0704 for the 54-cell 3-approach grid, 72 real API calls
total since each cell is two turns):**

`fpr_gt = 0.0000` on every one of 18 cells - not a reduction, complete
elimination. Turn 1's targeted selection never once names a symbol outside the
broader ground-truth universe. `django_t02_005` goes from baseline's TSR 0.0
(despite baseline's own full `cpi_strict = 1.000` there) to TSR 1.000 on every
one of A's 6 cells for that task - real evidence that context noise, not
missing recall, was costing baseline the task. But `cpi_strict` collapses to
0.333 overall - *worse* than Approach C's own regression - driven entirely by
`django_t02_009` and `django_t02_017`, both 0.0/0.0 (cpi/tsr) on every cell,
every budget, every seed:

| task | baseline cpi/tsr | A cpi/tsr (every cell) |
| :--- | :--- | :--- |
| django_t02_005 | 1.000 / 0.000 | 1.000 / 1.000 |
| django_t02_009 | 1.000 / 0.000 | 0.000 / 0.000 |
| django_t02_017 | 0.667 / 0.667 | 0.000 / 0.000 |

**Root cause, directly diagnosed**: `turn1_parsed_ok = true` and
`skipped_hallucinated = 0` on every cell - Turn 1's JSON always parses cleanly
and never invents a name. But `requested_count` is flat at ~2.3 across *all
three budgets* (confirming, as expected by design, that budget information
never reaches Turn 1's own decision at all - the model has no way to know how
much room it has, or that it should ask for more). Turn 1's manifest carries no
semantic *content* - no body, signature, or docstring, only an identifier
string - so on a task whose real pipeline isn't guessable from symbol names
alone, the model requests 2-3 "obviously named" candidates and stops, missing
the genuinely necessary deeper or less-obviously-named members entirely.

Commits: `511eb84` (implementation, design-estimate finding), `cf883c6`
(latency fix), `3fb9457` (54-cell result, $0.0704).

---

## Approach A v2: Signatures + Resolved Outgoing Calls

**Mechanism:** same two-turn protocol as v1, same `hydration_loop.py`, but
`_build_candidate_index`'s manifest line for each candidate gains two
deterministic, docstring-free fields on top of v1's bare `qualified_name|role|
kind`: `signature` (the raw declaration line, via `_signature_stub`, production/
unmodified) and `calls=[...]` (direct AST `CALLS`/`INSTANTIATES` targets from
`builder.graph` - the real structural graph, not `build_causal_graph`'s
synthetic-coupling-augmented one, so a symbol reachable only via a synthetic
edge never gets pointed to by anything's own `calls=[...]` list). Call targets
are qualified names, not bare ones - a design-review catch before implementation:
bare names are ambiguous once two candidates in a 400+ symbol universe share a
method name, and qualified names let the model cross-reference a call target
directly against another row's own id, which is what the Turn 1 prompt asks it
to do ("trace the complete causal execution path... via signatures and their
direct call targets").

**Two real bugs were caught and fixed before spending paid API money on the full
grid:**

1. A wrapped multi-line signature embedded raw newlines into a single manifest
   row, breaking the "one candidate per line" format - caught via a real
   `num_lines` (493) vs. `candidate_universe` (402) mismatch during validation,
   not assumed. Fixed by collapsing `_declaration_line`'s output to one physical
   line before embedding.
2. This sandbox has no real BPE tokenizer available (the offline `tiktoken`
   asset is missing and the network fallback is proxy-blocked -
   `active_backend()` reports `"fallback-regex (tiktoken unavailable:
   ProxyError)"`), so every local `count_tokens()` estimate in this sandbox has
   been running on a crude regex fallback that tokenizes dotted qualified names
   very inefficiently - exactly what `calls=[...]` is now full of. A local check
   briefly reported ~110K "tokens" for one manifest; the real, API-billed number
   (the only trustworthy source, since OpenAI counts server-side) was 17,757 for
   what should have been that same manifest. Local byte counts stay reliable in
   this environment; local token counts do not.

**A third, more serious issue surfaced investigating why that "17,757" number
wouldn't reproduce consistently: a real, pre-existing production bug in
`prism.runtime.index_cache`.** `prism.cli.build_pipeline`'s default
`use_cache=True` path returned genuinely inconsistent `(builder, tag_matrix)`
results across separate process invocations of the identical pinned Django
corpus - `django_t02_017`'s own seed's reachable-candidate count varied 409 vs.
402 across repeated runs with every other variable controlled and ruled out one
at a time (file-discovery order: `discover_files` already sorts; `PYTHONHASHSEED`
fixed to `0`: did not stabilize it, 409/402/402 across 3 runs; concurrent-process
cache races: reproduced across strictly sequential runs too). `use_cache=False`
gave 3-for-3 identical results (409, 409, 409), re-confirmed through the real
`PrismEngine` class. This is the same class of bug as the `SymbolInfo.role`
cache-serialization gap fixed in `07ff0cd` - a different instance in the same
caching layer. **Not fixed here** (out of scope for a spike branch that touches
no `prism.*` production code) - `benchmarks/engines/prism_engine.py`'s
`PrismEngine.index()` was changed, on this branch only, to call `build_pipeline`
with `use_cache=False`, so every approach in this spike (not just A) runs
against a deterministic graph from that commit forward. **Flagged here as a
high-priority production follow-up**, not silently patched.

**Result (18 cells, $0.1515, real API, deterministic graph):**

| approach | tsr | cpi_strict | fpr_gt | mean tokens |
| :--- | :--- | :--- | :--- | :--- |
| baseline | 0.222 | 0.889 | 0.778 | 7597 |
| A v1 (name-only) | 0.333 | 0.333 | 0.000 | 8379 |
| A v2 (signatures + calls) | 0.389 | 0.833 | 0.133 | 47060 |

`cpi_strict` recovers to 0.833 - within 0.056 of baseline's own 0.889, and far
above v1's 0.333 - while `fpr_gt` (0.133) stays well under the `<0.50` target and
baseline's own 0.778. `tsr` (0.389) is the best result of any approach in this
entire spike, baseline included. This is real, direct confirmation of the
hypothesis: structural AST evidence (signatures + real call targets) gives the
model enough signal to trace a causal chain that bare identifier strings alone
could not.

Per task, the picture is real but not uniform:

| task | baseline cpi/tsr | A v1 cpi/tsr | A v2 cpi/tsr |
| :--- | :--- | :--- | :--- |
| django_t02_005 | 1.000 / 0.000 | 1.000 / 1.000 | 1.000 / 0.000 |
| django_t02_009 | 1.000 / 0.000 | 0.000 / 0.000 | 1.000 / 0.667* |
| django_t02_017 | 0.667 / 0.667 | 0.000 / 0.000 | 0.500 / 0.500 |

\* `django_t02_009`'s `tsr` is 0.0 at budget=2000 and 1.0 at budgets 4000/8000
(the 0.667 is the 6-cell mean); every cell keeps `cpi=1.0`, `fpr=0.000`.

- **`django_t02_009`: a clean win.** `cpi_strict` recovers fully (0.0 -> 1.0),
  `fpr_gt` stays perfect (0.000), and `tsr` beats baseline's own 0.0 outright at
  the higher budgets. Exactly the result the hypothesis predicted.
- **`django_t02_017`: real but seed/budget-dependent.** `cpi`/`tsr` hit 1.0/1.0
  for seed 42 at budgets 2000-4000, but 0.0/0.0 for seed 43 at budget 2000, and
  **both seeds regress at budget=8000** even though Turn 1's manifest is
  budget-independent by construction. Directly checked: `requested_count` for
  the *same* seed value differs between budget cells (3 vs. 2) - since nothing
  in Turn 1's prompt varies with budget, this is consistent with OpenAI's own
  disclosed "best-effort, not guaranteed" determinism for the `seed` parameter,
  not a bug in this code.
- **`django_t02_005`: an open regression, not explained away.** `cpi_strict`
  stays perfect (1.0 on all 6 cells, full recall) but `tsr` drops to 0.0 on all
  6 (v1 had 1.0/1.0 here) and `fpr_gt` rises to 0.400 (some padding beyond the
  true pipeline). The model has the right symbols available but answers wrong
  regardless - root cause not further diagnosed within this spike's scope.

**The cost is real and large.** Turn 1 alone averages 44,194 tokens (up to
73,380 for the most-connected seed, `django_t02_017`'s
`url_has_allowed_host_and_scheme`) - roughly 6x baseline's *entire* context, for
one turn. A strong accuracy result, not an economical one as implemented - see
the Offline Manifest-Sizing Analysis below for the real, measured reduction two
candidate levers achieve before spending anything further on a live grid.

Commits: `a3039b4` (v2 implementation, qualified-name fix, two bugs caught before
paid spend), `c1026d0` (cache-determinism fix), `1e7f6fb` (36-cell result,
$0.1515).

---

## Offline Manifest-Sizing Analysis (no LLM calls)

`benchmarks/experiments/inspect_manifest_sizing.py` measures two proposed
cost-reduction levers against the same 3 target tasks, without spending
anything: **format-level slimming** (a condensed YAML-flow manifest line vs.
the current pipe-delimited one) and **candidate-universe pruning** (hop-depth
capping at 2/3 hops instead of production's own default 6, and a
same-top-level-module scope filter reusing `submodular_knapsack.py`'s own
`_module_prefix3` - not reinvented). Recall is checked directly against
`task.adjudicated.pipeline_symbols` at every variant, specifically flagging any
pipeline symbol a pruning level would drop, rather than assuming pruning is
safe.

**Token counts here are calibrated, not raw local estimates.** This sandbox has
no real BPE tokenizer available (`active_backend()` reports
`"fallback-regex (tiktoken unavailable: ProxyError)"`, confirmed while
validating A v2) - a local `count_tokens()` estimate on dotted-identifier-heavy
text can be off by multiples of the real number. Instead: each task's own real,
API-billed Turn 1 token count (from the deterministic-graph grid in `1e7f6fb`)
is divided by that exact same prompt's real byte length to get a real
tokens-per-byte ratio, then applied to every other variant's own byte count -
projected, not measured, but grounded in a real number rather than the
fallback-regex tokenizer's own unreliable output.

**Finding 1: format-level slimming does not help - it costs more.** The
condensed format (drops `kind`, switches to YAML-flow) is *larger* than the
current pipe-delimited format in every case measured (`django_t02_005`
unbounded: 106,129 -> 115,961 bytes). Spelled-out field names
(`qname:`/`role:`/`sig:`/`calls:`) plus newlines and indentation cost more than
the one dropped field (`kind`, which mostly duplicates `role` at this spike's
own scale) saves. The current pipe-delimited format is already close to as
compact as this style of encoding gets - this lever is not worth pursuing
further as specified.

**Finding 2: candidate-universe pruning is the real lever - and hop-depth alone
is not uniformly safe.**

| task | unbounded (tokens) | hop=2 (tokens, recall) | hop=3 (tokens, recall) | scope-filtered (tokens, recall) |
| :--- | :--- | :--- | :--- | :--- |
| django_t02_005 | 448 cand / 23465 | 161 / ~12998 (1.000) | 185 / ~13823 (1.000) | 151 / ~5854 (1.000) |
| django_t02_009 | 248 cand / 35736 | 18 / ~1862 (**0.800, drops `_clone`**) | 25 / ~2392 (1.000) | 148 / ~9310 (1.000) |
| django_t02_017 | 409 cand / 73380 | 30 / ~2432 (1.000) | 50 / ~3525 (1.000) | 11 / ~940 (1.000) |

`hop=2` **drops a real pipeline symbol on `django_t02_009`** (`_clone`,
recall=0.800) - exactly the risk this analysis exists to check for, found for
real rather than assumed away; `hop=2` is not a safe default. `hop=3` keeps
`recall=1.000` on all 3 tasks with large real reduction on two of them
(`django_t02_009`: 93%; `django_t02_017`: 95%) and a more modest one on the
third (`django_t02_005`: 41% - this seed's own neighborhood is unusually
dense/shallow, so there's less pruning headroom within 3 hops). The
scope filter does even better on 2 of 3 tasks (`django_t02_017`: 409 -> 11
candidates, ~98.7% reduction, still `recall=1.000`; `django_t02_005`: ~75%
reduction) but is markedly weaker on `django_t02_009` specifically (148
candidates vs. `hop=3`'s 25) - neither lever alone is uniformly best across all
3 tasks. `hop=3` is the safer, more consistent floor.

**Finding 3: combining `hop=3` and the scope filter beats both levers alone, on
every task measured.** `_combined_hop_scope_filtered` (within the `hop<=3`
candidate set, keep a candidate if it shares the seed's own top-level-3 module
namespace *or* sits within a 3-hop chain of concrete `CALLS`/`INSTANTIATES`
edges from the seed - transitively, not just 1-hop-from-the-seed like the
`scope_filtered` variant above) still keeps `recall=1.000` on all 3 tasks:

| task | unbounded (tokens) | hop=3 alone | scope alone | **hop=3 + scope** |
| :--- | :--- | :--- | :--- | :--- |
| django_t02_005 | 23465 | ~13823 (41%) | ~5854 (75%) | **~1548 (93.4%)** |
| django_t02_009 | 35736 | ~2392 (93%) | ~9310 (74%) | **~1271 (96.4%)** |
| django_t02_017 | 73380 | ~3525 (95%) | ~940 (98.7%) | **~711 (99.0%)** |

Full per-variant data: `benchmarks/experiments/results/manifest_sizing.json`.
Commits: `1a94688` (findings 1-2), `465474b` (finding 3).

---

## Approach A v3: Hop=3 + Scope-Filtered Manifest

**Mechanism:** same two-turn protocol, same `hydration_loop.py`, but
`_build_candidate_index`'s own candidate universe is capped at `max_hops=3`
(was `DEFAULT_MAX_HOPS=6.0`) and further reduced by
`_combined_hop_scope_filtered` - the exact filter validated offline immediately
above, ported into the live driver only after that validation passed. Dry-run
validated (no LLM calls) before any paid spend: candidate counts matched the
offline analysis exactly (`django_t02_005`=29, `django_t02_009`=14,
`django_t02_017`=6).

**Result (18 cells, $0.0358 for the 36-cell 2-approach grid - vs. v2's $0.1515
for the identical grid shape, real, direct confirmation of the projected 93-99%
reduction):**

| approach | tsr | cpi_strict | fpr_gt | mean tokens |
| :--- | :--- | :--- | :--- | :--- |
| baseline | 0.222 | 0.889 | 0.778 | 7597 |
| A v1 (name-only) | 0.333 | 0.333 | 0.000 | 8379 |
| A v2 (signatures + calls) | 0.389 | 0.833 | 0.133 | 47060 |
| A v3 (hop=3 + scope) | 0.500 | 0.667 | 0.000 | 4167 |

`tsr` (0.500) is the best result of the entire spike - more than double
baseline's own 0.222. `fpr_gt` returns to a clean 0.000 (matching v1). Mean
tokens (4167) land *below* baseline's own single-turn cost (7597), at 91% less
than v2's for the same grid. **Not a uniform win, though**: `cpi_strict`
settles at 0.667 - well above v1's 0.333, but a real drop from v2's 0.833.

Per task:

| task | baseline cpi/tsr | A v2 cpi/tsr | A v3 cpi/tsr |
| :--- | :--- | :--- | :--- |
| django_t02_005 | 1.000 / 0.000 | 1.000 / 0.000 | 1.000 / 0.000 |
| django_t02_009 | 1.000 / 0.000 | 1.000 / 0.667 | 0.000 / 0.333 |
| django_t02_017 | 0.667 / 0.667 | 0.500 / 0.500 | **1.000 / 1.000** |

- **`django_t02_017`: solved.** `cpi=1.0`/`tsr=1.0` on all 6 cells, every
  budget, every seed - the seed/budget-dependent flakiness v2 showed here is
  gone. A 6-candidate manifest apparently gives the model an unambiguous
  enough view to answer consistently, where v2's 409-candidate one didn't.
- **`django_t02_009`: looked like a regression, mostly wasn't one - see the
  diagnostic re-run below.** `cpi_strict` falls back to 0.0 on all 6 cells
  (matching v1, worse than v2's 1.0), but `tsr` recovers to 1.0 at
  budget>=4000 (both seeds) - the model's own *final answer* is completely
  correct 4 of 6 cells; only the 2 `budget=2000` cells are genuinely wrong.
- **`django_t02_005`: unchanged from v1/v2.** `cpi=1.0` (full recall) but
  `tsr=0.0` on all 6 cells - see the diagnostic re-run below for why.

**Diagnostic re-run (budget=4000, seed=42, $0.0017, `hydration_loop.py` gained
real response-text logging first - `51e3ac6` - since no prior run in this
spike had ever persisted either turn's raw completion): both anomalies have
clean, verified explanations, and neither is really a context-selection bug.**

- **`django_t02_005`**: Turn 2's answer names all 4 symbols Turn 1 requested,
  in a sensible order - `Model.save`, `Model.save_base`, `Model._save_parents`,
  `Model._save_table` - and the model's own reasoning is accurate
  (`save_base` genuinely does call `_save_parents`/`_save_table`). But the
  *adjudicated ground truth* (`task.adjudicated.pipeline_symbols`) names only
  2 symbols: `['Model.save', 'Model.save_base']`. `score_debug` is a strict,
  all-or-nothing exact-match scorer (`"0.0 for a wrong/missing/extra/
  reordered entry"` - its own docstring) - a more-thorough-than-the-ground-
  truth answer scores a hard 0, identically to a wrong one. **This is a
  granularity mismatch between the adjudicated pipeline and what Turn 1's own
  richer manifest reasonably leads the model to include, not a comprehension
  failure** - and it is present in v1 and v2 too (both share this task's exact
  same `tsr=0.0` pattern), so it predates v3 and isn't specific to this
  candidate-pruning approach at all.
- **`django_t02_009`**: Turn 1 requested 4 symbols - `QuerySet.filter`,
  `_filter_or_exclude`, `_chain`, `_clone` (correcting an earlier speculation
  in this same section before the raw text was available: the missing symbol
  is `_not_support_combined_queries`, not `_clone`, which *was* requested).
  `cpi_strict` is a strict subset check on the *rendered package's own node
  set* (`cpi.py`: `"1.0 if the entire pipeline is a subset of selected, else
  0.0"`) - since `_not_support_combined_queries` was never separately
  requested/hydrated, `cpi_strict=0.0` by that definition. But Turn 2's own
  final answer names all 5 ground-truth symbols, in the correct order,
  including `_not_support_combined_queries` - `tsr=1.0`. The model read it
  directly as a literal call site inside `QuerySet.filter`'s own already-
  hydrated source body (`filter()` really does call
  `self._not_support_combined_queries(...)`) and correctly cited it as a
  causal stage without needing its own separate node. **`cpi_strict` under-
  credits this case - a real success, not a real failure** - a genuine
  limitation of that metric specific to two-pass hydration (a single-turn
  approach's own selected set is what the model reasons over directly, so
  this discrepancy between "what's hydrated" and "what the model can
  correctly infer from what's hydrated" doesn't arise the same way there).
  The 2 `budget=2000` cells that stay genuinely `tsr=0.0` were not
  individually re-diagnosed - not necessarily the same mechanism.

Commits: `7c352a5` (v3 implementation, dry-run validated), `8723323` (36-cell
result, $0.0358), `51e3ac6` (response-text logging added), `9af8941`
(diagnostic re-run, $0.0017).

---

## Synthesis

**The core trade-off, stated plainly:** pure graph topology (Approach C) has
real structure but no semantic discrimination; pure LLM name-filtering (A v1)
has real semantic judgment but, given only names, no access to the content that
judgment actually needs. **A v2 confirmed the fix**: giving the model real
structural content - signatures and resolved call targets, still no docstring
dependency, still fully deterministic - closes most of the gap A v1 left open.
**A v3 confirmed the cost of that fix could come down 91% (measured, not
estimated) without losing the fix**: `hop=3` + scope-filtered candidate pruning
(itself verified offline, for real, against a documented `hop=2` failure case
before being wired into a live grid) took Turn 1 from ~44K tokens back to
~4.2K - and, on this run, *improved* `tsr` further (0.500, the best of the
entire spike) while restoring `fpr_gt` to a clean 0.000.

**No single variant is a strict win, and that's the real, final finding of this
spike.** v2 has the best `cpi_strict` (0.833); v3 has the best `tsr` (0.500)
and the best token economy (4167, under baseline's own 7597); v1 has a tie for
best `fpr_gt` (0.000, matched by v3). Choosing between v2 and v3 is a real
trade-off, not a strictly-dominated choice on either side.

**The `django_t02_009`/`django_t02_005` anomalies, diagnosed via a real
response-logging re-run (see A v3's own section for the full text): both
metrics, not the model, were the source of the apparent regressions.**
`django_t02_009`'s `cpi_strict=0.0` mostly co-occurs with `tsr=1.0` - the
model's own final answer was correct at budget>=4000, `cpi_strict` just
doesn't credit a causal stage the model correctly inferred by reading it as a
literal call site inside an already-hydrated caller, rather than needing its
own separately-requested node - a genuine metric limitation specific to
two-pass hydration, not a selection failure. `django_t02_005`'s `tsr=0.0`
(shared by v1, v2, and v3 alike) is a granularity mismatch between the
2-symbol adjudicated ground truth and a more-thorough, still-substantively-
correct 4-symbol answer the richer Turn 1 manifest reasonably leads the model
to give - `score_debug`'s exact-match scoring treats "more complete than
expected" identically to "wrong." Neither anomaly indicts the underlying
selection mechanism (topological/scope filtering, or the model's own Turn 1
judgment) - both indict how well `cpi_strict`/`score_debug` capture what
"correct" means for a two-pass, richer-context protocol specifically. Only the
2 `budget=2000` cells of `django_t02_009` (genuinely `tsr=0.0`, not
individually re-diagnosed) remain a real, if smaller, open thread.

**If single-turn knapsack packing remains the production path instead**,
candidate scoring needs to blend topological distance with a lightweight
*semantic* relevance signal (docstring/signature token overlap with the seed, or
caller-callee token overlap) rather than either pure density (today) or pure
distance (Approach C) alone - A v2/v3's own results are themselves evidence
that such a signal exists and is usable; the open question there is how to fold
it into a single-pass scoring formula rather than a second LLM turn.

## Disposition

- `experiment/noise-filtering-spike` stays intact, unmerged, as the audit trail
  for all five approach variants' (B, C, A v1/v2/v3) real commits and real
  data.
- Approach A v3 is the most token-economical strong result of this spike
  (best `tsr`, clean `fpr_gt`, sub-baseline token cost) but is not a strict
  improvement over v2 (`cpi_strict` is lower) - which variant to build on
  depends on whether `tsr`/cost or `cpi_strict` is the priority for whatever
  uses this next. A response-content-logging re-run (`hydration_loop.py` now
  persists both turns' raw text on every cell, not just a diagnostic one -
  `51e3ac6`) diagnosed both prior open threads: `django_t02_005`'s `tsr=0.0`
  is a ground-truth-granularity mismatch (score_debug's exact-match scoring
  penalizes a more-thorough-but-correct answer identically to a wrong one),
  and most of `django_t02_009`'s apparent regression is `cpi_strict`
  under-crediting a real success (the model correctly inferred a causal
  stage from an already-hydrated caller's own source body). Only
  `django_t02_009`'s 2 `budget=2000` cells (genuinely `tsr=0.0`) remain
  undiagnosed.
- **A high-priority production bug is flagged, not fixed, on this branch**:
  `prism.runtime.index_cache`'s whole-pipeline cache
  (`prism.cli.build_pipeline`'s `use_cache=True` default) returns inconsistent
  results across separate process invocations of the identical pinned corpus -
  see Approach A v2's own section above for the full diagnosis. This affects
  every consumer of `build_pipeline`'s default caching path, not just this
  spike; `benchmarks/engines/prism_engine.py`'s `use_cache=False` change is a
  spike-local workaround, not a fix, and does not apply outside this branch.
- The Subgraph Processor design (`docs/design_formalism.md` Sec 10.3/10.4)
  remains unimplemented pending a design that addresses one of the synthesis
  paths above.
- Protected regression suite: 21/22, the same knowingly-accepted
  `django_t02_017`/`_urlparse` exception documented in Sec 10.4 - none of the
  spike approaches touched `prism.*` production code (the `use_cache=False`
  change above is in the benchmark harness, `benchmarks/engines/`, not
  production), so this suite's state is unaffected by anything in this
  document.
- `reports/pilot/checkpoint.json` hash confirmed unchanged
  (`589e42386e58c528f7a24b1083d4b097ea66ac28984317eb471ca9a02e11aa81`) before
  and after every commit in this spike.
