# Summarizer candidate v4: escalate name-restating summaries to the source

**Status: implemented, unvalidated. v2 stays locked and shipped.** A v4 index
is used by `prism.ask` at weight 0 (its pipeline records `"lint": "v4"`, so it
is not the locked pipeline).

**Stage 1: PASS** (`reports/summary_candidate_v4_stage1/STAGE1.md`, NOTES.md):
restates_name 24.8% -> 5.8% pooled, 676 of 886 escalations kept, v2 flags
<= 0.1%, nothing lost. The Stage 1 gates were the proposal below (the cell ran
before explicit approval; approved afterwards). The set 6 rule below is approved.

**Stage 2: CONTINUE** (`reports/summary_candidate_v4_stage2/STAGE2.md`): pooled over sets 2-5
V4 vs V2 18 better / 17 worse / 153 ties (sign p = 1.0, recall@5 +0.003, MRR +0.007), so the
stop rule (more worse than better) does not fire. V4 and V2 both beat BASE (74/20 and 73/25).
Only 31 of the 188 questions have an escalated gold symbol; 35 questions move between V2 and V4
(12 of them with escalated gold: 7 better / 5 worse). Sanity check: set 5 V2 vs BASE reproduces the
recorded set 5 C vs B (6 / 6 / 18, recall@5 0.683 -> 0.767).

**Power note for set 6.** At this rate about 1 in 5 set 6 questions moves (about 11 of 60); a
two-sided sign test with 11 moved questions needs 10 / 1 to reach p < 0.05.

## Why

v3 / v3-name failed stage 1 (CANDIDATE_V3.md): repair rewords a thin summary
but cannot add behaviour the card does not show (`reports/summary_candidates_stage1/NOTES.md`):
thin summaries have an empty card 40% of the time (20% for the rest). v4 gives
the model the information instead of asking it to try harder - but only for
the summaries that need it (bodies: median 7 lines, p90 29).

## The pipeline (`prism.summaries.pipeline`, `CANDIDATES["v4"]`)

1. **Pass 1 = v2**, unchanged: same model, card, `SUMMARY_PROMPT`, v2 lint and
   `REPAIR_PROMPT`. A v4 index is built from a v2 one without re-summarizing
   (`prism summarize --candidate v4`); v2 summaries whose v2 repair already
   ran are not repaired again.
2. **Trigger**: the `restates_name` lint rule (at most 2 content words beyond
   the function's / class's name and generic filler) on the pass-1 summary.
3. **Pass 2 (escalation)**: for flagged functions only, `ESCALATION_PROMPT` with
   the card plus the function's source, capped at `BODY_CAP` = 60 lines (a
   longer body ends with "... (N more lines)"). The reply is reasoning-first
   JSON `{"effects": "<what it checks, computes, changes, sends or returns>",
   "summary": "<one sentence>"}`. The new summary is kept only if it has no v2
   lint flag and strictly fewer flags (v2 + restates_name) than pass 1 -
   escalation cannot make a summary worse by the lint's measure; otherwise
   pass 1 stays.
4. **Tracking**: every escalated entry records `escalation_tried`; a kept one
   also `escalated: true`, `pass1` (the v2 summary) and `effects`. The index
   pipeline records `lint: "v4"`, `escalation_sha` (the prompt) and
   `body_cap`. When a function's source changes (`source_sha`) but its card
   key does not, the entry reverts to `pass1` and is escalated again from the
   new body. v4 -> v2 starts over (a locked v2 index never holds v4 text).

Tests: `tests/test_summary_v4.py`, `tests/test_stage1_conversion.py`.

Budget (stage 1 v2 indexes): 886 of 3,569 summaries flagged (24.8%), one call
each, <= 60 body lines.

## Stage 1 - conversion on Kaggle (proposed gates)

`kaggle/summary_candidate_v4_cell.py`: the ten v2 indexes of the v3 stage 1
(`reports/summary_candidates_stage1/<repo>/v2.json`, same engine - no `src`
change since) are installed unchanged and converted with `stage1.py run
--steps v2,v4`. Proposed gates, pooled over the ten repos, after escalation
(the v3 gates for the same flag):

- `restates_name` <= 10% (pass 1: 24.8%; v3-name's repair reached 10.3%);
- every v2 flag <= 5%; no summary lost; every entry `checked`.

Also reported (not gates): how many escalations were kept, before/after
samples, and the `effects` of rejected ones.

## Stage 2 - sets 2-5 (diagnostic only)

As in CANDIDATE_V3.md: V4 vs V2 at w = 0.5; dropped if more questions worse
than better than V2, pooled.

## Stage 3 - fresh set 6 (the decision)

Set 6 as approved in CANDIDATE_V3.md (60 questions; httpx, rich, koa, ky;
written and frozen before anything runs on them); arms **BASE**, **V2**,
**V4**. With one candidate left, the proposed rule:

> **Proposed rule.** V4 qualifies if (1) V4 beats V2: more questions better
> than worse with a two-sided sign test p < 0.05, and recall@5 at most 0.02
> below V2; and (2) V4 beats BASE under set 4's rule (p < 0.05, recall@5
> within 0.02). Otherwise v2 stays locked.

(The v3 rule's 0.025 was 0.05 split over two candidates.) Shipping v4 means
`locked_pipeline()` records it, the weight rule accepts it, and v2 indexes are
upgraded by `prism summarize --candidate v4` (escalation only).
