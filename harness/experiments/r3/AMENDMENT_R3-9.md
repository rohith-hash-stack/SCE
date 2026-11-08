# Amendment R3-9: Family B's α after Family N's removal (2026-11-08, before any R3a run)

Written and committed before any R3a run starts. No R3a model call has been made. R3a's results do not exist.

**The question.** Amendment R3-1, item 3, tests Family B at a local α of 0.025 at its interim look, and at its final
look until Family N is rejected. The reason is that the two families share one Holm family (Maurer and Bretz, 2013).
Family N was dropped to R3b (decision of 2026-11-08, logged in `IMPLEMENTATION_LOG.md`). Does Family B keep 0.025?

- **Reading A:** keep α 0.025 as written. This is conservative: it treats Family N as if it were still in the
  family.
- **Reading B:** apply Holm to the family as it now exists, Family B alone. Holm over one hypothesis is α itself:
  0.05.

**Decision: Reading B.** Family B is tested at α 0.05.

**Why:**
- Holm's first-step α of 0.025 for Family B existed only because a second hypothesis, Family N, shared the error
  budget. With one hypothesis in the family, the family-wise error rate is controlled at 0.05 by testing at 0.05.
  That is the intent of the rule ("control error across the family").
- R3b is a separate round with its own pre-registration and its own α. It is not a co-test within R3a.
- The numbers are not new. R3-1's table already gives the 0.05 row ("after Family N is rejected"), computed by the
  same spending function. Reading B selects that row from the start.

**What it does not change:**
- the spending function (Lan-DeMets, O'Brien-Fleming type, two-sided);
- the one interim look at 40 of 80 tasks;
- the test statistic (Wilcoxon signed-rank on per-task means over the two seeds);
- the bootstrap condition;
- the +0.10 floor, the labels, the `invalid` checks, the per-seed reporting and the unstable flag;
- no futility stop.

**Caveat, recorded:**
- A claim that combines R3a and R3b (for example, "PRISM helps in both families") is a joint claim. It must be
  corrected across the two rounds. R3b's pre-registration should state how.
- R3a's own claims are about Family B alone.

## Thresholds (recomputed by the frozen analysis code, `analyze.interim_boundaries`)

| Look | Information fraction | Two-sided p threshold | z |
|---|---|---|---|
| Interim, at 40 of 80 tasks complete | 0.5 | **p < 0.0031** | 2.96 |
| Final, at 80 tasks | 1.0 | **p < 0.0490** | 1.97 |

For comparison, Reading A would have given an interim p < 0.0008 (z 3.34) and a final p < 0.0247 (z 2.25).

If the interim lands at another fraction, the boundaries are recomputed from the same spending function at α 0.05
for the fraction actually observed (R3-1, unchanged).

## Implementation (analysis code; the rule above is R3-1's)

Before this amendment, `analyze.py` had the boundary function, but nothing called it: the interim look was not
wired, and the final test used a flat 0.05. Both now follow R3-1 with this α:
- `LOCAL_ALPHA = {"B": 0.05}`, `INTERIM_TASKS = {"B": 40}`, `PLANNED_TASKS = {"B": 80}`.
- `interim_look`:
  - takes the first 40 tasks of the frozen order, with every run present;
  - runs Wilcoxon on the per-task means and stops in either direction if p < the interim boundary;
  - takes the label from the usual table at that boundary;
  - runs the `invalid` checks at the look;
  - reports "continue" otherwise.
- `threshold("B")` = the final boundary, 0.0490. The final label uses it.
- `--interim` in the CLI. The R3a cell runs it each session; it reports "not ready" until 40 tasks are complete.
- Tests: `tests/test_r3_interim.py` (the boundaries, a stop, a continue, not ready).
