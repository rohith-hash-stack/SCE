# Held-out set 7 (frozen, version 1)

The decision set for **digest-grounded summaries** (`prism.summaries.digest`): does a one-sentence summary
written from PRISM's digest of a function rank as well as one written from the function's raw source, and
better than today's shipped summaries? Set 6 was used repeatedly for development of these arms, so this
decision is made here, once.

This README, `select.py` and the rule below were committed **before any question was written**.

## Repositories

None of them was used by any earlier set, corpus, benchmark or tuning run of this project, nor by the digest's
development samples (`reports/digest/REPORT.md`). Each was checked only with a name-only pool count before
selection.

| repo | language | release | commit | root | library functions |
|---|---|---|---|---|---|
| `pallets/werkzeug` | Python | 3.1.9 | `6604c3b8784ecc7cf5ac0b7412d916989b4c7d45` | `src/werkzeug` | 1,073 |
| `aio-libs/aiohttp` | Python | v3.14.5 | `f17c631bddcfd4ec712342db28f1d3381a3fec71` | `aiohttp` | 1,531 |
| `kysely-org/kysely` | TypeScript | v0.29.7 | `ac876c212cfec0e62a77cccc39c7417a13d11ab0` | `src` | 1,760 |
| `nestjs/nest` | TypeScript | v12.2.0 | `af37111368de7fd2dfab0bdec2f530c7845510c2` | `packages` | 2,352 |

`socketio/socket.io` was considered and rejected: its server package has 181 functions (under the 300
minimum), and its monorepo root counts a compiled client bundle (`client-dist`, 791 functions).

## How the gold functions are chosen (new for set 7)

The author of these questions also designed the digest. To keep that from steering which behaviours get
asked about, **gold functions are drawn, not picked**:

1. **Eligible functions:** each repository's candidate pool (`prism.query.nlq.candidate_pool`, test files
   excluded), functions and methods only, excluding:
   - property setters (`#2`);
   - dunder methods;
   - functions shorter than 5 lines;
   - files under `examples/`, `docs/`, `benchmarks/` or `bench/`.
2. **Order:** `select.py` shuffles them with `random.Random(SEED + repo index)`, `SEED = 7007`, and writes the
   order to `draw.json`.
3. **Questions:** the author goes down the list in order. For each function, either write one developer
   question that it directly answers, or skip it with a recorded reason (`skips` in `dataset.json`):
   - `trivial`: delegates or returns with no behaviour of its own;
   - `unaskable`: no behaviour a developer would ask about;
   - `duplicate`: the same behaviour as an earlier question.

   This stops at 30 questions per repository.
4. **A second gold function** is added only when the drawn function's work is done by one named helper (as in
   set 6's oc08).
5. **Decoys:** 1-3 plausible wrong answers (a sibling, the caller or callee, the inverse operation), read from
   the source.
6. **Same rules as sets 1-6:**
   - developer phrasing;
   - no question contains its gold symbol's name or every word of it;
   - gold and decoys are library code in the pool.

   `check.py` enforces these.

## Arms (all through the shipped `NLQIndex`, test files excluded, summary weight 0.5)

| arm | summary text |
|---|---|
| `BASE` | none |
| `V2` | the shipped v2 summarizer (`prism.summaries.pipeline`, card without the body) |
| `SRC` | v2 card + the function's numbered source (first 60 lines), as in `harness/experiments/summary_grounding` |
| `DIGEST` | v2 card + the function's digest (`prism.summaries.digest`, `render(header=False)`) in place of the source |

`SRC` and `DIGEST` use the same sentence rules and prompt wording. The only difference is the source block
versus the digest block. All arms use the same model, sampling, lint and repair (the summary-grounding
settings). Every arm must cover >= 95% of each repository's functions.

## Pre-registered rule

Per question, the best-ranked gold symbol is compared (unranked = worst; ties dropped), with a two-sided
exact sign test, and mean recall@5 over the 120 questions. An arm **beats** another iff all three hold:
- more questions better than worse;
- sign p < 0.05;
- recall@5 at most 0.02 lower.

**DIGEST qualifies to replace V2** iff all three hold:
1. DIGEST beats V2;
2. SRC does not beat DIGEST;
3. DIGEST's mean recall@5 >= SRC's - 0.02.

Readings:
- If (1) holds and SRC beats DIGEST: the raw source is better input than the digest.
- If (1) fails: digest summaries do not improve on today's.

**Secondary (reported, not deciding):**
- SRC vs V2: does the set-6 development result hold on held-out questions?
- DIGEST vs BASE and SRC vs BASE.

**Diagnostics:**
- results per repository, and split by whether the gold function is longer than 60 lines (SRC truncates there);
- mean sentence words per arm;
- generation time and failures.

## Status

Frozen: 120 questions (30 per repository; 71 feature_logic, 34 io_sink, 15 routing), 25 recorded skips
(werkzeug 3, aiohttp 4, kysely 15, nest 3), `check.py` 0 violations; `questions_sha256` in `dataset.json`.
Not scored yet.

## Order

1. Commit this README and `select.py`; run the draw (`draw.json`) - before any question.
2. Write the questions in draw order; `check.py` (name-only).
3. Freeze: `"status": "frozen"` and `questions_sha256`. Nothing is edited after this.
4. Generate V2, SRC and DIGEST summaries on Kaggle.
5. Score once with `score.py`. Write `DECISION.md`.
