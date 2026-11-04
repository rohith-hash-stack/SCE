# R2 amendments (draft, written before any R2 task is frozen or any R2 call; **not hash-committed**)

R2's pre-registration is Amendment R1-A, A6 (`reports/suite/REPORT.md`). These amendments are folded into R3's
hash-commit after R2 completes. They change no rule except the seed count in R2-1, and only if confirmation item A
is answered "2 seeds".

## Amendment R2-1: seeds (~~conditional: applies only if the team answers item A = 2 seeds~~ → **in force: the team confirmed 2 seeds, 2026-11-04**)

- **Change:** A6's development seeds, ~~42, 1001 and 2002~~ → **42 and 1001**, for the development set **and** the
  held-out set. The per-task value is the mean over the two seeds.
- **Calls:** ~~4,080~~ → **2,720**. That is (192 development + 80 held-out tasks) × 5 versions × 2 seeds.
- **Why:**
  - it parallels R3, which runs at 2 seeds (amendment R3-1, item 1);
  - it saves about one Kaggle session;
  - stability is adequate at 192 + 80 tasks. The TOST and Wilcoxon run over tasks, and seeds only average within a
    task.
- **Per-seed reporting** (as R3-1, item 4): every R2 result is also reported per seed. "Dead weight at every seed",
  which R3-2 uses for the tool freeze, then means both seeds.
- **Unchanged:**
  - the ±0.03 equivalence margin (TOST);
  - 80 held-out tasks per version;
  - the 5 versions (full, −4, −6, −7, −4+7), each padded to the full context's real token count with BM25 code;
  - the held-out repositories, as approved;
  - Holm, the bootstrap, the truncation rule and the labels;
  - "load-bearing" and "dead weight" as A6 defines them.

If item A is answered "3 seeds", R2-1 is void, and R2 runs at seeds 42, 1001 and 2002, with about 4,080 calls.

## Amendment R2-2: scope and held-out selection (fills open items; no rule changed)

- **Types:** only forward enters R2. A6 admits a type only if its R1 label is `pass` or `below_floor`. R1 labelled
  forward `pass`, blast `not_shown` and newfn `not_shown`. So the blast and newfn parts of R2-H4 and R2-H6 are not
  run, and are reported as "not entered (R1 label)".
- **Held-out repositories:** as approved in `HELDOUT_PROPOSAL.md`, with the substitutions the mining check made
  (`MINING_CHECK.md`).
- **Held-out tasks:** the R1 forward pipeline, unchanged. That is `change_history.mine --forced` over the full
  history, then `suite.forced`'s eligibility filter.
