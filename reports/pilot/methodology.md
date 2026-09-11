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
