# R4: named, not scheduled (amendment R3-1, item 7, as resolved by R3-2)

R4 is "user query → plan": the agent starts from a natural-language request. It needs its own pre-registration.

## Base+delta and divergence detection (recorded 2026-11-06; documentation only, no build work)

"R4 will investigate base+delta (the pattern implemented by trail-code, codegraph, code-review-graph,
gitcortex-indexer, and CodeQL) as the PRISM architecture for continuous divergence detection. The prior-art
investigation (this session's report) documents how each system solves the problem, including codegraph's bug
history (#899 dropped callers, CG-33 4.3% edge drift from cold build). R4's pre-registration will select an
approach, define the equivalence invariant, and test it against cold builds. Base+delta is not tested in R3."

**Notes carried from that investigation, for R4's design:**
- **trail-code** avoids incremental edges. Extraction is memoized per file, and resolution reruns over the whole
  node set, so incremental output is byte-identical to a cold build.
  - PRISM's equivalent stages (pass 2, call linking, and tagging) are 69-91% of build time. Memoizing them is the
    cheaper road to speed.
- **codegraph** maintains edges incrementally. It snapshots incoming cross-file edges before deleting a changed
  file's nodes, re-pairs them by kind and name, and re-opens references for re-resolution.
  - Both fixes followed measured drift.
- **gitcortex-indexer** defers only outgoing cross-file edges (`GraphDiff.deferred_*`).
  - Incoming edges into a changed file are not visibly handled.
  - Its diffs are commit to commit, so uncommitted edits are not seen.
- **None of the five** has a parse gate or divergence warnings. R3-5's gate and a break check on applied edits are
  PRISM's own additions.
- **The break check** (`prism.impact.breaks.BreakChecker.check`) takes proposed text. On applied edits it needs the
  current text as `new_code`, and callers whose own edits are reflected. That is the piece a delta must supply.
- **Measured costs (2026-11-05):**
  - full reindex: 2-43 s on R3's pool;
  - incremental tree-sitter re-parse: 7.5-14.5× faster per file, but parsing is only 2.7-5.6% of a build;
  - break check: about 0.3-0.6 ms per caller, given an index.
