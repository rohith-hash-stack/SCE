# Phase 3b: revised design (proposal, awaiting approval)

**Status:** proposal only.
- No fix built, prompt changed, task mined or run launched.
- It becomes binding only as an `AMENDMENTS.md` entry, after approval.
- Evidence: `TASK_VERIFICATION.md` (here) and POC 2 (`FINAL_REPORT.md`, `reports/r4/phase3_poc2_ledger.md`).

**A correction to the brief.** POC 2's resolved counts over all 5 tasks are:

| Arm | Resolved (of 15) |
|---|---|
| PRISM_GW | **13** |
| PRISM_LSP | 9 |
| BASELINE_LSP | 5 |

On the 4 hidden-dependency tasks only, the counts are 10/12, 6/12 and 2/12.

## 1. What exists now

- **No Phase 3b design is in the repository yet,** only the task verification.
- **The arms so far** are the ones proposed in conversation, all on the PRISM_GW harness:
  - **A:** POC 2 unchanged;
  - **B:** the edit fix;
  - **C:** the gateway's exact replacement text after a refusal;
  - **D:** B and C together.
- **The user's latest decisions:**
  - the edit fix is evaluated **offline only** (a replay);
  - it is not combined with E.
  - So B and D leave the Kaggle runs, and the online arms are A, C (if kept) and the new E.
- **PRISM_GW stays exactly as it is,** as arm A: the gateway's logic and messages, its tools and the POC 2 flags
  (`--ledger --phase1 --raw-tools --feedback --guards`), on the same model.
- **Requirement on the code:** with E and C switched off, every tool answer and gateway message must be
  byte-identical to `d769d27c`'s on the same inputs. A test checks this before any run.
- **Nothing replaces, merges with or redesigns PRISM_GW.** E and C are further arms beside it, selected by their own
  switches.

## 2. Arm E: indentation kept in PRISM's source lines

**The only change:** where PRISM prints a source line in these outputs, it prints the line with its own leading
spaces and tabs:
- `prism_find_references`: its rows and its possible references;
- `prism_search`: its rows;
- the gateway's worklist and its other messages that quote a line.

**What it does not change:**
- which places are found, their order, labels, counts, truncation rules or wording;
- the gateway's decisions;
- the edit tool;
- the prompts.

**Rendering details (to be pinned in the amendment):**
- **Trailing whitespace** is still removed (`rstrip`), so only the leading whitespace comes back.
- **The layout stays as it is** (`  181:5 [annotation] in `f`: <line>`). The line follows the existing single `: `
  separator, so a column-0 line reads `…: ) -> X:` and a 4-space line reads `…:     return X`.
- **One residual ambiguity:** the separator's space sits next to the line's own spaces. The alternative is a fresh
  layout, for example the line on its own row after a marker. That would also change the layout, so it would not
  isolate indentation. I recommend keeping the layout.
- **Length caps** (`[:240]`, `[:100]`) count only the text after the indentation, so deep indentation does not cut
  more of the code.
- **"Multi-line references":** each listed place stays one row with one line. Each row keeps its indentation;
  E adds no continuation lines, since that would change content, not formatting.
- **Historical answers (deletion only)** are also stripped (`r3/tools.py:494`). They are outside the requested scope
  and inert on these rename tasks. I recommend leaving them unchanged.

**What E does not touch, so it stays identical across arms:**
- the edit tool's own error, which shows `number<TAB>text` (seed 203 copied that tab);
- `read_file` and `prism_read` (`number<TAB>text`).

So E fixes the listings only; it cannot fix a mismatch made after reading a file.

**Arm C is kept apart from E.**
- **The overlap:** C's exact replacement text necessarily carries the right indentation, so C and E act partly on the
  same cause.
- **The difference:** C gives a fix after an error; E prevents the error.
- **How they are compared:** C is measured against A and E against A. Neither is combined with the other.

## 3. Edit-tool fix: offline replay only

**Inputs:**
- every recorded `edit_file` call that the tool answered with its whitespace-only mismatch;
- 586 such calls in all, of which about 485 are under the current message. The two messages are reported
  separately.

**The file state at each call:**
1. the task's start commit;
2. the task's `start_patch`;
3. every edit the original run applied before that call, in order.

The replay is one step from the real state. It does not invent an alternative trajectory: after the fix applies an
edit, the recorded run went on as if it had failed.

**The proposed rule** (pinned before the replay; no tuning on the results):
- **Scope:** `old` is a single line, and so is `new`.
- **The match:** after removing leading whitespace, `old` equals **exactly one whole line** of the file.
- **Refused:**
  - several such lines (ambiguous);
  - a match on part of a line;
  - anything multi-line.
- **What is applied:**
  - that line, with `new`'s leading whitespace replaced by the file line's own indentation;
  - otherwise the model's wrong indentation would be written into the file (seed 201's `new` also had four spaces).
