# Phase 2 results (800 runs, analysed 2026-11-22)

**Design:** pre-registered in `AMENDMENTS.md`, "Phase 2, 4 arms": 20 tasks × 10 seeds × 4 arms, devstral-small-2,
code `ecdf23e0`. The analysis is `rebaseline_report`, design `phase2`, on all 10 shards merged:
`reports/r4/phase2_final/report.json`.

**Health:** every shard passed. Across the 800 runs there were 0 infrastructure deaths, and parse errors stayed
between 0.06% and 1.17% of turns per shard.

## Primary analysis (pre-registered)

Per task, the mean score over 10 seeds. Wilcoxon signed-rank test, two-sided, Holm-corrected over three comparisons.

| Comparison | Mean score | Difference | 95% bootstrap CI | Wilcoxon p (Holm) | Tasks favouring (arm / BASELINE / tie) |
|---|---|---|---|---|---|
| PRISM − BASELINE | 0.725 vs 0.695 | +0.030 | −0.070 to +0.155 | 0.919 (1.0) | 5 / 6 / 9 |
| INJECTED − BASELINE | 0.675 vs 0.695 | −0.020 | −0.135 to +0.120 | 0.418 (1.0) | 2 / 7 / 11 |
| PRISM_ONLY − BASELINE | 0.740 vs 0.695 | +0.045 | −0.035 to +0.165 | 0.953 (1.0) | 3 / 4 / 13 |

**No arm differs significantly from BASELINE.** Solve-level McNemar tests agree: p = 0.77, 0.64 and 0.22.

**Descriptive:**
- INJECTED − PRISM: −0.050 (−0.135 to +0.030).
- PRISM_ONLY − PRISM: +0.015 (−0.025 to +0.055).
- Derived tasks alone (15): PRISM +0.047, INJECTED −0.013, PRISM_ONLY +0.060 against BASELINE, none significant.
- Controls alone (5): PRISM −0.02, INJECTED −0.04, PRISM_ONLY 0.00.

## Per task (solved runs of 10)

| Task | BASELINE | PRISM | INJECTED | PRISM_ONLY |
|---|---|---|---|---|
| p2-rename-01 (werkzeug, 27%) | 4 | 5 | 2 | 4 |
| p2-rename-02 (litestar, 3%) | 0 | 0 | 0 | 0 |
| p2-rename-03 (fastmcp, 4%) | 1 | 2 | 0 | 0 |
| p2-rename-04 (pydantic-ai, 6%) † | 0 | 0 | 0 | 0 |
| p2-rename-05 (instructor, 5%) | 3 | 0 | 2 | 1 |
| p2-signature-01 | 10 | 10 | 10 | 10 |
| p2-signature-02 | **0** | **10** | **10** | **10** |
| p2-signature-03 | 10 | 10 | 10 | 10 |
| p2-signature-04 | 10 | 10 | 10 | 10 |
| p2-signature-05 | 10 | 9 | 4 | 9 |
| p2-move-01 | 10 | 8 | 10 | 10 |
| p2-move-02 | 10 | 9 | 7 | 10 |
| p2-move-03 | 10 | 9 | 9 | 10 |
| p2-move-04 | 10 | 10 | 10 | 10 |
| p2-move-05 | 6 | 9 | 8 | 9 |
| r4s-delete-05 (control) | 10 | **4** | 10 | 8 |
| r4s-delete-08 (control) | 10 | 10 | 10 | 10 |
| r4s-delete-09 (control) | 10 | 10 | 10 | 10 |
| r4s-delete-10 (control) | 10 | 10 | 10 | 10 |
| r4s-feature-02 (control) | 5 | 7 | 3 | 6 |

Percentages are the tasks' grep precision.

† A task-construction error, found after the analysis (below).

## What the data say

1. **PRISM's tools did not raise the solve rate on these tasks**, in any arm.
2. **PRISM_ONLY was not worse than BASELINE.** Without `grep` or `read_file` it scored 0.740 against 0.695, and the
   interval's lower end is −0.035. PRISM's tools carried the work: 11 PRISM calls a run, 0.1 exploration turns. The
   test was two-sided for a difference; a non-inferiority claim was not pre-registered.
3. **The tasks did not separate the arms.**
   - **Ceiling:** 9–13 of the 20 tasks tie in each comparison. Most move and signature tasks are 10/10 in every arm.
   - **Floor:** renames are near 0 in every arm.
   - **The low-grep-precision renames** (3–6%) were the hardest for every arm, PRISM's included.
4. **Renames fail by running out of turns, not by missing places.**
   - 185 of 200 rename runs end at the turn cap or the budget, with 2–8 uses of the old name left.
   - The limit of 30 uses per target did not account for one `edit_file` call per span within 28 turns: a 30-use
     rename needs about as many edits as the run has turns.
5. **One task separates the arms clearly:** `p2-signature-02` (fastmcp `Context.get_state`). BASELINE 0/10, every
   PRISM arm 10/10.
6. **delete-05's PRISM gap persists.** PRISM solved 4/10 against BASELINE's 10/10, as in Phase 1. The C1 edit warning
   fired in those runs; it did not change the result.
7. **Cost:** PRISM's arms used more tokens per run: BASELINE 78K, PRISM_ONLY 112K, PRISM 121K, INJECTED 165K.
   INJECTED hit the 300K budget in 11 of 200 runs (Phase 1: 30 of 200); the other arms never did. Turns per run were
   the same in all arms (22–23).

## The task-construction error (p2-rename-04)

**What happened:**
- The answer key for renaming `SpanTree.find` renamed every `find` token in the target's dependent files. That
  included unrelated calls: `soup.find()` (BeautifulSoup) and `content.find(...)` (a string method).
- The repository's tests do not run those lines, so validation passed.
- The completeness check then compared every run with that answer key. All 40 runs, in every arm, were scored as
  "the old name `find` is still used in 7 place(s)" for correctly leaving those calls alone.

**The effect:** the task scores 0 in every arm, a tie. The Wilcoxon test drops ties, so the primary p-values are
unchanged without it (PRISM 0.919, INJECTED 0.418, PRISM_ONLY 0.953).

**The other answer keys,** read line by line:
- the other four renames change only uses of their targets;
- the signature keys only rewrite calls to the target;
- move keys only rewrite imports that resolve to the target's module.

**The lesson:** a mechanical rename of a method needs the use sites PRISM resolved, not every token with the name.
