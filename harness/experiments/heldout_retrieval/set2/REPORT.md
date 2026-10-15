# Held-out set 2: validating the round-3 fixes


## Current result: all 60 questions

> After `41b012a` fixed the role-classifier bugs, the four names the name-only check had excluded are candidates again. ft24 and sg07 are restored exactly as written (`0352d90`), so this run scores all 60 frozen questions. The 58-question run further down is kept for history.

| system | top-1 | recall@5 | MRR | separation |
|---|---|---|---|---|
| ripgrep | 0.350 | 0.567 | 0.451 | 0.583 |
| BM25 | 0.400 | 0.767 | 0.571 | 0.767 |
| BM25 + stemming | 0.450 | 0.817 | 0.615 | **0.783** |
| Prism lexical leg | 0.450 | 0.817 | 0.614 | **0.783** |
| Prism triad, previous (rule intent) | 0.483 | **0.833** | 0.636 | 0.767 |
| + neutral unknown only | 0.483 | **0.833** | 0.636 | 0.767 |
| + class prior only | 0.533 | **0.833** | 0.668 | 0.767 |
| + migration demotion only | 0.500 | **0.833** | 0.646 | 0.767 |
| **Prism triad, fixed (rule intent)** | **0.550** | **0.833** | **0.678** | 0.767 |
| **`prism.query.nlq` (shipped API, `prism ask` / `prism.ask`)** | **0.550** | **0.833** | **0.678** | 0.767 |
| Prism triad, fixed, oracle intent | **0.633** | **0.833** | **0.735** | **0.817** |

Paired sign tests (top-1 wins–losses):

| comparison | top-1 | p | separation |
|---|---|---|---|
| fixed vs previous triad | +4 −0 (ft26, sg19, sg24, sg27) | 0.125 | +0 −0 |
| fixed vs stemmed BM25 | +7 −1 (lost: sg10) | 0.070 | +1 −2 |
| fixed + oracle intent vs stemmed BM25 | **+11 −0** | **0.001** | +3 −1 |
| fixed + oracle intent vs fixed (rule intent) | +5 −0 | 0.062 | +3 −0 |

- **The shipped API matches the benchmark's fixed arm.** Every metric and every gold rank agree on all 60 questions. The top-5 tails differ on two questions (ft15, sg01) because the harness keeps its corpus-specific stop words (`django`, `fastapi`) for the recorded probe, and the product does not. Those words occur in import lines.
- **The two restored questions change nothing between systems.** ft24 (`test_email`) is rank 1 everywhere; sg07 (`s3_get_credentials`) is rank 4 everywhere.
- **The conclusions are the same as the 58-question run, with one difference.** The fixed triad is still directionally better than stemmed BM25 with the rule intent, now +7/−1 (p = 0.07, not significant). With the oracle intent it is +11/−0 (p = 0.001).

## Why this set exists

The round-3 fixes were chosen after reading set 1's failures, so set 1 could only give an in-sample upper bound. Set 2 is out-of-sample:
- two repositories never used before;
- questions, gold answers, decoys and oracle intents written from their source after the fixes were committed (`58269fc`);
- everything frozen in `14013c7` before any system ran on them.

## Setup

- **Repositories:**
  - `fastapi/full-stack-fastapi-template` @ `7257e60`, `backend/`: FastAPI, SQLModel, SMTP email, JWT. 91 candidates.
  - `HackSoftware/Django-Styleguide-Example` @ `a70ef43`: Django/DRF services and selectors, Celery, S3 presigned uploads, Google OAuth, 2FA. 322 candidates.
- **Questions:** 60 written; 58 scored after the pool exclusions below.
  - Categories: 28 feature logic, 15 I/O sink, 15 routing. The Django repo has only 1 routing question, because its "routes" are DRF class methods.
  - Oracle capability: 34 DATABASE_IO, 10 network, 1 file read, 15 none.
