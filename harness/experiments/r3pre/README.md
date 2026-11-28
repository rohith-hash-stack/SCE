# Analyses before R3 (A1-A3)

A1 and A2 use existing data and make no model calls. Their results are in `reports/r1/REPORT.md`, "Before R3". A3
makes 20 model calls. **Its rubric below was committed before any of them.**

## A3: planning a multi-file change from digests alone (pre-registered rubric)

**Tasks** (`planner.py mine`, `build`; no model):
- 20 merged pull requests from the R1 repositories with full clones.
- Each PR's branch has 3-30 commits and changes 30-50 existing source functions in 3-25 non-test files.
- Drawn in a seeded order (seed 5501), at most 4 per repository.

**What the model sees:**
- the PR's title;
- every changed function's name, file and PRISM digest at the PR's base, in a seeded random order. Digests are cut
  to the same number of lines for every function, so the prompt fits in 15,000 real tokens. No code is shown.

**What it is asked:** an ordered list of subtasks, each naming files and functions.

**Model:** `qwen2.5-coder:14b-instruct-q8_0`, the model of every earlier round, at temperature 0.2, seed 42,
1 call per task.

**Truth:**
- the order in which the PR's own commits first touched each file (ties: the same commit);
- the PR's number of commits;
- the call edges among the changed functions, from PRISM's index at the base.

**Measures (per task):**

| measure | definition |
|---|---|
| file order | Kendall's tau-b between the order files are first named in the plan and the truth order |
| order baseline | the same tau for the files in alphabetical order; random order has expected tau 0 |
| first file | the plan's first file is one the PR's first commit touched. Base rate reported: the share of files in that commit. |
| subtask count | the plan's number of subtasks is within a factor of 2 of the PR's commits |
| dependency order | over call edges between changed functions in different files: the share where the callee's file comes no later than the caller's in the plan |
| directory grouping | the share of subtasks whose files are all in one directory |

Unparseable answers count as plans with no files.

**Verdicts (fixed now):**
- **Digests support planning** if all of these hold:
  1. the mean file-order tau's bootstrap 95% interval (10,000 resamples over tasks, seed 5501) is above 0;
  2. the mean difference from the alphabetical baseline's tau has an interval above 0;
  3. the first-file hit rate is above its base rate.

  Otherwise: **not shown**.
- **Dependency structure is visible in the plans** if the mean dependency-order share has an interval above 0.5,
  over tasks with at least one edge between files.

**Limits (stated now):**
- This is exploratory: 20 tasks, one model, one seed.
- There is **no version with code**, so "digests support planning" means better than plans that ignore the content.
  It does not mean as good as planning with the code. That comparison belongs in R3.
- The truth is one developer's order, and other orders can be valid. So tau measures agreement, not correctness.

**Amendment (after building, before any call):** 19 tasks, not 20. Of the 24 candidate PRs, 5 kept fewer than 30
functions resolvable at the base, and the 4-per-repository cap left no replacements.
- A plan that names fewer than 3 of a task's files has no measurable order: its tau counts as 0 in the mean.
- Ties in the truth order (files first touched in the same commit) cap the best possible tau below 1. The scorer's
  self-test gives 0.86 for a plan in exactly the truth order on the first task.
