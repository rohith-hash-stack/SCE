# Phase 3b amendment (draft, awaiting approval)

**Status:** draft for review.
- On approval it is copied unchanged into `AMENDMENTS.md` as "Phase 3b", with the approval date, and frozen.
- Nothing in it is built yet.

**Basis:**
- `DESIGN_PROPOSAL.md` (`339b591d`), as decided by the user (2026-11-26): option 1, arms A, C, E and BASELINE_LSP;
- the recovery tasks' turn sentence corrected;
- the historical answers left unchanged;
- the edit fix offline only.

## 1. Question

Does showing PRISM's source lines with their own indentation (E) reduce failed, unrecovered edits on the task where
POC 2's PRISM runs got stuck (p3-hidden-15)? And, descriptively, how does an exact replacement after a refused edit
(C) compare?

## 2. Arms

All arms run on one code commit, the devstral-small-2 24B model, and POC 2's flags
(`--ledger --phase1 --raw-tools --feedback --guards`), turn cap 60, budget 1M.

| Arm | Configuration | What differs from A |
|---|---|---|
| **A** | PRISM_GW, exactly as in POC 2 | nothing: its tools, gateway decisions, messages and flags are POC 2's |
| **C** | PRISM_GW + exact replacement after a refused edit | the gateway's message after a refused `edit_file` (below) |
| **E** | PRISM_GW + indentation kept in PRISM's source lines | leading whitespace of the source lines PRISM prints (below) |
| **BASELINE_LSP** | as in POC 2 | (the non-PRISM reference; neither change reaches it) |

**C and E are never combined.**

**Neither C nor E changes:**
- reference discovery, order, labels, counts or truncation;
- the gateway's decisions;
- the edit tool;
- `read_file` or `prism_read`;
- the prompts;
- the deletion-only historical answers.

**E, precisely:**
- **Where:** in `prism_find_references` (rows and possible references), `prism_search` (rows) and the gateway's
  messages that quote a source line.
- **The change:** each source line is printed with its own leading spaces and tabs, instead of `.strip()`. Trailing
  whitespace is still removed.
- **Layout:** unchanged. The line follows the existing `: ` separator.
- **Length caps:** counted on the text after the indentation.
- **Rows:** each listed place stays one row; no lines are added.

**C, precisely** (rename tasks, since the gateway acts only there):
- **When:** after an `edit_file` call that the edit tool refuses with "not found" (either message) in a file that has
  places still pending.
- **The added message, after the tool's own error:**
  - for the pending place in that file nearest the line the tool names (else the first pending place in that file);
  - the file's exact line, with its indentation, as `old`;
  - the same line with the target's name replaced at the places PRISM lists on that line, as `new`.
- **Message type:** actionable correction.
- **Unchanged:** the tool's error; no other message changes.
- **Why C is descriptive:** its text necessarily carries the right indentation, so C overlaps E's cause. It is
  compared with A descriptively only.

## 3. Tasks, seeds and runs

**p2-rename-04 failed admission and is excluded** (section 9; no replacement is mined). Two allocations, for the
user's decision:

**Allocation 1: as approved, without p2-rename-04.**

| Task | Role | Seeds | Runs (4 arms) |
|---|---|---|---|
| p3-hidden-15 (litestar) | primary: the stuck-run task | 301–310 | 40 |
| p3-hidden-03 (werkzeug) | recovery (not a stuck-run case) | 301–305 | 20 |
| **Total** | | | **60 runs, about 9.5 GPU hours** |

- **Shards:** 5. Shard k holds seeds 2k−1 and 2k of p3-hidden-15 and seed k of p3-hidden-03, so 12 runs per shard.

**Allocation 2 (recommended): the approved 80 runs, with the freed 20 on the primary task.**

| Task | Role | Seeds | Runs (4 arms) |
|---|---|---|---|
| p3-hidden-15 (litestar) | primary: the stuck-run task | 301–315 | 60 |
| p3-hidden-03 (werkzeug) | recovery (not a stuck-run case) | 301–305 | 20 |
| **Total** | | | **80 runs, about 13 GPU hours** |

