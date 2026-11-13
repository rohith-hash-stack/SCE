# R4 amendments

## R4-SANITY-2 (2026-11-11): Verification at done

**Status:** approved by the user (2026-11-11). A protocol change, applied to both arms with shared wording. It is a
development change in the sanity phase, not a pre-registered test, and goes into R4's pre-registration before the
main run.

**What changes:**
- **From the second sanity run on, a `done` call is accepted only if a gate verifies the task's goal** against the
  working tree. The gate uses checks the model could make itself (`done_gate.py`):

  | Family | Check |
  |---|---|
  | every family | at least one file changed |
  | delete | the target is no longer defined in its file, and no visible test that passed at the start (3 runs, flaky dropped) now fails. Deleted tests do not count |
  | feature | the listed fail-to-pass tests pass and the visible pass-to-pass tests still pass, with the test files restored to their start version for the check |
  | Family B | no new failure among the visible tests |

  The gate never uses the hidden scoring tests or PRISM's output.
- **A rejected done returns specific feedback.** It says what is still unmet: the target and its line, failing test
  names and the first error lines. It never lists call sites (user decision C1: listing them would shrink the
  PRISM-vs-baseline comparison; if ever added, grep-derived only). A rejected done counts as a turn.
- **Three rejected dones in a row** with no applied edit between them end the run as **`stuck_done`**. It counts as
  unresolved and is reported separately.
- **The task message states the gate and its checks** (`sanity_gate_tasks.gate_paragraph`). Both arms get the same
  gate, wording and limits.
- **Scoring is unchanged except for one thing.** The scored test files are restored to their start version before the
  hidden patch is applied: the files the hidden patch touches and, for feature, the task's test files. This fixes the
  first run's delete scorer bug, where the hidden patch did not apply over the agent's test edits.
- **A new counter, "gate accepted, scorer unresolved"** (user addition), measures how lenient the gate is
  (`sanity_report.gate_counts`).

**Why:** in the first sanity run, 25 of 37 failed runs ended with an accepted `done` while the goal was unmet. The
example is r4s-delete-02: done after 1 of 5 edits, with the target still defined and 58 tests failing. The protocol
intended `done` to mean "complete", but the model used it to stop. The gate enforces that intent.

**Not changed:**
- R3a's results;
- the turn cap (28), the window rule and the budget;
- the task prompts otherwise. In particular, the delete prompt still names the target by its index name: the
  explicitness fixes stay parked.
- The 12 runs that ended without calling done (edit-failure loops) are a separate problem; the edit-error fixes are
  parked until this run is read.

**Build check (before the run):** each task's developer change must pass the gate, and the unchanged start must not
(`sanity_gate_tasks.py`). For delete, the developer change is the source and test changes; for feature, the source
changes.

### Expectations, recorded before the second sanity run (user, 2026-11-11)

| Measure | Expected |
|---|---|
| Early done | near zero. **Accepted** early dones are near zero by construction; **attempted** early dones (rejected) are reported too |
| `stuck_done` | a new outcome; it will appear |
| Loop rate on the first run's 12 edit-loop runs | about unchanged: the gate does not help runs that never call done |
| Solve rate | uncertain |

**If the solve rate holds flat,** the edit-error fixes (currently parked) are the next step.

## R4-SANITY-3 (2026-11-11): explicit tool messages, the delete prompt, and the gate's no-change message

**Status:** approved by the user (2026-11-11), from `SANITY2_RESULTS.md`'s fixes 1–4. Both arms, shared wording.
Development changes in the sanity phase. The turn cap, budget, seeds and gate logic are unchanged, apart from fix 1.

**The fixes:**
1. **The gate's no-change message** (`done_gate.py`): when no file has changed, a delete task's rejection also states
   the target's status ("… is still defined in FILE at line N"). It is a fact, not an instruction, and stays within
   the approved scope: the target and its line, no call sites.
