# R2 amendments (draft, written before any R2 task is frozen or any R2 call; **not hash-committed**)

R2's pre-registration is Amendment R1-A, A6 (`reports/suite/REPORT.md`). These amendments are folded into R3's
hash-commit after R2 completes. They change no rule except the seed count in R2-1, and only if confirmation item A
is answered "2 seeds".

## Amendment R2-1: seeds (**conditional: applies only if the team answers item A = 2 seeds**)

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
