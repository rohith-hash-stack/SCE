# Why v4 barely moves retrieval - diagnosis on sets 2-5

Diagnostic only: sets 2-5 have all been scored before, so nothing here decides anything;
anything designed from it has to be confirmed on a fresh set. Data: the stage 1 v2 / v4
indexes, the 188 questions of sets 2-5 (205 gold symbols), the repositories at their pins.

## 1. Headroom is small

V2's best gold rank: 78 questions at rank 1, 74 at 2-5, 19 at 6-20, 17 beyond 20 or unranked.
152 of 188 (81%) are already top-5; 36 are misses.

## 2. The trigger mostly missed the misses - but that is not the main problem

- Escalated functions are smaller (median 7 body lines vs 11 for the rest) but not only
  trivial: 18% have >= 20 lines. Gold functions are larger (median 22 lines, pool 10) and are
  flagged less often (19% vs 25% of the pool).
- `restates_name` flagged the gold of only 8 of the 36 misses.
- The misses' summaries are not long and vague: median 9 words, mostly accurate
  ("Converts a number of bytes into a human-readable string."). They fail because the question
  asks in other words (the sets' paraphrase rule forbids the name's words), not because they say
  nothing.

Alternative triggers, scored on what they would cover:

| trigger | share of pool escalated | misses covered | top-5 hits touched |
|---|---|---|---|
| restates_name (v4) | 25% | 8 / 36 | 27 / 152 |
| >= 20 lines and < 10 words | 13% | 13 / 36 | 35 / 152 |
| >= 20 lines and < 12 words | 21% | 20 / 36 | 69 / 152 |
| >= 20 lines | 27% | 23 / 36 | 87 / 152 |
| restates_name or (>= 20 lines and < 10 words) | 34% | 16 / 36 | 51 / 152 |

## 3. Where v4 did escalate a miss, the rewrite rescued none

The 8 misses with escalated gold, V2 -> V4 rank: 95 -> 136, 7 -> 6, 28 -> 6, 17 -> 8, 13 -> 13,
21 -> 22, 25 -> 25, 61 -> 33. None reaches the top 5. The rewrites are more accurate but stay in
the code's vocabulary ("Converts unknown cause to TRPCError, inheriting stack if possible" for
"How is an arbitrary thrown value normalized into the framework's own error type?"). The one
large gain (28 -> 6) came where the rewrite repeated the question's concepts
(blueprint-level before app-wide; question/summary token overlap 1 -> 5).

So a sharper trigger alone would feed more functions to a rewriter that, on this evidence,
does not close the vocabulary gap.

## 4. The summary field could fix every miss (upper bound)

Giving each missed question's gold function the question itself as its summary (a leaky
upper bound, not a method) brings all 36 into the top 5 (33 to rank 1). The ranking at w = 0.5
is not the limit; the text in the summary field is.

## 5. A free test: more text per escalated function

v4 already stored an `effects` line for every escalated function. Indexing summary + effects
for them (V4E; no new model calls):

| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |
|---|---|---|---|---|
| V4 vs V2 | 18 / 17 / 153 | 1.0 | +0.003 | +0.007 |
| V4E vs V2 | 33 / 19 / 136 | 0.070 | +0.008 | +0.014 |
| V4E vs V4 | 24 / 12 / 152 | 0.065 | +0.005 | +0.007 |

A directional signal on sets that informed the design, not a result.

## Conclusions

- The lexical trigger is a real blind spot (8 of 36 misses), but the bigger limit is what the
  summary field contains: one 8-14 word sentence in the code's own vocabulary.
- A "semantic vagueness" lint would not find these misses: their summaries are specific and
  correct, just phrased differently from the question. Vocabulary mismatch cannot be linted
  without the queries.
- The lever the data points to is more, and more question-like, text for the functions that
  need it (effects; possibly a few developer-style questions per function, as in doc2query),
  with a trigger that also covers large functions with short summaries.
