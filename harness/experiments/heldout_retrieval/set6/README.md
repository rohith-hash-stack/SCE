# Held-out set 6 (frozen, version 1)

The decision set for summarizer candidate v5 (harness/experiments/purpose_search/CANDIDATE_V5.md).
120 questions on four repositories no earlier set, corpus or tuning run used, written from the
source at the pinned commits. The only tool run on them before the freeze was `check.py` (a
name-only index of the candidate pool). Scored **once**, by `score.py`.

| repo | language | pinned | root | pool (functions) | questions |
|---|---|---|---|---|---|
| `encode/httpx` | Python | 0.28.1 (`26d48e0`) | `httpx` | 533 (448) | hx01-hx30 |
| `Textualize/rich` | Python | v15.0.0 (`6ac483c`) | `rich` | 1,093 (912) | rc01-rc30 |
| `elysiajs/elysia` | TypeScript | 1.4.9 (`668c7a1`) | `src` | 502 (421) | el01-el30 |
| `oclif/core` | TypeScript | 4.5.4 (`cbd7135`) | `src` | 462 (417) | oc01-oc30 |

73 feature_logic, 30 io_sink, 17 routing; 121 gold symbols (oc08 has two: the function and the
helper that does its work). Repository criterion: >= 300 library functions; the first proposal's
koajs/koa (103) and sindresorhus/ky (105) failed it and were replaced (approved).

## How it was written

- Gold = the function(s) that directly answer; decoys = plausible wrong answers (a sibling, the
  caller or the helper, the inverse operation).
- Paraphrase rule: no question contains its gold symbol's name or every word of it (`check.py`,
  0 violations; four questions were reworded to satisfy it before the freeze).
- Gold and decoys are library code in the search pool, never test files or property setters.

## Pre-registered rule (approved before the freeze)

Arms through the shipped `NLQIndex`: **BASE** (no summaries), **V2** (locked), **V4**
(diagnostic), **V5** (the candidate), each summary index covering >= 95% of the functions.
Per question the best-ranked gold symbol is compared (unranked = worst; ties dropped);
two-sided exact sign test; mean recall@5.

> V5 qualifies if (1) V5 beats V2: more questions better than worse with sign-test p < 0.05,
> and recall@5 at most 0.02 below V2; and (2) V5 beats BASE under set 4's rule (p < 0.05,
> recall@5 within 0.02). Otherwise v2 stays locked.

## Order

1. Draft, `check.py` - done. 2. Freeze (`"status": "frozen"`, `questions_sha256`) - done.
3. Summarize on Kaggle (`kaggle/summary_candidate_v5_cell.py`, part B: v2, v4, v5).
4. After stage 2 continues, score once:
   `python -m harness.experiments.heldout_retrieval.set6.score APPS reports/summary_candidate_v5/set6 reports/heldout_set6`.