- The replay also reports the variant that writes `new` as given, to show what re-basing the indentation prevents.

**Reported per call, and in total:**
- applied, refused as ambiguous, refused as out of scope (multi-line, part of a line, not whitespace);
- the text sent, the file text, the text applied;
- the result:
  - **intended:** it equals the edit the model later made successfully at that line, or the gold patch's line;
  - **unintended:** it differs from both;
  - **undetermined:** neither exists;
- whether the file still parses (Python);
- coverage: single-line against multi-line.

**Needs:** clones of the task repositories at their commits, as the miners use. It runs locally, with no Kaggle time.

## 4. Tasks

| Task | Role | Shown so far | What it can show |
|---|---|---|---|
| p3-hidden-15 (litestar) | **primary** | persistent failure in 4 of 6 PRISM runs; 0 mismatches in `conftest.py` in 3 BASELINE_LSP runs | whether E prevents the stuck run |
| p3-hidden-03 (werkzeug) | recovery | mismatches in 7 of 9 runs (POC 1); none persistent | whether E reduces ordinary mismatches |
| p2-rename-04 (pydantic-ai) | recovery | mismatches in 10 of 40 runs (Phase 2); none persistent | the same |

**The recovery tasks are not stuck-run cases,** and will not be reported as such. p2-rename-05 is not used.

**Two risks to clear before the amendment:**
1. **p2-rename-04 was built for Phase 2:** a 28-turn cap, older arms and an older prompt layout.
   - **History:** it has never been run under the POC 2 configuration, and it was solved 0 of 40 times there.
   - **The target:** the method is named `find`, a very common word.
   - **The check:** before it is admitted, it must pass the POC 2 checks: the gold patch resolves, the empty patch
     does not, the gate accepts gold, and a dry run of the gateway shows it infers the target and its worklist.
   - **If it fails,** the recovery set is p3-hidden-03 alone.
2. **The turn sentence:** both recovery tasks came from tasks whose prompts said "28 turns". POC 2 corrected that
   sentence to the 60-turn cap (`phase3_poc2_tasks.py`, recorded in its amendment).
   - The same, and only that, correction would be applied here, identically in every arm.
   - This touches the task files' prompts, so it needs explicit approval.

## 5. Arms and run allocation

**Run time:** POC 2 measured about 9.5 min per run on average, and 8–9 min for PRISM runs on p3-hidden-15. The hours
below are approximate.

**Option 1 (recommended): A, C, E and BASELINE_LSP.**

| Task | Seeds | Arms | Runs |
|---|---|---|---|
| p3-hidden-15 | 10 | 4 | 40 |
| p3-hidden-03 | 5 | 4 | 20 |
| p2-rename-04 | 5 | 4 | 20 |
| **Total** | | | **80 runs, about 13 GPU hours** (4 shards of 20 runs) |

- **Why BASELINE_LSP:** it is the reference that does not see PRISM's listings. It tests the root-cause claim, since
  its zero-mismatch figure rests on 3 runs. E does not change it.
- **Why 10 seeds on the primary:** it is where stuck runs happen, and where the one test is made.

**Option 2 (smallest): A and E only.**

| Task | Seeds | Arms | Runs |
|---|---|---|---|
| p3-hidden-15 | 12 | 2 | 24 |
| p3-hidden-03 | 5 | 2 | 10 |
| p2-rename-04 | 5 | 2 | 10 |
| **Total** | | | **44 runs, about 7 hours** |

- **What it gives:** the most power for the primary test.
- **What it loses:** C, and the BASELINE_LSP check.

**Option 3 (fuller): A, C, E, PRISM_LSP, PRISM_LSP with E, and BASELINE_LSP.**

| Task | Seeds | Arms | Runs |
|---|---|---|---|
| p3-hidden-15 | 10 | 6 | 60 |
| p3-hidden-03 | 5 | 6 | 30 |
| p2-rename-04 | 5 | 6 | 30 |
| **Total** | | | **120 runs, about 19 hours** |

- **What it adds:** E's effect without the gateway, since p3-hidden-15's persistent failures split 2 PRISM_LSP and
  2 PRISM_GW.
- **What it costs:** most of a weekly Kaggle quota.

**Every option:**
- keeps PRISM_GW (arm A) unchanged;
- runs the control alongside the new arms, not taken from POC 2;
- uses one code commit;
- puts every arm in every shard, in an interleaved order.

## 6. Metrics