2. **Edit errors that help** (`r3/shared_tools.py`, `explicit=True`):
   - "not found" shows the closest lines in the file, numbered as `read_file` shows them;
   - "occurs N times" gives the line numbers of the matches;
   - an empty `old` on an existing file is explained ("an empty `old` only creates a new file; give the exact lines to
     replace").
3. **An honest cut read:** a `read_file` that hits the 4,000-token cap shows only whole lines and says so, in its
   header and at its end: "lines 1–212 of 3265 shown (cut at 4,000 tokens); continue with start=213".
4. **The delete prompt** (root cause R1, `sanity3_tasks.py`): the target is named as written in the code, with its
   kind and its start line, e.g. "Remove the method `_generate_definitions` of class `GenerateJsonSchema` from
   `pydantic/json_schema.py` (it is defined at line 377)". The gate paragraph and the gate's messages use the same
   name.

**Held:** PRISM's "not found" hints (R7), for the PRISM tool work.

**Tasks:** `sanity/tasks_v3.json`, the second run's 20 tasks with `explicit_tools` and the delete prompts above. The
tasks' gate check still holds, because the checks themselves are unchanged.

### Expectations, recorded before the third sanity run (user, 2026-11-11)

"Sanity 3 expectations: (1) Loop rate drops (target <30%). (2) Applied edits rise to ≥60%. (3) Solve rate rises above
Sanity 2's 2 delete / 0 feature. If applied edits rise but solves don't, the binding constraint becomes task
difficulty — next step is difficulty calibration or model change. If applied edits don't rise, the edit-error fixes
are wrong — next step is diagnosis. If solves rise, pipeline is validated for R4. Note: if zod/pydantic solve, the
prompt fix is likely the primary driver — the gate's status message shouldn't be doing the finding."

**To report when it lands:**
- the applied-edit rate (target ≥60%) and the loop rate (target <30%);
- the solve rate per family, the `stuck_done` count and the gate-leniency counter;
- for zod and pydantic: solved via the prompt fix, the gate fix, or both. This is read from the turn logs: whether
  the first edit to the target came before or after a rejection that stated the target's line;
- any new failure modes.

**Decisions, as recorded:**
- **Solves rise:** the R4 pipeline is validated. Next are R4's remaining prerequisites: the upstream check's false
  alarms, `prism_check_changes`, and R4's pre-registration.
- **Applied edits rise, solves don't:** task difficulty. Next is difficulty calibration or a model change, not more
  tool fixes.
- **Applied edits don't rise:** diagnosis before any more iterations.

## R4-SANITY-4 (2026-11-12): the three harness bugs fixed; thinking + planning tested first

**Status:** approved by the user (2026-11-12). Both arms, shared wording. Same 20 tasks (`tasks_v3.json`), seed 42,
turn cap 28; the gate stays active.

**Bug fixes** (all from `SANITY3_RESULTS.md`):
1. **The indentation message** (`r3/shared_tools.py`): when `old` matches once apart from whitespace, the error shows
   the file's own lines there, numbered as `read_file` shows them, so the indentation can be copied. Before, it only
   said "copy the indentation exactly".
2. **The gate and vanished tests** (`done_gate.py`): a start-passing test that did not run counts as deleted by the
   model only if the model changed that test file and the test's name is gone from it. Otherwise it is a regression
   ("N tests that passed at the start did not run …"), and done is rejected. Sanity 3's zod case: one of the suite's
   two copies stopped running after a syntax error. Vitest's "Transform failed … ERROR:" lines are now quoted.
3. **Vanished tests are visible to the model** (`run_tests`): a file whose start-passing tests (delete:
   `visible_start_pass`; feature: the listed tests) did not run, or whose test count fell since an earlier run in the
   same task, gets a warning: "N tests in this file that ran at the start did not run now …".

**The configuration:**
- **Qwen3-14B (`qwen3:14b-q8_0`)** with thinking on (answer limit 12,288 tokens, thinking included; the prompt window
  unchanged) and the plan field (`--plan`). Passed to every run via `pilot_run` (`--model --think --plan
  --answer-tokens`).
- **40 runs,** about 13–28 GPU-hours, 2–3 sessions.
- **No current-model runs and no comparison runs** unless this configuration fails.
- **The optional attribution run** (thinking without planning) is not run.

### Expectations, recorded before the run (user, 2026-11-12)

"Testing thinking+planning config first. (1) Edit rate target ≥78% (thinking alone was 78% on old harness; planning
should be neutral or slightly worse). (2) Solve rate target ≥3 of 20. (3) If <60% edits → planning is actively
hurting, revert to thinking-without-planning. (4) If edits ≥78% and solves ≥4 → this config goes to R4 directly,
comparison optional. (5) If edits ≥60% but solves <4 → model can edit but tasks too hard; next is difficulty
calibration, not model config. (6) If edits <50% → bugs weren't the only problem; think again."

**Measures:**
- **Edit rate** = applied / attempted `edit_file` calls, over all 40 runs; the "solve rate" counts tasks solved, per arm.
- **Rules (3) and (6) overlap below 50%.** Both are reported.

**To report:**
- the edit rate (target ≥78%), the solve rate per family (target ≥3 of 20), the loop rate;
- false dones: the gate should catch all, and the "gate accepted, scorer unresolved" count is reported;
- actual GPU-hours;
- a per-task breakdown of what failed and why;
- the comparison with the planning test's 78% edit rate on the old harness.

### Addition before the run (user, 2026-11-12): the per-turn progress summary (ledger)

