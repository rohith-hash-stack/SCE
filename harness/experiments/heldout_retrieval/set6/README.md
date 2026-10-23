# Held-out set 6 (DRAFT - not frozen)

The decision set for summarizer candidate v4 (harness/experiments/purpose_search/CANDIDATE_V4.md):
arms **BASE**, **V2**, **V4** through the shipped `NLQIndex`. Written from the source at the pinned
commits before any engine, search, ranking or summarizer ran on these repositories; the only tool
run is `check.py` (a name-only index of the candidate pool).

| repo | language | pinned | root | pool (functions) | questions |
|---|---|---|---|---|---|
| `encode/httpx` | Python | 0.28.1 (`26d48e0`) | `httpx` | 533 (448) | hx01-hx15 |
| `Textualize/rich` | Python | v15.0.0 (`6ac483c`) | `rich` | 1,093 (912) | rc01-rc15 |
| TypeScript half | - | - | - | - | 30 to write |

**Open: the TypeScript half.** The approved repositories `koajs/koa` (v3.2.1: 103 library
functions) and `sindresorhus/ky` (v2.2.0: 105) fail the approved criterion of >= 300 library
functions per repository, so they are not drafted. Replacements need approval.

## Rules (approved, CANDIDATE_V4.md)

Per question the best-ranked gold symbol is compared (unranked = worst; ties dropped); two-sided
exact sign test; mean recall@5.

> V4 qualifies if (1) V4 beats V2: more questions better than worse with a two-sided sign test
> p < 0.05, and recall@5 at most 0.02 below V2; and (2) V4 beats BASE under set 4's rule
> (p < 0.05, recall@5 within 0.02). Otherwise v2 stays locked.

Summaries: v2 summarized fresh with the locked pipeline, then converted to v4 by escalation only.

## Order

1. Draft (this file, `dataset.json`), `check.py`: 0 violations on the 30 drafted questions.
2. Approve the TypeScript replacements; write their 30 questions; `check.py`.
3. Freeze: `"status": "frozen"`, `questions_sha256`.
4. Summarize (Kaggle: v2, then `--candidate v4`); score once.
