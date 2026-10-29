# PRISM task suite: results

Ten repositories, six task types, 742 tasks, 2,036 model calls. The design was pre-registered in
`harness/experiments/suite/README.md`. Numbers come from `RESULTS.json`, written by
`python -m harness.experiments.suite.analyze`.

**Setup**
- Model `qwen2.5-coder:14b-instruct-q8_0` (Ollama 0.34.0, Kaggle T4 ×2).
- Temperature 0.2, seed 42, 20,480-token window, 4,096-token answer cap.
- Code pinned at `793c40a`.

## Summary

| type | task | baseline | PRISM | p (Holm) | verdict |
|---|---|---|---|---|---|
| 1 forward | callers of a change updated | FILE 92/298 (31%) | **214/298 (72%)** | 1.5e-26 | PRISM better |
| 4 blast | traced callers named (recall) | GREP 0.129 | **0.302** | 2.4e-4 | PRISM better |
| 2 newfn | existing helpers reused | FILE 0.591 | **0.708** | 1.1e-3 | PRISM better, with costs (below) |
| 3 bugs | a bug function in top 5 | BM25 97/184 (53%) | 92/184 (50%) | 0.77 | no difference |
| 6 tests | all written tests pass | FILE 0/62 | 2/62 | 0.77 | no difference (format-bound) |
| 5 concepts | an answer function in top 5 | BM25 93/100 | 95/100 | 0.69 (raw) | no difference; not a decision test |

- Three of the five primary tests are significant after Holm: forward, blast and newfn.
- Bugs and tests are not. Per the pre-registration, neither losing version is tuned and re-run.
- Every interval below is a bootstrap 95% interval over tasks (10,000 resamples, seed 5501).

## Type 1: forward (update the callers a change breaks)

192 tasks × 3 versions. 298 callers must be updated.

| | FILE | PRISM | PRISM_BREAKS |
|---|---|---|---|
| callers found, per task | 0.299 | 0.693 | 0.750 |
| tasks with every caller found | 26% | 68% | 73% |
| wrong functions named, per task | 0.99 | 0.52 | 0.43 |
| prompt tokens | 2,270 | 4,243 | 4,388 |
| seconds per answer | 34.2 | 36.2 | 36.8 |

**PRISM vs FILE**
- McNemar over callers: 214 vs 92 found, p = 3.0e-27 (Holm 1.5e-26).
- Difference: +0.64 callers per task [0.50, 0.79].

**By tier** (callers found share, FILE → PRISM)
- easy (98 tasks): 0.49 → 0.65
- medium (64 tasks): 0.03 → 0.70. Here the callers sit outside the anchor's file.
- hard (30 tasks): 0.27 → 0.80. Wrong functions are lower only with breaks: 0.70 (FILE), 0.87 (PRISM), 0.57 (PRISM_BREAKS).

**By language** (callers found share, FILE → PRISM)
- Python, 120 tasks: 0.38 → 0.79.
- JavaScript, 72 tasks: 0.17 → 0.53. On JavaScript, wrong functions barely move (0.78 → 0.72).

**By repository**
- PRISM beats FILE in every repository.
- Weakest: hapi (0.00 → 0.40, wrong functions up 1.00 → 1.15) and mocha (0.20 → 0.45).

**Break check (PRISM_BREAKS vs PRISM)**
- Callers found: 227 vs 214, p = 0.098.
- Wrong functions per task: 0.43 vs 0.52, p = 0.10, difference −0.09 [−0.20, 0.01].
- Decision rule: the break section becomes the default only if it finds significantly more callers. It
  did not, so **the break section stays optional**. The direction favours it on both measures, and in
  most repositories.

## Type 2: newfn (write a new function that reuses the project's code)

128 tasks × 2 versions: 113 Python, 15 JavaScript.

| | FILE | PRISM |
|---|---|---|
| helper reuse | 0.591 | 0.708 |
| invented calls per task | 0.19 | 0.30 |
| the code parses | 96% | 88% |
| prompt tokens | 2,174 | 6,144 |

**Primary test (helper reuse)**
- Wilcoxon p = 3.5e-4 (Holm 1.1e-3).
- Difference: +0.12 [0.06, 0.18].
- Reuse rises in every repository with more than one task.

**The costs.** PRISM's context makes the model call more names that do not exist, and break the syntax
more often.
- Invented calls rise in 6 of 8 repositories. On mocha they go from 0 to 0.38.
- Parse failures concentrate in rich (80% → 67%), scrapy (100% → 85%) and aiohttp (95% → 85%).
- So `newcode`'s helper list helps reuse but needs work on invented calls (see product issues).

## Type 3: bugs (which functions must change to fix this bug)

184 tasks × 3 versions. Context budget 6,000 tokens for each version.

| | TREE | BM25 | PRISM |
|---|---|---|---|
| top 1 | 4% | 31% | 28% |
| top 5 | 9% | 53% | 50% |
| recall@5 | 0.08 | 0.46 | 0.43 |

**Primary test (top 5, PRISM vs BM25)**
- McNemar: 92 vs 97, p = 0.38 (Holm 0.77).
- Difference: −0.03 [−0.08, 0.02].
- **PRISM's ranking adds nothing over plain BM25 on the same pool.**

