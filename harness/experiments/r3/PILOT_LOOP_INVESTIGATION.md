# R3 pilot: the PRISM loops (investigation, 2026-11-08; no code changes, no model calls)

**Data.** The saved pilot runs (`reports/r3/pilot/`, code `8ced8a58`). The per-turn logs keep only the first 300
characters of each tool output, so the full windows the model saw were rebuilt by **replaying each run's recorded
replies through the real harness**: the same runner, tools, window and stubbing rule, and budget accounting, with a
stand-in model that returns the recorded reply and token counts.
- All three looping runs replayed exactly: 25, 23 and 23 turns, ending at the budget as on Kaggle.
- Output differences in the replay:
  - `run_tests` failure counts, which also vary on Kaggle (B-02's IPC tests are timing-dependent);
  - the blast radius's internal `budget_consumed`, because tiktoken cannot download here and the tokenizer falls
    back. Not a semantic difference.

## 1. The three looping runs (PRISM arm)

| Task | Repo | Anchor change | Changed callers | Sequence (T run_tests, C change_context, R blast_radius) | Edits |
|---|---|---|---|---|---|
| B-02 | execa (JS) | `onceMessage` now takes one destructured object and calls a new `getMessage(...)` | 1 (`getOneMessage`) | T C R, repeated 8 times + T (25 turns) | 0 |
| B-08 | execa (JS) | `normalizeStdio` gains `isSync` and calls `normalizeStdioSync` / `normalizeStdioAsync` | 1 (`handleInput`) | T, then C R repeated 11 times (23 turns) | 0 |
| B-09 | aiohttp (Py) | `_resolve_host_with_throttle` gains `futures` and calls `set_result` / `set_exception` | 1 (`_resolve_host`) | T C R, repeated 7 times + C (23 turns) | 0 |

**The common property, and the only one that separates these 3 from the other 7 pilot tasks:** the anchor's new
code, which the prompt says is "already in the working tree", calls a function that is **not defined or imported
in its file** at the start state. That is `getMessage` in B-02, `normalizeStdioSync` and `normalizeStdioAsync` in
B-08, and `set_exception` in B-09. The developer's commit added those helpers, or the import, outside the anchor.
So the remaining work is not only "update the caller": it is also "write or import the missing helper".

**B-02.**
- Turn 1: `run_tests` shows 38 failing tests in `get-one.js`, including a stack trace:
  `TypeError: Cannot read properties of undefined (reading 'connected')` in `disconnect`, from `onceMessage`, from
  `getOneMessage`. The caller still passes 3 positional arguments to a function that now takes one object.
- Turn 2: `prism_change_context` shows the anchor's code, a digest, the caller with "passes 3 positional
  arguments", and the caller's code.
  - The digest lists the anchor's calls as `throwOnDisconnect` and `disconnect`. **`getMessage(...)` is not
    mentioned as a call at all**, and section 5 says "No resolved callee raises, returns nothing or changes
    state".
  - The positional-arguments line is correct, but nothing says that this is the mismatch.
- Turn 3: `prism_blast_radius` repeats the same single caller inside a ~1,600-token XML block (engine metadata,
  coverage features, edges).
- From turn 4 the model repeats T C R with identical arguments. Each repeat returns identical C and R outputs; only
  the flaky test count moves (32 to 39 failed). The window keeps the last few cycles verbatim. From turn 13 the
  oldest outputs are stubbed, but the loop was set by then.
- Solvable from where it stood? Yes, in principle: the caller's code and the anchor's code were both on screen. But
  the task also needs a `getMessage` helper that nothing on screen points to.

**B-08.**
- Turn 1: `run_tests` shows 0/134 passing, with `ReferenceError: normalize…` (truncated in the summary, present in
  the raw tail).
- Turn 2: `prism_change_context`'s digest line `L6 return isSync ? normalizeStdioSync(...) : normalizeStdioAsync(...)`
  shows the code but **gives no sign that neither function exists**. Section 5 lists only `getStdioArray`'s errors.
- The caller "passes 1 positional argument". The forced change is "new required parameter: isSync", but nothing
  says the caller does not pass it.
- The model then alternates C and R 11 times. Every output is identical (all 23 output heads match the replay).

**B-09.**
- Turn 1: 17 of 139 failing.
- Turn 2: `prism_change_context` **resolves the bare call `set_exception(fut, e)` to
  `aiohttp.client_proto.ResponseHandler.set_exception`**, a method of an unrelated class. Section 5 then states,
  as fact, that it "changes state: self._should_close = True".
  - In reality `set_exception` is not imported in `connector.py`. The right function, `aiohttp.helpers.set_exception`,
    exists next to the imported `set_result`, and the developer's fix adds it to the import line.
  - So the one missing piece was presented as already present.
- The blast radius (15 transitive callers) is cut at 4,000 tokens.
- The model repeats T C R 7 times with identical outputs.

In none of the three runs was an edit attempted and then abandoned: there were no `edit_file`, `read_file` or `grep`
calls at all. The model used the two PRISM tools as its only read actions, and re-asked them when the answer did not
show it what to do.

## 2. The same three tasks, baseline arm

| Task | Outcome | Turns | What it did |
|---|---|---|---|
| B-02 | unresolved | 8 | grep, edited a *test* file and added a null check in `validation.js`, then `done`: "Some tests still fail due to undefined references" |
| B-08 | unresolved | 8 | `related_code` for `normalizeStdioSync` (it returned only `normalizeStdio`), one real edit, one identical no-op edit, then `done`: "`normalizeStdioAsync` is not defined" |
| B-09 | unresolved | 22 | grep, edit, test, repeated 5 times with 8 edits (7 applied), then `done` |

Baseline did not solve them either (0 of 3). It also did not write the missing helpers. The difference is that it
kept acting (edits, varied search queries) and then gave up with an honest summary, where PRISM's model kept
re-reading. So the loop is not what made these tasks fail: both arms failed them. It is how the PRISM arm's failure
looks.

## 3. Pattern: looping vs non-looping PRISM runs

| Task | PRISM sequence | Outcome | Anchor calls a missing name |
|---|---|---|---|
| B-01 | T g g g D | unresolved (early done) | no |
| B-02 | (T C R)×8 T | budget | **yes** |
| B-03 | T C R D | unresolved (early done) | no |
| B-04 | T g E T E T D | unresolved (33/53 fail-to-pass passed) | no |
| B-05 | T E E g g D | unresolved (both edits refused) | no |
| B-06 | T D | unresolved (visible tests all pass) | no |
| B-07 | T C g E E T C E T D | **resolved** | no |
| B-08 | T (C R)×11 | budget | **yes** |
| B-09 | (T C R)×7 C | budget | **yes** |
| B-10 | T g D | unresolved (early done) | no |

- Five PRISM runs called `prism_change_context`:
  - 3 looped: B-02, B-08, B-09, the three missing-name tasks;
  - 1 resolved: B-07, whose remaining work, passing the new parameter in the caller, was in the caller code the
    tool showed;
  - 1 quit falsely: B-03, which said "ran tests to ensure they pass".
- The runs diverge at turn 3 or 4. Non-looping PRISM runs go to `grep` or `edit_file` by turn 2 to 4. The looping
  ones never call anything but the two PRISM tools and `run_tests`.
- Language and repository do not separate them: execa has 2 loops and 3 non-loops; aiohttp has 1 loop and 1
  non-loop.

## 4. Tool-response quality

- **`run_tests`:** correct, and clear enough. Failing test names come first, then the raw tail with the stack
  trace. The cause (`ReferenceError`, `TypeError ... 'connected'`) was visible in all three runs, partly truncated
  in B-08's summary.
- **`prism_change_context`:** what it shows is correct (anchor code, caller list, caller code), with one exception
  and one gap.
  - **Wrong fact (defect D1, B-09):** a bare call to a name that is not in scope is resolved by name to a method of
    an unrelated class, and that method's side effects are reported as the anchor's dependencies.
  - **Silent gap (D2):** a call to a name that resolves to nothing is dropped from the digest's call list and from
    section 5 (B-02, B-08). When the unresolved call is the line's main call, it is shown as `-> ?` (seen in R3a
    tasks B-46 and B-70). `-> ?` is terse, and the embedded-call case is silent.
  - **Not flagged (D3):** "passes N positional arguments" is stated, but its mismatch with the anchor's new
    parameter list is not drawn. The break check that would draw it (`prism.impact.breaks`) runs only when a
    tool caller passes `new_code`, and R3's tool layer does not.