- The rubric is otherwise unchanged. The summary and verdict are computed by `planner.py score`.
- **Reference (from the scorer's self-test, before any call):** the PRs' own commit order, scored as a plan, gives
  file-order tau 0.74 (the ceiling under ties), first-file hit 1.0 (base rate 0.35), and dependency order 0.56,
  with an interval reaching below 0.5. So developers do not work callees-first. The dependency verdict is reported
  beside this 0.56 reference, not as a target the truth itself meets.

**Amendment 2 (before any call):**
- **The primary is now the first-file hit, not tau.** The PRs' own commit order scores tau 0.74 against itself:
  work-in-progress commits, fix-ups and review rounds make the commit sequence a noisy reference. Agreement with a
  noisy sequence is not a clean measure of a plan. The first file is the decision that matters most for an agent.
- **Decision:** digests support planning if the first-file hit rate is above both baselines, with both bootstrap
  intervals (95%, 10,000 resamples over tasks, seed 5501) of the per-task difference above 0. The baselines are:
  - **random:** per task, the share of the PR's files its first commit touched;
  - **alphabetical:** whether the alphabetically first file was touched by the first commit.
- **Exploratory, beside it:** tau and its alphabetical baseline. Unchanged as **secondaries:** subtask count within
  2×, dependency order (with the truth-order reference 0.56), and directory grouping.
- **The tasks are rebuilt with digest v2,** which adds the parameter list. R3 will use v2, so A3 must test v2. It is
  the same 19 PRs by the same seeded draw; only the digest text in the prompts changes.

## A3 result (19 calls, `qwen2.5-coder:14b-instruct-q8_0` on Kaggle, run against `538949b`)

- No errors. 2 answers were cut at the answer cap and unparseable; by the rubric they count as plans with no files.
- **Primary, the first-file hit:** 8 of 19 = **0.42**.
  - Against random (base rate 0.35): +0.07 [−0.05, +0.20].
  - Against alphabetical (0.26): +0.16 [−0.11, +0.42].
  - **Both intervals include 0. Verdict: not shown (flat).** The point estimates are above both baselines, so this is
    a null, not a falsification.
  - On the 11 PRs where a random first pick rarely hits (base rate below 0.30), the plan hit **1** time. Nearly all its
    hits came where most files were in the first commit anyway.
- **Exploratory, tau:** 0.001 [−0.08, +0.08], against the developers' own order at 0.74 and alphabetical at −0.09.
- **Secondaries (context):**
  - subtask count within 2× of the PR's commits: 0.42;
  - dependency order: 0.45 [interval vs 0.5: −0.21, +0.12], against the developers' 0.56;
  - single-directory subtasks: 0.60.
- **Dependency structure visible:** not shown.
- **Decision for R3:** flat. R3 runs **without** a plan-from-digests step (recorded in
  `harness/experiments/r3/README.md`).

## What A3 tested, and what it did not (interpretation, after the result; no number changed)

- **The design:** each prompt gave the PR's title and **exactly the functions the PR changed**, each with its file
  and digest ("These N existing functions will be changed"), in a random order. It was one call, with no tools,
  no code, and no chance to explore or revise.
  - So the model was handed the answer set, functions and files, and asked to order it.
  - Discovery (from a request to the functions that matter) was removed entirely.
- **What it supports:** given a pre-selected set of changed functions as static digests, this model, in one shot,
  could not pick the PR's first file better than chance (0.42 vs 0.35 random and 0.26 alphabetical, intervals
  including 0).
- **What it does not support:** any claim about planning from a request, planning that explores the code, or
  planning that can consult digests along the way. It does not show that digests are useless for planning.
- **The null, restated (first correction):** ~~"digests alone did not let this model choose where a multi-file
  change starts better than chance"~~ → ~~"ordering a pre-selected list of changed functions from their digests, in
  one shot, did not beat chance."~~ → **"ordering a pre-selected list of node updates from digests, in one shot,
  without tools and without the ability to explore or revise, does not beat chance."**
- **Second correction: A3 asked the wrong question.**
  - A3 also tested PRISM as the planner. That is not the architecture. **The AI is the planner; PRISM provides
    primitives the AI uses:** look up a digest, see who calls a function, see what breaks, search.
  - A3 did not test whether the AI can plan a multi-step task using PRISM's primitives. It tested whether PRISM's
    summaries, given as a one-shot static input, could plan, which is not PRISM's job.
  - The correct question, whether the AI as planner benefits from PRISM's primitives, is tested in R3.
- **Scope correction:** A3 tested one flavor of multi-step work, ordering a pre-selected list of node updates.
  - Multi-step tasks also include creating, refactoring, moving, deleting, changing interfaces and adding tests.
    A3 tested none of them.
  - A3's null should not be read as evidence about those flavors.
- **A3 does not support anything about:**
  - the AI planning with PRISM's primitives;
  - creating, refactoring, moving, deleting, changing interfaces or adding tests;
  - planning from a user request;
  - iterative planning, with tools and with revision.
- **The R3 decision stands, for a different reason.** R3's agent plans the way real planning works: it starts from
  the request, explores with tools (including, by R3's amendment, a digest on request), drills in, and revises.
  - A dump-based plan step would add the kind of planning A3 tested, which is not the kind R3's loop already does.
  - So it is left out, not because A3 showed digests do not help planning, but because a one-shot ordering of a
    given list is the wrong shape of planning for an agent.
