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

## Phase 0 on a local model (in force 2026-11-15, before the pilot)

**Status:**
- **Drafted** 2026-11-13; **finalized** 2026-11-15, when the user confirmed devstral-small-2 after the ornith
  diagnostic (`MODEL_CAPABILITY_TEST.md` §10.1). That confirmation overrides the diagnostic's pre-registered rule; see
  "Override" below.
- **The Phase 0 model is variant A, devstral-small-2.** Variant B (ornith) was not taken; it is kept below as a
  record.
- **Everything in "Phase 0: model capability check" above stands** unless changed here.

**Model change:**
- **From:** `openai/gpt-oss-120b` (Groq, reasoning effort `medium`).
- **To (variant A):** `devstral-small-2:24b-instruct-2512-q4_K_M`. Ollama 0.34.0 on Kaggle 2× T4, one lane split
  across both GPUs, flash attention, q8_0 KV cache.

**Rationale:**
- **Groq is unviable.** It caps `openai/gpt-oss-120b` at 8,000 tokens per minute for this account (from Groq's
  rate-limit headers, 2026-11-13). That is below one agent turn's prompt, so stop condition (a) above holds for every
  run.
- **A local model is the only available path.**
- **Why devstral-small-2** (capability test, `MODEL_CAPABILITY_TEST.md` §9):
  - it fits 2× T4 at `num_ctx` 20,480 (17.2 GB, 100% on GPU);
  - it completed a ~16K-token prompt with a 500-token answer (12.1 tok/s generation, 399 tok/s prompt);
  - it solved the 10-turn tool loop in 5 turns;
  - it is built for agentic coding;
  - it has no thinking mode (qwen3.8 with thinking on is estimated at 27–33 h for 40 runs; with it off, 4–6 h).