- **Shards:** 5. Shard k holds seeds 3k−2 to 3k of p3-hidden-15 and seed k of p3-hidden-03, so 16 runs per shard.
- **Why:** the single test's simulated power for a large effect (0.67 → 0.10) rises from 0.59 to 0.85
  (`ADMISSION.md` section 3).

**Order within a shard:** interleaved: task, seed, then arm, in a fixed rotation, so no arm runs systematically first
or last.

**Task text:**
- p3-hidden-15 is POC 2's task, unchanged.
- p3-hidden-03 is copied from `phase3/poc.json`. One change is made, identical in every arm: "You have 28 turns in
  total" becomes "You have 60 turns in total". This is the correction POC 2 made, by the same function.
  - **Verified:** it is the only field that differs, at that one place.
- One task file serves every arm. Its sha256 is pinned in the cell.

## 4. Outcome definitions (frozen)

**Target places:** the start-side lines that the task's answer key changes and that contain the old name.
- This is the same rule for all three tasks; p2-rename-04 has no `places` field.
- It is read after the run, for analysis only.

**Reconstruction:** after each run, the file state after every turn is rebuilt from the task's start tree by
replaying the run's own `edit_file` calls through the same edit tool. Nothing is added to the run.

**Trigger** (per target place): the first `edit_file` request whose text does not match the file, refused as "not
found" (whitespace-only or not), and that the tool locates at that place. The location is the line the tool's error
names (whitespace match or closest lines). Requests that cannot be located are counted separately.

**Recovery:** within the next 5 model turns after the trigger, the place's line (followed through later edits by a
line diff) no longer has the old name at the listed column(s) and has the new name there.

**Run-level failure (primary outcome):** a run **fails** if any target place's trigger is not recovered within
5 turns. A run with no trigger, or with every trigger recovered, does not fail. Defined in every arm.

**Recovery mechanism** (per recovered trigger, first that applies):
1. automatic edit-tool correction (structurally 0 online: no arm has the edit fix);
2. gateway-provided correction: a C message was shown between trigger and recovery, and the recovering call's
   `old` and `new` equal its text exactly;
3. model correction after refusal: the model's own later edit recovered it;
4. other (a multi-line edit covering the place, or a file rewrite).

## 5. The analysis (frozen)

**The single test:** on p3-hidden-15, arm A against arm E, run-level failure, two-sided Fisher exact test,
α = 0.05, unpaired.

**Identical transcripts:**
- Within an arm and task, runs whose sequences of (tool, arguments) are identical are one observation.
- The test uses the deduplicated counts, and the report states n per arm before and after.
- The undeduplicated test is reported beside it as a sensitivity check, never instead of it.

**Descriptive only** (no p-value, or one labelled descriptive):
- C against A;
- E against C;
- every arm against BASELINE_LSP;
- the recovery tasks;
- resolved counts, turns and tokens;
- triggers per run and per 100 edits;
- whitespace mismatches per 100 edits;
- failed edit attempts per run;
- loop-breaker refusals;
- turns to recovery;
- recovery mechanisms.

**Limits stated in the report:**
- The test is small. Simulated power for a large effect (failure rate 0.67 → 0.10) is 0.59 at 10 seeds per arm and
  0.85 at 15 (allocation 2). For 0.67 → 0.33 it is 0.17 and 0.30.
  - At 10 per arm: 7/10 against 1/10 gives p = 0.020; 6/10 against 1/10 gives p = 0.057.
  - At 15 per arm: 9/15 against 2/15 gives p = 0.021; 9/15 against 3/15 gives p = 0.060.
- A non-significant result is not evidence that E has no effect.
- POC 2's runs are not a control: arm A is run alongside.

## 6. Ledger and instrumentation (analysis-side; nothing shown to the model changes)

**Per edit request:**
- run, turn, file, located place;
- text sent, source text at that line, text applied;
- whether the match was unique;
- the intended change present after the turn (from the reconstruction).

