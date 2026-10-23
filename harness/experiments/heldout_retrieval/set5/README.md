# Held-out set 5 (DRAFT, version 1)

30 questions on one repository that no earlier set, corpus or tuning run
scored: Hono, which set 4 dropped because PRISM did not index its
`export const f = () => {}` API. Written after the JS/TS const-binding parser
fix (`3cd55ed`), against the natural-language search pool with test files
excluded. It is scored **once**, after it is frozen, for the two
pre-registered checks below.

| repo | language | pinned | root | candidates (name-only index) | questions |
|---|---|---|---|---|---|
| `honojs/hono` | TypeScript | v4.9.9 (`16eb882`) | `src` | 776 (555 functions/methods) | hn01-hn30 |

Categories: 19 feature_logic, 6 io_sink, 5 routing. 32 gold symbols, 27 of
them const-bound functions that had no symbol before the parser fix; two
questions have two gold symbols (hn17: entry + helper, hn23: `compose` and
its `dispatch`).

## How it was written

- From Hono's source at the pinned commit. The only tool run on the repo was
  a name-only index of the candidate pool (`check.py`), to check that every
  gold and decoy is a candidate. No search, ranking, embedding or summarizer
  has seen it.
- Gold = the function(s) that directly answer; decoys = plausible wrong
  answers (the inverse operation, the caller or the helper, a sibling with
  the same stems).
- Paraphrase rule: no question contains its gold symbol's name or every word
  of it - `check.py`, 0 violations.
- Gold and decoys are library code, never test or type-test files.

## Pre-registered rules (DRAFT - to be approved before freezing)

Arms, all through the shipped `NLQIndex` (`score.py`):

- **A** - parser fix, test files in the search pool (`exclude_test_files=False`);
- **B** - parser fix + test-file exclusion (the shipped pool, no summaries);
- **C** - B + the summary field via the shipped rule (`summaries="auto"`), from
  the live `prism summarize` index of Hono; the run stops unless that index
  resolves to w = 0.5.

Per question, the best-ranked gold symbol is compared (unranked = worst; ties
dropped); two-sided exact sign test on improved vs. worsened questions; mean
recall@5 over the 30 questions.

> **Rule 1 (test-file exclusion), B vs A.** Keep the exclusion unless B is
> inferior: more questions worse than better with sign-test p < 0.05, or mean
> recall@5 more than 0.02 below A. If inferior, revert it.
>
> **Rule 2 (summary field on Hono), C vs B.** Confirmed if C beats B (more
> questions better than worse, sign-test p < 0.05) without recall@5 dropping
> more than 0.02. Otherwise: not confirmed on Hono - investigate; the shipped
> setting is not changed on this set alone.

Rule 1 is a non-inferiority test on purpose: the exclusion is motivated
independently (a helper in a test file is never an answer to a question
about the code under test) and makes the pool smaller; it needs to not hurt,
not to win. Rule 2 is a superiority test, as on set 4.

## Order

1. Review this draft; approve or edit the questions and the rules.
2. Freeze: `"status": "frozen"` in `dataset.json`, rules final in this file.
3. Summarize Hono on Kaggle (`kaggle/prism_summarize_cell.py`, target `hono`,
   no sample questions) - `.prism/summaries.json` must reach >= 95% coverage.
4. Score once: `python -m harness.experiments.heldout_retrieval.set5.score
   HONO_CHECKOUT reports/heldout_set5 --summaries <summaries.json>`.

## Status

Draft. Not frozen; `score.py` refuses to run until it is.
