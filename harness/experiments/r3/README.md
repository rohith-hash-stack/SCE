# Round R3: does PRISM help more when the work takes many steps? (pre-registration; not built, nothing run)

Every earlier round was single-shot. Blast, newfn and tests are multi-step by nature (`reports/r1/REPORT.md`,
"Scope"). R3 tests them as an agent works them: read, edit, run the tests, iterate. R1's labels stay the answers to
the single-shot question. The open items are decided in "Decisions" below. The one decision that waits for A3's result
(the plan-from-digests step) was settled by A3: no planning step. This file is hash-committed once it is filled in, before any R3 build.

## Hypothesis

**H-R3:** in a multi-step loop, an agent with PRISM's tools resolves more tasks than the same agent without them, and
the advantage grows with the task's depth.

It is falsified if either of these holds:
1. **No advantage:** the primary is `not_shown` or `padding_better` in both families (below). PRISM's value is then
   atomic-only: shown for single-shot forward, not for multi-step work.
2. **Flat advantage:** the primary passes, but the scaling test does not, that is the gap does not rise across depth
   strata. Then the reframe's mechanism is wrong. PRISM helps multi-step work by a constant amount, not by scaling
   with depth.

Before R3 there is one piece of evidence: A1. On R1's single-shot forward tasks, the gap rises with the number of
callers to update: +0.15 for 1 caller, +0.19 for 2, +0.35 for 3-4, +0.35 for 5+ (Spearman 0.22, p = 0.003). It is
post hoc, and the higher strata have few tasks (18 and 8).

## Task families

**Family B (blast-natural): propagate a change.**
- **Source:** a commit that changes a function's signature or contract and updates its callers. R1 forward's miner
  is used, keeping commits with at least 1 changed caller and a test change.
- **Start state:** the parent revision, plus the developer's change to the anchor only.
- **Prompt:** "Function X was changed as shown. Update the rest of the project so its tests pass."
- **Validity gate**, run before freezing, each test 3 times (a test that flakes is dropped):
  - fail-to-pass (F2P) tests: they fail at the start state and pass at the commit; there must be at least 1;
  - pass-to-pass (P2P) tests: they pass at both.
- **Depth strata:** the number of callers the developer changed (1, 2-4, 5+).

**Family N (newfn-natural): implement a function to its spec.**
- **Source:** a commit that adds one function and tests for it, using suite type 2's miner joined with type 6's
  gate.
- **Start state:** the parent revision.
- **Prompt:** the signature and the docstring or JSDoc, and the file (and class) where the function goes, as in suite
  type 2. The developer's new tests stay hidden.
- **Validity gate:** the hidden tests fail at the start state (the function is missing) and pass at the commit.
- **Depth strata:** the number of existing repository functions the developer's body calls (2, 3-4, 5+).

**Forward (secondary):** the Family B tasks in stratum "1 caller" are the forward analogue. They are reported
separately and are not in Holm's correction. Single-shot forward is already settled by R1.

**Tests: out of R3 (decided).** Type 6 was invalid by format in the suite, and a test-writing loop needs a
mutation-testing score to be meaningful, roughly doubling the cost. Family N's hidden tests already exercise
run-and-iterate.

**Blast is Family B.** Blast's natural framing is an action: propagate a change, fix the callers that break, make the
tests pass. That is Family B. There is no separate blast family.
- Blast2 (`harness/experiments/blast2/`) is demoted to a post-R3 explanatory test of the retrieval mechanism.
- It is not built or run before R3, and it does not gate R3.

**Size and selection:**
- **80 tasks per family (160 in all), committed.** With R1's variances, 80 per family gives 80% power for the
  pre-registered +0.10 floor; 50 would detect only about +0.20. R1's blast result was underpowered, and R3 should not
  repeat that.
  - If the full candidate pool yields fewer, a family runs with what it yields, down to a minimum of 50.
  - Below 50, that family is not run, and is reported as not run.
