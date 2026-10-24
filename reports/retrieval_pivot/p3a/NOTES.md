# P3a (exploratory) - outcome and notes

Run: `kaggle/p3a_cell.py` at code a952a77; 16 tasks x 10 arms x 10 seeds = 1,600 cells, all `ok`
(no leak, error or timeout). Statistics: `P3_STATS.md`, `p3_stats.json`. P3a decides nothing
(P3.md): the 4 Python forward tasks helped design R1, and 8 forward tasks cannot reach the rule's
threshold - a sign test over 8 tasks needs 7 / 0 or 8 / 0.

## Forward blast radius, 8k (mean F1 over the 8 forward tasks x 10 seeds)

| arm | F1 | precision | recall | fabrication | context tokens |
|---|---|---|---|---|---|
| R0_A0 | 0.360 | 0.528 | 0.315 | 9.9% | 3,787 |
| R1_A0 (reach) | 0.477 | 0.539 | 0.508 | 2.9% | 7,170 |
| R1_A1n (reach + outer signatures) | 0.478 | 0.510 | 0.537 | 3.9% | 5,072 |
| R1_A4 (reach + helper profiles) | 0.463 | 0.516 | 0.498 | 2.6% | 7,218 |
| R1_A5 (reach + both) | 0.465 | 0.498 | 0.528 | 4.4% | 5,126 |

The fixed-sequence rule, as a diagnostic: step 1 (reach) is 6 better / 1 worse / 1 tie, p = 0.125,
mean F1 +0.117 - in the expected direction, but it does not pass with 8 tasks, so the chain stops.

## What the run shows

1. **Reach helps where the production pack is thin.** The gain comes from three tasks: express_02
   0.00 -> 0.64 (the pack delivered 0 of 11 gold callers; the fill 11 of 11), express_01 0.38 -> 0.52
   and trpc_02 0.40 -> 0.54. The other five move by at most 0.03.
2. **More context, less fabrication.** Names not in the corpus fall from 9.9% (R0_A0) to 2.6-4.4% of
   all names with reach.
3. **Compression keeps F1 in 29% fewer tokens, but not on every task.** R1_A1n equals R1_A0 (0.478
   vs 0.477) at 5.1k vs 7.2k tokens: higher recall, lower precision. It loses where the code
   matters: django_02 0.744 -> 0.597 (0.485 with profiles too).
4. **4-axis helper profiles do not help.** R1_A4 vs R1_A0: 0.463 vs 0.477; R1_A5 vs R1_A1n: 0.465 vs
   0.478. Mixed per task, no consistent direction.
5. **Backward adaptation, for the first time above the floor - only with full code.** Passes at 8k:
   R0_A0 5 / 80, R1_A0 12 / 80, R1_A4 12 / 80, the compressed arms 0-1 / 80. Concentrated in
   django_02 (R0 5 / 10, R1 9 / 10) and trpc_02 (R1 3 / 10). Implementing a body needs the
   neighbours' code, not their signatures.

## Against the earlier tournament (same tasks, model, seeds; another run and code)

| | tournament `prism_full` | tournament `prism_oracle` | P3 R1_A0 @8k | P3 R1_A1n @8k |
|---|---|---|---|---|
| mean forward F1 | 0.496 | 0.783 | 0.477 | 0.478 |

R1_A0 vs `prism_full`: 4 tasks better / 4 worse. P3's R0_A0 is P1's one-pass rendering of the
production pack, **not** the strongest shipped path. `prism_full` is two-pass: a Turn-1 model call
selects from a manifest. So the reach fill brings a one-pass slice up to `prism_full`'s level
without the extra model call, but does not beat it. Both sit far below the oracle context (0.78):
the headroom is in what is selected, not in how it is compressed.

## Implications for P3b (need approval before P3b is frozen)

- **Baseline.** The rule's step 1 compares R1_A0 with R0_A0. To claim an improvement over what
  Prism does today, P3b should also run `prism_full`, and step 1 should be against it (or both
  pre-registered).
- **Compression and profiles.** On this evidence, compression is a token saving with a fidelity
  risk on code-heavy tasks and kills backward adaptation, and profiles add nothing. P3b could drop
  R1_A4 / R1_A5, which also removes the profiles step from the chain.
- **Backward tasks** are no longer pure floor with full-code arms (django_02, trpc_02); a P3b with
  more of them could grade them as a secondary endpoint.
