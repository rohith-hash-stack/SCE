# The 5 drift failures: investigation (2026-11-09; nothing re-recorded, nothing changed)

From `COVERAGE_BASELINE.md`. Each failure is traced to its cause. The responsible commit was found by running the
test at the commit before and the commit itself.

**Summary:**

| # | Test | Verdict | Cause | Recommended action |
|---|---|---|---|---|
| 1 | `test_nlq_p0.py::test_recorded_ranks_of_the_ten_unseeded_questions` | **intended change, incomplete re-record** | `61c4b06d` re-recorded the fixture and did not update this test's hard-coded values | update the test's expected values to the fixture (a re-record of the test, not of the fixture) |
| 2 | `test_nlq_p0.py::test_recorded_candidate_list_growth` | **intended change, incomplete re-record** | the same commit | the same |
| 3 | `test_nlq_p1.py::test_proof_states_propagate_from_the_existing_predicates` | **neither: environment** | tiktoken cannot fetch its encoding here; PRISM's tokenizer fallback adds a warning | fix the environment dependency; do not re-record |
| 4 | `test_nlq_p1.py::test_unsupported_requirement_is_not_credited_with_other_envelopes` | **neither: environment** | the same | the same |
| 5 | `test_nlq_p1.py::test_scope_compliance_is_recorded_without_changing_the_proof_state` | **neither: environment** | the same | the same |

**None of the five is a PRISM regression.**

## Failures 1 and 2: recorded lexical-search results

**What the tests check.** They do not run PRISM. They compare a recorded file,
`harness/experiments/query_to_evidence/nlq/fixtures/lexical_recorded.json` (the lexical search's ranks and
candidate-list sizes over the FastAPI and Django corpora), with numbers written into the test.

**What changed:**

| Question | Field | Test expects | Fixture now has |
|---|---|---|---|
| q12 | largest candidate list (`@50`) | 185 | 172 |
| q14 | target rank (N, NB) | (2, 4) | (2, 5) |
| q15 | target ranks | (325, 96), (331, 1438) | (326, 100), (332, 1499) |
| q15 | largest candidate list | 169 | 171 |
| q16 | target rank | (1114, 89) | (1114, 87) |
| q17 | largest candidate list | 388 | 389 |

(The coverage report's summary wrote "q12 172 → 185" the wrong way round. The test expects 185, the fixture has
172.)

**The responsible change:**
- `3cd55ed2` (2026-10-23), "Index JS/TS functions bound by declaration (`const f = () => {}`)", is a parser fix.
  A module-level `const f = () => {}` used to be registered as an attribute, so it was not searchable and had no
  callers. After the fix it is a function.
- This grew the search pools: Django 19,794 → 19,868 functions (75 added, all vendored or docs JavaScript, for
  example `django.contrib.admin.static.admin.js.collapse.toggleFunc`), FastAPI 2,957 → 2,958. BM25 statistics
  depend on the pool, so ranks and expansion sizes moved by a few places.
- `61c4b06d` (2026-10-23) re-recorded the fixture for exactly this reason, and its message explains it ("21 fields
  moved … because BM25 statistics change with the pool"). It changed only the JSON, not the test's hard-coded
  copy of those numbers.

**Confirmed by bisection:** both tests pass at `61c4b06d^` and fail at `61c4b06d`.

**Verdict: intended.** The parser fix is correct (functions that were invisible are now indexed). The re-record was
deliberate and documented. Only the test's duplicate of the numbers was left behind.

**Recommended action:** update the two tests' expected values to the fixture's current values, in one commit
citing `61c4b06d`.
- The fixture itself stays as it is.
- One question to flag, not a blocker: the 75 new Django functions are vendored admin JavaScript. Whether
  vendored static files belong in a Python repository's search pool is a scoping choice, separate from this drift.

## Failures 3, 4 and 5: proof states `PROVED_WITH_WARNING` instead of `PROVED`

**What the tests check.** They build a tiny two-file repository, run PRISM's blast radius and slice through the
evidence planner, and check each requirement's proof state.

**What changed:** requirements r1, r2 and r3 read `PROVED_WITH_WARNING` where the test expects `PROVED`. The only
warning on every PRISM call is `TOKENIZER_FALLBACK`.
- `tiktoken.get_encoding("cl100k_base")` fails here with a `ProxyError`: the sandbox's network policy denies
  `openaipublic.blob.core.windows.net`.
- So PRISM's tokenizer falls back to its regex counter, and the envelope says so (`src/prism/surface/build.py`,
  line 744: `if not is_exact(): … TOKENIZER_FALLBACK`).
- The proof evaluator downgrades any proof with a warning to `PROVED_WITH_WARNING`
  (`query_to_evidence/design/proof_eval.py`, line 135). That is the correct behaviour for an approximate token
  count.

**Confirmed:** with `is_exact` forced to true in-process (a probe, not a change), every requirement reads `PROVED`,
and all of `test_nlq_p1.py` passes (32 passed, 2 skipped).

**The responsible change:** none in the code.
- The evaluator is unchanged since the test was written (`fda65d65`, 2026-10-09), so the tests pass wherever
  tiktoken's encoding is available.
- This dependency is known and documented. `src/prism/slicer/tokenizer.py` (the `OFFLINE_BPE_ASSET_PATH` comment)
  and `tests/fixtures/tokens/README.md` describe an offline copy that "works the moment a maintainer commits the
  real ~1.6MB asset". It was never committed, because the sandbox it was written in had no network to fetch and
  verify it.

**Verdict:** neither intended nor a regression. It is an environment dependency.

**Recommended action:** do not re-record. Make the real encoding available, with one of:
1. allow `openaipublic.blob.core.windows.net` in this environment's network settings;
2. fetch the file on Kaggle (which has internet) and commit it at `OFFLINE_BPE_ASSET_PATH`, with its sha256;
3. at minimum, mark these three tests to skip with a reason when the tokenizer is the fallback.

Option 2 also closes the go-live tokenizer item, and it is a **prerequisite for the golden-output tests**. Saved
outputs recorded under the fallback would not match outputs under the real tokenizer: the pilot showed a
blast-radius envelope of 1,443 tokens on Kaggle against 1,829 here.