- **What it is** (`progress_ledger.py`): every turn after the first, the harness appends a short summary (~200
  tokens) to the newest message. It is rebuilt from tool results each turn, never written by the model, so it
  survives the window rule. It has seven deterministic items (user, 2026-11-12):
  1. the turn number (N of 28);
  2. the files read so far, with the line ranges shown;
  3. the edits applied (file:line, and what changed, e.g. "replaced 7 lines with 1");
  4. the edits failed (the count, and the last 3 with turn, file and reason);
  5. the latest test result against the start state, per file: "M of the N tests that ran at the start pass now; K
     fail or did not run". It comes from the model's own last `run_tests`;
  6. task progress: for delete, the target still defined, at which line (the gate's file check); for feature, how
     many listed tests still fail (tests in files not yet run are counted apart);
  7. a one-line reminder of the goal.
- **What it never does:** run tests by itself, name callers or places to fix, or use the hidden tests. It is the same
  in both arms.
- **Why:** the model loses its earlier results once outputs are stubbed, and its earlier thinking is never kept (the
  thinking run called its own edits "the user has already tried that"). A self-written summary would carry its false
  beliefs forward ("Removed the method and its usages" after 1 of 5 edits applied); a harness-built one cannot.
- **Attribution caveat:** sanity 4 now changes three things against sanity 3: the model with thinking, the plan field
  and the ledger (plus the bug fixes). Its result tells whether this configuration works, not which part helps. Per
  the decision above, there are no comparison runs unless it fails.
- **Also reported:** how often a turn's plan contradicts the ledger's facts, for example planning an edit that is
  already applied, or claiming tests pass after a failing run.

## Post-Sanity-4 ablation plan (pre-registered 2026-11-12, while sanity 4 runs, before any of its results were seen)

Recorded verbatim (user, 2026-11-12):

"Post-Sanity-4 ablation plan (conditional on S4 outcome):
- If S4 fails all thresholds: no ablation, return to diagnosis.
- If S4 passes: run A4 first (bugs + summary, current model, no thinking/planning, 40 runs, ~3.5 hrs).
  - A4 passes → R4 on A4 config. Skip thinking entirely. Best outcome — 4–8× cheaper.
  - A4 fails → run A1 (bugs + planning + summary, current model, no thinking, 40 runs, ~3.5 hrs).
  - A1 passes → R4 on A1. Skip thinking.
  - A1 fails → thinking essential. Run A2 (bugs + thinking + summary, no planning) and A3 (bugs + thinking + planning,
    no summary). R4 on minimal winning config.
- Pass = edit rate ≥78% + solves ≥4 of 20 + loops <30%. Same as S4.
- All configs use fixed harness, same 20 tasks, seed 42.
- The order is fixed: A4 → A1 → A2/A3. Not reordered after seeing S4 results."

**Why pre-registered:** to prevent post-hoc ordering bias. Looking at S4 first invites testing "the variable that
looks good"; a fixed order removes that.

**The configurations, in terms of the harness flags** (all with the fixed harness, the gate, `tasks_v3.json`, both
arms, seed 42, turn cap 28):

| Config | Model | `--think` | `--plan` | `--ledger` |
|---|---|---|---|---|
| S4 (running) | qwen3:14b-q8_0 | on | yes | yes |
| A4 | qwen2.5-coder:14b-instruct-q8_0 (the current model) | — | no | yes |
| A1 | qwen2.5-coder:14b-instruct-q8_0 | — | yes | yes |
| A2 | qwen3:14b-q8_0 | on | no | yes |
| A3 | qwen3:14b-q8_0 | on | yes | no |

**Measures,** as defined for S4: the edit rate is applied / attempted `edit_file` calls over all 40 runs; "solves"
counts tasks resolved out of 20, per arm; the loop rate is the share of runs with a loop (R3's `turn_flags` rule).

**Open, flagged before the results (not resolved here):** the plan defines "S4 passes" (all three thresholds) and "S4
fails all thresholds", but not a partial result, for example edits ≥78% and loops <30% but solves <4. S4's own
recorded rules give a reading for some of those cases (rule (5): edits ≥60% with solves <4 means task difficulty).
Which branch a partial S4 result takes is for the user to decide, ideally before the S4 results are read.

## Phase 0: model capability check, pre-registered (2026-11-13, before any Phase 0 run)

**Status:**
- **Approved by the user** (2026-11-12/13). Phase 0 runs in the cloud environment, launched by the assistant, with
  three checkpoints at which it pauses for the user's go-ahead.
- **Paused** until the user upgrades the Groq plan or confirms another provider. The account's limit for
  `openai/gpt-oss-120b` is 8,000 tokens per minute (from Groq's rate-limit headers, 2026-11-13). That is below one
  agent turn's prompt, which triggers stop condition (a) below.

**Question:** a capability claim, not a PRISM claim. Can this model complete the sanity tasks on the fixed harness?

**Protocol:**
- **Model:** `openai/gpt-oss-120b`, reasoning effort `medium`.
  - Provider: Groq, or the one the user confirms. The provider and model ID are recorded per call.
  - Seed 42 is passed and recorded, though the provider may not honour it.
- **Client** (Phase 0a, `r3/loop.OpenAIChat`):
  - the tools as native function schemas (`--native-tools`; `r3/loop.native_tools`): one schema table, the same for
    both arms; the model's call is mapped back to the loop's text format; `tool_choice` required, no parallel calls;
  - JSON mode off. Groq's JSON mode returned `json_validate_failed` with an empty generation in the 0a smoke test;
  - answer limit 8,192 tokens, reasoning included; 300 s per call;
  - up to 3 retries on HTTP 429/5xx, waiting for Retry-After; retries are not turns.
- **Run endings:**
  - a call that still fails after its retries ends the run as an infrastructure death;
  - a provider-refused tool call (HTTP 400 `tool_use_failed`) is a bad reply that costs the model its turn.
- **Harness:** the fixed sanity-4 harness without the plan field:
  - the gate (R4-SANITY-2) and the three bug fixes (R4-SANITY-4);
  - the explicit tool messages and the delete prompt (R4-SANITY-3);
  - the progress summary (ledger) on;
  - the full-prompt and full-output hashes in every turn record (Phase 0b, committed before any Phase 0 run).

  Turn cap 28; window and budget unchanged.
- **Tasks:** `sanity/tasks_v3.json`, the same 20 tasks, both arms: 40 runs.

**Measurements:**
1. infrastructure deaths;
2. parse errors per turn, with provider-refused tool calls counted apart;
3. edit rate: applied non-churn edits over attempted, with churn (re-edits of the same lines with no test run in
   between) reported separately;
4. false dones: gate rejections, `stuck_done`, and what follows a rejection;
5. tests run after the last edit and before done;
6. solves per family per arm;
7. loop rate: R3's rule, plus churn;
8. gate accepted, scorer unresolved;
9. tokens, dollars and wall time per run, and the rate-limit headers.

**Pass bar (all four):**
- ≤ 2 of 40 infrastructure deaths;
- parse errors < 2% of turns;
- ≥ 4 of 20 tasks solved in at least one arm;
- a non-churn edit rate ≥ 60%.

**Readings:**
- **Pass:** go to Phase 1.
- **Infrastructure fail:** fix the client or limits and re-run; nothing about capability is concluded.
- **Infrastructure fine, 0–1 solves:** the ceiling check (gpt-oss-120b at `high` effort, BASELINE arm, the 20
  tasks).
  - If it also solves ≤ 1, the tasks are the limit, and task validity is next.
  - If it passes, the next decision is effort or model size.
- **2–3 solves:** pre-declared as not a pass. Reported; the user decides.

**Rate limits (user, 2026-11-12):**
- **Serial without approval** if the pilot shows parallel lanes are throttled.
- **Stop and report** if:
  - (a) a single call exceeds the per-minute limit;
  - (b) the projected serial time is over 12 hours;
  - (c) the projected tokens exceed the daily cap.
- **(b) and (c)** are the user's decisions (upgrade the tier, or spread the run over days).

**Checkpoints (user, 2026-11-13), each a pause for the user's go-ahead:**
1. 0a and 0b ready, before the pilot.
2. After the 2-run pilot: rate limits, anomalies, projected serial time and tokens.
3. After 10 of 40 runs: infrastructure deaths, parse errors, the edit rate so far, surprises.

### Phase 0b: "identical" for replays (user, 2026-11-12)

"'Identical' means same Python version, same installed package versions (recorded in the replay record), same OS,
same environment variables. Comparison across machines is out of scope."

**How the replay tool applies it** (`harness/experiments/r4/replay.py`):
- **The environment record:** every replay writes `replay.json`, with Python version and implementation, OS and
  machine, every package with its version, environment variable names and one hash of their values (secret-looking
  values left out), and the code commit.
- **Different environments:** two replays whose records differ compare as `not_comparable`, with the differences
  listed.
- **Within one environment:** each turn's tool, arguments, prompt, output and gate decision are compared, then the
  outcome and the scores per test ID.
- **The results:**
  - `identical`: byte for byte;
  - `identical_normalized`: equal once per-run temporary worktree paths and test timings are masked, since these
    differ between any two runs;
  - `different`: the first difference is given.