**Client:** `r3/loop.OllamaChat` (the client of every Kaggle round) replaces Phase 0a's `OpenAIChat`:
- `format: "json"` (the capability test's mode, which devstral passed);
- the `think` field is not sent;
- 4,096 answer tokens; 900 s per call;
- a call that fails is an infrastructure death;
- no retries; there is no rate limit.

The native-tool client (Phase 0a) is not used.

**Unchanged:**
- the harness (the sanity-4 fixes, the gate, explicit tool messages, the ledger on, no plan field, the Phase 0b
  hashes);
- the 20 tasks of `sanity/tasks_v3.json`, both arms, 40 runs;
- turn cap 28; the window and budget;
- the nine measurements, except that dollars become GPU-hours.

**Pass bar, unchanged (all four):**
- ≤ 2 of 40 infrastructure deaths;
- parse errors < 2% of turns;
- ≥ 4 of 20 tasks solved in at least one arm;
- a non-churn edit rate ≥ 60%.

**Claim wording:** "With devstral-small-2, the fixed harness solves ≥ 4 of 20."
- **The claim is about this model on this harness.** It says nothing about PRISM versus BASELINE, and nothing about
  other models.

**Comparability caveat:**
- **Every earlier round failed** with other models (Qwen2.5-Coder-14B, Qwen3-14B), so no result is being compared.
- **This tests whether the harness plus a capable model can solve at all.**
- **A model change has always meant partial comparability with earlier rounds.** It does again here.

**Context gap:**
- **What the capability test verified:** single calls at ~16K tokens, and a short tool loop whose prompts stayed under
  1,100 tokens.
- **What it did not verify:** a multi-turn loop at 16K-token prompts.
- **How Phase 0 checks it:** its first 10 runs (checkpoint 3) are where any degradation would appear. For each turn,
  checkpoint 3 reports parse errors, edit applies and answer quality against prompt size.

**Readings, changed:**
- **The ceiling check is not available.** gpt-oss-120b at `high` effort was to be the ceiling check after 0–1 solves,
  and it runs on Groq.
  - **If Phase 0 has 0–1 solves with infrastructure fine: accept the finding** (user's decision, 2026-11-15,
    recorded before the pilot).
    - **No ceiling check is run.** The result is reported as: "with devstral-small-2, the fixed harness solves 0–1
      of 20".
    - **Task validity is examined next,** as the pre-registration's "tasks are the limit" branch.
    - **Not chosen:** qwen3.8 with thinking on (BASELINE arm, ~13–17 h), a paid Groq tier for gpt-oss-120b at
      `high`, or deciding at the results (that risked post-hoc rationalization).
- **The rate-limit stop rules** (a) and (c) do not apply.
- **Rule (b) becomes:** stop and report if the projected time exceeds two Kaggle sessions.

**Where and how it runs:**
- **Where:** on Kaggle, not the cloud environment. The model needs the GPUs. The user runs the cell
  `kaggle/r4_phase0_cell.py`; the assistant analyses the pushed results.
- **The checkpoints become the cell's stages,** each followed by the user's go-ahead:
  1. 0a/0b ready (done);
  2. `STAGE = "pilot"`: 1 task, both arms (2 runs);
  3. `STAGE = "first10"`: 5 tasks (one per family in turn), 10 runs;
  4. `STAGE = "all"`: the remaining 30 runs.
- **Finished runs are kept** across stages and sessions.

**Variant B (ornith): not taken** (override below). Recorded as drafted:
- **Model:** `ornith:35b-q4_K_M`, `think` off.
- **Client:** `OllamaChat` with JSON mode off (`--no-json-format`); the loop parses the JSON object out of free text.
  - **If the diagnostic instead shows tool-protocol interference** (it passes only with native tool calls): a native
    Ollama tool path in `OllamaChat` must be built and tested before variant B can run. That is a further change.
- **Claim wording:** "With ornith-35B, the fixed harness solves ≥ 4 of 20."
- **Provisional:** ornith's origin is unverified, so the claim stays provisional until Phase 0 shows it working.
- **Speed:** 4.5× faster generation (54.6 tok/s) and 3.3× faster prompt processing (1,336 tok/s) than devstral, on
  the same test rig.
- **Everything else as in variant A.**

### Override of the ornith diagnostic's rule (user, 2026-11-15)

**The user's decision, verbatim:**

"The pre-registered rule returned 'JSON-mode interference confirmed' (ornith: json 1/3, free 2/3). Override
decision: keep devstral. Rationale: the diagnostic shows the actual cause is a call-shape habit — path at top level
instead of under args — not JSON mode. Free mode still failed 1/3 (seed 43, 10/10 turns, fenced JSON, no
constraint). JSON mode amplifies but doesn't cause. If ornith runs with JSON off, expected ~27 failures across 80
runs from this bug alone, confounding Phase 0's interpretation. Devstral passed 9/9 in all modes with no call-format
issues. The rule's label matches but its intent doesn't — the rule was designed to distinguish 'JSON mode is
fixable' from 'model is broken,' and this is a third case neither branch anticipated. This is the only override of
this pre-registration; future rules must distinguish 'label matches' from 'intent matches.'"

**Assistant's note on the arithmetic,** which leaves the decision unchanged:
- **Phase 0 has 40 runs** (20 tasks × 2 arms).
- **At free mode's observed rate** (1 of 3 runs stuck on this bug), the expectation is about 13 affected runs, not
  27 of 80.
- **The rate rests on n = 3 on a toy rig.** The harness's error message for the bug is more informative
  (`bad arguments for read_file: ... missing ... 'path'`), so the real rate in Phase 0 is unknown.
- **The confound argument holds at either figure.**

**The evidence:** `MODEL_CAPABILITY_TEST.md` §10.1.

**For future pre-registered decision rules:** each branch states the intent it serves, not only its label. A
result that matches a branch's label but not its intent goes to the user, not to the branch's action.

### Deferred to Phase 1: ornith with native tool calling (not built)

- **Why consider it:** ornith passed 3/3 with native tool calling, where the schema carries the argument structure.
  It is 4.5× faster at generation and 3.3× faster at prompt processing than devstral.
- **What it needs:** a native Ollama tool path in `r3/loop.OllamaChat`, with tests, and its own amendment.
- **When:** a future option for Phase 1, if ornith's speed becomes valuable. Nothing is built now.

### Phase 0 scoring: the primary score and a secondary, diagnostic score (user, 2026-11-15; before the first10 stage)

**Recorded:** after the pilot (checkpoint 2), before any of the first10 stage's 8 further runs exist.

**Primary (official) score:** strict, unchanged.
- **The rule:** each task's fail-to-pass and pass-to-pass tests exactly as in `sanity/tasks_v3.json`.
- **Pre-existing failures count:** a run broken only by a test that was already failing counts as unresolved. delete-01
  is the example.
- **The pass bar and the claim use only this score.**

**Secondary (diagnostic) score:** the runs re-scored with each task's pre-existing failures set aside.
- **What it tells us:** whether the model would have solved the task if the task were not broken.
- **It never changes the pass bar or the claim.** It is reported next to the primary score.

**The set-aside list, per task:**
- **Which tests:** the task's pass-to-pass tests that are not `passed` in a scored run that left the repository
  unchanged (empty `agent_files`) on the Kaggle run environment.
  - **With several such runs:** their intersection.
- **Where it comes from:** only runs made before Phase 0 (sanity 1–4), or a dedicated start-state scoring run. Never
  the tests a Phase 0 run broke.
- **Without such a run:** the secondary score equals the primary until a start-state run measures the list.
- **Otherwise the same as the primary:** a delete task also needs its target absent; a feature task needs its
  fail-to-pass tests passing.

**The lists for the first10 tasks, fixed now** (evidence: `PHASE0_RESULTS.md`, "Task validity, first10 batch"):

| Task | Set aside | Evidence |
|---|---|---|
| delete-01 (zod) | 1 test: `recursive-types.test.ts::recursive type with \`id\` meta` (its duplicate ID, prefixed with the file path, passes and stays scored) | 4 unchanged runs (sanity 1 and 2, both arms), all with exactly this test failed |
| delete-02 (instructor) | 57 tests in `tests/v2/test_request_local_streaming.py` | 2 unchanged runs (sanity 2 PRISM, sanity 3 BASELINE), identical 57-test sets |
| feature-01 (hono) | none | 4 unchanged runs, 0 pass-to-pass failures |
| feature-02 (litestar) | none | 4 unchanged runs, 0 pass-to-pass failures |
| delete-03 (pydantic-ai) | **not established**: no unchanged run exists. Secondary = primary until a start-state run is made | – |

**The other 15 tasks:** the same rule. Their lists are fixed before the `all` stage's results are read.

**The lists for the other 15 tasks, fixed 2026-11-15, after checkpoint 3 and before any `all`-stage run:**
- **Same rule:** unchanged runs from sanity 1–4 only.

| Tasks | Unchanged runs | Set aside |
|---|---|---|
| delete-06, delete-09, delete-10, feature-03 … feature-10 (11 tasks) | 2–6 each | **none**: 0 pass-to-pass failures in every unchanged run |
| delete-04 (hono), delete-05 (multidict), delete-07 (scrapy), delete-08 (werkzeug) | **0** | **not established**: secondary = primary unless a start-state scoring run is made before the `all` results are read |

- **Flagged, not set aside:** three of the four unestablished tasks have pass-to-pass tests broken in every earlier
  (edited) run:
  - delete-04: 4 tests;
  - delete-05: 13 tests;
  - delete-07: 10 tests;
  - delete-08: 0 tests.
- **Two explanations remain:** these may be pre-existing failures like delete-01's and delete-02's, or the
  unavoidable fallout of every agent's deletion.
- **Only a start-state run can tell them apart.** Without one, they stay scored.

### Before the `all` stage (user, 2026-11-15)

- **Start-state scoring is skipped for now.** It affects only the secondary score.
  - **Why:** the full 40-run dataset comes first.
  - **When it would run:** after the final results, only if the primary result is borderline, to explain the gap.
- **The three flagged tasks** (delete-04, -05, -07) **stay scored strictly** in both readings.
- **No change mid-run:** not to the harness, and not to the 300,000-token run budget.
- **Budget endings are tracked per run.** If the budget is a major factor across the 40 runs, the final report
  records it as a known limitation of this model on this harness.

### After Phase 0 (user, 2026-11-15)

- **Phase 0 passed:** 7 of 20 solved.
- **The start-state check is skipped.** The secondary reading stays as recorded.
- **Broken tasks are a known issue for future task mining:** delete-01, -02 confirmed; delete-04, -05, -07 flagged.
  The list is in `PHASE1_PLAN.md` §8.
- **Phase 1:** planning only (`PHASE1_PLAN.md`); nothing built yet.

### Phase 1 decisions (user, 2026-11-15)

1. **Budget accounting is unchanged.** Every prompt token counts, for comparability with earlier rounds. Prompt size
   is reduced by compaction (pointers and summaries), not by changing the counting.
2. **PRISM symbols:** a clearer error message only. File paths are not accepted. Whether the message alone changes
   the model's behaviour is seen first.
3. **The comparison's target is a 10–15% relative improvement in solve rate.** No new task mining before the
   re-baseline.
4. **Seed noise (H5) is measured after the harness fixes,** on the harness the comparison will use.
5. **Go-ahead for steps 1a–1d** (no model calls). Built and tested locally; reported in `PHASE1_BUILD.md` before any
   Kaggle cell.

**Assistant's note on decision 3:** this is a size calculation, recorded so the comparison's design is settled before
any run.
- **What the target means:** on Phase 0's BASELINE rate (6 of 20 tasks, 30%), a 10–15% relative gain is 3–4.5
  percentage points (30% → 33–34.5%).
- **What detecting it takes:** at α = 0.05 and 80% power, a two-proportion test needs about 1,700 runs per arm for
  4.5 points, and about 3,800 for 3 points. A paired design (the same tasks and seeds in both arms) lowers this, but
  it stays in the thousands of runs.
- **What that costs:** at 6 minutes a run, that is hundreds of GPU-hours, against 4 hours for Phase 0.
- **The design question for after the re-baseline:**
  - **(a)** a larger effect as the confirmatory target, for example 30% → 45%;
  - **(b)** a cheaper outcome measure with less noise than solve rate, such as tests passing per task;
  - **(c)** more tasks.

## Phase 1: the primary metric changes to "share of target tests passed" (user, 2026-11-15)

**The decision:** solve rate needs too many runs for a 10–15% relative effect. From the re-baseline on, the primary
metric is the share of each task's target tests passed. Solve rate becomes a secondary metric.

**The exact definition (approved by the user, 2026-11-15):**
- **Each run gets a score from 0 to 1.** The score is 1 exactly when the run is resolved.
- **A feature task:**

  score = (fail-to-pass tests passing ÷ fail-to-pass tests) × (pass-to-pass tests still passing ÷ pass-to-pass tests).

  Without the second factor, a run could pass the new tests by breaking the old ones.
- **A delete task:** score = 0 if the target is still defined; otherwise, the share of pass-to-pass tests still
  passing.
  - **Why the target condition:** a delete task names no fail-to-pass tests, and doing nothing keeps 100% of the
    pass-to-pass tests.
  - **"Still passing":** in the scorer's sense. A test the agent deleted is not counted.
- **Strict lists, as for the primary solve score.** delete-01's and delete-02's pre-existing failures keep those
  tasks below 1. The secondary set-aside reading applies to the score too.
- **Unit of analysis:** the task, as the mean of its runs' scores, paired across arms.

**The size of the comparison:**
- **Still to be set,** from the within-task, across-seed variance that step 1e part B measures.
- **What the continuous score adds:** partial progress counts. Phase 0's feature-06 PRISM (15 of 41 target tests)
  scores 0.37, where solve rate gives it 0.
- **Not a guarantee:** a smaller required sample is expected, not certain.

## Phase 1 step 1e: degeneration check and seed noise (pre-registered 2026-11-15, before the run)

**Approved by the user (2026-11-15).**
- **Model:** devstral-small-2:24b-instruct-2512-q4_K_M.
- **Where:** one Kaggle session, about 3–4 h, in the cell `kaggle/r4_phase1e_cell.py`.
- **Code:** pinned in the cell, with every Phase 1 build step at that commit.

### Part A: do the trailing-off parse errors go away?

**The material:** the 17 Phase 0 turns whose reply trailed off: delete-05 PRISM, turns 18–21, 24 and 27; feature-08
PRISM, turns 8, 15 and 19–27.

**Rebuilding the prompts:** each turn's prompt is rebuilt by the replay tool (`r4/degeneration_check.py`), from the
recorded replies and real tools.
- **The fidelity check:** every earlier tool output is byte-identical to Phase 0's, except `run_tests` outputs. Those
  carry per-run temporary paths and must match Phase 0's logged head.
- **Checked locally on delete-05:** 6 of 6 target turns pass.
- **A turn that fails the fidelity check** is reported and excluded from the comparison.

**The two conditions:** each turn is sent 10 times (seeds 42–51) in each.

| | Harness | Client |
|---|---|---|
| **before** | Phase 0 | `OllamaChat` as in Phase 0 |
| **after** | `--phase1`: hygiene + compaction, the same trajectory | `OllamaChat(stream_cut=True)` |

340 calls in all.

**Recorded per call:**
- a valid tool call or not;
- a degenerate tail (over 400 characters) or not;
- cut;
- repaired;
- seconds;
- answer tokens.

**Expectations, recorded before the run:**
1. **before** reproduces the problem: a parse-error rate of at least 30% over its 170 calls. If it does not, the
   problem was not reproduced and nothing is concluded about the fix.
2. **after**, the main criterion:
   - every degenerate tail is cut (no call returns over 400 characters of tail);
   - the parse-error rate is at most a quarter of before's.
3. **The feature-08 shape** (`args` complete, the final brace missing) should end up valid, repaired.
   - **The delete-05 shape** (escaped newlines inside a string) cannot be repaired. Its calls can stay parse errors,
     but the reply sent back into the history is cut to 400 characters. So "gone" is expected for the first shape
     only; the second becomes cheap, not absent.
4. **after's mean seconds per call is below before's.**

**Limit:** the trajectory is Phase 0's.
- **In a live Phase 1 run,** an earlier repaired reply would change everything after it.
- **What part A isolates:** the reply given a fixed history.

### Part B: seed noise on the Phase 1 harness (H5)

**The runs:** the 4 tasks whose arms disagreed in Phase 0 (delete-06, delete-09, feature-05, feature-09) × seeds 42,
43 and 44 × both arms = 24 runs.
- **Settings:** `--phase1`; turn cap 28; ledger on; gate on; budget unchanged (every prompt token counts).

**Reported:**
- **Per task and arm:**
  - solves out of 3;
  - the continuous score (mean and spread across seeds);
  - endings;
  - budget endings;
  - parse errors;
  - compaction counts;
  - re-reads of compacted reads.
- **Per arm:** the share of PRISM output in the prompt.

**Readings, recorded before the run:**
- **H5 is supported (the Phase 0 discordance was noise)** if either holds:
  - for at least 3 of the 4 tasks, an arm's outcome differs across its own seeds, with solves 1 or 2 of 3;
  - the arm that won a task in Phase 0 does not win it on solves across the 3 seeds.
- **Otherwise the discordance is treated as real,** and the diagnosis's H1 (PRISM output volume) is followed up first.
- **The across-seed variance of the continuous score sizes the re-baseline comparison.** No PRISM-versus-BASELINE
  claim is made from 4 tasks.
- **Also checked:** whether compaction caused extra re-reads or stale edits against Phase 0. This is descriptive;
  there is no bar.

**No change mid-run.** Results go to `reports/r4/phase1e`.

**Step 1e launched by the user on Kaggle (2026-11-15)** with `kaggle/r4_phase1e_cell.py` (code `9e46c23c`).

## Phase 1 step 1f: the reply format (pre-registered 2026-11-15, before the run)

**Why:**
- **Step 1e part A:** the cut-off fix did not meet its criterion. The broken replies are short, stop inside a JSON
  string, and come back from Ollama with no stop reason or token counts.
- **The user's reading** (Mistral's documentation): devstral is a function-calling model. Forcing JSON mode keeps it
  from its native tool format.
- **Confirmed from Ollama's registry:** devstral-small-2's chat template renders tools as `[AVAILABLE_TOOLS]`, calls
  as `[TOOL_CALLS]name[ARGS]{...}`, and results as `[TOOL_RESULTS]` (the `tool` role).
- **Not established:** the server-side reason replies stop. This step saves Ollama's log to check it.

