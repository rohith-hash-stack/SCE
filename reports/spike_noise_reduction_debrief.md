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
sweeps):** ~$0.18
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
| A - Two-Pass Hydration (LLM-driven manifest selection) | 0.333 | 0.333 | 0.000 | 3.0 | 8379 |

None of the three approaches achieves the goal that motivated this spike:
**suppress `fpr_gt` without dropping `cpi_strict`.** B doesn't move either metric
at all (by design - it only changes rendering). C moves `fpr_gt` a little at the
cost of `cpi_strict`. A eliminates `fpr_gt` completely but at a much larger
`cpi_strict` cost than C. Each failure is diagnostically different, which is
itself the finding - see Synthesis below.

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

## Approach A: Two-Pass Hydration Protocol

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

## Synthesis

**The core trade-off, stated plainly:** pure graph topology (Approach C) has
real structure but no semantic discrimination; pure LLM name-filtering
(Approach A) has real semantic judgment but, given only names, no access to the
content that judgment actually needs. Neither alone solves "suppress `fpr_gt`
without dropping `cpi_strict`" - and, critically, they fail in *different,
explicable* ways rather than the same way, which is the real finding: the
problem has (at least) two separable halves - deciding what's structurally
reachable, and judging what's actually relevant - and each spike solved a
different half while leaving the other one unaddressed.

**For whichever path is picked up next** (recorded in
`docs/design_formalism.md` Sec 10.5):

1. If single-turn knapsack packing remains the production path, candidate
   scoring needs to blend topological distance with a lightweight *semantic*
   relevance signal (docstring/signature token overlap with the seed, or
   caller-callee token overlap - something content-derived) rather than either
   pure density (today) or pure distance (Approach C) alone.
2. If a multi-turn/frontier-manifest protocol is revisited, Turn 1's manifest
   needs to carry lightweight content - an L2-style signature or a docstring
   summary, not a bare qualified name - so the model has something to judge
   relevance *from*. The token cost of that richer manifest is the real open
   design question (Turn 1 alone already cost ~6-7K tokens at a bare-name
   index and `max_hops=6.0` - see Approach A's own section above).

## Disposition

- `experiment/noise-filtering-spike` stays intact, unmerged, as the audit trail
  for all three approaches' real commits and real data.
- No further implementation followed this spike. The Subgraph Processor design
  (`docs/design_formalism.md` Sec 10.3/10.4) remains unimplemented pending a
  design that addresses synthesis point 1 or 2 above.
- Protected regression suite: 21/22, the same knowingly-accepted
  `django_t02_017`/`_urlparse` exception documented in Sec 10.4 - none of the
  three spike approaches touched production code, so this suite's state is
  unaffected by anything in this document.
- `reports/pilot/checkpoint.json` hash confirmed unchanged
  (`589e42386e58c528f7a24b1083d4b097ea66ac28984317eb471ca9a02e11aa81`) before
  and after every commit in this spike.