- **`prism_blast_radius`:** correct callers, but rendered as machine-oriented XML (metadata, coverage features,
  warnings, edges) that repeats the caller code `change_context` already showed. In B-09 it is cut at 4,000
  tokens. Low added value for these tasks. It is a format issue, not a wrong fact.
- **Baseline's tools at the same point:**
  - `grep` and `related_code` gave smaller, query-dependent answers. B-08's `related_code` for `normalizeStdioSync`
    returned only `normalizeStdio`, which implied, correctly, that the name does not exist.
  - Baseline's model concluded "not defined". PRISM's model was told nothing (B-02, B-08) or the opposite (B-09).

## 5. Loop detection

The harness serves identical outputs silently. Nothing tells the model it has made the same call before. A "you
already called this with these arguments" signal would change the loop itself (both arms). The loop is
pre-registered for parity (R3-1, R3-5), so this is out of scope for R3: an R4 candidate, applied to both arms.

## 6. Early-done runs (done within 5 turns, no edit)

| Run | Turn-1 tests | Model's summary |
|---|---|---|
| B-06 PRISM, B-06 BASELINE | 149/149 pass | "All tests passed" |
| B-10 PRISM, B-10 BASELINE | 23 failing | "No tests found for the updated function" |
| B-04 BASELINE | 2 failing | "No tests found for shouldSplitLines" |
| B-01 PRISM | 11 failing | "No tests found that directly call `coerce_type`" |
| B-03 PRISM | 4 failing | "ran tests to ensure they pass" (false) |

