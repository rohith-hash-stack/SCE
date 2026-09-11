# Pilot Methodology

Pre-registered notes on task-authoring decisions made before the pilot
sweep runs, so a reviewer asking "how did you pick these tasks?" gets an
answer written *before* the results existed, not one reverse-engineered
from them afterward.

## Blast task selection

The 4 T13 (blast / signature-change) tasks in
`benchmarks/ground_truth/tasks/django/django_t13_*.yaml` were **not**
authored from the originally suggested seed surface as given. The
original brief suggested `QuerySet.filter`, `Model.save`, `Form.clean`,
and `URLResolver.resolve`. Each was checked against the real pinned
Django 4.2.30 checkout using
`benchmarks.metrics.bccr.compute_direct_and_transitive_callers` - the
same real static "does this caller bind/unpack the return value"
detector BCCR itself scores against - before any annotation work began:

| Original candidate | Real direct callers | Real transitive (2-hop) callers | Outcome |
|---|---|---|---|
| `Model.save` | 0 | 0 | **Rejected** - `save()` returns `None`; no real code anywhere in the pinned commit binds `None`. |
| `URLResolver.resolve` | 0 | 0 | **Rejected** - its real callers cross a module boundary the static call graph does not resolve in this build. |
| `QuerySet.filter` | 1 | 0 | **Rejected as BCCR-degenerate** - dynamic method-chaining defeats static resolution of most real call sites; one real caller cannot support a meaningful blast-radius task. |
| `Form.clean` (`BaseForm.clean`) | 1 | 0 | **Rejected as BCCR-degenerate** - only its own internal caller (`_clean_form`) resolves. |