**Built (no model calls; tests pass):**
- **`OllamaChat(tools=native_tools(version))`:**
  - Ollama native tool calling, with JSON mode off;
  - the loop's text history is framed natively (`native_messages`): a call becomes `tool_calls`, its output becomes a
    `tool` message;
  - the first call comes back as the loop's text, as `OpenAIChat` does;
  - the same schemas in both arms.
- **The runner:** `--native-tools` now applies to Ollama.
- **Free-text mode:** already `--no-json-format`.

**The check:** `kaggle/r4_phase1f_cell.py`.
- **The prompts:** step 1e's 17 turns, rebuilt the same way (7 faithful to Phase 0).
- **The calls:** 10 per turn (seeds 42–51), under 4 conditions:

| Condition | Prompts | Reply format |
|---|---|---|
| `before` | Phase 0 | JSON mode |
| `free` | Phase 0 | JSON mode off |
| `native` | Phase 0 | native tools |
| `native_p1` | Phase 1 (hygiene + compaction) | native tools |

**Decision rule, recorded before the run:**
- **The primary comparison:** parse-error rate per condition, paired on the same prompts. It is reported on all 17
  turns, and separately on the 7 faithful ones.
- **A format replaces JSON mode for the re-baseline** if its parse-error rate is:
  - at most 5% over all 17 turns;
  - below `before`'s on both the 17 turns and the 7 faithful ones.