- PRISM 4 runs, BASELINE 3; B-06 and B-10 are early-done in both arms.
- Two causes, the same in both arms:
  - **B-06:** the visible tests all pass at the start, because the tests that fail are the hidden ones. A task
    property: the agent has no failing signal.
  - **The others:** the model greps the *test* directory for the changed function's name, finds nothing, and
    decides nothing is needed, ignoring the failing tests it was shown at turn 1 (one false "tests pass").
- That is model reasoning, not a tool, and it is not arm-specific. It is not tied to task difficulty in an obvious
  way: B-10 needs 5 files, B-06 needs 1.

## 7. Verdict

**Mostly (b), model behavior on hard tasks, with one real PRISM defect on the same tasks.**

- **The loop** is the 14B model's repetition behavior: identical, correct outputs re-requested, with no edit tried.
  It happens on the three tasks whose remaining work, a missing helper or import, sits outside every caller. Both
  arms failed those three. So there is no evidence that PRISM's tools made a solvable task unsolvable. Baseline
  failed them by giving up, PRISM by looping.
- **But the PRISM output on exactly those tasks is weaker than it should be:**
  - **D1, a real defect (wrong fact).** A bare call whose name is not in scope resolves to an unrelated class's
    method of the same name (B-09: `set_exception` resolved to `ResponseHandler.set_exception`), and section 5
    reports that method's state changes.
    - **Minimal fix:** in the call resolver, an unqualified call (no receiver) does not resolve to a method. It
      resolves only to a module-level function or class in scope (same file, or imported); otherwise it is
      unresolved.
    - Cost: about half a day of code and tests.
    - Risk: it changes call-graph edges everywhere, not only in the digest (callers, blast radius, R2-era
      metrics). So it needs E1–E3, the dry run and the anchor check re-run, and strictly a re-pilot of the
      PRISM arm.
    - It changes no pre-registered rule.
  - **D2, a gap (silent unresolved calls).** Render every call whose callee is not found as an explicit line, for
    example "calls `getMessage()`: not defined or imported in this file", in the digest and in section 5.
    - Render-only, low risk (output text only), about 2 hours.
    - It changes no pre-registered rule.
  - **Correction (2026-11-09, `CONFIDENCE_DESIGN_STUDY.md` §0):** the break-check results quoted under D3 below
    came from a probe that passed the current code as both old and new. The break check compares the anchor's
    old code with its new code, and R3's index holds only the new code. With the old code from version control
    (`git show HEAD:<file>`), B-08 gives the right "passes nothing for `isSync`" and B-09 the right "`traces` now
    goes to `futures`". Only B-02's wrong statement is a real defect, an arrow-function parse bug in
    `breaks.signature`.
  - **D3, not flagged:** the caller/signature mismatch. The existing break check is not usable as is: on the
    three start states it gave one wrong statement (B-02, "calls `async`, which is now named `getMessage`"), one
    empty result (B-08) and one half-right one (B-09, the right call, the wrong parameter named). Enabling it
    would add wrong facts. Not proposed for R3.
- **Frequency in R3a.** A heuristic scan gives 16 of 80 tasks whose anchor calls a name not defined or imported in
  its file. The PRISM probe, which renders each with R3's own call, confirms about 8:
  - 0 resolved to an unrelated function (D1 not seen);
  - 2 shown as `-> ?`;
  - about 6 silent or only in the raw code line;
  - the rest are heuristic false positives.

## What this changes about R3a

**Recommendation: proceed with R3a as planned, with no PRISM change**, for three reasons:
- The loop is model behavior, on tasks that both arms failed.
- D1 was seen once, in the pilot, and not in R3a's probe.
- Any change to PRISM's resolver after the pilot would need the verification and the pilot redone.

Record D1 and D2 as R4 fixes, along with a loop signal applied to both arms and a readable blast-radius format.
Two things to add before R3a's analysis is frozen; they are analysis code, not rules:
- the diagnostic counters, which are **not implemented yet**: "looping runs per arm" (the same tool and arguments
  3 or more times with no applied edit between) and "early-done runs per arm" (`done` within 5 turns with no
  applied edit);
- the missing-name property per task, as a covariate.

If R3a's loop rate is much higher in PRISM than in baseline, or PRISM's loss concentrates on missing-name tasks,
that is a finding for the interpretation section: PRISM's context is caller-oriented and says little or nothing
when the gap is a missing callee.

**Alternative, if a wrong fact in the treatment is unacceptable:** apply the D1 and D2 fixes, re-run the
verification (E1–E3, dry run, anchor check) and the PRISM half of the pilot, then R3a. That costs about 1 to 1.5
days plus a Kaggle pilot session.