- **5 tasks per repository**, drawn per repository in the seeded order `suite.forced` uses (seed 5501):
  - up to 2 easy, 2 medium and 1 hard (the suite's 7 : 7 : 6 quota, scaled to 5);
  - a short tier is filled from the others: hard, then medium, then easy.
- **Repository HEADs:** pinned at the commits recorded in `MINING_CHECK.md`.

## Amendment R2-3: held-out contingency (**committed after the failed mining check, before the resupply check**)

**Timing:**
- This contingency was **not** drafted or committed before the first mining check (`MINING_CHECK.md`, commit
  `7099f2cc`). No earlier version exists in this repository's history.
- It is committed now, after that check failed (Python 45 of 50, TypeScript 0 usable), and **before** the resupply
  check on the candidates below is run.
- Its thresholds were set by the team, knowing the first check's result.

**Resupply candidates** (R1's forward miner and filter, unchanged, full history, no model calls):
- TypeScript: `mobxjs/mobx`, `immerjs/immer`, `ReactiveX/rxjs`, `pmndrs/zustand`, `sinclairzx81/typebox`,
  `effect-ts/effect`;
- Python: `pallets/itsdangerous` or `jazzband/tablib`, as named by the team. Both were already screened in the
  first check: 2 and 4 qualifying tasks.

**Counting (fixed now):**
- A repository counts only with **at least 5 qualifying tasks**, and contributes **5** (the locked 5-per-repository
  rule).
- TypeScript usable = 5 × the TypeScript repositories reaching 5, across the first check and the resupply.
- The raw qualifying total (17 from the first check, plus the resupply) is reported beside it, but does not choose
  the branch.

**Branch, chosen mechanically by the yield** (not by team preference after seeing it):

| branch | condition | action |
|---|---|---|
| 1 | TypeScript usable ≥ 30, and held-out total 80 at 50/30 | build R2's harness at 2 seeds; run |
| 2 | TypeScript usable ~~20-29~~ → ≥ 20, and held-out total ≥ 62, and not branch 1 | add a power note to R2's pre-registration for the smaller confirmation set; build; run |
| 3 | TypeScript usable < 20, or held-out total < 62 | do not build. Report; the team chooses among the options in `MINING_CHECK.md`. |

**Python:** 9 repositories at 5 = 45, unless the resupply adds one reaching 5. The held-out total is Python usable
plus TypeScript usable.

**Correction to the branch table** (2026-11-04, after effect reached 5 and before typebox's and rxjs's yields were
known):
- As first written, TypeScript usable = 30 with a held-out total below 80 (Python at 45) fell in no branch.
- Branch 2 now covers it. "≥ 20, total ≥ 62, not branch 1" is the only reading consistent with the table's intent.
- **At the time of this correction:**
  - TypeScript usable was 20 (immer, zustand, mobx, effect), and Python usable 45, so the total was 65;
  - typebox and rxjs were still being mined.

## R2-3, branch 2: power note (2026-11-04; written before any R2 call; no rule changed)

**Branch:** 2, fixed mechanically.
- TypeScript usable ≥ 20 (immer, zustand, mobx, effect), and Python usable 45.
- The held-out size is 65, 70 or 75, as typebox and rxjs finish.
- It is still 50/30 in intent; the actual mix is 45 Python against 20-30 TypeScript.

**Power (normal approximation; per-task SD of the full-minus-section difference unknown before R2, so shown over a
range):**

| per-task SD | TOST at ±0.03, true difference 0: development (192) | held-out 65 | held-out 80 | same direction on held-out, true drop 0.05: 65 / 80 |
|---|---|---|---|---|
| 0.15 | 0.74 | 0.00 | 0.11 | — |
| 0.20 | 0.34 | 0.00 | 0.00 | 0.98 / 0.99 |
| 0.30 | 0.00 | 0.00 | 0.00 | 0.91 / 0.93 |
| 0.41 (R1 forward, PRISM vs pad) | 0.00 | 0.00 | 0.00 | 0.84 / 0.86 |

**What this means, stated before the run:**
- **"Dead weight" on the held-out set is essentially unreachable** at the locked ±0.03 margin, at 65-75 tasks and
  also at the full 80. A 90% interval narrower than ±0.03 needs a per-task SD near 0.1 at 80 tasks.
  - So R3-3's route (a) will very probably prune nothing.
  - **A held-out TOST that fails is reported as "equivalence not shown (underpowered)",** never as evidence that a
    section matters.
- **"Load-bearing" and R3-3's route (b), harmful,** need only the direction to replicate on the held-out set. That
  has power 0.84-0.99 for a true change of 0.05. These routes remain informative.
- The development set's TOST is informative only if the per-task SD is about 0.15 or less.

## R2-3: branch classification, recorded precisely (2026-11-04; team-confirmed; no rule changed)

- **Classification:**
  - "Branch 1 on TS count (≥30 met at exactly 30), branch 2 on total size (75 < 80). R2-3's branch 2 outcome
    applies: run at achieved mix, no rules relaxed, power note added. The classification is boundary-by-count,
    branch-2-by-total."
- **Added to the power note:**
  - "Held-out set is 75, not 80. Dead-weight equivalence test at ±0.03 is underpowered. R2's design-gate value
    rests on (a) the harm test under R3-3 and (b) catching rendering bugs before R3 spends 10–15k calls."
- **The JavaScript files in the TypeScript slots: team confirms keep.**
  - "5 of the 30 TS-slot tasks are JS files (immer early history, zustand rollup.config.js). They pass the
    unchanged miner and filter. Kept under the R2-2 draw, not removed after seeing the draw. If dropped, TS count is
    25, still branch 2. Team confirms keep; the protocol outcome is unchanged either way."
- **R3-3 verified:** committed in `2b321db4` (2026-11-04 01:52 UTC). That was before the resupply check, the held-out
  freeze (`80f78aba`), any harness code and any R2 call.

## Amendment R2-4: the contexts are built with the current code, for both sets (2026-11-04; before any R2 call)

- **Why:** PRISM's code changed after R1's development contexts were frozen at `793c40a`.
  - `71b975ce` digest v2 added the signature fact.
  - `c4455ee0` and `7e72f8ad` index only git-visible files.
- The held-out contexts were mined with the current code. A6 says no section, wording or budget changes between the
  development and held-out sets, so both must come from the same code.
- **The choice: the current code for both,** for three reasons:
  - R3 runs the current code, and R2 freezes R3's tools;
  - R2's bug check must exercise the renderer R3 will use;
  - the held-out set already uses it.
- **Effect measured:** every development context differs from R1's frozen one by exactly the digest v2 line
  (`L<n> parameters (...)` in the anchor's digest). The build records this per task (`stored_context_equal`).
- **A determinism check on the held-out set:** each context rebuilt from the same code must equal the mined one byte
  for byte, or the build stops.
- **What this does not change:** R1's results and R1's frozen contexts. R2's FULL version is not R1's PRISM prompt
  byte for byte (it carries digest v2), so R2's FULL numbers are not pooled with R1's PRISM numbers.
- No other rule changes: the budget (6,000), tests excluded, the prompt text, the scorer, the answer key and the
  model settings are all R1's.

## Amendment R2-5: analysis details (2026-11-04; before any R2 call; `harness/experiments/r2/analyze.py`)

These fill in what A6 and R3-3 leave open. They change no margin, floor, α or seed.

| item | fixed as |
|---|---|
| measure | forward callers found (`found / changed`, R1's H-TM1 measure), per task the mean over seeds 42 and 1001 |
| removal effect | drop = FULL − version, per task |
| directional test | Wilcoxon signed-rank, two-sided; bootstrap 95% interval (10,000 resamples, seed 5501) |
| TOST | two one-sided paired t-tests against ±0.03, p = the larger; a 90% bootstrap interval reported beside it |
| A6 family (development) | Holm across R2-H4 (directional, NO4), R2-H6 (TOST, NO6), R2-H7 (TOST, NO7) and R2-H47 (p = the larger of the Wilcoxon p of NO47 vs NO4 and NO47 vs NO7; both intervals must lie above 0) |
| R3-3, "Holm-adjusted within forward" | **directional:** Holm across the three single-section removals (4, 6, 7). **Equivalence:** Holm across their three TOSTs. The pair NO47 is not a section and never prunes. |
| load-bearing (A6) | development: directional adjusted p < 0.05, interval above 0, drop ≥ 0.05; held-out mean drop > 0 |
| dead weight (R3-3a) | development: equivalence adjusted p < 0.05; held-out TOST p < 0.05; TOST p < 0.05 at each seed, on both sets |
| harmful (R3-3b) | development: directional adjusted p < 0.05, interval below 0, mean drop < 0 at each seed; held-out mean drop < 0 |
| truncation (A1) | per comparison, tasks cut at the answer cap in either version at any seed are excluded; more than 20% makes the comparison invalid, and an invalid comparison never prunes |
| unstable | the mean drop has opposite signs at the two seeds |
| language | reported by repository and by anchor file (the JavaScript files in the TypeScript slots) |
| sections 1, 2, 3, 5, 8 | not removed in R2 (A6, order step 3, is not run), so never pruned |
