# Phase 3b amendment (final draft: built and verified; awaiting the user's approval to launch)

**Status:**
- The design was approved by the user (2026-11-26), and is built and verified (`phase3b/IMPLEMENTATION.md`).
- On approval to launch, it is copied unchanged into `AMENDMENTS.md` as "Phase 3b", with that date, and frozen.
- The cell is not pinned, and nothing has been run on Kaggle.

**Basis, as the user decided (2026-11-26):**
- option 1: arms A, C, E and BASELINE_LSP;
- the approved 80 runs, with 15 seeds per arm on the primary task;
- p2-rename-04 excluded;
- the recovery task's turn sentence corrected;
- the deletion-only historical answers left unchanged;
- the edit fix offline only;
- arm C's mechanics as below;
- a trigger per target place.

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

**C, precisely** (rename tasks, since the gateway acts only there; `Gateway(exact_fix=True)`):
- **When:** after an `edit_file` call that the edit tool refused (any `error`), or that the loop breaker refused as a
  repeat, in a file that has places still pending.
- **Which place qualifies:**
  - every use of the name on its line is the target's (no look-alike use to keep);
  - its whole line occurs exactly once in the file, so the `old` it gives matches once;
  - the task's new name is known (from the prompt's "to `NEW`").
- **Which place is named:**
  - the qualifying place nearest the line the tool's error names ("at line N" / "from line N");
  - for a loop refusal, the one nearest the place its last note for the same call named;
  - with no line, a note only if exactly one place qualifies;
  - **two qualifying places equally near: no note** (ambiguous).
- **The note:**
  - it comes after the tool's own error and after every other gateway message;
  - it gives the file's exact line as `old` and the same line with every use of the old name renamed as `new`,
    both JSON-quoted so the indentation is explicit.
- **Message type:** actionable correction (`warning_type`).
- **Recorded per note:** the refusal, the refused text, the place, the given `old` and `new`, and the model's
  response:
  - `exact`: its next applied edit sent that text;
  - `different`: the place was fixed by other text;
  - `none`.
- **Guidance only:** no file is changed, and no other gateway decision depends on it.
- **Why C is descriptive:** its text necessarily carries the right indentation, so C overlaps E's cause. It is
  compared with A descriptively only.

## 3. Tasks, seeds and runs

**p2-rename-04 failed admission and is excluded** (section 9; no replacement is mined).

| Task | Role | Seeds | Runs (4 arms) |
|---|---|---|---|
| p3-hidden-15 (litestar) | primary: the stuck-run task | 301–315 | 60 |
| p3-hidden-03 (werkzeug) | recovery (not a stuck-run case) | 301–305 | 20 |
| **Total** | | | **80 runs, about 12.7 GPU hours** |

- **Shards:** 5. Shard k holds seeds 3k−2 to 3k of p3-hidden-15 and seed k of p3-hidden-03, so 16 runs per shard
  (`phase3b_tasks.shard_order`; written into `phase3/phase3b.json`).

**Order within a shard:**
- Each task-and-seed block runs every arm, and the arms' order rotates by one from block to block.
- **Verified:** over the 80 runs, each arm holds each position in its block exactly 5 times.

**Task text:**
- p3-hidden-15 is POC 2's task, unchanged.
- p3-hidden-03 is copied from `phase3/poc.json`. One change is made, identical in every arm: "You have 28 turns in
  total" becomes "You have 60 turns in total". This is the correction POC 2 made, by the same function.
  - **Verified:** it is the only field that differs, at that one place.
- One task file serves every arm. Its sha256 is pinned in the cell.

## 4. Outcome definitions (frozen)

**Target places:** the task's recorded `places`, plus the definition's line.
- **Checked:** this equals the start-side lines that the answer key changes and that contain the old name:
  - p3-hidden-15: 16 lines;
  - p3-hidden-03: 17 lines;
  - the two rules differ only by the definition line.
- It is read after the run, for analysis only.

**Reconstruction:** after each run, the file state after every turn is rebuilt from the task's start tree by
replaying the run's own `edit_file` calls through the same edit tool. Nothing is added to the run.

**Trigger** (per target place; every place recorded separately): the first `edit_file` request whose text does not
match the file, refused as "not found" (whitespace-only or not), and that the tool locates at that place. The location is the line the tool's error
names (whitespace match or closest lines). Requests that cannot be located are counted separately.

**Recovery:** within the next 5 model turns after the trigger, the place's line (followed through later edits by a
line diff) no longer has the old name at the listed column(s) and has the new name there.

**Places with no trigger:** prevention outcomes (`no_mismatch`), recorded with their final state, never as missing.

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
- The test is small. At 15 runs per arm (fewer, if identical transcripts are collapsed), simulated power is:
  - 0.85 for a large effect (failure rate 0.67 → 0.10);
  - 0.61 for 0.67 → 0.20;
  - 0.30 for 0.67 → 0.33.
  - Examples: 9/15 against 2/15 gives p = 0.021; 9/15 against 3/15 gives p = 0.060.
- A's failure rate is estimated from 6 POC 2 runs, and is unknown at this commit.
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
- 586 in all: 512 under the current message, 74 under the older one;
- the two messages are reported separately.
- (The earlier figure of about 485 was a rougher classification.)

**The file state at each call:**
1. the task's start commit;
2. its `start_patch` and `after_index_patch`;
3. the run's earlier applied edits, in order.
4. **Check:** each rebuilt state must show the line the recorded error showed. **All 586 do.**

**The rule, as replayed** (`phase3b_edit_replay.py`, v2):
- **Applies only when:**
  - `old` and `new` are each one line, one trailing newline aside;
  - and `old` with its leading whitespace removed equals exactly one whole line of the file with its leading
    whitespace removed.
- **What is applied:**
  - that line (and its newline, if `old` ended with one) is replaced by `new`, with `new`'s leading whitespace
    replaced by the file line's own;
  - an empty `new` deletes the line.
- **Refused, never applied:**
  - several matching lines (ambiguous);
  - a part of a line, or more than leading whitespace different (out of scope);
  - anything multi-line.

**v1, the rule as first pinned, mishandled two cases. The first replay showed them; this is disclosed, not tuned:**
- **Deletion:** an empty `new` left the line's indentation behind, instead of deleting the line (4 events).
- **Trailing newlines:** "one line" stripped every trailing newline, so an `old` followed by many blank lines
  counted as one line (2 events).
- v2 fixes exactly these two cases, and nothing else changed. Both passes are kept (`IMPLEMENTATION.md`).

**Judged on the whole resulting file:**
- **intended:** it equals the file the run's own later edit of that line produced, or, failing that, the answer
  key's line;
- **unintended:** it differs;
- **undetermined:** neither exists.
- **Also reported:** whether the file still parses, single-line against multi-line, and a variant that writes `new`
  as given (no re-basing).

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
   - The test environment's own outputs (`run_tests`, a gate-checked `done`) are compared apart, against the same
     calls replayed locally on `d769d27c`.
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
