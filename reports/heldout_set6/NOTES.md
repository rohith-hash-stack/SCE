# Set 6 / candidate v5 - outcome and notes

**Decision (pre-registered rule, scored once): V5 does not qualify; v2 stays locked.**
`DECISION.md`: V5 vs V2 25 better / 16 worse / 79 ties, sign p = 0.211 (rule needs < 0.05),
recall@5 +0.008, MRR +0.040. V5 vs BASE 40 / 16 / 64, p = 0.0018 (passes, but both parts are required).

## The path to it

- **Stage 1** (`reports/summary_candidate_v5/stage1/STAGE1.md`): PASS - first pass flagged 1,215
  of 3,569 (34%), rewritten 923, restates_name 5.8%, long_short 3.3%, v2 flags <= 0.1%, nothing
  lost, all checked. Deviation: the gate script also applied the default 5% limit to
  `long_short`, which CANDIDATE_V5.md said would be reported but not gated; it is 3.3%, so the
  verdict is the same either way.
- **Stage 2** (`reports/summary_candidate_v5/stage2/STAGE2.md`, sets 2-5, diagnostic): CONTINUE -
  V5 vs V2 37 / 24 / 127 (p = 0.124), recall@5 +0.021.
- **Set 6 summaries** (`reports/summary_candidate_v5/set6`): v2 fresh, v4 and v5 by escalation;
  every index complete and checked; escalations tried / kept - httpx 135 / 103, rich 315 / 235,
  elysia 206 / 145, oclif 145 / 116. Checkouts verified at the frozen pins before scoring.

## Diagnostics (decide nothing)

| per repository | V5 vs V2 | V2 vs BASE |
|---|---|---|
| httpx | 4 / 1 / 25 | 5 / 6 / 19 |
| rich | 2 / 5 / 23 | 7 / 5 / 18 |
| elysia | 13 / 4 / 13 | 14 / 4 / 12 |
| oclif | 6 / 6 / 18 | 11 / 5 / 14 |

- V5's gain is concentrated in elysia; on the other three it is 12 / 12.
- V4 vs V2 20 / 11 (p = 0.15); V5 vs V4 16 / 13 (p = 0.71): the wider trigger plus effects add
  little over v4 here.
- V2 vs BASE on this fresh set: 37 / 20 / 63, p = 0.033, recall@5 +0.058 - the shipped summary
  field helps on set 6 (not a pre-registered check; set 5 had not confirmed it).
- The direction matches every earlier set (V5 ahead of V2 on sets 2-5 and 6, MRR +0.04 here),
  but the effect is small relative to the power of 120 questions: 41 questions moved; p < 0.05
  needed about 28 / 13.

## Anomaly found (separate bug)

Rich's v2 run built cards for 905 of 912 functions; the v4 run on the same code built all
912, summarizing 7 `@overload` stubs (`#3` variants, e.g. `progress.Progress.open#3`) that v2
lacks. Card building is not deterministic for overload variants. None of the 7 is a gold or
decoy; scoring V5 without them gives the identical 25 / 16 / 79.
