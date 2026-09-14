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
2. **Verify every pipeline stage is transitively reachable from the
   seed, not just real** (added after Milestone 2 batch 3's review
   caught two pipelines with a real-but-unreachable terminal stage -
   see G40 in `docs/design_formalism.md`; *corrected* after two
   Calibration-round reseed attempts and a full-corpus audit found the
   original version of this check - direct edges between *consecutive*
   pipeline symbols - was itself wrong. Most real pipelines are
   sibling-under-a-common-caller sequences reflecting execution order,
   not a literal call chain: `_clean_fields` and `_clean_form` are both
   called by `full_clean`, not one by the other. Requiring a direct
   edge between every consecutive pair rejected 15 of 20 real,
   perfectly reachable T02 tasks on a full-corpus re-audit). Existing
   in the symbol table is necessary but not sufficient - a symbol with
   zero real path from the seed is exactly as useless to a retrieval
   engine as a symbol that doesn't exist at all. For task `T` with seed
   `S` and pipeline `P = [p1, ..., pn]`:
     1. `p1 == S` - the seed is the pipeline's first symbol.
     2. Every symbol exists in the symbol table (item 1 above).
     3. Every `pi` (`i >= 2`) is **transitively** reachable from `S` -
        a directed path of *any length* exists in `builder.graph`,
        using only `CALLS`, `INSTANTIATES`, `EXTENDS`, `IMPLEMENTS`,
        `OVERRIDES`, or `EMBEDS` edges. Check directly (`networkx.
        has_path(allowed_relations_subgraph, S, pi)`, or equivalent BFS
        over `builder.graph.out_edges`/`in_edges` filtered to those six
        relations) - never assume from reading source alone, and never
        require a *direct* edge between consecutive pipeline entries;
        pipeline order reflects real execution sequence at the seed's
        own call site, not a call-chain requirement between neighbors.
        `READS_STATE` edges (an attribute read/write, e.g. `self.x = ...`
        in one method, `self.x` read in another) do not count toward
        reachability at all - a pipeline that only connects via
        `READS_STATE` is genuinely unreachable and must be rejected or
        re-seeded at a real, structurally-connected point instead
        (cross-method instance-state data flow is v1.2 scope; see
        `reports/pilot/methodology.md`).
        `OVERRIDES` is a real, traversable relation, not merely
        structural metadata: confirmed directly in
        `src/prism/graph/concrete_builder.py`'s `TRAVERSABLE_RELATIONS`
        (which includes `OVERRIDES`) and `calls_graph` property (the
        exact subgraph `prism.slicer`'s distance engine and the
        knapsack packer traverse), and in
        `src/prism/slicer/distance.py`'s `RELATION_STRUCTURAL_WEIGHT`
        (`OVERRIDES: 0.90` - a real Dijkstra hop-cost, more expensive
        than a plain call but real and reachable). A `super().method()`
        call - a real Python pattern - shows up in `builder.graph` as
        an `OVERRIDES` edge to the parent method, and that edge is
        traversable; do not exclude a pipeline stage just because its
        connecting edge is `OVERRIDES` rather than `CALLS`.
        Confirmed separately (G40): this codebase's call resolution
        does **not** currently follow `EXTENDS` to find an inherited
        method when a `self.method()` call site's simple-name
        resolution lands on a subclass that doesn't itself define that
        method - the `EXTENDS` edge can be real while the method call
        still dangles to a nonexistent symbol (distinct from
        `OVERRIDES`, which concrete_builder.py adds explicitly for
        every directly-declared method that overrides a base method,
        not inferred from an unresolved call site). Don't assume
        inheritance "just works" for a dangling call site; check the
        actual edge.
     4. Every symbol a path in check 3 passes through is what the real
        call site *actually* resolves to on `builder.graph`, not merely
        a same-named method on some other class the source's own types
        would suggest. Confirmed directly (G41): a `self.<attribute>.
        <method>()` call where `<method>` exists on more than one class
        in the codebase can resolve to the *wrong* class's definition -
        a real symbol, on a real reachable edge, but not the one the
        source's own types say should run. Two confirmed instances:
        `self.nodelist.render(context)` resolving to `Template.render`
        instead of `NodeList.render`, and `self.filter_expression.
        resolve(context)` resolving to `Variable.resolve` instead of
        `FilterExpression.resolve` - see G41 in
        `docs/design_formalism.md`. Read the source and check the
        receiver's actual declared/assigned type, not just whether *a*
        same-named symbol is reachable.
   If any stage fails check 2 or 3, trim the pipeline to its last
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
