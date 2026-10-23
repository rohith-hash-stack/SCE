# Purpose-enriched search: locked production candidate

Locked after the set-3 14B v2 run (`reports/purpose_search_set3_14b_v2`). Nothing
below changes until set 4 has been scored; a change means a new candidate and a
new fresh validation set.

| component | locked value |
|---|---|
| summarizer | `qwen2.5-coder:14b-instruct-q8_0` (Ollama), batches of 12 cards |
| card | `cards.build_card(..., axes=False)`: no Architectural Axes line, signature capped at 240 chars, 12 callees/callers, unresolved receiver calls merged |
| prompt | `run.SUMMARY_PROMPT_V2` (`--variant v2`) |
| repair | `run.REPAIR_PROMPT`, `REPAIR_ATTEMPTS = 2`; lint = `lint.py` |
| scoring | `BM25(code) + w * BM25(summary)`, summary as an independent field (`run.fielded`) |
| weights | **w = 0.5** primary (balanced: best recall@5 and pool recall on set 3); **w = 1.0** tracked (highest MRR) |
| shipped default | `summary_weight = 0.0` (off) until set 4 passes |

## Evidence so far

| run | recall@5 | pool recall | MRR | better / worse vs BASE (w=0.5) |
|---|---|---|---|---|
| set 3 BASE | 0.740 | 0.900 | 0.503 | - |
| set 3, 14B v2, w=0.5 | 0.770 | 0.930 | 0.549 | 23 / 8 (sign p = 0.011) |
| set 3, 14B v2, w=1.0 | 0.760 | 0.930 | 0.618 | 21 / 9 (p = 0.043) |

Set 3 was used three times (7B v1, 14B v1, 14B v2) and the v2 card/prompt were
designed from its v1 failures, so these numbers are optimistic. Remaining gates:

1. Python regression (set 2 + Q2E fastapi/django) with this exact configuration: done
   (`reports/purpose_search_py_14b_v2`), no loss against v1 on set 2.
2. Set 4 (`heldout_retrieval/set4`), written and frozen before any run, scored once.

## Pre-registered set-4 decision rule

Recorded before set 4 was summarized or scored; applied mechanically by
`decide.py` (written to `DECISION.md` / `decision.json` in the run's results):

> Evaluate w=0.5 first on Set 4. If it beats BASE (sign test p < 0.05) without
> dropping Top-5 Recall by more than 0.02, ship w=0.5. Otherwise, fall back to
> w=1.0 under the exact same criteria. If neither passes, do not ship.

Operational definitions (the same measures as every earlier run):

- *beats BASE*: over the 48 set-4 questions, compare each question's
  best-ranked gold symbol under the arm and under BASE (unranked = worst); more
  questions improve than worsen, and the two-sided exact sign test on
  improved vs. worsened (ties dropped) gives p < 0.05;
- *Top-5 Recall drop*: mean recall@5 over the 48 questions, arm minus BASE;
  passes if >= -0.02;
- the configuration is the locked candidate above, unchanged: code commit,
  cards, prompts, repair, weights and summarizer fixed before the run.

Results of the earlier gates for context (not part of the rule):
Python regression (set 2: w=0.5 22 better / 2 worse, MRR 0.678 -> 0.748;
w=1.0 recall@5 0.833 -> 0.950, MRR -> 0.763; Q2E q13 back in the pool at
w=1.0, q11 not recovered).

Forward blast-radius retrieval (`prism.blast_radius`) is not affected: it stays
on the R0 caller rule.