**Trigger (the user's definition):** the first edit request at a target place whose text does not match the file,
including leading-whitespace mismatches.
- **The target places** are the task's recorded `places`.
- **An edit is located** by the tool's own line, when it reports one (whitespace match, closest line).
- **An edit that cannot be located** is counted separately, not assigned to a place.

**Recovery:** the intended change at that place is in the file within the next 5 model turns. It is read from the
file after each turn, replayed from the run's applied edits.

**Recovery mechanism:**
- automatic edit-tool correction (always 0 online, since the edit fix is not in any Kaggle arm);
- model correction after the tool's refusal;
- correction after the gateway's replacement text (C only);
- other.

**Primary metric, so that E is not measured on a selected subset:**
- **The problem:** E's main effect should be fewer triggers. So recovery *given a trigger* would compare different
  sets of runs in A and E.
- **The definition instead:** a run counts as **failed** when it has a trigger that is not recovered within 5 turns.
  A run with no trigger, or with every trigger recovered, counts as **not failed**.
- **Why this works:** it keeps the trigger and recovery definitions, and it is defined in every arm.

**Secondary (descriptive):**
- triggers per run and per 100 edits;
- whitespace mismatches per 100 edits;
- failed edit attempts per run;
- turns to recovery;
- refusals by the loop breaker;
- resolved count;
- tokens and turns;
- the gateway's intervention audit, per solve:
  - did it intervene?
  - did the model follow it?
  - was the place already right before the intervention?
  - solves with 0 interventions counted separately.

## 7. Ledger fields

- **For every edit request:**
  - its target file and place;
  - the text sent, the source text and the text applied;
  - whether the match was unambiguous;
  - whether the intended change was present afterwards;
  - and, for the replay only, whether the edit fix fired.
- **For every gateway message:** its warning type, either **problem description** ("N places still use …", stall)
  or **actionable correction** (C's exact replacement text). A further field says whether the model then sent that
  exact text, something different, or nothing related.
- **Separation:** the gateway analysis and the edit-tool analysis are kept in separate tables.

## 8. Inference: what can and cannot be tested

**The only statistical test, pre-registered: on p3-hidden-15, A against E.**
- **The measure:** the run-level primary metric.
- **The test:** a two-sided Fisher exact test, α = 0.05.
- **Pairing:** none. Seeds do not pair outcomes, and they diverge after the first sampled token.
- **Power (rough):**
  - The rate in A is estimated at 4 of 6 runs, which is uncertain, and unknown at the new commit.
  - With 10 runs per arm, 7/10 against 1/10 gives p ≈ 0.02, but 6/10 against 1/10 gives p ≈ 0.057. That is about
    even odds of a significant result when the true effect is large.
  - With 12 per arm (option 2) it is somewhat better.
  - A smaller effect cannot be detected at all.

**Descriptive only:**
- C against A;
- E against C;
- E against BASELINE_LSP;
- every recovery-task result;
- the resolved counts;
- the turns and tokens;
- the warning-type analysis.

**Exact, not statistical:** the offline replay of the edit tool. Its counts are what the rule does on the recorded
calls.

**Identical transcripts:** before any test, transcripts that are identical across seeds are found and collapsed (as
POC 2's p3-hidden-06 showed is possible).

## 9. Confounds, and what would invalidate the comparison

1. **A control that is not identical to PRISM_GW:** any change that reaches arm A, through shared code or a default,
   invalidates A against E. **Guard:** the byte-identity test (section 1), plus a gold replay through A and E.
2. **Historical controls:** comparing E with POC 2's runs, instead of an A run alongside it. The commit, seeds and the
   Kaggle image all differ. Not done.
3. **Environment drift:** Ollama version, model digest, quantisation, GPU type. All are pinned and recorded, as in
   POC 2, and checked at the start of each shard.
4. **Prompt differences between arms:** the turn-sentence correction (section 4) is applied to every arm or to none.
   No prompt differs by arm. C's text and E's formatting are tool output, not prompt.
5. **E leaking beyond formatting:** a change to discovery, order, labels, truncation or gateway decisions. **Guard:**
   a test that A's and E's outputs are equal once leading whitespace is removed.
6. **The edit fix, or the edit tool's message, differing by arm:** neither changes in any online arm.
7. **Shard and order effects:** every arm in every shard, in an interleaved order.
8. **Conditioning on triggers:** handled by the run-level primary metric (section 6).
9. **Multiple comparisons:** one test only. Everything else is descriptive and is labelled so.
10. **Task admission after seeing results:** p2-rename-04's admission checks (section 4) are run and recorded before
    the amendment is frozen.
11. **Model:** devstral-small-2 24B, as in POC 2, for every Phase 3b arm. The frontier-model evaluation belongs to
    Phase 4, with a current Claude model, and is never mixed into Phase 3b. Its exact model ID, date and settings
    are recorded there.

## 10. Order of work after approval

1. Run p2-rename-04's admission checks (no harness change).
2. Write the amendment: arms, tasks, seeds, the metric definitions, the single test, the rendering rule for E, the
   edit-fix rule, and the ledger fields.
3. Build:
   - E (and C if kept) behind arm switches;
   - the byte-identity and equal-apart-from-whitespace tests;
   - the replay harness.
4. Run the offline replay and report it.
5. Pin the cell. The user launches it.