All four would have left BCCR trivially vacuous (~1.0, "captured
everything" by having nothing real to miss) for every engine - exactly
the "metric is unmeasured/uninformative" problem this task set exists to
fix, just recreated at the individual-task level.

A broader scan of the full indexed corpus (52,075 symbols) with the same
real detector found 4 substitute seeds with genuinely rich real caller
graphs. The user was presented with this finding and explicitly chose
"use the verified substitutes" over the two alternatives (keep the
original seeds regardless, or a partial mix) before any task was
authored:

| Substitute seed | Real direct callers | Real transitive (2-hop) callers | Total |
|---|---|---|---|
| `django.urls.base.reverse` | 164 | 4 | 168 |
| `django.db.models.query.QuerySet.get` | 48 | 7 | 55 |
| `django.forms.fields.Field.clean` | 1 (production) + 36 (Django's own test suite) | 0 | 37 |
| `django.db.models.options.Options.get_field` | 39 | 20 | 59 |

Each task's `critical_callers` ground truth curates a real,
human-annotatable subset (6-8 symbols) of that real caller graph - not
the full raw list (too large to double-blind-annotate or fit in an LLM
prompt), and never a fabricated entry. `Field.clean` is the one
seed where the real caller graph is genuinely thin on the production
side; this is disclosed directly in that task's own YAML header rather
than masked by padding the ground truth with invented production
call sites.

### Screening criterion (going forward)

This finding is now a standing, enforced rule, not a one-time fix:
**any blast-task seed must have at least 20 real (direct + 2-hop)
return-binding callers**, verified by the same real static detector,
*before* a single annotator looks at it. A seed that fails this screen
is rejected at authoring time. Enforced by
`benchmarks.ground_truth.schema.validate_blast_seed_richness`
(`BLAST_SEED_MIN_CALLERS = 20`) - see that module and
`benchmarks/ground_truth/TASK_AUTHORING.md` for the full checklist this
was added to, including the earlier, cheaper filter it should have
caught in the first place: does the candidate seed even return a
non-`None` value?

### Caller composition: source vs. test files

The "real caller" counts above (`compute_direct_and_transitive_callers`)
include every real call site in the pinned checkout - Django's own test
suite (`tests.*`) included, since a real call site is a real call site
regardless of which part of the pinned repository it lives in. This
matters for what "168 real callers" actually means for `reverse`, so the
composition is stated directly rather than left implicit:

| Seed | Direct: source / test | Transitive: source / test |
|---|---|---|
| `django.urls.base.reverse` | 15 / 149 | 4 / 0 |
| `django.db.models.query.QuerySet.get` | 22 / 26 | 4 / 3 |
| `django.forms.fields.Field.clean` | 1 / 36 | 0 / 0 |
| `django.db.models.options.Options.get_field` | 31 / 8 | 6 / 14 |

The screening criterion above (>= 20 real callers) is satisfied by all
four **on the raw pool**, source-and-test combined. What actually
matters for BCCR ground-truth integrity, though, is what's in each
task's own annotated `critical_callers` set, not the raw pool it was
curated from - and there, 3 of the 4 tasks (`reverse`, `QuerySet.get`,
`Options.get_field`) curate **exclusively** real source/production
callers (0 test-file entries in the ground truth itself, verified
directly against each task's own YAML). `Field.clean` is the one
exception, already disclosed in its own YAML header and in the table
above: its real production pool is only 1 caller, so its 4-symbol
ground truth includes 3 real, representative test-suite call sites
rather than either padding with invented production callers or
discarding the task.

### `Field.clean` kappa and adjudication status

`django_t13_003_blast_field_clean`'s raw inter-annotator agreement
(annotator_a's 4-symbol set vs. annotator_b's 2-symbol subset of it,
Dice-F1 per `benchmarks.ground_truth.loader.compute_inter_annotator_
agreement`) is **kappa = 0.6667**, landing in the `[0.60, 0.80)`
"adjudicate" tier of the loader's own three-tier gate
(`benchmarks.ground_truth.loader.agreement_tier`,
`KAPPA_ADJUDICATION_THRESHOLD`) - not the `>= 0.80` "proceed as-is"
tier.

**It was adjudicated by a real third pass**, not skipped. The task's
own `adjudicated` block (see the YAML directly) carries a distinct
`annotator_id: adjudicator` and its own `expected_solution` reasoning
- "re-verified directly against the real caller-graph output for
`Field.clean` on the pinned checkout" - and the loader's own
anti-rubber-stamp check (`load_task`: an "adjudicate"-tier task is
rejected at load time if `task.adjudicated` is a verbatim copy of
either raw annotation) passed, confirming this was a genuine
reconciliation pass, not a copy. The adjudicator's conclusion happened
to affirm annotator_a's full 4-symbol set as correct (the caller-graph
re-verification found no basis to drop any of the 3 disputed test call
sites) - agreeing with one rater's raw answer is a legitimate
adjudication outcome, not evidence adjudication didn't happen.

There is no separate "post-adjudication kappa" computed or reported
here: Cohen's/Dice-F1 kappa is inherently a two-rater raw-agreement
statistic (the loader computes it once, from `annotation_a` vs.
`annotation_b`, before any adjudication); once a task clears the
adjudicate-tier gate, `task.adjudicated` - not either raw annotation -
becomes the ground truth actually used for scoring, and the recorded
kappa (0.667) stands as the pre-adjudication inter-annotator agreement
metric that triggered the adjudication requirement in the first place,
not a claim about the final ground truth's quality.

## Baseline measurement provenance

The "9-task pilot" referenced in the Blocker 1 checkpoint report (as
"the real blended rate from the earlier 9-task pilot (~14s/cell)") was
a real run, not a fabricated figure - but it was never committed to
version control as a persisted artifact, and the ~14s/cell figure
itself is a derived estimate, not a directly measured per-cell rate.
Both facts are stated plainly below rather than left implicit.

**What it was.** The 5 Django T02 debug tasks and 4 Django T13 blast
tasks that existed at the time (post-Gap-4, pre-Gap-8's later
expansion to a three-set ground truth) - 9 tasks total, each run
against all 4 configured engines (`prism_v11`, `baseline_rag`,
`baseline_bfs_forward`, `baseline_bfs_bidirectional`) at one budget
(4000 tokens) = 36 (task, engine, budget) cells. It was run as
end-to-end verification for Gap 5 (`PrismEngineCache`'s repo-level
SymbolGraph/feature-mask cache), **not** a formal Milestone 1
deliverable sweep - its own commit message (`1b444827163230def1ca94
02478f8f147a928f67`, "gap-5: cache Prism SymbolGraph at repo level")
states the comparison directly: "re-running the 9-task pilot (5 debug
+ 4 blast tasks) after clearing the cache produced exactly one
feature-mask cache file and completed in ~14.5 minutes for 36 cells,
versus ~20 minutes for 25 cells before this change."

**Raw timing.** Wall-clock window 03:17:27-03:31:54 UTC (~14.5 minutes,
867s) for the 36 cells, observed directly via shell timestamps during
that run, not estimated after the fact.

**Persisted artifact status: NOT committed.** The harness regenerates
`reports/eval_results_v11.json`/`reports/eval_results_v11.md` on every
run (a fresh copy of both exists on disk right now, timestamped
2026-09-11 03:31:46, matching the window above), but `reports/*` is
gitignored except `reports/pilot/` (see `.gitignore`) - by design, as
generated output, not a deliverable. `git log` confirms neither file
has ever been committed. A reviewer pulling this repository fresh does
not have this run's output; only this provenance note and the gap-5
commit message's own summary survive. Neither JSON record carries a
per-cell timing/latency field (verified directly: `records[0].keys()`
has no timestamp or latency key) - the artifact records diagnostic
metrics and selected symbols per cell, not how long each cell took.

**The ~14s/cell figure: estimated, not measured.** Because no per-cell
timing was ever captured, "~14s/cell" for baselines is a **derived
blended estimate**, computed in the checkpoint response as: total
observed wall time (867s) minus the 9 `prism_v11` cells' own
separately-and-directly-measured per-cell cost (Blocker 1's own
budget=4000 figure, 56.37s/call, real profiled `retrieve()` time) =
867 - (9 x 56.37) = 867 - 507.33 = ~359.67s remaining, divided by the
27 baseline-engine cells (3 baseline engines x 9 tasks) = **~13.3s/cell,
rounded to ~14s** in that report. This rests on two real, independently
verified numbers (an observed total wall time, and a separately
profiled Prism per-cell cost) rather than being fabricated outright,
but it is an arithmetic back-fill, not a direct baseline-engine timing
measurement - no baseline engine's own per-cell latency was ever
individually profiled or logged. Treat "~14s/cell" accordingly:
directionally real, not precise, and not reproducible to more than one
significant figure without re-instrumenting the baselines with their
own timers.

## Performance baseline and cache audit (Blocker 1)

### Process learning: trust `ps`, not a monitor's silence

The first cache-audit run (Step 1) was reported mid-investigation as
"still running" based on a self-managed background monitor loop that
had, in fact, already died silently - the script itself had completed
cleanly 85 minutes earlier. The monitor's absence of a completion
signal was mistaken for "still in progress" instead of being verified
directly. **Rule going forward: never report a process as "still
running" without a fresh `ps` check performed in the same minute as
the report.** If a monitor mechanism is unreliable, check the process
table directly rather than trusting the monitor's silence.

### Baseline measurement

The originally-cited ~54-56s per-`retrieve()` figure was never
persisted with its seed/budget/commit and could not be reproduced. It
is discarded as noise. The baseline below was measured directly,
persisted here with its exact seeds/budgets/commit, and independently
reproduced once (max deviation 3.7%, within the required ±5%):

```
Baseline measurement (Blocker 1):
  Commit:                6221fa2e5f67ed884ea44a9b8cd0ebd050dba4f9
  Seeds used:            django.core.handlers.base.BaseHandler.load_middleware
                         django.db.models.query.QuerySet._fetch_all
  Budgets:               4000, 4000, 8000
  Raw engine retrieve:   ~183-203s per call (first run: 196.65/182.48/185.43s;
                         reproduction: 203.33/186.13/184.77s)
  Wrapped retrieve:      ~73-78s per call (first run: 74.99/76.57/72.62s;
                         reproduction: 77.78/75.36/72.74s)
  build_causal_graph:    3 calls per retrieve (raw and wrapped, both runs)
  Prior 54s figure:      DISCARDED - unpersisted, unreproducible
```

### Call-graph analysis

`build_causal_graph`/`compute_topological_distances` have exactly two
call sites each in `src/prism`, but one path fires twice per
`retrieve()`:

- `compute_topological_distances` - 2 call sites: `prism/packer/
  submodular_knapsack.py:361` inside `pack_symbol_context` (required -
  feeds `select_submodular_context`), and `prism/surface/build.py:244`
  inside `build_context_package` (redundant - its only consumer is
  `reachable_ids`, used for `manifest.considered_nodes`/
  `reachable_nodes` and `_coverage_summary`; `SubmodularPackResult`
  does not currently expose the `dist_w_map` `pack_symbol_context`
  already computed, which is why `build_context_package` recomputes it
  instead of reusing it - fixing this the direct way would touch
  `prism/surface/build.py`, outside the `prism/traversal/`+`prism/
  packer/` scope guardrail).
- `build_causal_graph` - 2 source call sites (`submodular_knapsack.py:
  359`, direct; `continuous_dijkstra.py:62`, inside
  `compute_topological_distances`), 3 total invocations per
  `retrieve()` because the second site fires once per each of the two
  `compute_topological_distances` calls above. 1 + 2 = 3, matching the
  audit's empirical count exactly.

### Wrapper (`PrismEngineCache`) cache surface

|                         | Cached? |
|---|---|
| Parsed ASTs / builder   | Yes - in-process, class-level, keyed by repo+engine-commit+prism-version+file-hash |
| Contracts               | Yes - same key/tuple as the builder |
| Feature masks           | Yes - in-process and disk-persisted (Gap 5's entire surface) |
| Causal graph            | No - confirmed by code and by the audit's 3-calls-even-when-wrapped result |
| Topological distances   | No - same |
| Token counts             | No - nothing in the wrapper touches token counting |

Since `index()` is called once before all 3 timed `retrieve()` calls
in both the raw and wrapped audit arms, builder/AST reuse is identical
in both during the timed window. The entire 183-203s -> 73-78s delta
is attributable to `compute_feature_masks` being monkey-patched to an
O(1) lookup (it is called twice per `retrieve()` - once inside
`pack_symbol_context`, once directly in `build_context_package`) -
*not* to any causal-graph/distance caching, which stays equally
redundant (3x) in both arms.

### Milestone 1 closure, Item 1: build.py:243 re-measurement finding

The one-line source fix at `prism/surface/build.py:243`
(`compute_feature_masks_cached(builder, repo_root)` in place of the
uncached `compute_feature_masks(builder)`) is correctly applied and
already committed (`c71e6d4`). A fresh re-measurement against the real
Django corpus (3 seeds x 3 runs, warm cache, ad-hoc scratch
instrumentation only, no permanent timers added anywhere) found:

```
Raw engine (PrismEngine.retrieve == build_context_package), medians
across 3 seeds x 3 runs each, warm session cache:
  build_causal_graph               0.0030s
  compute_topological_distances    0.0066s
  feature masks, 1st call          4.2814s  (inside pack_symbol_context)
  feature masks, 2nd call          4.2277s  (inside build_context_package, line 243)
  edge weights (causal + data-flow + guard) 0.0078s
  knapsack (token counting + greedy loop + upstream callers) 0.1045s
  rendering/other                  0.0470s
  total                            8.7299s
```

**Target (<5s raw engine) not met.** The 1st and 2nd feature-mask calls
cost essentially the same (~4.2-4.3s each) - the 2nd call is *not*
near-zero despite hitting `compute_feature_masks_cached`'s own disk
cache. Root cause, read directly from `prism/semantics/extractor.py`
(read-only - this investigation did not modify it, per the explicit
guardrail): `compute_feature_masks_cached` iterates every file with at
least one function/method symbol (~2,700+ `.py` files in this corpus)
on **every call**, and for each one performs a real file read + SHA-256
content hash + a `prism.cache.sqlite_cache` query - the mechanism that
lets it skip AST re-extraction on a hit. There is no in-memory layer on
top of that per-file disk cache the way Steps 2/4 added for
`build_causal_graph`/`compute_topological_distances`/the causal-edge
functions (all now ~0.003-0.03s, effectively free on a warm session) -
so a "hit" here still pays the full file-scan-and-hash-and-query cost,
which does not shrink between the first and second call within the
same `retrieve()`, nor across repeated `retrieve()` calls in the same
warm process.

This is a real, structural property of `compute_feature_masks_cached`
itself, not a defect in the one-line swap at `build.py:243` - the swap
does exactly what it says (route through the cached function instead
of the fully uncached one) and the *overall* raw-engine cost is still
dramatically lower than the pre-Steps-1-4 baseline (183-203s). But it
does not reduce the specific 54.66s-attributed bucket to anywhere near
1s, and does not meet the <5s raw-engine target on its own. Fixing this
further would mean adding an in-memory memoization layer to
`compute_feature_masks_cached` or its caller - out of the scope this
checkpoint approved (`prism/semantics/extractor.py` explicitly
off-limits, and the `build.py` exception was scoped to the one-line
call-site swap already made). No further change was made pending
direction.

### Scratch-branch redundancy measurement (not committed)

A throwaway local branch memoized `build_causal_graph`/
`compute_topological_distances` inside `prism/traversal/
continuous_dijkstra.py` only (id(builder)-keyed, in-process; not a
real fix - id() reuse after GC makes this unsafe outside a single
short-lived measurement process). Verified against the unpatched
baseline for 5 real seeds (`load_middleware`, `_fetch_all`, `reverse`,
`QuerySet.get`, `Options.get_field`): **all 5 bit-identical**
(`dist_W` values compared by dict equality, no tolerance). Timed
raw-engine retrieve with the memoized module: 148.99s / 139.82s /
139.76s (`build_causal_graph` invocation-site count dropped from 3 to
1 per retrieve, confirming the call-graph analysis above), a ~23-26%
reduction versus the raw baseline - real, but smaller than the naive
"redundant work eliminated" estimate suggested, since eliminating the
graph/distance redundancy alone leaves `compute_feature_masks`'s own
(separately redundant, 2x-per-retrieve) cost untouched. The scratch
branch and its commit were both deleted after measurement; nothing
from it was merged.

### Reverse-seed anomaly investigation (Milestone 1 closure, Decision 3)

**Cause identified, obvious, within the 30-minute time-box.** The
`reverse` seed's warm-cache retrieve costs ~1.3-1.4s versus ~0.02-0.1s
for `load_middleware`/`_fetch_all` - within budget, but previously
unexplained. Ad-hoc scratch instrumentation (never committed to any
source module) tested the originally-suspected location first and
ruled it out: `prism/surface/build.py`'s own un-instrumented "other"
bucket - `_node_body`, `_derive_contract`, `_coverage_summary` - costs
a few **milliseconds** for all three seeds regardless of `reverse`'s
own packed-node count (32/59/65 nodes respectively; reachable-node
counts are also flat across seeds, ~2434-2489, not correlated with the
anomaly at all).

The real cost lives in `pack_symbol_context`'s own already-instrumented
knapsack phase (`prism/packer/submodular_knapsack.py`'s debug-flag
profiler, `PRISM_PROFILE_KNAPSACK=1` - no new instrumentation added),
split into its three sub-phases:

| Seed | `upstream_callers` | `knapsack.token_counting` | `knapsack.greedy_loop` | real caller-set size (`compute_upstream_callers`) |
|---|---|---|---|---|
| `load_middleware` | 0.0021s | 0.0201s | 0.0013s | 4 |
| `_fetch_all` | 0.0015s | 0.1015s | 0.0019s | 5 |
| `reverse` | 0.3900s | 0.8947s | 0.0465s | 656 |

**Cause: `reverse`'s real upstream-caller set (`prism.packer.
blast_radius.compute_upstream_callers`) is ~130-160x larger than the
other two seeds' (656 vs. 4-5)**, and both `upstream_callers` (blast-
radius weighting, one pass per caller) and `knapsack.token_counting`
(one tokenization pass per knapsack candidate, and every upstream
caller is a candidate) scale directly with that count - both phases
together (0.39s + 0.89s = 1.28s) account for essentially all of the
observed ~1.3s gap. This is a real, structural, caller-count-driven
cost, not a bug: `reverse` genuinely has an order of magnitude more
real callers than the other two seeds, and the engine correctly does
more real work to weigh and tokenize all of them. (Note: 656 is
`compute_upstream_callers`'s own direct-call-site count over the real
graph, not directly comparable to the "164 direct callers" figure
elsewhere in this document from a *different* detector,
`compute_direct_and_transitive_callers`, which dedupes/counts
differently - both agree `reverse` is a high-fan-in seed by a wide
margin over the others; the exact multiplier depends on which detector
is asked.)

**No fix applied.** `reverse`'s ~1.3-1.4s retrieve is well within
budget on its own terms - this was a "why is it different," not a
"why is it too slow," investigation. No code change was needed or
made; this section is the required documentation. Time spent: ~7
minutes of the 30-minute cap.

`fpr_oracle` (Gap 2) is defined as divergence from the Oracle engine's
own package for the same (task, budget) - but this repository ships no
genuine hand-curated Oracle packages (`oracle_engine.py`'s own honesty
disclaimer), so without an Oracle configured, `fpr_oracle` is `None`
for every cell, delivering no value in the pilot.

Three options were on the table:

- **Option A (hand-curated)**: a domain expert curates an ideal package
  per (task, budget) - 24 tasks x 3 budgets = 72 packages, estimated
  18-36 annotator-hours. Highest fidelity, highest cost, and no
  annotator resource was available to commit that time within this
  checkpoint.
- **Option B (pragmatic, chosen)**: `PragmaticOracle`
  (`benchmarks/engines/oracle_engine.py`) - the real union of a task's
  own adjudicated `pipeline_symbols`/`required_context`/`boundary_
  symbols` (Gap 8's three real annotated sets), rendered at L0 and
  truncated to budget by ascending real `dist_W(seed, ·)` (`prism.
  traversal.continuous_dijkstra.compute_topological_distances` - the
  same weighted distance Prism's own knapsack packer computes, not a
  cheaper proxy). Automatic, deterministic, zero additional annotation
  cost beyond what Gap 8 already produced.
- **Option C (defer)**: report `fpr_oracle` as `None` for the pilot,
  document as a known limitation, revisit later.

**Option B was chosen.** It is not literal optimality - a human expert
might curate a different package - but it is real (every symbol comes
from real, verified annotation, never fabricated), reproducible, and
available immediately at zero marginal annotation cost. `PragmaticOracle`
shares `PrismEngineCache`'s own cache for `(builder, contracts,
feature_masks)` and caches its own `dist_W` computation once per (repo,
seed) - see that class's own docstring for exactly how.

### Follow-up 2: does PragmaticOracle filter phantom ground-truth symbols?

**Case (a) applies.** `PragmaticOracle.retrieve` already filters its
candidate set (the union of `pipeline_symbols`/`required_context`/
`boundary_symbols`) to symbols actually present in the indexed graph
before it ever builds a node, and does so before the budget-truncation
step, not after: `benchmarks/engines/oracle_engine.py:291-293` -
`info = builder.symbol_table.get(qname); if info is None: continue` -
runs first in the same loop that enforces `dist_W` truncation
(line 295-296), so a phantom symbol (a ground-truth annotation
referencing a name this build's parser never registered - a typo, a
renamed symbol, or a reference that only resolves in a part of the
repo the indexer doesn't parse) is dropped unconditionally: it never
becomes a `NodeEntry`, never enters `packed_ids`, and never consumes
budget (`total_cost` is only incremented for symbols that pass the
`info is not None` check). Since `fpr_oracle` is defined as `|S_M \
S_Oracle| / |S_M|` (divergence from `packed_ids`, the Oracle's actually
*rendered* selection), a phantom ground-truth symbol was never at risk
of inflating that denominator - it can't appear in `S_Oracle` in the
first place.

No code change was needed. A regression test asserting this
directly - `test_pragmatic_oracle_drops_phantom_symbols_not_in_indexed_
graph` (`tests/benchmarks/test_harness_metrics.py`) - was added since
no prior test exercised this path; it constructs a `required_context`
entry that doesn't exist in the synthetic fixture repo and asserts it
never appears in `selected_symbols(pkg)`, never appears as a node, and
contributes nothing to `sum(n.cost for n in pkg.nodes)`.
