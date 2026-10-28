# PRISM task suite: six kinds of work on ten repositories (pre-registered before mining)

This suite replaces the 22-repository plan ("Run 4" in `../change_history/README.md`): ten repositories,
six kinds of tasks. Every answer key comes from the repositories themselves (their git history, or their own
tests run with a tracer), except type 5, which is written by hand and reported separately. Each type is
mined, selected and frozen before any model sees it. The selection seed, the sizes and the scores are fixed
here.

## Repositories

| language | repositories |
|---|---|
| Python | `pallets/werkzeug`, `marshmallow-code/marshmallow`, `Textualize/rich`, `tornadoweb/tornado`, `aio-libs/aiohttp`, `scrapy/scrapy` |
| JavaScript | `axios/axios`, `mochajs/mocha`, `fastify/fastify`, `hapijs/hapi` |

None was used in earlier runs or to design PRISM's change context or break check. `koajs/koa` was in the
first list; it was replaced by `hapijs/hapi` before any task was selected, because its whole history gives
one candidate commit.

## Model and settings (all types)

- Model `qwen2.5-coder:14b-instruct-q8_0` (Ollama 0.34.0, Kaggle T4 ×2), temperature 0.2, seed 42.
- Context window 20,480 tokens; answers capped at 4,096.
- Ollama's own token counts and stop reason are saved with every answer.
- Every version of a task has the same instructions. Only the context differs.

## The six types

### 1. Forward: update the callers a change breaks

- **Source:** the run-2/3 miner (`change_history/mine.py --forced`), the same filter and tiers, with
  `--no-self-callers`.
- **Versions:**
  - `FILE`: the anchor's file;
  - `PRISM`: + the change context;
  - `PRISM_BREAKS`: + the wording-2 break section.
- **Scores:** callers found; wrong functions named; added lines kept; removed lines gone; new code parses.

### 2. Backward: write a new function that reuses the project's code and follows its style