**Per gateway message:**
- type: **problem description** (pending-places worklist, stall, done block) or **actionable correction** (C);
- for C: whether the model's next edit was the exact text, different text at that place, or unrelated.

**Separation:** the gateway analysis and the edit-tool analysis are separate tables.

**Per solve:**
- whether the gateway intervened;
- whether the model followed it;
- whether the place was already right before the message;
- solves with no intervention counted separately.

## 7. The edit-tool fix (offline replay; no Kaggle arm)

**Inputs:** every recorded `edit_file` call the tool refused with a whitespace-only mismatch:
- 586 in all, about 485 under the current message;
- the two messages are reported separately.

**The file state at each call:**
1. the task's start commit;
2. its `start_patch`;
3. the run's earlier applied edits, in order.

**The rule** (pinned before replay; no tuning on its results):
- **Applies only when:**
  - `old` and `new` are each one line;
  - and `old` with its leading whitespace removed equals exactly one whole line of the file with its leading
    whitespace removed.
- **What is applied:** that line is replaced by `new`, with `new`'s leading whitespace replaced by the file line's
  own.
- **Refused, never applied:**
  - several matching lines (ambiguous);
  - a part of a line;
  - anything multi-line.

**Reported:**
- applied, ambiguous or out of scope;
- the text sent, the source text, the text applied, and the resulting file;
- the outcome:
  - **intended:** it equals the model's later successful edit at that line, or the answer key's line;
  - **unintended:** it differs from both;
  - **undetermined:** neither exists;
- whether the file still parses;
- single-line against multi-line;
- a variant that writes `new` as given, to show what re-basing the indentation prevents.

## 8. Environment

**Pinned and checked at the start of every shard:**
- the Ollama version;
- the model digest (devstral-small-2:24b-instruct-2512-q4_K_M, as in POC 2);
- the GPU type (T4 x2);
- the cell configuration.

**Records:** each shard records them, and a shard that differs stops before its first run.

## 9. Admission of the recovery tasks (run 2026-11-26, at the current code = `d769d27c`'s harness)

**Checks:**
- the scoring check (`derive_scoring_check`): gold resolves, empty does not, the gate accepts gold and rejects empty;
- POC 2's dry run (`phase2_dryrun --guards`): gold replay and empty script through BASELINE_LSP, PRISM_LSP and
  PRISM_GW.

**Results** (details in `ADMISSION.md`):
- **p3-hidden-03 is admitted.** Every check passes. In PRISM_GW the gateway infers the target and lists 17 places,
  with 0 blocks.
- **p2-rename-04 is excluded.** The gold replay through PRISM_GW (arm A) ends `stuck_done`, with 16 gold edits
  refused. Two causes:
  - 13 are genuine `tree.find(...)` test calls whose receiver PRISM cannot type, so the gateway refuses them as
    look-alikes;
  - 4 are the answer key's own renames of `str.find` and `soup.find`, which are not the target.

## 10. Before the cell is pinned (tests; all must pass)

1. **A is POC 2:**
   - With C and E off, every PRISM tool answer and gateway message is byte-identical to `d769d27c`'s on the same
     inputs.
   - This is checked by replaying the tool calls of the 15 POC 2 PRISM_GW transcripts against their recorded
     `output_sha`, plus the gold replays.
2. **E is formatting only:** on the same inputs, E's outputs equal A's once only the leading whitespace of each
   printed source line is removed.
3. **C adds only its message:** C's outputs equal A's with C's added message removed. C's message appears only after
   a refused edit.
4. **The deletion answers are unchanged:** historical answers are byte-identical with E and C on or off.
5. **The prompts:** every arm receives the same task file (one sha256). The recovery tasks differ from their sources
   only by the turn sentence.
6. **BASELINE_LSP is unaffected:** its tool answers are byte-identical with any switch.
7. **Replay sanity:**
   - gold replay resolves in every arm;
   - the empty script is unresolved or stuck.