- Each family has at least 8 repositories, with at most 10 tasks from any one.
- At least half of the tasks come from repositories never used before. No suite, R1 or blast2 task is reused.
- **Training cutoff:** Qwen does not publish the training-data cutoff of `qwen2.5-coder:14b-instruct`. Its public
  release was 2024-11-12 [to be checked against the model card at freeze].
  - **Every task's commit must be dated on or after 2024-12-01**, so the model cannot have seen the developer's
    change.
  - The starting code may be in the training data. That is the normal case for an agent working in an existing
    repository, and it is the same for both versions.
  - All tasks are post-cutoff, so there is no pre-cutoff stratum to analyse. Commit dates are recorded per task.
- Languages: Python, and JavaScript or TypeScript where the repository's tests run in the environment builder.
- The repository list and the seeded draw (seed 5501) are committed before any task is gated.

## The loop and its parity (the control)

Both versions run the same agent loop, as fixed below.

| | both versions |
|---|---|
| model | `qwen2.5-coder:14b-instruct-q8_0` (fixed: the model of R1 and the suite), temperature 0.2, seeds 42, 1001, 2002 |
| loop | at most 30 turns; each turn is one model call that returns one tool call or `done` |
| window | 20,480 tokens per call; the history is kept by the same rule in both versions: keep the system prompt, the task and the last turns that fit; older tool outputs are replaced by one-line stubs |
| total budget | 300,000 input+output tokens per run, counted with the Qwen2.5 tokenizer; at the cap, the run ends as if `done` |
| per-call tool output cap | 4,000 tokens for every tool, in both versions |
| shared tools | `read_file`, `list_dir`, `grep`, `edit_file` (replace a span), `run_tests` (the project's test command; it reports failures, not which tests are hidden), `done` |
| system prompt | identical, except the list of tool descriptions |
| skill file | none in either version |

- **PRISM version:** the shared tools plus five PRISM tools, from the code frozen for R3:
  - `prism_change_context`, `prism_blast_radius`, `prism_new_function_context` and `prism_ask`;
  - **`prism_digest(symbols)`**, added by amendment: the digest (v2, with the signature) of up to 10 named functions
    per call, under the 4,000-token cap.

  So the agent can consult a function's digest whenever it explores, not only as part of a bundled context. Without
  it, digests reach the agent only inside the change context (the anchor and its callers) and the new-function
  context (helpers). R3 would then test the call graph, but not the digest as an exploration aid.
- **Baseline version:** the shared tools plus `related_code`. It returns the functions a BM25 search over the
  repository's functions ranks highest for a query, as code, cut to the same 4,000-token cap: R1's control, moved
  into the loop. Its counterpart to `prism_digest` is `read_file`, which already gives any function's code.
- **Parity is fixed by:** the same model, seeds, loop, window rule, total budget, per-call cap, shared tools and test
  command. ~~"and one extra search-type tool on each side"~~ **Corrected:** the tool count is not matched. PRISM's
  version has five extra tools and the baseline one, and that difference is the treatment. Every tool, on both
  sides, is bound by the same per-call cap and the same total budget, so neither version can see more tokens.
- **Note (A3):** in R3 the agent can request a function's digest as part of its exploration. A3's dump-based plan step
  is not included, because it tested ordering a pre-selected list, not planning from a request.

## Scope: the AI is the planner (after A3; no pass/fail rule changed)

**What each family gives the agent, and what it leaves to it:**

| family | given | left to the agent |
|---|---|---|
| B | the start state (the parent, plus the developer's change to one function), the prompt "Function X was changed as shown. Update the rest of the project so its tests pass.", and the project's test command | which callers break; in what order to fix them; how to fix each; when it is done |
| N | the signature, the docstring, and the file (and class) where the function goes, as in suite type 2 | which existing functions to reuse, how to structure the body, which tests to run, when it is done |

- No caller list, plan or approach is supplied in either family.
- **Note (pre-registration):** R3 tests the AI as the planner, using PRISM's primitives, which is the architecture.
- **Limit:** each task starts from a given root: a change already made (B), or a function already specified (N).
  Deciding *what* to change from a feature request, before any root exists, is not tested. It is a separate
  question for a future round.

## Tool audit: which primitives are generic, and which task flavors R3 covers

| tool | generic or task-specific | what it gives |
|---|---|---|
| `prism_digest(symbols)` | **generic** | any function's facts and signature |
| `prism_ask(question)` | **generic** | search |
| `prism_blast_radius(symbol)` | **generic**: any change to, or removal of, an existing symbol | every transitive caller |
| `prism_change_context(symbol)` | **generic for existing symbols**: updates, interface changes, refactors, moves, deletions; not for new code | callers with per-call-site facts, callees, conventions, callers' code, wider impact |
| `prism_new_function_context(file, name, signature, purpose)` | **task-specific**: creating a new function only | helpers, siblings, conventions |

| task flavor | tasks in R3 | PRISM tools that serve it | covered |
|---|---|---|---|
| update the callers of a changed function | Family B | change context, blast radius, digest, ask | **yes** |
| change an interface (signature) and propagate | Family B (most anchors change signature) | as above | **yes** |
| create a new function to a spec | Family N | new-function context, digest, ask | **yes**; its result is partly about a task-specific tool |
| refactor without changing behaviour | none | change context, blast radius (generic) | **no**: the tools would apply, but there are no tasks |
| move code between files or modules | none | blast radius lists callers, but no tool lists import sites | **no** |
| delete code | none | blast radius (generic) | **no** |
| add tests | none (test writing is out) | no test tool in R3 (`prism.impact.testcontext` exists but is not given) | **no** |
| a feature spanning several new functions | none (Family N adds one) | new-function context, once per function | **no** |

- **R3's results apply to the flavors marked "yes".**
- **Family N's result is partly a result about `prism_new_function_context`.** It is the one task-specific tool, and
  only Family N can use it.
- R3 does not test refactoring, moving, deleting, adding tests, or multi-function features.
- No tool is added for those now.


## Answer key and scoring

**Primary: resolved.** After the run, the final repository passes:
- Family B: every F2P and every P2P test;
- Family N: every hidden test, and every test that passed at the start.

It is binary per run. The per-task value is the mean over the three seeds.

**Diff quality (secondary, deterministic, no model judge):**
- the F1 of the set of files the agent changed against the merged commit;
- the F1 of the set of functions changed;
- Family N: helper reuse in the final code (`newfn.score_task`), and invented calls (scorer v2).

There is no model judge: the only judge available on the same hardware is the model under test.

**Process measures (secondary):** turns used; tokens used; runs that hit the budget or the turn cap, per version;
test runs; the first turn at which every F2P test passes.

## Tests and decisions

- **Primary per family:** PRISM vs baseline on the per-task resolved share. Wilcoxon signed-rank over tasks, plus
  McNemar per seed.
- **Holm** across the two families (B and N), α = 0.05, two-sided.
- Bootstrap 95% interval over tasks, 10,000 resamples, seed 5501.
- **Floor:** +0.10 resolved, absolute (80 tasks per family; see "Size and selection").
- **Scaling (named secondary, one per family):** Spearman correlation between a task's depth and its per-task gap
  (PRISM minus baseline), one-sided (rising). The prediction holds when ρ > 0 with p < 0.05, and the gap in the
  deepest stratum exceeds that in the shallowest with a bootstrap interval above 0.
- **Labels,** per family, as in R1-A, A3:

| label | condition |
|---|---|
| `invalid` | a version's cap-hit rate differs from the other's by more than 20 points, or more than 20% of runs died for reasons other than the model (infrastructure) |
| `padding_better` | the baseline is significantly ahead |
| `not_shown` | not significant, or the interval reaches 0 |
| `below_floor` | significant, but under +0.10 |
| `pass` | everything else |

- Runs that hit the budget or the turn cap are **not** excluded: they count as unresolved. In a loop, stopping is
  part of the outcome. The cap-hit rates are reported per version, and a gap above 20 points makes the result
  invalid.

**Negative branch (fixed now):**
- **Both families `not_shown` or `padding_better`:** "No multi-step advantage was shown." PRISM's demonstrated
  value shrinks to single-shot forward: organising the callers of one change. R1's blast and newfn single-shot
  results are not re-read as support. The product story for agents is limited to that.
- **One family passes:** the claim is limited to that family's kind of work.
- **Passes, but the scaling test fails:** "PRISM helps multi-step work by a constant amount." The depth claim is
  withdrawn.
- **No secondary rescues a primary:** neither diff quality, nor process measures, nor a stratum.
- **No re-tuning:** PRISM's tools, prompts or budget are not re-tuned and re-run on these tasks. A changed version
  needs new tasks and a new pre-registration.

## Cost (estimate, for the build decision)

- **Runs:** 160 tasks × 2 versions × 3 seeds = 960 runs (fallback: 100 tasks, 600 runs).
- **Calls:** about 15-25 turns per run, so about 14,400-24,000 model calls (fallback 9,000-15,000). R1 had 3,378.
- **Call time:** with contexts of 8,000-20,000 tokens, about 30-60 s per call on Kaggle's T4 ×2. That is about
  120-400 GPU-hours, or **11-36 Kaggle sessions of 11 hours** (fallback: 7-23).
- **Test runs:** about 8,000 on the CPU, alongside the model.
- **The pilot comes first:** 20 runs, 10 per family, on separate pilot-only tasks. It checks the tool-call format,
  completion within budget, that tests can be found and run inside the loop, that cap hits and truncation are logged,
  and the scoring path end to end on synthetic answers.
  - A failure in format or budget is fixed in the harness (the tool-call format, the parser, the history rule) and
    the pilot is run again.
  - **The model is not changed.** If no tool-call format works with it, R3 is reported as not runnable with this
    model, and a stronger model is a separate round (R4).

## Order and gates

1. **A1, A2 and A3 reported** (A3 needs its 20 calls run). They inform this design; they do not gate it.
2. **The open items decided (below), A3's step decided, and this file hash-committed.** This gates everything after it.
3. **R2 run and its result in hand.** R3's PRISM version uses the full change context; if R2 shows a section is dead
   weight, R3 still keeps it, and that is recorded. R2 gates the R3 build only so that R3's PRISM tools are frozen
   once, after R2.
4. **The JavaScript/TypeScript environment builder (named schedule item, does not exist yet).**
   - **What exists:** `harness/experiments/suite/execute.py` builds a `uv` environment per Python repository and
     revision and runs selected tests. Layer 4 runs Node tests only on corpora prepared by hand (express, tRPC), with
     a fixed command each.
   - **What is needed:**
     - per repository and revision: install from the repository's lockfile (npm, pnpm or yarn), pick the Node version
       from `engines` or `.nvmrc`, run any build step;
     - detect the test runner (vitest, jest, mocha, node:test, tap) and run selected tests with a machine-readable
       reporter;
     - flake detection for the validity gate, and caching between tasks of one repository;
     - installing Node in the Kaggle cell.
   - **Estimate:** about 2-4 days, most of it on the per-repository quirks of the 7 JavaScript/TypeScript repositories
     in the pool.
   - The validity gate for those repositories cannot run until it works.
5. **The harness built** (loop, tools, budget accounting, truncation and cap-hit logging from the start) and **the
   pilot run on pilot-only tasks.** Pilot findings may change the harness, as a recorded amendment, but not this file's rules.
6. **Tasks mined, gated and frozen** with a manifest of sha256 hashes.
7. **The Kaggle runs,** pinned to the frozen commit and manifest. **Then the analysis code,** committed before any
   answer is opened.

## Decisions (the draft's open items)

| item | decision | why |
|---|---|---|
| model | `qwen2.5-coder:14b-instruct-q8_0`, fixed | R3 must be comparable with R1 and the suite. A second model is a separate cross-model round (R4), never a substitution into R3. |
| task count | 80 per family, minimum 50 (see "Size and selection") | power for the +0.10 floor |
| budget for 80 per family | **[TBD — team]: approval of about 11-36 Kaggle sessions** | the cost section below. If it is not approved, the fallback (fixed now) is 50 per family, with the power statement that only about +0.20 is detectable. That is decided before any mining, never after. |
| training cutoff | commits on or after 2024-12-01; the release date is checked at freeze | no memorization of the change itself |
| repositories | the candidate pool below, in its order | at least half never used before |
| test writing | out | as above |
| skill file | **out of scope**, recorded as a known limit | Agents in practice often get a repository skill file. R3 measures PRISM's tools without one, in both versions alike. A skill-file round is separate. |
| blast | Family B is blast-as-action; no separate family; blast2 is post-R3 explanatory | as above |
| plan-from-digests first step | **none** | A3 (first-file hit 0.42 vs 0.35 random, 0.26 alphabetical; intervals include 0) tested ordering a pre-selected list of changed functions in one shot, not planning from a request (`harness/experiments/r3pre/README.md`, "What A3 tested"). R3's agent plans by exploring, with `prism_digest` available. A one-shot ordering step would be the wrong shape of planning, so neither version has one. No pass/fail rule changed. |
| digest on request | **added: `prism_digest(symbols)`** | without it, R3 could not test the digest as an exploration aid; tools are a design parameter, not a scoring rule |
| equivalence margin, floor, Holm family, negative branch | unchanged | not open items |

**Repository pool, confirmed against the cutoff** (`pool_scan.py`, 34 repositories; counts are candidates
before the validity gate):
- "Since cutoff" counts non-merge commits on or after 2024-12-01.
- "30-50" counts commits changing 30-50 existing functions.
- B and N are the candidates defined in `pool_scan.py`.
- A repository qualifies for a family with at least 5 candidates.

| repository | new | since cutoff | 30-50 | B | N strict | N relaxed |
|---|---|---|---|---|---|---|
| `tobymao/sqlglot` | yes | 3,773 | 2 | **66** | 2 | **5** |
| `vitest-dev/vitest` | yes | 2,189 | 2 | **106** | **6** | **32** |
| `unjs/nitro` | yes | 1,619 | 0 | **40** | 3 | **21** |
| `unjs/h3` | yes | 746 | 0 | **21** | 2 | **6** |
| `sindresorhus/execa` | yes | 117 | 0 | **20** | 0 | 0 |
| `python-poetry/poetry` | yes | 490 | 0 | **18** | 2 | 4 |
| `unjs/unstorage` | yes | 276 | 0 | **17** | 0 | 0 |
| `litestar-org/litestar` | yes | 597 | 0 | **14** | 1 | 4 |
| `pypa/packaging` | yes | 444 | 1 | **10** | 1 | **5** |
| `mswjs/msw` | yes | 227 | 0 | **10** | 0 | 0 |
| `aio-libs/multidict` | yes | 700 | 3 | **10** | 0 | 1 |
| `unjs/unplugin` | yes | 244 | 0 | **7** | 0 | 0 |
| `aio-libs/yarl` | yes | 464 | 1 | **6** | 0 | 0 |
| `fastapi/typer` | yes | 933 | 1 | **5** | 0 | 0 |
| `scrapy/scrapy` | used | 1,065 | 4 | **62** | **6** | **22** |
| `aio-libs/aiohttp` | used | 3,229 | 2 | **41** | 1 | **6** |
| `pallets/werkzeug` | used | 280 | 1 | **16** | 1 | 2 |
| `tornadoweb/tornado` | used | 314 | 0 | **7** | 1 | 2 |

**Dropped (short for every family):**
- new: structlog, loguru, more-itertools, pendulum, arrow, tomlkit, pluggy, ts-pattern, neverthrow, ofetch, cattrs,
  c12, p-queue;
- used: marshmallow, rich, fastify.

**Added after the first scan**, as replacements from the same language families: litestar, yarl, multidict and
poetry (Python); unstorage, nitro, vitest and unplugin (JavaScript/TypeScript). cattrs, c12 and p-queue were scanned
as replacements too, and were short.

**Family B: confirmed.**
- 18 repositories qualify: 11 Python (sqlglot, poetry, litestar, packaging, multidict, yarl, typer, scrapy, aiohttp,
  werkzeug, tornado) and 7 JavaScript/TypeScript (vitest, nitro, h3, execa, unstorage, msw, unplugin).
- With at most 10 tasks per repository there are 165 candidates, 128 of them from new repositories.
- Reaching 80 gated tasks needs about half the candidates to pass the gate.
- **Family N: not confirmed. [TBD — team]**
  - Under the strict rule, 2 repositories qualify, with 12 candidates.
  - Under the relaxed rule (any new function a changed test names; `pool_scan.py`), 7 qualify, with 52 candidates
    at 10 per repository before the gate. After the gate this falls short of the minimum of 50, so under the current
    rules Family N would not run.
  - **Options:**
    1. the relaxed rule with at most 20 tasks per repository: about 82 candidates before the gate, from 7
       repositories;
    2. allow commits before the cutoff for Family N, with a pre/post-cutoff stratum and the primary on
       post-cutoff tasks only;
    3. drop Family N from R3, and run it as its own round.
  - **Recommendation: option 1**, with option 3 as the fallback if fewer than 50 tasks pass the gate. Option 2
    reopens the memorization question that the cutoff rule closes.

"Never used" means the `owner/repo` name appears nowhere in this repository's benchmarks, harness, reports or
docs, as checked when this file was written. The half-new rule is enforced on the tasks, not on the repositories:
mining stops taking used repositories once they would hold more than half of a family's tasks.

## Recommendations awaiting team approval (recorded before the hash; no pass/fail rule changed)

| item | recommendation | why |
|---|---|---|
| budget | **Family B at 80. Family N at 80 under the relaxed rule if the gated pool reaches 80; otherwise as below.** About 11-36 Kaggle sessions with both families, 6-18 with Family B alone. | R1's blast test was underpowered (about 54%), and 80 gives 80% power for the +0.10 floor. Family B's pool (165 candidates) supports 80 if about half pass the gate. |
| Family N | **Option 1: the relaxed rule, at most 20 tasks per repository, target 80.** If 50-79 pass the gate, it runs at that size, with its power statement amended before any model call ("effects below about +0.15 to +0.20 are not detectable at this size; a `not_shown` result for Family N is underpowered"). If fewer than 50 pass, it is dropped from R3 and run as its own round with a wider pool. | It keeps the post-cutoff rule, which rules out memorization, rather than reopening it (option 2). The relaxed rule changes only which commits are candidates, not how they are scored. |
| JS/TS builder contingency | **One-week rule.** If the builder does not pass its own check within 5 working days of starting, on all 7 JS/TS repositories (it installs, finds the tests, and runs a named test at two revisions), R3 round 1 is Python-only, and the JS/TS tasks are R3b under these same rules. | A slipping builder should not hold up the Python half. |

**What Python-only round 1 would be:**
- Family B from the 11 Python repositories: about 98 candidates before the gate, at most 10 per repository; 61 of
  them from new repositories, so the half-new rule holds.
  - Target 80, minimum 50, with the same power amendment as Family N if it lands between 50 and 79.
- Family N Python-only: about 36 candidates under the relaxed rule, below 50, so it would not run in round 1. It
  waits for R3b, or its own round.

**Also to fix before the hash:**
1. **Pilot-only tasks:**
   - commits from the same pool repositories dated **2024-10-01 to 2024-11-30**, so they never overlap the main
     tasks;
   - gated the same way;
   - 10 per family, drawn with seed 6601;
   - never reported as results.
2. **Gate attrition:** the number of candidates mined, gated and passed is reported per repository and family, so a
   family's final size is traceable.
3. **The model's release date:** 2024-11-12 is to be checked against the model card at freeze, as written above. If
   the card shows a later date, the cutoff moves to the first day of the month after it, before any mining.

**Not blocking the hash, but gating the build:** R2's two open items (held-out repositories, language mix) must be
decided and R2 run, because R3's PRISM tools are frozen after R2 (step 3).