- **Source:** commits that add one new function (6-60 lines, not a test, not nested, not `__dunder__`)
  whose body calls at least two functions that already existed (resolved in the parent's index). At most 3
  changed source files.
- **The task:** the file at the parent, the new function's signature and its description at its real place;
  the AI writes the body.
- **Amendment (before selection, from a sample of candidates):**
  - a description is required: the docstring (Python) or the `/** */` comment above the function
    (JavaScript), at least 5 words. A commit subject was too often unrelated to the function;
  - the function's class must already exist at the parent.
- **Versions:**
  - `FILE`: the file;
  - `PRISM`: + `prism.impact.newcode`'s context: helpers that fit (by name, docstring and group), their
    digests, the group's conventions, and the two closest siblings.
- **Scores:**
  - helper reuse: the share of the existing functions the developer's body calls that the AI's body also
    calls;
  - invented calls: calls to repository-looking names that do not exist;
  - the code parses;
  - the naming style matches the group's convention.

### 3. Bug localisation: which functions must change to fix this bug

- **Source:** commits whose subject says they fix something and which also change a test.
- **Amendment (before selection, from a sample of candidates):**
  - only `fix`, `bug`, `regression`, `crash`, `broken`, `incorrect` and `wrong` count: `error` and
    `issue` also matched refactors;
  - at least 8 words besides "fix"/"bug" and the masks are required;
  - SVN/Mercurial conversion lines are removed;
  - a description in which an answer name survives the masking is dropped. The commit must modify 1-4 existing source functions in
  1-3 files, with at most 120 changed source lines.
- **The bug description:** the commit's subject and body, with every name of a changed function, its class
  and its file masked as `<name>`. Descriptions shorter than 6 words after masking are dropped.
- **Versions:**
  - `TREE`: the repository's file list;
  - `BM25`: + the 10 functions a BM25 search over function code ranks highest, with their code;
  - `PRISM`: + the 10 functions `prism.ask` ranks highest (no summaries), with code and digests.
  - All three fit the same budget (6,000 tokens of context).
- **Scores:**
  - recall@1 and recall@5 of the changed functions in the AI's ranked answer;
  - the retrieval's own recall@10, without a model.

### 4. Blast radius: what does a change to this function affect

- **Source:** each Python repository's own test suite, run at a fixed commit with the runtime tracer
  (`harness` Layer 4). This records which production functions really call which. Anchors: functions with
  at least 3 traced production callers. JavaScript repositories join only if their test suites run under
  the Node tracer; otherwise they are listed as not covered.
- **Versions:**
  - `FILE`: the anchor's file;
  - `GREP`: + every line in the repository that mentions the anchor's name;
  - `PRISM`: + `prism.blast_radius`.
- **Scores:** recall of the traced callers, direct and transitive. A traced call is real, but the suite may
  not reach every caller, so precision is reported only against traced plus PRISM-certain callers, and
  labelled as such.

### 5. Conceptual questions (hand-written; reported separately)

- **Source:** questions written from each repository's documentation ("how does X work", "where is Y
  handled"). Each question has the functions the documentation describes as its answer. They are written
  and frozen before any version is run.
- **Versions:**
  - `TREE`: the repository's file list;
  - `BM25`: + the top 10 functions by BM25;
  - `PRISM`: + `prism.ask`.
- **Scores:** recall of the answer functions.
- **Weight:** never used for a decision. The answer key is ours, not the repository's.

### 6. Write tests for a function (Python repositories)

- **Source:** commits that only add tests: new test functions, no source change. The target is the
  production function the new tests call most (resolved in the parent's index).
- **Amendments (before selection, from a planning trial on rich):**
  - the target may be a class, since most tests construct one (`Tree(...)`, `Table(...)`);
  - it must be called by at least half of the new tests;
  - its name must be unique in the repository;
  - ties go to the one defined in the file the test file is named after (`test_tree.py` → `tree.py`);
  - commits are taken from 2019 on, because older revisions rarely install on today's Pythons.
- **The task:** write tests for the target in the test file.
- **Versions:**
  - `FILE`: the target's file and the test file;
  - `PRISM`: + `prism.impact.testcontext`'s context: the target's change context, the existing tests that
    reach it, the two closest tests' code, and the test conventions (fixtures, assertion style).
- **Scores:** run in the repository at the parent commit, on Kaggle:
  - the tests run;
  - the tests pass;
  - the tests call the target;
  - line coverage of the target by the AI's tests, compared with the developer's tests.
- **Validity gate:** before any model runs, tasks whose developer tests do not pass in that environment
  are dropped.
- JavaScript test files are not covered by this type: their test functions are callbacks, not named
  functions.

## Sizes and selection (fixed now)

- **Per type and repository:** up to 20 tasks, drawn with seed 5501 from that type's candidates in that
  repository (types 1-3 and 6). Type 1 keeps its tiers: up to 7 easy, 7 medium and 6 hard per repository,
  with any shortfall filled from the other tiers.
- **Type 4:** up to 15 anchors per Python repository.
- **Type 5:** 10 questions per repository.
- A repository that yields fewer tasks gives what it has; nothing is redrawn after tasks are seen.

## Tests and decisions (fixed now)

- **Per type:** the primary comparison is `PRISM` vs the strongest baseline (`FILE` for types 1, 2 and 6;
  `BM25` for type 3; `GREP` for type 4). It is tested two-sided, at α = 0.05:
  - McNemar's exact test for yes/no outcomes per item (a caller found, a test passing, a bug function in
    the top 5);
  - the Wilcoxon signed-rank test for per-task scores (helper reuse, coverage, recall);
  - with a bootstrap 95% interval (10,000 resamples over tasks, seed 5501).
- **The five primary tests are corrected with Holm's method** (type 5 excluded).
- **Type 1 also tests `PRISM_BREAKS` vs `PRISM`,** with the decision rule of the run-4 pre-registration:
  - the break section becomes the default when new code is known only if it finds significantly more
    callers;
  - and names no more wrong functions per task.
- **Reporting:** per type, per repository and per language.
  - A type where PRISM is not better is reported as such.
  - A version that loses is not tuned and re-run on the same tasks.

## Execution

- Several Kaggle sessions, one cell per type.
- Each cell resumes from the answers already pushed.
- Type 6's cell also builds each repository's environment at every task's parent commit.
