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

## Oracle for fpr_oracle (Gap 2 Blocker 2)

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