**Search vs file list.** Both searches beat the file list by far: PRISM vs TREE p = 2.1e-19.

**By language**
- Python: 42% (BM25) vs 43% (PRISM).
- JavaScript: 66% vs 59%.
- PRISM is ahead in scrapy and tornado, behind in werkzeug, hapi and axios.
- rich has only 4 tasks.

## Type 4: blast (what does a change to this function affect)

76 anchors in the 6 Python repositories. The answer key is caller edges recorded by running each
repository's own tests under a tracer. JavaScript is not covered.

| | FILE | GREP | PRISM |
|---|---|---|---|
| recall of traced callers (transitive) | 0.127 | 0.129 | 0.302 |
| recall of direct callers | 0.39 | 0.43 | 0.65 |
| functions named | 4.1 | 8.1 | 21.1 |
| prompt tokens | 1,709 | 2,356 | 5,443 |
| seconds per answer | 11.1 | 39.8 | 61.8 |

**PRISM vs GREP (primary)**
- Wilcoxon p = 5.9e-5 (Holm 2.4e-4).
- Difference: +0.17 [0.10, 0.25].

**PRISM vs FILE**
- p = 1.6e-6.
- PRISM is highest in all six repositories.

**Caveats**
- The tests cannot reach every caller, so the key is incomplete. Precision is not claimed.
- The "outside traced and PRISM" count is lowest for PRISM by construction. It is shown for completeness, not as evidence.
- PRISM's answers are longer: 21 names, against 8 for GREP.
- aiohttp's async code gives a thin trace (49 edges, 11 anchors). scrapy has 5 anchors.

## Type 5: conceptual questions (hand-written; never used for a decision)

100 questions, 10 per repository.

| | TREE | BM25 | PRISM |
|---|---|---|---|
| top 1 | 17% | 74% | 72% |
| top 5 | 41% | 93% | 95% |
| recall@5 | 0.30 | 0.81 | 0.83 |

- PRISM vs BM25: 95 vs 93, p = 0.69. No difference.
- Both are near the ceiling.
- Python: 98% (PRISM) vs 95% (BM25). JavaScript: 90% vs 90%.

## Type 6: tests (write tests for a function; Python)

62 tasks × 2 versions: rich 20, aiohttp 20, scrapy 10, marshmallow 5, tornado 5, werkzeug 2. The
tests were run in each repository at the parent commit.

**Strict score (pre-registered primary)**

| | FILE | PRISM |
|---|---|---|
| all tests pass | 0/62 | 2/62 |
| the tests call the target | 61% | 66% |
| coverage of the target vs the developer's tests | 0.11 | 0.15 |
| prompt tokens | 3,016 | 4,698 |
| seconds per answer | 149 | 154 |

- All tests pass: McNemar p = 0.5 (Holm 0.77).
- Coverage: Wilcoxon p = 0.78.

**The format problem.** The strict score is bound by format, not by test quality. Of 124 answers:
- 42 hit the 4,096-token cap mid-answer;
- 67 put markdown fences inside the code;
- only 12 were clean.

**Salvage sensitivity analysis (post hoc, amendment 4, not in Holm)**
- Every answer of both versions is rescored the same way: fences removed, complete tests of a cut answer kept.

| | FILE | PRISM |
|---|---|---|
| all tests pass | 3/62 | 5/62 |
| share of written tests passing | 0.36 | 0.43 |
| the tests call the target | 95% | 92% |
| coverage vs the developer's tests | 0.89 | 0.97 |

- All pass: p = 0.69.
- Pass share: p = 0.19, difference +0.08 [−0.04, 0.19].
- Coverage: p = 0.30, difference +0.08 [−0.07, 0.23].
- The direction favours PRISM, but nothing is significant.
- Coverage can exceed 1, when the AI's tests reach lines the developer's did not.
- By repository:
  - PRISM is ahead on aiohttp (coverage 0.71 → 1.00, passing tests 2.5 → 6.4) and tornado;
  - behind on scrapy (0.66 → 0.55) and werkzeug (2 tasks).

**Fix before re-testing.** For this type, the answer format must change: ask for the code without
fences and raise or remove the answer cap. That is a change to the task, so it needs new tasks, not a
re-run of these.

## Costs

- PRISM's contexts are 1.3–2.8× the baseline's prompt (forward 1.9×, newfn 2.8×, blast 2.3× GREP).
- They stay well inside the 20,480 window.
- Answer time rises little, except on blast (62 s vs 40 s), where PRISM's answers name many more functions.

## Product issues this run found

1. **Index-cache staleness.** After a checkout, a cached index keeps functions that were deleted (43
   extra on aiohttp). The suite indexed fresh for every task; the cache needs invalidation by revision.
2. **`prism.ask` ranking gives no gain over BM25** on bug localisation (type 3) or concept questions
   (type 5). Its value is in the structured contexts (change context, blast radius), not in search ranking.
3. **`newcode` raises invented calls** (0.19 → 0.30) and parse failures (4% → 12%) along with reuse.
   The helper list should mark which names are real and callable from the target's scope.
4. **The break section** (wording 2) points the right way but is not proven; it stays opt-in.
