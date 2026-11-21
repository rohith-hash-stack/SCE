# Summarizer candidate v5: wider escalation trigger + effects in the search text

**Outcome: v5 does not qualify on set 6; v2 stays locked and shipped** (`reports/heldout_set6/DECISION.md`,
NOTES.md): V5 vs V2 25 / 16 / 79, sign p = 0.211. Stage 1 PASS; stage 2 CONTINUE (37 / 24).

**Status: implemented, not validated.** A v5 index is used by
`prism.ask` at weight 0 (its pipeline records `"lint": "v5"`).

**Approved** (specification, set 6 repositories and size, the rule below) before any v5 or
set 6 result existed. Motivated by `reports/summary_candidate_v4_stage2/DIAGNOSIS.md`.

## Why

v4 (CANDIDATE_V4.md) did not move retrieval on sets 2-5 (V4 vs V2 18 / 17). The diagnosis: its
trigger reached the gold of 8 of the 36 V2 misses, and its one-sentence rewrites stayed in the
code's vocabulary. Adding the `effects` line v4 already stores to the indexed text gave 33 / 19
against V2 (p = 0.07) on those sets - a hint from data that informed the design, so only a
fresh set can confirm it.

## The pipeline (`prism.summaries.pipeline`, `CANDIDATES["v5"]`)

v4 unchanged (pass 1 = v2; escalation prompt, 60-line body cap, keep-only-if-lint-better), plus:

1. **Wider trigger**: escalate when `restates_name` fires **or** the function has >= 20 lines and
   its summary < 10 words (`long_short`). A rewrite is kept only if it has fewer flags, counting
   `long_short`, than pass 1 - a long function's rewrite must reach 10 words.
2. **Effects in the search text**: for every escalated function (kept rewrite or not), the
   summary field indexes `summary + " " + effects` (`prism.summaries.index.search_text`, used
   by `NLQIndex(summaries="auto")` when the pipeline sets `index_effects`).
3. **Built from v4**: `prism summarize --candidate v5` on a v4 index reuses its escalations
   (same prompt and cap) and escalates only the new `long_short` functions; v5 -> v2 starts over.

Tests: `tests/test_summary_v5.py`. Budget on the sets 2-5 repositories: about 300 new
escalations (10% of the pool) on top of v4's 886.

## Stage 1 - conversion on Kaggle

`kaggle/summary_candidate_v5_cell.py` part A (output `reports/summary_candidate_v5/stage1`): the ten v4 stage 1 indexes, converted with
`stage1.py run --steps v2,v4,v5`. Gates (v4's, unchanged), pooled: `restates_name` <= 10%, every
v2 flag <= 5%, nothing lost, every entry checked. `long_short` after escalation is reported,
not gated (a rewrite that stays short is rejected and pass 1 stays, as for `restates_name`).

## Stage 2 - sets 2-5 (diagnostic only)

`stage2.py --candidate v5`: arms BASE, V2, V4, V5. Stop rule: V5 is dropped before set 6 is
scored if, pooled, more questions are worse than better against V2.

## Stage 3 - set 6 (the decision)

`harness/experiments/heldout_retrieval/set6/`: 120 questions, 30 each on `encode/httpx`,
`Textualize/rich`, `elysiajs/elysia`, `oclif/core`; written from source and frozen
(`questions_sha256` 66905f75...) before any engine, search, ranking or summarizer ran on them.
Summaries: part B of the Kaggle cell - v2 fresh with the locked pipeline, then v4 and v5 by
escalation only. Scored once with `set6/score.py`, after stage 2 continues.

> **Rule (approved).** V5 qualifies if (1) V5 beats V2: more questions better than worse with
> a two-sided sign test p < 0.05, and recall@5 at most 0.02 below V2; and (2) V5 beats BASE
> under set 4's rule (p < 0.05, recall@5 within 0.02). Otherwise v2 stays locked. V4 is scored
> as a diagnostic only.

Shipping v5 means `locked_pipeline()` records it, the weight rule accepts it, and v2 indexes are
upgraded by `prism summarize --candidate v5` (escalation only).

Reserved: if v5 does not qualify, the next idea is generating developer-style questions per
function (doc2query) - not built, and it would need its own fresh set.