- **If both qualify, `native` is preferred** (the model's own format), unless `free` is lower by more than 5 points.
- **If neither qualifies:** JSON mode stays, with the step 1c history cut (which stops the repeats), and the server log
  decides what is tried next.
- **Also reported:** whether a valid reply calls a tool from the arm's list, and seconds per call.

### Override of the step 1f rule: native tool calling adopted (user, 2026-11-16)

**The user's decision:**
- **What was overridden:** the pre-registered 5% bar. The rule's intent was to find a format that fixes the cut-off
  problem, and the drop from 70% to 8.8% (native tools with the Phase 1 harness, all 17 turns) meets it.
- **The new default:** native tool calling with the Phase 1 harness.
- **Evidence:** `PHASE1F_RESULTS.md`.

**Not established:** the cut-off's server-side cause. JSON mode is ruled out: free text fails as often.

**Added at the user's direction, both arms (withdrawn the same day, before any run; see "No prompt changes" below):**
the native-mode system prompt (`loop.SYSTEM_NATIVE`) said "each turn, call exactly one of the given tools; never reply
in prose". The format reminder after an invalid reply says the same
(`FORMAT_REMINDER_NATIVE`).
- **Tested:** in unit tests only. The re-baseline's first wave is its first contact with the model, and the wave-1
  health check covers it.
- **The rule from the earlier override applied:** a result matching a rule's label but not its intent went to the
  user, who decided.

## Phase 1 re-baseline: PRISM against BASELINE (pre-registered 2026-11-16, before any run)

**Approved by the user (2026-11-16).**

**The design:**
- the 20 tasks of `sanity/tasks_v3.json` (sha `a187478c…`) × seeds 42–51 × both arms = **400 runs**;
- devstral-small-2:24b-instruct-2512-q4_K_M on Kaggle 2× T4;
- cell `kaggle/r4_rebaseline_cell.py`;
- results in `reports/r4/rebaseline`.

**The harness, fixed for the whole run:**
- native tool calling (`--native-tools`), with **no prompt change**: the system prompt and the format reminder are
  Phase 0's, in both arms (see "No prompt changes" below);
- `--phase1` (history hygiene and compaction);
- ledger on, gate on, no plan field;
- turn cap 28; budget 300,000 tokens (every prompt token counts, as decided).

**The order:** waves of one seed over all 20 tasks (40 runs, about 4 h).
- **If the run stops early,** the completed waves form a balanced design.
- **The analysis then uses the complete waves only,** and says so.

**Primary metric:** the approved continuous score (amendment "the primary metric changes").
- **Feature:** (fail-to-pass passing share) × (pass-to-pass passing share).
- **Delete:** 0 with the target present; else the pass-to-pass passing share.
- **Strict lists.**
- **Code:** `harness/experiments/r4/rebaseline_report.py`, fixed in this commit.

**Primary analysis:**
- **The unit:** per task, each arm's mean score over the 10 seeds, and the paired difference d = PRISM − BASELINE
  over the 20 tasks.
- **The test:** the two-sided Wilcoxon signed-rank test on d (zero differences dropped), at α = 0.05.
- **Also reported:**
  - the mean difference with a 95% bootstrap interval (tasks resampled);
  - the paired t-test;
  - the tasks favouring each arm.

**Readings, recorded before the run:**
- **PRISM is better on these 20 tasks** if the Wilcoxon test gives p < 0.05 with mean d > 0.
- **BASELINE is better** if p < 0.05 with mean d < 0.
- **No detectable difference** otherwise. The bootstrap interval is then reported as the range of effects the data
  allow.
  - **The design can detect** a difference of about 0.08 (about 15% relative), from step 1e's spread. That was
    estimated on 4 unstable tasks, so it is approximate.
- **Every claim is limited** to these 20 tasks and this model. The tasks include the known broken ones (delete-01,
  delete-02, with pre-existing failures), scored strictly.

**Secondary:**
- **Solve rate per arm:** all runs, and tasks solved at least once. McNemar's exact test on the task-seed pairs.
- **The set-aside score:** delete-01's 1 test and delete-02's 57 tests set aside.
- **Per family.**
- **Process:**
  - infrastructure deaths;
  - parse errors per turn;
  - endings (done / turn cap / budget / stuck);
  - tokens per run;
  - PRISM output tokens per run;
  - failed edits per turn (step 1e's possible compaction side-effect).

**Stop rules:**
- **The wave-1 health check:** the cell stops for review if wave 1 (seed 42, 40 runs) has more than 2 infrastructure
  deaths, or parse errors at 2% of its turns or more.
- **Otherwise no change of any kind mid-run.** A stop at a session limit is not a change.

**Cost:** about 40 GPU-hours, or 4 Kaggle sessions. Kaggle's weekly GPU quota (about 30 hours) means this likely
spans two weeks.

### No prompt changes for native tool calling (user, 2026-11-16, before any re-baseline run)

**The user's decision:**
- **The strict line is removed** ("call exactly one of the given tools; never reply in prose"), from the system prompt
  and from the format reminder.
- **Why:** prompt-engineering one behaviour is brittle and would not generalise across tasks or models. The harness
  should be robust without it.
- **The formatting errors are left to** native tool calling and the Phase 1 history cut.
- **The residual failure rate is accepted** (8.8% of calls in step 1f, against JSON mode's 70%).

**The result:**
- **No prompt text was changed in either arm.** The system prompt (`loop.SYSTEM`) and the format reminder
  (`loop.FORMAT_REMINDER`) are Phase 0's, byte for byte.
- **What does differ is the client's framing:**
  - Ollama's `tools` field;
  - past calls sent back as native calls and their outputs as `tool` messages;
  - JSON mode off.
- **This is exactly the configuration step 1f measured** (`native_p1`, 8.8%).

**In the code:**
- `SYSTEM_NATIVE`, `FORMAT_REMINDER_NATIVE` and the loop's `native` switch are removed.
- A test pins that native tool calling adds no prompt text.
- The re-baseline cell's smoke test uses a plain request ("Use the read_file tool to read the file a.py.") and no
  system prompt.


### Re-baseline wave 1 stopped; restart with the raw tool path (user, 2026-11-16)

**What happened:** wave 1 (seed 42, 40 runs, code `a1d8d0dc`) was stopped by the pre-registered health check.
- **Parse errors:** 7.1% of turns, against the 2% bar. Infrastructure deaths: 0.
- **The cause** (`REBASELINE_WAVE1_STOP.md`): all 76 were empty replies. Ollama's tool parser discards a `[TOOL_CALLS]`
  reply it cannot complete, and `Parser.Content()` returns "" for that tag. The model's output was lost.
- **The wave-1 runs are kept** in `reports/r4/rebaseline` as a record. They are **not part of the re-baseline:** the
  harness changes.

**The user's decision: Option A, the raw tool path** (`--raw-tools`, `harness/experiments/r3/mistral_raw.py`).
- **The prompt:** the harness renders devstral-small-2's Ollama template itself, from the registry template and the
  same native message list. The JSON it prints is reproduced as Go writes it.
- **The call:** the harness calls `/api/generate` with `raw: true`.
- **The parsing:** the harness parses `[TOOL_CALLS]name[ARGS]{...}` itself. A malformed call becomes a visible parse
  error with its text, so nothing is discarded unseen.
- **No prompt wording changes.** Everything else in the re-baseline pre-registration stands.

**The gate before any run** (`r4/raw_preflight.py`):
- **The check:** the same messages (each arm's real system prompt; past calls whose arguments contain `<`, `>`, `&`,
  quotes and newlines; tool outputs; a reminder) go to Ollama's `/api/chat` and to the raw path.
- **The bar:** the prompt token counts must be equal, or the cell stops.
- **Also recorded:** whether the two give the same first call.

**The restart:**
- **From wave 1,** under a new pin, with results in `reports/r4/rebaseline2`.
- **The wave-1 health check stands as pre-registered:** stop if more than 2 infrastructure deaths, or parse errors at
  2% or more.

**Added at the user's request: turn efficiency** (descriptive, no test; `rebaseline_report.turn_efficiency`).
- **Per run:**
  - turns by kind (PRISM calls; exploration with `read_file`/`grep`/`list_dir`/`related_code`; edits applied and
    failed; test runs; parse errors);
  - the turn of the first applied edit;
  - turns between a PRISM call and the next applied edit;
  - model seconds per turn and per run;
  - mean prompt tokens.
- **Reported:** per arm, per task paired, and for solved runs only.

**Assistant's note on the premise** (`turns 20–28 because of PRISM`): it does not hold.
- **Both arms take that many turns.** Phase 0: 25 per run in each arm. Wave 1: PRISM 544 turns over 20 runs,
  BASELINE 527.
- **Wave-1 turn efficiency** (broken client, so indicative only):
  - per run, PRISM made 1.8 PRISM calls, 14.2 exploration turns, 1.7 applied and 5.4 failed edits;
  - BASELINE made 15.6 exploration turns, 2.4 applied and 5.3 failed edits;
  - model seconds per turn: 25.7 against 21.8 (PRISM's prompts are about 13% larger).
- **So in both arms the turns go to exploration and failed edits,** not to PRISM output. The re-baseline's turn
  efficiency will test this properly.

### Post-PRISM behaviour metric (user, 2026-11-16, after the re-baseline cell was launched, before any of its results were read)

**Analysis only:** `rebaseline_report.post_prism`, descriptive, no test. The pinned run is unchanged; the cell's own
`report.json` comes from the pinned script, so this metric is computed from the pushed turn logs afterwards.

**Per PRISM call, the next action:**
- an edit;
- another PRISM call;
- a `grep` for the same symbol;
- a `read_file` of the file PRISM named;
- something else.

The last two named actions count as a "post-PRISM search".

**Per run:** the turn of the first PRISM call, the exploration turns before it, and whether PRISM is called at all.

**Limit:** logs keep each output's first 300 characters, so only the target symbol and its file are checked; callers
PRISM lists further down are not.

**The historical distinction, checked against the repository:**
- **R1/R2 were single-shot:** one prompt, one answer, no tools in either arm (`harness/experiments/r1/README.md`,
  `r2/run.py`).
- **The harness did the retrieval up front:** PRISM's context in one arm, file, grep or BM25 context of the same size
  in the other.
- **Neither arm searched,** so PRISM was not replacing the model's search. It was one retrieval method against
  another, chosen by the harness.
- **Since R3, PRISM is an optional tool** the model must choose to call, next to `read_file` and `grep`.

**What the existing logs show** (descriptive; Phase 0 and wave 1, PRISM arm, 20 runs each):

| | Phase 0 | Wave 1 |
|---|---|---|
| Runs that never call PRISM | 3 | 4 |
| First PRISM call (mean turn) | 9.1 | 7.9 |
| Exploration turns before it | 7.8 | 6.2 |
| PRISM calls | 38 | 36 |
| Next action an edit | 32% | 6% |
| Next action another PRISM call | 29% | 33% |
| Next action a post-PRISM search | 13% | 19% |

**Reading:**
- **The model mostly explores on its own first** and calls PRISM late, if at all.
- **A same-target search right after PRISM is a minority** of what follows a call.
- **Most turns of exploration happen before any PRISM call,** not after it.
- **A `read_file` before an edit is often required anyway:** `edit_file` needs the file's exact text.

**What this does not change:** the re-baseline. It measures PRISM as an optional tool in an agent loop, as designed
and pre-registered.

**A different question, for later:** "PRISM as the context source" (R1/R2's setting) would need a different design.
For example, an arm that gets PRISM's context for the task's target in the first message.

## Phase 1 re-baseline, 3 arms (pre-registered 2026-11-16, before any run of this design)

**Approved by the user (2026-11-16):**
- **The question:** an agentic-tool arm and an upfront-context arm, each compared with the baseline.
- **This supersedes the two-arm design** above. Everything not changed here stands:
  - the harness: the raw tool path with its pre-flight gate, `--phase1`, ledger, gate, turn cap 28, budget 300,000
    with every prompt token counting, no prompt changes;
  - the tasks, the 10 seeds, the continuous primary metric and its strict lists;
  - the secondary readings and the turn-efficiency and post-PRISM metrics;
  - the wave-1 health check.

**The arms (versions):**

| Arm | Version | Tools | Task message |
|---|---|---|---|
| 1 | `BASELINE` | shared tools + `related_code` | the task |
| 2 | `PRISM` | shared tools + the five PRISM tools; the agent chooses when to call them | the task |
| 3 | `INJECTED` | **BASELINE's**: same tools, same system prompt | PRISM's context for the task, then the task |

**What Arm 3 gets** (`harness/experiments/r4/inject.py`): the retrieval runs once, before the first turn, through the
same `ToolLayer` call Arm 2's tools use, with the same 4,000-token cap.
- **The query uses only what the agent is told:**
  - **delete:** `prism_change_context` on the target the prompt names, as PRISM's dotted symbol. If PRISM cannot
    resolve it, `prism_ask` with the target's name and file.
  - **feature:** `prism_ask` with the feature description from the prompt (first 400 characters).
- **The block** is headed "Context from the code index (PRISM), retrieved for this task before you started:" and
  ends with a `---` line before the task. This label is the only text Arm 3 adds.
- **Recorded per run:** the tool, the query, any fallback or error, and the size in tokens.
- **Checked locally:**
  - delete-09 (litestar): `prism_change_context` on `litestar.params.Dependency`;
  - feature-04 (typer): `prism_ask`;
  - each about 1.3K tokens.
- **Its cost:** the block sits in the task message, which the window rule always keeps, so it is re-sent every turn.
  At about 1.3K tokens over 28 turns that is about 36K of the 300K budget, and up to about 112K at the 4K cap. This is
  part of what Arm 3 measures, as in R1/R2, where the whole prompt was the context.

**Primary analysis** (`rebaseline_report.py`, fixed in this commit):
- **The comparisons:** per task, each arm's mean score over the 10 seeds, then two paired comparisons over the 20
  tasks:
  - **H-tool:** `PRISM` − `BASELINE` ("does the tool help?");
  - **H-inject:** `INJECTED` − `BASELINE` ("does injected context help?").
- **The test for each:** the two-sided Wilcoxon signed-rank test, with p-values Holm-adjusted over the two
  comparisons (family-wise α = 0.05).
- **Reported for each:** the mean difference with a 95% bootstrap interval, the paired t-test, the tasks favouring each
  arm, the set-aside score, and McNemar's test on solves.
- **Descriptive only:** `INJECTED` − `PRISM`.

**Readings, recorded before the run, per comparison:**
- **The arm is better than BASELINE on these 20 tasks** if the Holm-adjusted p < 0.05 with the mean difference > 0.
- **The arm is worse** if the Holm-adjusted p < 0.05 with the mean difference < 0.
- **No detectable difference** otherwise, with the interval reported.
- **The detectable difference** is about 0.08–0.09 (about 15–17% relative), slightly larger than the two-arm design's
  because of the Holm correction. As before, it was estimated on step 1e's 4 tasks.
- **Every claim is limited** to these 20 tasks and this model.

**The order:** waves of one seed over all 20 tasks and the 3 arms. Each wave is 60 runs, about 6 h. Within a task the
arms run BASELINE, PRISM, INJECTED.
- **The wave-1 health check** (seed 42, 60 runs) stops the cell for review if there are more than 2 infrastructure
  deaths, or parse errors at 2% of turns or more.

**Cost:** 600 runs, about 60 GPU-hours, or 6 Kaggle sessions. With Kaggle's weekly quota of about 30 hours, that is
about two weeks or more.

**Results:** `reports/r4/rebaseline3`.
- **Not part of this design:** the stopped wave-1 runs in `reports/r4/rebaseline`, and any runs of the two-arm cell in
  `reports/r4/rebaseline2`.

### The 3-arm cell's pre-flight stopped it; two rendering fixes (2026-11-17, before any run)

**What the pre-flight found** on Kaggle (`reports/r4/rebaseline3/preflight.json`, code `12b0f9c8`). It worked as
designed and stopped the cell before any run:
1. **Prompt token counts 1–2 short** (raw against `/api/chat`): PRISM cases −2, BASELINE cases −1.
2. **Calls not recognised:** `/api/generate` drops the special tokens from its text, so a call came back as
   `read_file{"path": "src/a.ts"}`, without `[TOOL_CALLS]`/`[ARGS]`. The parser would have made every call a parse
   error.

**How fix 1 was found:**
- **The setup:** Ollama v0.34.0 run locally (CPU), with devstral-small-2's registry template on a small model
  (`smollm2:135m`).
- **What it showed:** both endpoints send the engine prompts of identical length, yet the token counts differ. Tool by
  tool, only `read_file` and `prism_new_function_context` differed: the two tools whose parameter names are not
  already in alphabetical order with the property that tips the tokenisation.
- **The cause:** Ollama writes **parameter property names sorted alphabetically**, and keeps **call arguments in
  their original order**.
- **The check:** with that rule, the raw and chat token counts match on all 12 tools and all 4 pre-flight cases.

**The fixes** (`harness/experiments/r3/mistral_raw.py`):
1. **Rendering:** property names are sorted when rendering tools; arguments are unchanged.
2. **Parsing:** `parse_raw(text, names)` also reads `name{...}`, where `name` is one of the arm's tool names directly
   followed by a JSON object, as well as the tagged form. A known name followed by invalid JSON is a visible parse
   error.

**The pre-flight is stricter:** wherever `/api/chat` returns a call, the raw path must parse the same call (same
prompt and seed), as well as matching the token counts. This checks the parser on the real model.

**Nothing else changes** in the 3-arm pre-registration. The cell is re-pinned, and results still go to
`reports/r4/rebaseline3` (it holds only the pre-flight file).

### 3-arm wave 1 stopped by the health check: a harness parser bug (2026-11-17)

**What happened:**
- **The pre-flight passed** (token counts and calls matched in all 4 cases).
- **Wave 1 then ran** (seed 42, 60 runs, code `3f4bcafd`), with no infrastructure deaths.
- **The health check stopped the cell:** parse errors at 11.0% of turns (161 of 1,459), against the 2% bar.

**The cause was in the harness, not the model.**
- **The replies:** 155 of the 161 were prose followed by a valid call, written as the model does: "…Let me continue
  reading the file.read_file{"path": …}".
- **The parser:** the untagged-call rule in `mistral_raw.parse_raw` refused a tool name directly after a `.`, which
  was meant to keep out dotted names such as `pkg.read_file`. The full stop ending the prose matched that rule.
- **Ollama's own parser** (the `native` path that step 1f measured) reads these replies as calls.
- **The fix:** the name may now follow any non-word character. A test pins the prose-then-call form.
- **Checked on wave 1's own replies:** the 155 parse as calls to tools in the arm's own list. The 6 left are 5
  malformed `edit_file` calls (unterminated strings, 4 of them at or near the answer limit) and 1 other. That is
  0.4% of turns.

**Wave 1 is not used.**
- **Why:** each misread call cost its run a turn and a format reminder, so the trajectories are not the harness's.
- **Kept as a record** in `reports/r4/rebaseline3`.
- **The 3-arm design restarts from wave 1** under a new pin, with results in `reports/r4/rebaseline4`.
- **Nothing else changes,** the health check included.

### Smoke test before the 3-arm re-baseline (user, 2026-11-17)

**Why:** Kaggle's execution queue is long, so a short cell (`kaggle/r4_smoke_test.py`) first shows that the full
cell's plumbing works.

**What it runs:**
- **Scope:** one task (`r4s-feature-02`, litestar), seed 42, the three arms: 3 runs.
- **Turn cap:** 12, for time; the full run keeps 28.
- **Unchanged from the full cell:** the same code, the same pre-flight gate (token counts and calls), the raw tool
  path, `--phase1`, ledger and gate.
- **Then:** the analysis script on the 3 runs. Its output is printed: per run, per task, process, turn efficiency,
  post-PRISM and injection.
- **The paired tests need at least two tasks,** so they show as `null` here.

**Not part of any result.** Results go to `reports/r4/smoke3`. The 3-arm pre-registration is unchanged.

### A sixth PRISM tool, `prism_read_code` (user, 2026-11-17, before the 600-run launch)

**Why** (the smoke test's PRISM run, `reports/r4/smoke3`, traced in `harness/experiments/r4/SMOKE3_FORENSICS.md`):
- **`prism_ask` returns no code:** the turn-1 answer was 1,308 tokens of ranked names, locations and score legs.
- **The agent read the files itself:**
  - 6 `read_file` calls and 1 `list_dir` before its first edit;
  - the code `prism_change_context` serves drops blank lines and has no line numbers, so it cannot be copied into
    `edit_file` either.
- **Its first edit failed:**
  - **The cause:** `old` held a docstring the file does not have ("Create HTML response content for an exception."
    against the real "Given an exception, produces its traceback in HTML.").
  - **Why:** by turn 10, compaction had reduced the whole-file read of turn 2 to a one-line summary (rule 4, more
    than 6 outputs back), so the agent wrote `old` from memory.
  - **The cost:** 2 turns and about 106 s of model time.

**What the tool does** (`ToolLayer._prism_read_code`, `harness/experiments/r3/tools.py`):
- **Input:** `{"symbol": str}`, any of what `prism_ask` / `prism_change_context` return:
  - a qualified name (`hits[].qname`). A partial name that exactly one indexed symbol ends with resolves as
    `prism_change_context` resolves it (`_resolve_seed_or_raise`). An unknown or ambiguous name is an error that
    lists the candidates.
  - a location `path:line` (`hits[].location`): the innermost indexed definition containing that line.
  - a file path: the whole file.
- **Output:**
  - a header (`path (lines a-b of N: `qname`, kind)`), then every line of the definition, decorators included,
    exactly as the file holds it, blank lines and indentation kept;
  - each line numbered as `read_file` numbers it (the number, a tab, the text).
- **The bytes are the shadow's** (R3-5: what PRISM may show). For a file the agent has not left unparsable, they are
  the working file's current bytes.
- **The same `ToolLayer.call` path as the other PRISM tools:** refresh, the gated-file notice, the 4,000-token cap.
- **Compaction treats it as an index tool** (rule 4: its first lines once old), as it treats the other `prism_*`
  tools. The read-specific rules (superseded, stale) stay `read_file`'s.
- **Description in the tool list** (descriptive only, no instruction on when to use it): "The exact code of a symbol,
  a location (path:line) or a file that prism_ask or prism_change_context named, with line numbers."
- **Nothing else changes:** `prism_change_context` and the other PRISM tools are unchanged; no prompt wording is
  added.

**The arms after this change:**
- **Arm 2 (`PRISM`):** shared tools + six PRISM tools.
- **Arm 3 (`INJECTED`): unchanged, without `prism_read_code`.**
  - **Why:** Arm 3 is defined as BASELINE's tools with PRISM's context prepended once ("does injected context
    help?"). An agent-callable PRISM tool there would make it a second tool arm and blur H-inject.
  - **Its injected block is also unchanged** (the `prism_ask` / `prism_change_context` answer).
  - **The user may override this before the launch.**
- **Arm 1 (`BASELINE`):** unchanged.

**Checked before the re-pin:**
- **On the smoke task's commit** (litestar `6e432d0d6b4a`):
  - `create_html_response_content` returns lines 128-160 exactly, including line 129's real docstring;
  - the location `_debug_response.py:59` from `prism_ask` returns `create_line_html`;
  - a template path returns the whole file;
  - `get` returns the ambiguity error with its 19 candidates.
- **Tests:** `tests/test_prism_read_code.py` covers:
  - exact lines with blank lines;
  - decorators;
  - partial names, locations, files;
  - errors, including a path outside the repository;
  - a file edited after indexing;
  - the tool in the PRISM arm only.
- **Template rendering** (local Ollama v0.34.0, devstral's template): raw and `/api/chat` token counts match in all 4
  pre-flight cases with the new tool list. The Kaggle pre-flight gate checks this again on the real model.

**A change to Arm 2's tools, so a new pin:**
- **The smoke test runs again first:** results to `reports/r4/smoke4`.
- **Then the 600-run cell:** results to `reports/r4/rebaseline5`. `reports/r4/rebaseline4` holds no runs of this
  design.
- **The rest of the 3-arm pre-registration stands:** the tasks, the seeds, the metric, the comparisons, the
  readings, the health check.

### Arm 3 gets the PRISM tools (user, 2026-11-17, before any run of the 3-arm design)

**The user's correction:** "For any arm, in any turn, the AI has the freedom to use PRISM tools. It is a PRISM
experiment."
- **Why it matters:** without the tools, Arm 3 is the baseline with a larger prompt, and it cannot refresh its map
  of the code as the code changes.
- **This supersedes** the previous amendment's "Arm 3 unchanged, without `prism_read_code`", and the 3-arm table's
  "BASELINE's" tools for Arm 3.

**The arms:**

| Arm | Version | Tools | System prompt | Task message |
|---|---|---|---|---|
| 1 | `BASELINE` | shared tools + `related_code`; no PRISM tools | BASELINE's | the task |
| 2 | `PRISM` (optional PRISM) | shared tools + the six PRISM tools | PRISM's | the task |
| 3 | `INJECTED` (injected PRISM) | shared tools + the six PRISM tools | PRISM's | PRISM's context for the task, then the task |

**What changed:**
- **The code:** `loop.PRISM_ARMS = ("PRISM", "INJECTED")`. The tool list, the system prompt, the native tool
  schemas and the allowed tools follow it.
- **Arms 2 and 3 now differ only in the injected block.**
- **The block is unchanged** (`r4/inject.py`: the same retrieval, label and cap).

**What the comparisons now read:**
- **H-tool** (`PRISM` − `BASELINE`): "do the PRISM tools help?"
- **H-inject** (`INJECTED` − `BASELINE`): "do the PRISM tools, plus PRISM's context up front, help?"
- **`INJECTED` − `PRISM`** (descriptive): the effect of the up-front context when the tools are there either way.
- **Unchanged:** the tests, the Holm correction, the readings, the health check, the tasks and seeds.

**The post-PRISM metric** is now also reported for `INJECTED` (`post_prism_by_arm`). `post_prism` stays PRISM's.

**Pre-flight:** Arm 3's prompt is Arm 2's (the same system prompt and tool list) with a longer task message. The
pre-flight's PRISM cases therefore cover Arm 3's tool rendering.

**Re-pinned:** the smoke test and the 600-run cell, at the same code. Results still go to `reports/r4/smoke4` and
`reports/r4/rebaseline5`; neither holds any run.

### The smoke-4 pre-flight stopped the cell: its call check read replies more narrowly than a run (2026-11-17)

**What it found** (`reports/r4/smoke4/preflight.json`, code `e045bcb6`):
- **Token counts:** raw and `/api/chat` matched in all 4 cases. The rendering, including the six-tool PRISM list, is
  right.
- **Calls:** one case did not match, "PRISM: two calls and a reminder".
  - **The reply:** devstral wrote the call in the harness's JSON format,
    `{"tool":"read_file","args":{"path":"src/a.ts"}}`, as the case's history shows earlier calls.
  - **`/api/chat`** read it as a `read_file` call.
  - **The pre-flight** counted a raw call only when `parse_raw` found the `name{...}` form, so it saw none.

**A run would have executed that call:**
- `OllamaRawChat` returns `parse_raw`'s text unchanged when it finds no `name{...}` call.
- The loop's `parse_call` then reads the JSON format, as it always has.
- So the mismatch was in the pre-flight's own reading, not in the harness.

**The fix** (`raw_preflight.py`):
- **The raw reply is read as a run reads it:** `parse_raw`, then `parse_call`.
- **The gate is otherwise unchanged:** equal token counts in every case, and wherever `/api/chat` returns a call, the
  same call.
- **Checked:**
  - the stopped pre-flight's 4 replies all match under the fixed check;
  - a test pins the JSON-format case;
  - the local Ollama pre-flight passes.

**Nothing in the design changes.** Both cells are re-pinned. Results still go to `reports/r4/smoke4` (the cell
overwrites `preflight.json`; the stopped one stays in git history) and `reports/r4/rebaseline5`.

### A descriptive code-review score (user, 2026-11-18, before the 3-arm launch)

**Why:** the review of one task's 28-turn runs (feature-02, `rebaseline3`, a voided wave):
- **BASELINE's code crashed** every HTML debug page: `sys.exc_info()` with no `import sys`.
- **PRISM's** put the traceback into the page unescaped.
- **INJECTED's** was close to the developer's.

Tests alone do not show this kind of difference.

**What it is** (`harness/experiments/r4/code_review.py`):
- **When it runs:** at scoring time, in `runner.run_task`, before `score` applies the hidden tests.
- **Deterministic:** no model is called.
- **What it looks at:** the agent's changes to source files (`.py`, JS/TS, `.html`, `.css`; test files left out),
  against the start state and against the developer's change (the task's commit).

| Check | Applies when | Value |
|---|---|---|
| No new undefined names | a Python file changed | 0/1: no name used but never defined or imported (`pyflakes` 3.2.0) that the start state's file did not already have |
| No new syntax errors | a source file changed | 0/1: no changed file that parsed at the start fails to parse now (`ast`; tree-sitter for JS/TS) |
| Helper reuse | the developer's added code calls a function defined in the repository before and after the change | share of those the agent's added code calls |
| Escaping kept | the developer's added code calls an escaping/sanitising function | 0/1: the agent's added code calls one |
| API match | the developer's added code calls other APIs (built-ins and its own new functions left out) | share the agent's added code calls |
| No regressions | always (from the scorer) | 0/1: no pass-to-pass test broken |

**The review score:**
- **Per run:** the mean of the checks that apply, in [0, 1].
- **A run that changed no source file** is not reviewed (every check is "not applicable").
- **Calls are read** from added lines by one regular expression, the same in every arm.

**Reported** (`rebaseline_report.code_review`):
- per arm: each check's mean and the number of runs it applies to, and the review score;
- the same for solved runs only;
- per task, the paired differences of the mean review score for the three arm pairs.

**Descriptive only:**
- no test;
- not part of the primary metric, the comparisons or their readings.

**Limits, recorded before any data:**
- **Checks 3–5 measure agreement with one correct solution,** not correctness. For example, API match counts a
  fallback the developer also wrote as much as the main call: it scores PRISM's `document.execCommand` copy above
  INJECTED's `navigator.clipboard.writeText`.
- **Undefined names are checked for Python only.**

**Checked:**
- **On the three feature-02 runs above:** BASELINE 0.40, PRISM 0.77, INJECTED 0.90.
  - BASELINE: `sys` flagged, helper not reused, a regression.
  - PRISM: escaping missing.
- **On multidict and typer runs:** a delete's own target is not counted as a helper, and runs with no source change
  are not reviewed.
- **Tests:** `tests/test_code_review.py`.

**The cells install `pyflakes==3.2.0`.** Both cells are re-pinned. Results stay in `reports/r4/rebaseline5` (no runs
yet).

### Seed shards (user, 2026-11-18, after wave 1, before any further run)

**Why:**
- **Runs take longer than estimated:** wave 1 averaged about 9 minutes a run, so the 600 runs need about 91
  GPU-hours, not about 60.
- **A Kaggle session stops at 12 hours.**
- **One session at a time would take about 3 weeks.** Shards let several sessions run at once.

**The change** (`kaggle/r4_rebaseline_cell.py`):
- **One shard = one seed.** `SHARD_ID` (1–10), at the top of the cell, selects seed `41 + SHARD_ID` (1 → 42, …,
  10 → 51).
  - **One cell run:** that seed's 60 runs (20 tasks × 3 arms).
  - **Results:** `reports/r4/rebaseline5_seed<SEED>`.
- **Parallel shards:**
  - each pushes only its own folder;
  - a push rejected because another shard pushed first is retried on the new head, up to 10 times, with waits
    staggered by shard.
- **Resume:** running the same `SHARD_ID` again restores that seed's folder and skips every finished run (as before).
- **Health check, per shard:** when a shard's 60 runs are done, the same check runs on its seed (infrastructure deaths
  ≤ 2, parse errors < 2% of turns). On failure, the cell pushes, says which shard failed, and stops.

**The analysis:**
- `rebaseline_report.py` accepts a glob (or a comma-separated list) of results folders and merges them:
  `'reports/r4/rebaseline5_seed*'`.
- **A run found in two folders stops the report,** so nothing is counted twice.
- **The pre-registered analysis runs on all shards merged.** Each shard's own `report.json` is a progress view only.

**Unchanged:** the design (20 tasks × 10 seeds × 3 arms), the arms, the harness, the metric, the tests, the readings.
- **The code the runs use is unchanged:** since `fed054b3`, the only change under `src/` and `harness/` is
  `rebaseline_report.py`.
- **So runs from `fed054b3` and from the new pin are the same experiment.**

**Existing runs moved, not repeated:**
- **Seed 42:** all 60 runs from `reports/r4/rebaseline5` → `reports/r4/rebaseline5_seed42`, with:
  - the wave-1 health check (passed: 0 deaths, 0.52% parse errors);
  - the pre-flight;
  - the `runs_*.log` files, which also hold seed 43's first runs.
- **Seed 43:** its 8 finished runs → `reports/r4/rebaseline5_seed43`.
- **`pilot_runs.jsonl`:** split by seed.
- **Dropped:** the partial log of the run cut off by the session limit (`r4s-delete-03__INJECTED__43`; it reruns
  from the start) and a leftover per-repository task file.
- **Shard 1 (seed 42) is therefore complete;** shard 2 (seed 43) continues from 8 of 60.

## Phase 2 changes to what the arms see (recorded 2026-11-20; not in force for any run yet)

The Phase 2 work (`PHASE2_PLAN.md`) changes the agent's tool answers. None of the 600 re-baseline runs used it. A run
on the new code is a new experiment and needs its own pre-registration, written before it starts; this entry lists
what that run would inherit.

1. **C1–C3, harness feedback, all arms** (`--feedback`; `harness/experiments/r3/feedback.py`):
   - an edit that leaves a file unparseable is applied and warned about;
   - a failing test run shows its first real error;
   - a repeated identical call with an identical answer gets a note.

   It is off unless `--feedback` is given.
2. **Pass 2d, the reference graph** (`fc602f09`): PRISM records imports, `__all__`, type use, isinstance/except,
   decorators, values and attribute reads. Before B10 this changed no tool answer: the main graph was untouched.
3. **B10, the tools show the references** (`301db5a9`):
   - `prism_change_context` (section 4) and `prism_blast_radius` list a symbol's uses other than calls, labelled by
     kind, with files and lines;
   - **arms affected:** PRISM and INJECTED (INJECTED's injected change context included); BASELINE has no PRISM
     tools.

   Measured (`harness/experiments/graph_coverage/PROGRESS.md`): 27% → 61% of sampled uses shown to the agent.
4. **Grouped listing** (`58309494`):
   - when a symbol's dependents exceed their share of the answer, the nearest are listed in full and the rest
     grouped by file;
   - an anchor whose code exceeds its share is given as an outline.

   Shown: 61% → 63%. Arms affected: PRISM and INJECTED.
5. **Bug fix: PRISM's test-file rule** (`prism.scanner.entrypoint_gate.is_test_file`):
   - **`spec/` directories:** they counted as test code in every language. Now only for JS/TS/Ruby files.
   - **Modules named `test.py`/`tests.py`:** they counted as test code by name. Now only when their code is test
     code (a `test*` function, a `TestCase` subclass, a pytest/unittest import).
   - **What was hidden:** with tests left out (the harness), production code such as litestar's
     `litestar/openapi/spec` and werkzeug's `werkzeug/test.py` was hidden from the agent's tool answers.
   - **Arms affected:**
     - every arm's PRISM tools (PRISM, INJECTED);
     - wherever PRISM decides what is test code.

     It changes no task, test or metric.
6. **Variable-type tracking in the reference pass** (`6a20ee91`):
   - **What it adds:** uses through a typed variable (`self.client.send`, an annotated parameter, a module-level
     instance) are linked with call resolution's own resolver, clean resolutions only.
   - **Measured** (with a corrected benchmark, `harness/experiments/graph_coverage/PROGRESS.md`): shown to the
     agent 74.1% → 77.3% of sampled production uses.
   - **Arms affected:** PRISM and INJECTED.
7. **Phase D, PRISM search and read:**
   - **`prism_search`:** `grep` over the working files. Each hit is labelled with what it is (import, definition,
     call, type annotation, decorator, base class, isinstance, except, string, comment), the definition it is in, and
     its file's kind (test, docs, config, template). Labels come from the current text.
   - **`prism_read`:** `read_file`'s exact output, plus a footer with the file's kind and the definitions in the lines
     shown.
   - **Arms affected:** both tools are added to the PRISM and INJECTED arms' tool lists, which changes their prompts.
   - **A new arm, `PRISM_ONLY`:** PRISM's tools without `grep` or `read_file`. It is available
     (`runner.py`/`pilot_run.py`) and runs only under its own pre-registration.
   - **Known wording:** the shared tools' edit-error messages refer to "the line numbers read_file shows". In
     PRISM_ONLY those are `prism_read`'s, which are numbered the same way.

## Phase 2, 4 arms (pre-registered 2026-11-21, before any run of this design)

The user's decisions (2026-11-21): derived tasks; 20 tasks × 10 seeds × 4 arms = 800 runs; PRISM_ONLY as the fourth
arm; sharded by seed; a smoke test first.

**What is in force for this design:** every change in "Phase 2 changes to what the arms see" above (items 1–7).
- The C1–C3 harness feedback, for every arm (`--feedback`).
- PRISM's reference graph, with variable-type tracking and the test-file rule fix.
- The tools' labelled other uses and the grouped listing.
- `prism_search` and `prism_read` in every PRISM arm.
- Otherwise the Phase 1 harness as in the 3-arm re-baseline: native tool calls read by the harness (`--raw-tools`),
  hygiene and compaction (`--phase1`), the progress ledger, the done gate, turn cap 28, budget 300,000 tokens,
  devstral-small-2 (`devstral-small-2:24b-instruct-2512-q4_K_M`, Ollama 0.34.0).

**Arms:**

| Arm | Tools | Context |
|---|---|---|
| BASELINE | shared tools (`read_file`, `list_dir`, `grep`, `edit_file`, `run_tests`, `done`) + `related_code` | none |
| PRISM | shared tools + PRISM's 8 tools (`prism_search`, `prism_read` among them) | none |
| INJECTED | as PRISM | PRISM's change context for the target (derived and delete tasks) or `prism_ask` on the feature description, retrieved before the first turn (`r4/inject.py`) |
| PRISM_ONLY | as PRISM, without `grep` and `read_file` | none |

**Tasks** (`harness/experiments/r4/phase2/tasks.json`; `TASKS.md`; sha256 `62c83aea7f26…`, in the file):
- **15 derived:** 5 rename, 5 signature change (a parameter made keyword-only), 5 module move.
  - Each target is used from 3–10 other files (PRISM's graphs ∪ the graph-coverage benchmark's reading).
  - Each task was validated in its repository's environment and scored end to end (`derive_scoring_check.py`): the
    answer key is resolved and accepted by the done gate; the unchanged start is neither.
- **5 controls:** the 5 tasks of the re-baseline every arm solved most often (`r4s-delete-10`, `-08`, `-09`,
  `r4s-feature-02`, `r4s-delete-05`), unchanged.

**Seeds:** 42–51, one shard each (`SHARD_ID` 1–10 → seed 41 + `SHARD_ID`), 80 runs per shard, in
`reports/r4/phase2_seed<SEED>`.

**Primary metric:** each run's score in [0, 1], 1 exactly when the run is resolved.
- derived task: 0 if the static completeness check fails (`r4/derived_checks.py`), else (fail-to-pass passing /
  fail-to-pass) × (pass-to-pass passing / pass-to-pass);
- delete and feature (the controls): as in the re-baseline.

**Primary analysis** (`rebaseline_report`, design `phase2`, chosen by the task file):
- Per task, the mean score over its 10 seeds in each arm.
- Three paired comparisons over the 20 tasks, each against BASELINE: PRISM − BASELINE, INJECTED − BASELINE,
  PRISM_ONLY − BASELINE.
- The pre-registered test: the Wilcoxon signed-rank test, two-sided, zero differences dropped, at α 0.05,
  Holm-corrected over the three.
- Also reported: the mean difference with a 95% bootstrap interval (tasks resampled, 10,000 draws, fixed seed), the
  paired t-test, the tasks favouring each arm.

**Descriptive (no test):**
- INJECTED − PRISM and PRISM_ONLY − PRISM.
- The same comparisons on the 15 derived tasks alone and on the 5 controls alone (`subsets`).
- Per family.
- Solve rates and McNemar's exact test on task-seed pairs.
- Process: infrastructure deaths, parse errors, endings, tokens, PRISM output share, compaction, failed edits.
- Turn efficiency: `prism_search` and `prism_read` count as PRISM calls.
- Post-PRISM behaviour, for the three PRISM arms.
- The code review. Its checks against the developer's change do not apply to derived tasks: the answer key is
  mechanical, not a commit.

**Readings fixed now:**
- **What a significant effect means:** a positive PRISM_ONLY − BASELINE means PRISM's tools can stand in for `grep`
  and `read_file` on these tasks; a negative one, that they cannot (yet).
- **What the subsets mean:** a positive effect on the derived tasks with none on the controls is the hypothesis
  Phase 2 was built for. A negative effect on the controls means PRISM costs something on easy tasks.

**Health check, per shard** (as the re-baseline's):
- **The limits:** at most 2 infrastructure deaths of its 80 runs, and parse errors below 2% of its turns.
- **On failure:** the cell pushes, reports the shard as failed, and stops.

**Smoke test first** (`kaggle/r4_phase2_smoke_cell.py`):
- **What runs:** 4 tasks (`p2-rename-02`, `p2-signature-02`, `p2-move-05`, `r4s-delete-05`) × 4 arms × seed 42 =
  16 runs, in `reports/r4/phase2_smoke`.
- **What it checks:** that the 4th arm, the new tasks, the generated tests and the static check work on Kaggle.
- **Its results are not part of the 800 runs.**
- **It passes when:**
  - all 16 runs end and are scored;
  - no infrastructure death;
  - parse errors below 2% of turns;
  - every derived run's score records the static check;
  - INJECTED's retrieval returns no error;
  - each derived task's environment sets up.
- **Before any shard:** the results are reported back to the user.

**Fixed for the run:** the code commit and its `src`/`harness` trees are pinned in both cells (`r4/phase2_cell.py`).

**Smoke test, first attempt (2026-11-21): stopped by the raw-template pre-flight, before any run.**
- **What it found:**
  - the prompt token counts matched in all 4 cases, so the harness renders the template exactly as Ollama does;
  - in one case ("PRISM: task only") the two paths' replies diverged inside an argument (`"src/a.object"` against
    `"src/a.ts"`), with the same tool and identical prompts. At temperature 0.2 the two paths batch the same prompt
    differently, and a near-tie token flipped. It is a choice of the model, not a misreading by the harness. The
    case passed in the re-baseline; Phase 2's PRISM prompt lists two more tools.
- **Change** (`r4/raw_preflight.py`, before any run of this design):
  - the pre-flight samples greedily;
  - wherever `/api/chat` returns a call, the raw reply must be read as a call to the same tool whose argument values
    are all in the raw reply's own text (`call_read_ok`, which catches a parser fault);
  - equal argument values are recorded (`args_match`), not required;
  - the token-count check is unchanged.
- **The runs are unchanged** (temperature 0.2).

**Smoke test, second attempt (2026-11-21): the full cell ran instead.**
- **What ran:** shard 1 (seed 42, 80 runs, `reports/r4/phase2_seed42`), started instead of the smoke cell. Same code
  commit (`ecdf23e0`), tasks and settings: it is a shard of the pre-registered design, not a different experiment.
- **Its first runs stand in for the smoke test.** The smoke criteria are checked on them before any other shard
  starts. At 54 of 80 runs (read from its checkpoints, outcomes not analysed):
  - no infrastructure death;
  - parse errors 5 of 1,287 turns (0.4%);
  - INJECTED's retrieval: no error;
  - every derived run's score records the static check;
  - the pre-flight passed.
- **One observation, recorded before any analysis:** the control `r4s-delete-09` (litestar) reached the turn cap in
  all four arms, where the re-baseline solved it 28 times in 30. The turn logs show steady progress (edits to the
  tests that use the removed function), not a crash. It is examined after the shard, before shards 2–10.

**Shard 1 complete (2026-11-21), the smoke criteria met:**
- 80 of 80 runs ended and were scored, in 6.9 hours.
- Health check passed: 0 infrastructure deaths; parse errors 1.07% of turns.
- The pre-flight passed; INJECTED's retrieval had no error; every derived run records the static check; every
  derived task's environment set up.

**The `r4s-delete-09` observation was a misreading** (corrected before any analysis):
- In both the re-baseline and here, every run of it reaches the turn cap without calling done, and its final tree
  scores resolved (target absent, every scored test passing). The re-baseline's "28 of 30 solved" counts those trees.
- So nothing changed. The note above compared the run's ending ("stuck") with the re-baseline's solve count, which
  measures something else.

**Shards 2–10 may start.** Shard 1's own `report.json` is a progress view only; the pre-registered analysis runs on
all 10 shards merged.

**Phase 2 analysed (2026-11-22): `PHASE2_RESULTS.md`.**
- **Pre-registered primary result:** no arm differs from BASELINE (Holm p = 1.0 for all three).
- **A task-construction error found after the analysis:** `p2-rename-04`'s answer key also renamed unrelated `find`
  calls, so the task scores 0 in every arm. It is a tie, and leaving it out changes no primary p-value.
  `PHASE2_RESULTS.md` reports it; the pre-registered analysis is not altered.

## Phase 3, hidden dependencies, two stages (pre-registered 2026-11-22, before any run of this design)

The user's decisions (2026-11-22): a proof of concept of 45 runs whose only output is a go/no-go verdict, then a
confirmatory stage of 225 runs; 3 arms (INJECTED dropped); 2-3 easy controls, `r4s-delete-05` among them; real code
only, at most 12 places to fix per task, answer keys from PRISM's resolved uses rather than token matching. This
section, the go/no-go rule above all, is fixed before any Phase 3 run, POC included.

**Why Phase 3:** in Phase 2, every place a derived task had to change contained the target's unique name, so `grep`
found all of them (recall 100%), and no arm differed from BASELINE. Phase 3's tasks rename a method whose name other
objects share, so that grep cannot tell the places from the look-alikes.

**What is in force:** everything in force for "Phase 2, 4 arms", plus one tool change made since (commit `c208936c`,
the delete-05 fix): `prism_read_code` and `prism_change_context` show a definition's enclosing `if`/`else`, `try`,
`with`, loop or `match` block (up to 40 lines), and say which branch it is in and whether it is that branch's only
statement. Phase 2's runs of `r4s-delete-05` deleted the two lines `prism_read_code` showed and left an empty `else:`.
Turn cap 28, budget 300,000 tokens, devstral-small-2, as in Phase 2.

**Arms:** BASELINE, PRISM, PRISM_ONLY, exactly as in Phase 2.

**Tasks: hidden-dependency renames** (`r4/hidden_scout.py`, `r4/hidden_tasks.py`; family `rename_hidden`):
- **The target:** a production method whose simple name other code also uses (other classes' methods, attributes,
  keywords), in the repositories and at the revisions of Phase 2.
- **The places:** the calls PRISM resolves to it, recorded site by site while PRISM builds its graph, plus the
  non-call uses in PRISM's reference graph. A candidate is dropped when PRISM's reading is not unambiguous:
  - a guessed (tentative) call into it, an override in either direction, or an edge no recorded place explains;
  - a reference-graph line with more or fewer uses of the name than the edge counts;
  - the name in any string literal (a `getattr` PRISM does not read).
  2-12 places outside the definition, in 2+ files besides its own, at least one in production code.
- **The answer key** renames the definition and each place at its line and column, and nothing else.
- **Validated in the repository's environment, as Phase 2's derived tasks were:**
  - the scored tests pass at the start, 3 runs;
  - the answer key passes them, a generated hidden test and the static check, 3 runs, nothing flaky;
  - the start fails the check and at least one test;
  - **new:** renaming the definition alone fails at least one scored test, so the places are live code;
  - end to end through the runner's scorer and the done gate (`derive_scoring_check.py`).
- **Methods of classes with a base from outside the repository** are dropped (a rename may stop overriding the
  library's method), as in Phase 2, except for `ABC`, `Generic` and `Protocol`, which define no such method (added
  2026-11-22 during mining, before any run).
- **The scored tests:** up to 4 test files: those with a place, then those naming the method's class, then those
  importing a changed module. A file is used only if every test in it passes at the start; one failing for reasons of
  its own (the network, in the mining sandbox) is skipped rather than rejecting the candidate (added 2026-11-22
  during mining, before any run).
- **A known bias, stated now:** a candidate whose answer key fails the tests (PRISM missed a place the tests
  exercise) is rejected. The task set therefore holds only targets PRISM resolves completely enough for the tests.
  The rejections are counted and reported as a measurement of PRISM's recall, never hidden.
- **The static check** (`derived_checks.py`, kind `rename_sites`): in every file, every scope (the enclosing
  definitions) and every receiver (`self.conn` in `self.conn.close`), the uses of the old name must be exactly as
  many as in the answer key. More is a place still to rename; fewer is a look-alike renamed or removed. The done gate
  gives counts only, never where.
- **The prompt** names the method, its file and line and the new name. It says that other functions, methods and
  attributes with the same name belong to other objects and must stay unchanged.
- **Recorded per task** (`probes`): what the definition-only rename and the grep-and-replace rename (every token of the
  name in every file that has it) do to the scored tests and to the static check.

**Controls:** `r4s-delete-05` (the check on the fix above), `r4s-delete-08` and `r4s-delete-10`. Every arm solved the
last two in all 10 of Phase 2's seeds. All three are unchanged.

**Task selection, fixed now:** the validated hidden tasks are ranked by grep's precision (files with a place / files
`git grep -w` finds the name in), lowest first; ties go to the most look-alikes, then the repository, then the name.
- **POC (5 tasks):** the top 4, at most one per repository, plus `r4s-delete-05`.
- **Confirmatory (15 tasks):** the POC's 4, then the next 8 in the same ranking with at most 2 per repository (3 if
  fewer than 12 qualify), plus the 3 controls.

**Seeds:**
- POC: 42, 43, 44 (5 tasks × 3 arms × 3 seeds = 45 runs), one shard per seed, in `reports/r4/phase3_poc_seed<SEED>`.
- Confirmatory: 101-105 (15 × 3 × 5 = 225 runs), one shard per seed, in `reports/r4/phase3_seed<SEED>`.
- The POC's runs never enter the confirmatory analysis.

**Primary metric:** each run's score in [0, 1], as in Phase 2: a hidden task scores 0 if the static check fails,
else (fail-to-pass passing / fail-to-pass) × (pass-to-pass passing / pass-to-pass). A run is solved when it is resolved.

**POC go/no-go rule** (`rebaseline_report.go_no_go`; task file `"stage": "poc"`; constants `GO_MIN_EXTRA_SOLVES = 3`,
`GO_MAX_TASKS_LOST = 1`, `GO_MAX_DEAD = 2`). On the 4 hidden tasks, 12 runs per arm:
- **GO** when both hold:
  1. at most 2 of the 45 runs are missing or died on infrastructure;
  2. PRISM or PRISM_ONLY solves at least 3 more of its 12 hidden runs than BASELINE solves of its 12, and BASELINE's
     mean score is higher than that arm's on at most 1 of the 4 hidden tasks.
- **NO-GO** otherwise: the confirmatory stage is not run as registered here. The POC is reported, and any redesign
  is a new amendment.
- **Not a test, and nothing else is read:** no p-value is computed for the decision. `r4s-delete-05`'s result is
  reported (did the fix work?) but does not enter the verdict. Neither does the verdict rest on any other number.

**Confirmatory analysis** (`rebaseline_report`, design `phase3`):
- **The data:** per task, the mean score over its 5 seeds in each arm.
- **Primary, on the 12 hidden tasks:** PRISM − BASELINE and PRISM_ONLY − BASELINE, the Wilcoxon signed-rank test,
  two-sided, zero differences dropped, α 0.05, Holm-corrected over the two.
- **Also reported:** the mean difference with a 95% bootstrap interval, the paired t-test, the tasks favouring each
  arm, and McNemar's exact test on task-seed solves.
- **Descriptive:**
  - PRISM_ONLY − PRISM;
  - the same comparisons on the 3 controls (`subsets`);
  - process, turn efficiency and post-PRISM behaviour, as in Phase 2;
  - per task, whether a failed run missed places or renamed look-alikes (the check's two counts).

**Readings fixed now:**
- **A significant positive PRISM − BASELINE on the hidden tasks:** PRISM's resolved uses help where grep's matches
  mislead.
- **Neither arm significant:** on these tasks devstral-small-2 does as well with grep and reading. The task-set bias
  above makes that the stronger negative result.
- **A negative effect on the controls:** PRISM costs something on easy tasks.

**Health check, per shard:** at most 2 infrastructure deaths of the shard's runs, and parse errors below 2% of its
turns. If a shard fails, the cell pushes, reports it as failed, and stops.

**Fixed for the runs:** the code commit and its `src`/`harness` trees, and the sha256 of the task list, pinned in the
cells.

### Phase 3 amendment 1: the LSP-enabled baseline (2026-11-22, before any Phase 3 run)

**The user's decision:** "Baseline should be like Copilot/Claude, using LSP, not just grep." PRISM is compared against
an agent with a language server's reference search, not against grep alone.

**The arm:** BASELINE_LSP replaces BASELINE in both Phase 3 stages. It is BASELINE's tools (the shared tools and
`related_code`) plus `find_references`:
- the language server's Find All References (`r3/lsp_tools.py`), backed by jedi 0.19.2 (the engine of
  jedi-language-server and multilspy's Python default), pinned in the cell;
- it resolves a dotted name or a `path:line[:column]`, and returns every place in the project whose name infers to
  that definition, as `path:line:column: <line>`, up to 200 lines;
- it reads the working tree as it is now, edits included, as a language server reads the editor's buffers;
- its output is not summarised and not padded: it is what the server returns, rendered as text.

The arm name `BASELINE` keeps its Phase 1/2 meaning. PRISM and PRISM_ONLY are unchanged and do not get
`find_references`. The comparisons become PRISM − BASELINE_LSP and PRISM_ONLY − BASELINE_LSP. The go/no-go rule
compares against BASELINE_LSP.

**A second reader in mining:** a hidden task is kept only if jedi's references to the definition are exactly PRISM's
places, by file and line (`hidden_tasks.jedi_agreement`).

**Measured before any run, on the 23 validated candidates:**
- **jedi agreed with PRISM on 22 of 23.**
- **On the 23rd (scrapy `ResponseTypes.from_args`), jedi found a call PRISM missed:**
  `scrapy/utils/_download_handlers.py:105`, `responsetypes.responsetypes.from_args(...)`. That answer key was
  incomplete, and the scored tests did not exercise the missed call. The task is dropped.
- **`find_references`' answers:** 378-1,836 characters (mean 855) for 3-11 references, 0.1-3.9 s.
- **One task (`ContentSecurityPolicy.to_header`), the first call's output:**

  | Arm | First call | Tokens |
  |---|---|---|
  | BASELINE_LSP | `find_references` | 166 |
  | PRISM | `prism_change_context` | 1,706 |
  | PRISM_ONLY | `prism_search` | 3,410 |

**What this means for the design (recorded now, before any data):** on these tasks the LSP baseline sees exactly the
places the answer key holds, at a tenth of the tokens of PRISM's change context. A correctness gap like Phase 2's
`p2-signature-02` is not expected, and the token premise (an LSP floods the context) does not hold at 3-12
references. Whether the go/no-go rule or the primary metric changes is the user's decision. Until the user amends
it, the rule above stands unchanged.

### Phase 3 amendment 2: an IDE agent's baseline, PRISM's own references (2026-11-22, before any Phase 3 run)

**The user's decisions:** the baseline should have what Claude Code, Cursor and Copilot have (semantic search, AST
search, a shell, a language server's references). The PRISM arms get PRISM's own equivalents, never the baseline's
tools: in Phase 1 the agent preferred the standard tools whenever both were offered, and bypassed PRISM.

**Phase 3's arms** (`r3/loop.py` `PHASE3_ARMS`; these replace amendment 1's):

| Arm | Tools |
|---|---|
| BASELINE_LSP | the shared tools (`read_file`, `list_dir`, `grep`, `edit_file`, `run_tests`, `done`), plus `find_references` (jedi 0.19.2), `semantic_search` (BM25 over code chunks), `ast_search` (ast-grep 0.39.6) and `run_shell` (read-only) |
| PRISM_LSP | the shared tools and PRISM's tools, `prism_find_references` among them; no language server, semantic search, ast-grep or shell |
| PRISM_ONLY | PRISM's tools with `prism_find_references`; without `grep` and `read_file` (as in Phase 2) |

- `related_code` (BM25 over functions) is dropped from BASELINE_LSP: `semantic_search` covers it.
- Phase 2's arms (BASELINE, PRISM, INJECTED, PRISM_ONLY as run) are unchanged at their pinned commit.

**`prism_find_references`** (`r3/tools.py`):
- **The places:** every place PRISM's graph resolves to the definition:
  - calls and instantiations at each call's own line and column. The builder now records every call's position on
    its graph edge (`sites`, `prism.graph.concrete_builder._note_call_site`; index schema 10). Before this, an edge
    stood for all of a caller's calls to a callee;
  - the reference graph's labelled uses (import, `__all__`, type annotation, subclass, isinstance, decorator,
    value, ...);
  - overrides in both directions.
- **Each place:** labelled with its kind and the definition it is in, grouped by file, production before tests.
- **Possible references, listed apart for the agent to check:** calls PRISM matched only by name (receiver type
  unknown, newly marked `via="name_match"` on the edge) and guessed calls (`TENTATIVE_CALL`). A language server
  leaves these out silently.
- **The footer:** how many other uses of the same name belong to other objects.
- **On the edited tree:** it reads the edited tree as PRISM's other tools do, through the WorkingIndex refresh.

**The baseline's new tools** (`r3/baseline_tools.py`, the working tree as it is now):
- **`semantic_search`:** BM25 over Python functions, methods, classes and module code, and 40-line windows of
  other text files, with split identifiers; the top 10 chunks. An embedding model was ruled out: it would be
  downloaded and run on CPU every run, beside the GPU model.
- **`ast_search`:** ast-grep patterns over Python files; up to 100 matches.
- **`run_shell`:** an allow-list of read-only commands joined by `|`, `&&`, `;`. Redirection, substitution,
  background jobs, writing or program-running options (`find -exec/-delete`, `git -c`, `rg --pre`, `sort -o`,
  `awk system`) and any command not on the list are refused. 20 s per call, 200 lines.

**Dry run** (`ContentSecurityPolicy.to_header`, the answer key replayed through the real runner): resolved in all
three arms. The first calls' output tokens:
- `find_references` 166, `semantic_search` 1,290, `ast_search` 1,554, `run_shell` 37;
- `prism_find_references` 287, `prism_change_context` 1,706, `prism_search` 3,410.
