# Ground-Truth Task Authoring Checklist

A living checklist for authoring new `benchmarks/ground_truth/tasks/<repo>/*.yaml`
files - written after Gap 4's blast-task authoring found that 3 of 4
originally-suggested seeds turned out to be unusable, discovered only
*after* real static analysis was run against the real pinned repo. This
exists so the same mistakes aren't repeated for Milestone 2's 15
additional Debug tasks, the 4 blast tasks, or a future repo's tasks.

## Every task, every type

1. **Verify every symbol against the real, indexed pinned commit** -
   `prism.cli.build_pipeline(repo_path)` then check
   `builder.symbol_table.get(qualified_name)`. Never guess a qualified
   name, a line range, or whether a symbol exists. A seed/reference
   symbol that doesn't resolve in the real index is not a real task.
2. **Verify every pipeline stage is reachable, not just real** (added
   after Milestone 2 batch 3's review caught two pipelines with a real-
   but-unreachable terminal stage - see G40 in `docs/design_formalism.md`).
   Existing in the symbol table is necessary but not sufficient - a
   symbol with zero real path from the seed is exactly as useless to a
   retrieval engine as a symbol that doesn't exist at all. For every
   pipeline stage:
     1. The symbol exists in the symbol table (item 1 above).
     2. A reachable edge exists from the *previous* pipeline stage to
        it - check directly with `builder.graph.out_edges(prev_stage,
        data=True)` / `in_edges(this_stage, data=True)`, don't assume
        from reading source alone that a real Python relationship
        (a call, an inherited method) became a real graph edge.
     3. The edge chain is `CALLS`, `INSTANTIATES`, or - for a method
        inherited rather than directly defined - actually resolves
        through a real `EXTENDS` edge to its defining class. Confirmed
        directly (G40): this codebase's call resolution does **not**
        currently follow `EXTENDS` to find an inherited method when a
        `self.method()` call site's simple-name resolution lands on a
        subclass that doesn't itself define that method - the `EXTENDS`
        edge can be real while the method call still dangles to a
        nonexistent symbol. Don't assume inheritance "just works" here;
        check the actual edge.
   If any stage fails check 1 or 2, trim the pipeline to its last
   reachable stage and move the unreachable stage to `boundary_symbols`
   instead, with a header-comment note explaining why it's excluded
   (see `django_t02_014_send_mail_pipeline.yaml` for a worked example).
3. **Real, verified pinned commit.** `pinned_commit` in the YAML must
   match `benchmarks/corpora/pinned_commits.json`'s own entry for that
   repo - re-verify with `git ls-remote --tags <url>` /
   `git rev-parse HEAD` on the actual checkout if in doubt, never typed
   from memory.
4. **Two independent annotators + a real adjudicator.** Compute
   agreement with `benchmarks.ground_truth.schema.
   compute_inter_annotator_agreement` (Positive Specific Agreement /
   Dice-F1, not literal Cohen's kappa - see that function's own
   docstring for why). Let the loader compute `cohen_kappa` rather than
   hand-typing a number you might get wrong (omit the field from the
   YAML). `>= 0.80` proceeds, `[0.60, 0.80)` requires the adjudicated
   annotation to be a real reconciliation (not a copy of either raw
   annotation), `< 0.60` is rejected outright - never lowered to make a
   task pass.
5. **Structured, deterministically-scorable prompts.** A task's prompt
   should ask for a form a scorer can check without an LLM-judge second
   call or fragile prose-regex heuristics (a fenced JSON array of
   ordered symbol names for a `debug`/`chain` task; free prose naming
   symbols for a `blast` task, matched by `scorer_blast.py`'s own
   substring convention).
6. **Disclose real limitations honestly, in the YAML itself.** If a
   seed's real caller/reference graph is thin, or skews toward the
   target repo's own test suite rather than production code, say so in
   a header comment - don't pad the ground truth with invented entries
   to make it look richer than it is (see `django_t13_003_blast_field_clean.yaml`
   for an example).

## Blast (T13 / signature-change) tasks specifically

Ground truth for a blast task is the real set of direct + 2-hop callers
that bind/unpack the seed's return value - computed with
`benchmarks.metrics.bccr.compute_direct_and_transitive_callers(builder,
candidate_seed)`, the exact same real static detector BCCR itself scores
against. Before proposing *any* candidate seed to an annotator:

1. **Does the seed even return a non-`None` value?** Check by reading
   the real source. This is the cheapest possible filter and should
   have caught `Model.save` (which always returns `None`) before it was
   ever suggested - no real code anywhere binds `None`, so a `None`-
   returning seed's real caller count is trivially, structurally zero.
   Do this filter *first*, before running the detector at all.
2. **Run the real detector and check the count.** Call
   `compute_direct_and_transitive_callers` for real, then
   `benchmarks.ground_truth.schema.validate_blast_seed_richness(direct,
   transitive)` - it raises `BlastSeedTooThinError` if the total is
   below `BLAST_SEED_MIN_CALLERS` (currently 20). A seed that fails this
   screen is rejected before a single annotator looks at it, full stop
   - it is never "the annotators just didn't find many callers."
3. **Curate, don't dump.** A seed with hundreds of real callers (Django's
   `reverse()` has 164) still needs a human-annotatable, LLM-promptable
   ground truth - pick a representative subset (~6-10 symbols,
   preferring production/non-test call sites when there are enough of
   them) rather than listing every single real caller. Document in the
   YAML header how many real callers exist in total and how the subset
   was chosen.
4. **Record the rejection, not just the acceptance.** If a suggested
   seed fails either filter, that's a real, useful finding - write it
   down (see `reports/pilot/methodology.md`'s "Blast task selection"
   section for the template), don't just silently swap in a different
   seed and move on.

## Debug (T02) tasks specifically

Ground truth is an ordered causal pipeline (`pipeline_symbols`) plus,
where applicable, `required_context`/`boundary_symbols` (Gap 8). Trace
the real call chain by reading the real source - `grep`/`sed` the pinned
checkout directly, then verify every symbol and line range against the
real indexed symbol table as in the general checklist above. Prefer
seeds whose causal chain crosses at least two files/modules (a single-
file chain is a weaker test of retrieval quality than one requiring the
engine to actually traverse the real call graph).