- **Pool exclusions**, found by the name-only check before any ranking:
  - Prism's role classifier marks four application functions as test code: `test_token`, `test_email` and `test_non_concurrent_task` (all named `test_*`), and `s3_get_credentials` (its body calls `assert_settings(...)`).
  - They are not candidates for *any* system. ft24 and sg07, whose gold was among them, were dropped. These misclassifications are real Prism bugs, reported below and not fixed mid-protocol.
- **Runner:** same as set 1 (`run.py --dataset set2/dataset.json --oracle set2/intent_oracle.json`). The embedding baseline was not run because huggingface.co is blocked.

## Results (58 questions)

| system | top-1 | recall@5 | MRR | separation |
|---|---|---|---|---|
| ripgrep | 0.345 | 0.586 | 0.452 | 0.586 |
| BM25 | 0.397 | 0.776 | 0.571 | 0.759 |
| BM25 + stemming | 0.466 | 0.828 | 0.623 | **0.776** |
| Prism lexical leg | 0.431 | 0.828 | 0.606 | **0.776** |
| Prism triad, previous (rule intent) | 0.466 | **0.845** | 0.628 | 0.759 |
| + neutral unknown only | 0.466 | **0.845** | 0.628 | 0.759 |
| + class prior only | 0.517 | **0.845** | 0.661 | 0.759 |
| + migration demotion only | 0.483 | **0.845** | 0.639 | 0.759 |
| **Prism triad, fixed (rule intent)** | **0.534** | **0.845** | **0.672** | 0.759 |
| Prism triad, fixed, oracle intent | **0.621** | 0.828 | **0.730** | 0.810 |

Paired sign tests (top-1 wins–losses):

| comparison | top-1 | p |
|---|---|---|
| fixed vs previous triad | +4 −0 (ft26, sg19, sg24, sg27) | 0.125 |
| fixed vs stemmed BM25 | +6 −2 | 0.29 |
| fixed + oracle intent vs stemmed BM25 | +10 −1 | **0.012** |
| fixed + oracle intent vs fixed (rule intent) | +5 −0 | 0.062 |

## What it says

1. **The fixes generalize in direction but not significantly on their own.** They gained 4 top-1 hits and lost none. The contributions differ from set 1:
   - The **class prior** carries most of it (+3). The Django repo's DRF API classes (`UserSessionLoginApi`, ...) outranked their own `post`/`get` methods. On set 1 it had moved ranks only below the top.
   - **Migration demotion** gives +1.
   - **Neutral unknown** gives 0 here. Its motivating case, a route touching a proxy beating the sink, did not recur.
2. **With rule intent, the fixed triad beats stemmed BM25 by 6–2 (p = 0.29).** That is suggestive, not established.
3. **With a correct intent, the fixed triad beats stemmed BM25 by 10–1 (p = 0.012).** This is the first statistically significant win over the lexical baseline on code nothing was tuned on. The intent is the lever: oracle vs rule intent is +5/−0. The rule intent produced an action on 39 of 58 questions but a capability on only 10.
4. **Separation does not improve.** The fixed triad sits at 0.759 against 0.776 for stemmed BM25. The oracle intent lifts it to 0.810.
5. **Prism's own lexical leg is slightly below stock stemmed BM25** here (0.431 vs 0.466 top-1). Its NB documents include path tokens and its stop words differ. This is worth a look independently of the triad.

## Caveats

- 58 questions over two repositories. Only the oracle-intent comparison clears p < 0.05, and that is one test among several, so treat p = 0.012 as strong but not conclusive.
- The same author wrote both sets and knows how Prism works. Freezing before any run guards against tuning, not against phrasing bias.
- The oracle intent is an ideal Turn-1 output written by a person. A real model's intent would land somewhere between the rule and the oracle.

## Prism bugs found by this set

- **Application functions named `test_*` are classified as test code** and excluded from search: endpoints `test_token` and `test_email`, and a demo task `test_non_concurrent_task`. Name-based classification should apply only inside test files or test modules.
- **A body that calls an `assert_*` helper is classified as an assertion-shaped test body:** `s3_get_credentials` calls `assert_settings(...)`. The check should look at `assert` statements and assertion methods, not at function names that start with `assert`.
