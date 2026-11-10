# R3a's 2/320 solve rate: investigation (2026-11-10; accepted the same day)

**Question:** why was almost nothing solved? PRISM resolved 0 of 160 runs, and the baseline 2 of 160 (one task,
r3a-B-14, both seeds).

**Constraints:** investigation only. No code changes, no fixes, no re-runs on Kaggle. Step 1 gated the rest: a
broken scoring pipeline would have made steps 2–7 moot.

**Status of R3a's result:** unchanged. The null result stands as pre-registered:
- Family B primary, 80 tasks: −0.0125, Wilcoxon p = 0.317, against the R3-9 final threshold p < 0.049;
- the interim at 40 tasks: z 1.0, "continue".
This investigation explains the floor. It does not reinterpret the test.

**Verdict:** (e), a combination. **No scoring bug.** In order of weight:
1. **task design** (the template, the hidden target, no failing signal);
2. **task validity and difficulty** (the developer's change goes well beyond the stated change);
3. **model capability** (quitting when tests fail; wrong-file edits; loops).

Scripts and outputs: `harness/experiments/r3/inv3/`.

## 1. Scoring sanity: works

**The check:** the developer's fix for 10 tasks, scored by R3a's own `runner.score()`.
- **Setup,** as a run gets it: worktree at the parent, start patch, `make_env` and setup, the start snapshot.
- **The fix:** every gold file set to its version at the commit.
- **Script:** `inv3/gold_score.py`; output `inv3/step1.jsonl`.

| Task | Repo | Resolved | Fail-to-pass passed | Pass-to-pass broken | File / function F1 |
|---|---|---|---|---|---|
| B-03 | svelte | yes | 18/18 | 0 of 164 | 1.0 / 1.0 |
| B-04 | pydantic | yes | 6/6 | 0 of 8 | 1.0 / 1.0 |
| B-08 | unstorage | yes | 25/25 | 0 of 60 | 1.0 / 1.0 |
| B-11 | poetry | yes | 11/11 | 0 of 78 | 1.0 / 1.0 |
| B-12 | sqlglot | yes | 19/19 | 0 of 179 | 1.0 / 1.0 |
| B-13 | packaging | yes | 57/57 | 0 of 2,195 | 1.0 / 1.0 |
| B-19 | execa | yes | 22/22 | 0 of 41 | 1.0 / 1.0 |
| B-28 | nitro | yes | 4/4 | 0 of 14 | 1.0 / 1.0 |
| B-30 | h3 | yes | 11/11 | 0 of 33 | 1.0 / 1.0 |
| B-32 | scrapy | yes | 4/4 | 0 of 33 | 1.0 / 1.0 |

**The Kaggle side agrees** (from R3a's score files, all 320 runs):
- for 9 of these tasks, every pass-to-pass test passed in every run;
- the fail-to-pass IDs were found, and failed at the start state;
- in the 213 runs with no edit, no pass-to-pass test broke.

**One scoring weakness, not a cause:**
- **What happens:** in 9 runs the agent edited the visible test file, so the hidden test patch could not apply and
  the run was unscored (B-30 ×4, B-51 ×3, B-57, B-72).
- **Why it lost nothing:** none was a lost solve. Each agent had edited only the test file, or the test plus the
  wrong code.

## 2. The 130 early-done runs (done within 5 turns, no edit)

**Shape:** 58 PRISM, 72 baseline.
- 111 called `run_tests` once.
- The most common sequence (50 runs) is `run_tests > done`.

**What run_tests showed at the start, all 80 tasks.** This is a full rerun through the harness's own `run_tests`
(`inv3/start_tests.py`, outputs `inv3/start_*.txt`), not the logs' 300-character heads.

| What the agent sees at the start | Tasks | Early-done runs |
|---|---|---|
| **Every visible test passes** | **18** | **64 of their 72 runs** |
| **No visible tests** (4 nitro, 1 unstorage) | **5** | 6 |
| Some visible tests fail | 57 | 60 |

**The hypothesis is confirmed for 23 of 80 tasks (29%).**
- **The finding:** the fail-to-pass tests exist only in the hidden patch (the developer's new tests), so the agent
  never sees a failing test.
- **Why it matters:** against "update the rest of the project so its tests pass", calling done at once is a correct
  reading.
- **What the agents wrote:** "All tests passed after updating the project…". The summaries are false (no edit was
  made), but stopping was reasonable.

**The hypothesis is refuted for the other 60 early-done runs.** They saw failures and quit anyway. That is model
behaviour:
- B-46 and B-42 read PRISM's context and stopped;
- B-62 tried to edit the test file;
- B-26 grepped once and assumed nothing was needed.

**The sample** (seeded, 5 per arm; transcripts in `inv3/step2.txt`):

| Run | Start-state run_tests | Done summary |
|---|---|---|
| B-22 PRISM (svelte) | 82 + 2,708 passed, 0 failed | "All tests passed after updating the project…" |
| B-15 PRISM (yarl) | 690 + 50 passed, 0 failed | the same |
| B-66 PRISM (packaging) | 1,049 passed, 0 failed | the same |
| B-48 baseline (h3) | 10 + 4 passed, 0 failed | the same |
| B-43 baseline (scrapy) | 13 passed, 0 failed (ran twice) | "All tests passed after updating the project." |
| B-46 PRISM (unstorage) | 16 failed | "…caused multiple tests to fail. Further investigation… needed" |
| B-42 PRISM (execa) | 24 failed | "the impact on the codebase has been assessed" |
| B-62 baseline (typer) | 12 failed | tried to edit the test file, then gave up |
| B-26 baseline (svelte) | 396 failed | "Assuming no changes are needed" |
| B-78 baseline (nitro) | "no test files given, and the task has no visible tests" | "No test files found in the repository." |

## 3. The task message, read literally

**The template** (every task; the system prompt adds only "When your changes are complete, call "done""):

> Function `X` in `path` was changed as shown: *(the anchor's diff)*
> Update the rest of the project so its tests pass. The change to `X` is already in the working tree. When your
> changes are complete, call done.

- **Which tests:** not stated. "Its tests" means the project's. `run_tests` with no files runs the visible test files
  at the parent. The scored tests are hidden and never named. In 23 tasks they have no failing visible counterpart.
- **Done:** "when your changes are complete". There is no instruction for "the tests already pass" or "I am
  stuck", so stuck agents call done.
- **The start state:** stated clearly. That part is fine.
- **Would a human know what to do from the message alone?**
  - **typer B-62:** yes. A parameter was removed, so a developer would update the callers that pass `metavar`.
  - **svelte B-22:** partly. They would look for callers, but could not tell when the work was finished.
  - **scrapy B-43:** no. The visible tests pass, and the diff removes behaviour.

## 4. The one-caller tasks (62 tasks, 248 runs)

| | Edited the gold caller | Edited only other parts of the gold file | Edited elsewhere | No edit |
|---|---|---|---|---|
| PRISM (124) | 22 | 1 | 6 | **95** |
| Baseline (124) | 39 | 10 | 11 | 64 |

**"One caller" understates the work.**
- **The developer's change goes beyond the changed caller in 54 of 62 one-caller tasks:** a median of 3.5 functions in
  2 files, and up to 87.
- **The hidden tests check that whole change.** Some need content the prompt cannot supply:
  - B-44 (nitro): a new `GLOB_MODULES_SCAN_PATTERN` constant, used in `scanModules`, plus docs;
  - B-15 (yarl): new IDNA host-handling behaviour.

**The sample** (seeded, 5 per arm; `inv3/step4.txt`):
- **No correct caller edit failed the tests,** so nothing points back to step 1.
- **B-64 (sqlglot), partly right:**
  - it dropped the `exp.Rollup`/`exp.Cube` argument, which was correct;
  - but it missed the caller's restructuring;
  - then 3 edits failed: "occurs 2 times", "not found".
- **B-44 (nitro), wrong:** it passed `nitro.options.scanPatterns`, where the developer added a new constant.
- **B-10 and B-09, wrong place:** B-10 edited the wrong driver; B-09 edited 1 of 6+ gold functions and broke 79
  tests.
- **B-11:** tried to edit the test file.
- **B-22, B-15, B-37:** saw all tests passing and stopped.
- **B-27:** looped until the budget ran out.

**Partial passes elsewhere:** B-24 (45/47), B-79 (70/74), B-58 (24/27) and B-50 (30/35).

## 5. Test infrastructure (B-62 typer, B-15 yarl, B-19 execa; `inv3/step5_*.txt`)

- **Format:**
  - one line per file: "N passed, M failed, K skipped";
  - up to 30 failing test names;
  - the last 1,500 characters of raw output.
- **It parses in all 16 repositories,** from every `run_tests` call in the Kaggle logs. The exceptions:
  - pydantic (8) and svelte (7) got "produced no results";
  - execa hit the 4,000-token cap 9 times;
  - the 5 tasks with no visible tests answer only "no visible tests".
- **Python shows the cause.** pytest's summary ends the tail, for example `TypeError: get_default_option_flag_name()
  takes 1 positional argument but 2 were given`.
- **JS shows names only.** Node's TAP output ends with passing subtests, so the tail is "ok 57 …" lines.
- **Fail-to-pass and pass-to-pass are never distinguished:**
  - typer: the 12 visible failures are exactly the target tests (the good case);
  - execa: 21 visible, and the hidden patch adds a 22nd;
  - yarl: all 740 visible tests pass, and the 10 targets exist only in the hidden patch.

## 6. Runs cut off just before solving

- **8 runs** edited, re-ran the tests, then hit the budget or the turn cap: 6 PRISM, 2 baseline.
- **All 8 ended with 0 fail-to-pass passing.** Most had edited a test helper, or broken the test file's import; one
  broke 198 pass-to-pass tests.
- **Five more turns would not have solved any of them.**

## 7. PRISM context size in the 18 budget runs (all PRISM)

- **17 of the 18 are pure loops:** 2–6 distinct calls repeated over 21–27 turns, with no edit. For example, B-09
  called `prism_change_context` 12 times and `prism_blast_radius` 11 times, on the same symbol.
- **The exception, B-20:** 9 edits, 0 of 34 fail-to-pass passed, 73 broken.
- **Where the tokens go:**
  - PRISM's answers are 90% of the tool output (780k of 864k tokens);
  - but the budget is spent re-sending a window of about 14–16k tokens every turn, close to the 20,480-token
    context;
  - PRISM's median prompt per turn is 3,960 tokens, the baseline's 1,679.
- **Would a smaller answer have helped?** No. These runs would stop at the 28-turn cap instead of the budget, because
  they repeat calls rather than progress. The remedy is the repeat notice (C1).

## 8. Verdict: causes, weights, fixes

| Cause | Weight | Evidence | Fix |
|---|---|---|---|
| **(b) Task design** | primary | 23 of 80 tasks have no failing visible test; 70 of their 92 runs quit early. The scored tests are hidden. "Done" is undefined. | Family-specific templates, done criteria and signal gates (`ROADMAP_R4_R6.md`, "Task families: templates"). For Family B: at least one visible failing test at the start (G1), the target test files named, done defined. |
| **(c) Validity and difficulty** | large | 54 of 62 one-caller tasks need changes beyond the caller. Some hidden tests need content no prompt gives. | Gate on the gold change being close to the stated change. Drop commits that bundle feature work. |
| **(d) Model capability** | contributing | 60 early quits despite failing tests; test-file edits; failed `edit_file` calls; partial edits | Protocol help (C1 repeat notice, C2 pinned test result); the cross-model round (R6) |
| **PRISM-specific** | contributing | loops in 53 of 160 runs (baseline 15); 17 of 18 budget runs repeat calls; fewer edits (95 of 124 one-caller runs made none, baseline 64) | C1; B6 (relational summary); A1/A3 (D1, D2) |
| **(a) Scoring** | **none** | 10 of 10 developer fixes resolve; Kaggle's results consistent | — (optional: score edits to visible test files without blocking the hidden patch) |

**What R3a measured:** with about 30% of tasks giving no signal, and most of the rest needing more than the stated
change, the 80 tasks measured "can a 14B model complete a developer's whole commit from one function's diff". That
is not the caller-update skill PRISM targets. The null result stands, but says little about PRISM.
