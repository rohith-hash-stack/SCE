# Phase 3b: evidence audit (2026-11-28)

An independent check of the 80-run Phase 3b experiment: its runs, its analysis, its transcripts and the claims in
`RESULTS.md`.

**Scope:**
- No experiment artifact was modified: no source, gateway, prompt, task, seed, score file or transcript.
- No arm was re-run, and the pre-registered test is unchanged.

**How:**
- **A new script:** `harness/experiments/r4/phase3b_audit.py`, which shares no code with the experiment's
  `phase3b_analysis.py`. It reimplements, from the amendment's text:
  - target places, edit application and line tracking;
  - triggers, recovery and run-level failure;
  - the Fisher test.
- **No answer key:** the audit never reads the answer key (`phase3/gold/*.patch`) or the hidden tests. Its target
  places are the task file's recorded `places` plus the definition's line, found in the source.
- **Outputs**, in `phase3b/verification/`:
  - `audit_ledger.csv` and `audit_ledger.json`: the 80-run ledger;
  - `audit_runs.json`: every run's per-place, per-edit and per-note detail.

**Labels used throughout:**
- **[PRE-REGISTERED]:** the amendment's primary analysis.
- **[DESCRIPTIVE]:** a comparison the amendment lists as descriptive.
- **[EXPLORATORY]:** computed for this audit; not in the amendment.
- **[POST-HOC]:** an interpretation from reading transcripts after the outcomes were known.
- **[UNVERIFIED]:** could not be checked from the evidence.

## 1. Executive summary

**The evidence holds up:**
- All 80 runs are present, correctly assigned and consistent.
- The experiment's analysis output agrees with the audit's independent re-derivation on every run's failure flag,
  every run's resolution and every place's outcome: 80 of 80 runs, 1,300 places.
- The primary p-value reproduces exactly.

**The primary result [PRE-REGISTERED]:**
- On p3-hidden-15, A (PRISM_GW) failed 14/15 runs and E (PRISM_GW_E) 9/15. Two-sided Fisher p = 0.0801.
- **Not significant at α = 0.05.** It neither shows that E reduces unrecovered failures nor that it has no effect.

**`RESULTS.md` is accurate in its numbers.** Three passages need correcting (section 3.3):
- **One is substantive:** the line-181 mechanism. The refused edits there carry a leading **tab** more often than four
  spaces in arms C and E.
- **Two are wording:** C against the baseline, and "no response" to C's notes.

**Most important findings:**
1. **One line decides the primary outcome.** Every failed run in every arm fails at `tests/conftest.py:181`, an
   unindented line, and A never recovered there within 5 turns.
2. **C's recoveries are traceable to its notes [DESCRIPTIVE].** All 43 of C's recoveries came after a note. In 42 of
   them, the recovering edit was the note's exact text, applied one or two turns later.
   - In C's 5 failed runs, the model ignored the same note on every repeat.
3. **PRISM's effect depends on the task [DESCRIPTIVE]:**
   - On p3-hidden-03, every PRISM arm resolved 5/5 and the baseline 2/5. The baseline's unresolved runs left 14–15
     of 17 places unedited.
   - On p3-hidden-15, the baseline resolved 12/15 with 0 run-level failures. PRISM arms resolved 2 (A), 11 (C) and
     6 (E).

## 2. Verification of all 80 runs

| Check | Result |
|---|---|
| Distinct run ids | **80**; no duplicates; none missing against the task file's 5 × 16 shard plan |
| Run assignment | Each run's file name, score file (`task`, `version`, `seed`) and shard folder agree with the plan; 0 discrepancies |
| Runs per arm | 20 each: 15 on p3-hidden-15 (seeds 301–315) and 5 on p3-hidden-03 (seeds 301–305) |
| Turn logs | 80, with 3,366 tool turns and 3 parse-error turns |
| Edit reconstruction | Every edit reported as applied re-applies to the rebuilt file at exactly the line the tool reported ("edited F at line N"): **0 mismatches** |
| Code and environment | 6 Kaggle sessions, every one at code `f77411d4`, with one config sha256, one set of model layers, Ollama 0.34.0 and 2 × T4 |
| Health | 0 infrastructure deaths; parse errors 0–0.4% per shard; no run exited with an error |

**Leakage check (could any scoring or analysis artifact have reached the model?)**

| Check | Result |
|---|---|
| Artifact names in outputs and replies | Scanned every tool answer (first 300 characters) and every model reply for `hidden_patch`, `gold_patch`, `fail_to_pass`, `pass_to_pass`, `phase3/gold`, `.score.json`, `scoring_check` and "answer key": **0 hits** |
| Paths outside the repository | Scanned every tool call's arguments for paths outside the task repository: 0. The 18 matches on `..` are reStructuredText `.. versionadded::` inside werkzeug docstrings. |
| The prompts | Turn 1's prompt hash is identical across A, C and E on each task, so the three arms were given the same prompt. It differs for BASELINE_LSP, which has its own tool list, as designed. |
| Order | The analysis output was committed at 02:45 UTC on 2026-11-28, after the last shard's final push (02:34 UTC). The cell runs no outcome analysis. |

- **Limit [UNVERIFIED in part]:** the turn logs keep only the first 300 characters of each tool answer. A leak
  located later in an answer, such as in a long `run_tests` output, cannot be ruled out from these logs.
  - Nothing in the harness's design passes the answer key to the agent, but that design was not re-audited here.

## 3. Verified task-level and arm-level results

### 3.1 Results

**Resolution** (from the score files' `score.resolved`) and **run-level failure** (the amendment's definition,
re-derived):

| Task | Arm | Runs | Resolved | Run-level failed | Resolved but failed | Unresolved, not failed | Distinct transcripts |
|---|---|---|---|---|---|---|---|
| p3-hidden-15 | A | 15 | 2 | 14 | 1 | 0 | 15 |
| p3-hidden-15 | C | 15 | 11 | 5 | 1 | 0 | 13 |
| p3-hidden-15 | E | 15 | 6 | 9 | 0 | 0 | 15 |
| p3-hidden-15 | BASELINE_LSP | 15 | 12 | 0 | 0 | 3 | 15 |
| p3-hidden-03 | A | 5 | 5 | 0 | 0 | 0 | 5 |
| p3-hidden-03 | C | 5 | 5 | 0 | 0 | 0 | 5 |
| p3-hidden-03 | E | 5 | 5 | 0 | 0 | 0 | 5 |
| p3-hidden-03 | BASELINE_LSP | 5 | 2 | 0 | 0 | 3 | 5 |

**Per arm** [DESCRIPTIVE], matching the reported table:

| Arm | Resolved | Run-level failed |
|---|---|---|
| A | 7/20 | 14/20 |
| C | 16/20 | 5/20 |
| E | 11/20 | 9/20 |
| BASELINE_LSP | 14/20 | 0/20 |

### 3.2 Run-level failure is not task resolution

The two outcomes differ, and the table shows both:
- **Resolved but failed** (2 runs): the trigger at line 181 was recovered, but after more than 5 turns.
  - A seed 311 recovered at turn 55, 37 turns after its trigger.
  - C seed 303 recovered at turn 24, 9 turns after.
- **Unresolved but not failed** (6 baseline runs): no edit was ever refused at a target place, so there was no trigger
  to fail. These runs failed to resolve for other reasons (section 7).
- **Consequence:** "0 failed runs" for BASELINE_LSP does not mean it resolved everything. The run-level failure
  outcome only measures recovery from mismatched edits.

### 3.3 The analysis outputs and `RESULTS.md` against the evidence

**Accurate (re-derived independently):**
- `reports/r4/phase3b_analysis.json`: every run's `failed`, `resolved` and per-place outcomes.
- In `RESULTS.md`:
  - every table figure: failed edits per run, loop refusals, whitespace mismatches per 100 edits, triggers and
    recoveries;
  - the line-181 table: triggers in A 14, C 12, E 10, baseline 0; recovered there in A 0, C 7, E 1.

**Discrepancies (recorded, not corrected in place):**

| # | Where | What it says | What the evidence shows | Impact |
|---|---|---|---|---|
| D1 | `RESULTS.md` §3 | The model sends `old` with four leading spaces | Four spaces is true of the **first** attempt. Across all line-181 attempts, the leading whitespace is:<br>A: 4 spaces 425, tab 64, 8 spaces 16, none 2;<br>C: tab 105, 4 spaces 67, none 11;<br>E: **tab 304**, 4 spaces 68, none 11;<br>baseline: none 21.<br>Runs often switch to a tab after reading the file (section 6). | **Substantive for the interpretation.** E's remaining failures involve a tab, plausibly copied from the `number<TAB>text` layout of `read_file` and the edit tool's own error, which E leaves unchanged by design. The conclusion that the failures lie outside E's scope still holds; the stated mechanism was incomplete. |
| D2 | `RESULTS.md` §2 | Of C's 211 notes, 162 "got no response" | This is the gateway's own `response` field. The audit found the model's next edit was **different text** after 163 notes, mostly its own refused call repeated, and exactly the note's text after 44. | **Wording:** "ignored" is more accurate than "no response". No figure changes. |
| D3 | `RESULTS.md` §2 | "On this task, the gateway arms do worse than the baseline" | True for A (2 against 12) and E (6 against 12). For C it is 11 against 12, one run. | **Wording:** overstated for C. |
| D4 | The first analysis invocation | – | A folder glob matched no runs. It was re-run with the `.jsonl` glob; `RESULTS.md` and `LAUNCH.md` already disclose and correct this. | None on the results. |
| D5 | The audit brief | The amendment is at `phase3b/AMENDMENTS.md` | It is at `harness/experiments/r4/AMENDMENTS.md`, section "Phase 3b …". The draft is `phase3b/AMENDMENT_DRAFT.md`. | None. |

**Use of the answer key:** the experiment's analysis takes its target places from the answer key's patch, which the
amendment allows after the runs. The audit's places come from the task file instead, and give identical results.
That supports the amendment's statement that the two definitions agree.

## 4. Primary statistical test verification [PRE-REGISTERED]

| Check | Result |
|---|---|
| The comparison | p3-hidden-15, A (PRISM_GW) against E (PRISM_GW_E), run-level failure. Matches amendment section 5. |
| n | 15 per arm |
| Failures | A 14, E 9: from the audit's independent per-run derivation, identical to the analysis output |
| Test | Two-sided Fisher exact |
| p | **0.08008**: the audit's own implementation, and `scipy.stats.fisher_exact` (two-sided) gives the same; the reported 0.080 is correct |
| Deduplication | Required by the amendment. It changes nothing: A and E each have 15 distinct transcripts. |
| Conclusion | Not significant at α = 0.05. As pre-registered, not evidence of no effect. |

No other confirmatory test exists in the amendment, and none is added here. Every other comparison in this report is
descriptive or exploratory.

## 5. Arm C recovery analysis [DESCRIPTIVE / EXPLORATORY]

All counts below are from C's 20 transcripts and its per-turn gateway records.

**Where the note's content comes from:** the turn logs cut each answer at 300 characters. So the note's content is
taken from the gateway's per-turn record (`fix_old`, `fix_new`), not from the displayed text.
- Pre-run test 3 verified that the note's text is appended to the tool's answer.

**Counts:**

| Measure | p3-hidden-15 | p3-hidden-03 |
|---|---|---|
| Edit calls refused | 211 (67 whitespace-only, 7 other not-found, 137 loop refusals) | 10 (all whitespace-only) |
| Refusals followed by a C note | **211 of 211** | 10 of 10 |
| Notes in all / new (not a repeat of an earlier note's text) | 211 / 51 | 10 / 10 |
| New notes: the next edit sent the note's exact text, and it applied | **43 of 51** | 10 of 10 |
| New notes: the next edit sent different text | 8 of 51 | 0 |
| Gap from a new note to the next edit | 1 turn: 47; 2 turns: 4 | 1 turn: 9; 2 turns: 1 |
| All notes: next edit exact and applied / different text / no later edit | 44 / 163 / 4 | 10 / 0 / 0 |
| Triggers (first mismatch at a target place) | 48 | 10 |
| Recovered within 5 turns | **43** | 10 |
| Recoveries with a C note shown between trigger and recovery | 43 of 43 | 10 of 10 |
| Recoveries whose recovering edit was a shown note's exact old and new text | **42 of 43** | 10 of 10 |
| The other recovery | a multi-line edit after a note | – |
| Turns from trigger to recovery | 1 turn in most cases (`RESULTS.md`) | 1–2 |

**The claim "42 of 43 recoveries used the note's exact text": supported.**
- It was established from the sequence of events in each transcript: the trigger, then the note, then the recovering
  edit with the note's exact text.
- It was not inferred from co-occurrence alone.
- **What is not shown:** that the model would not have recovered without the note. That is a counterfactual.
  - A's comparable recoveries happened too (34 of its 50 triggers, by the model's own edits), but none at line 181.

**When the note was ignored:**
- **The 8 new notes answered with different text:** in C's failed runs, at line 181. The model kept its own refused
  edit with four spaces, then a tab, while the note gave the unindented line.
  - For example, C seed 305: notes at turns 17, 19, 20, 21 and 22, each with
    `old=') -> SessionMiddleware[ClientSideSessionBackend]:'`. The model sent `'    ) -> …'` and then `'\t) -> …'`.
- **The 163 "different text" responses** among repeated notes are mostly those loops.

**Unintended edits:** none detected.
- 0 of C's 296 applied edits changed a line that lacks the old name.
- The score files' code review records no new undefined names or syntax errors in any C run.
- Every C correction is, by construction, a rename on the one line it names.

**Extra work:**

| | C | A |
|---|---|---|
| Notes per run on p3-hidden-15 (mostly repeats during loops) | 14.1 | – |
| Mean turns per run | 34.4 | 57.5 |
| Edit calls per run | 28.9 | 49.1 |

- C used fewer edit calls and fewer turns than A.
- The notes add text to answers, not tool calls.
- C's tokens per run were 321k, against A's 621k.

## 6. Arm A/E whitespace failure analysis (`tests/conftest.py:181`)

### 6.1 The verified facts

**The source line** (litestar at the task commit, `cat -A`): `) -> SessionMiddleware[ClientSideSessionBackend]:$`.
- It has **no leading whitespace**.
- It closes a multi-line signature whose previous line is indented by four spaces.

**Attempts at line 181 on p3-hidden-15** (an edit whose `old` contains the line's text):

| | A | C | E | BASELINE_LSP |
|---|---|---|---|---|
| Runs that tried the line | 15 | 15 | 15 | 12 |
| Attempts | 507 | 183 | 383 | 21 |
| Refused, whitespace-only | 52 | 28 | 35 | 0 |
| Refused, not found for another reason | 1 | 7 | 5 | 9 |
| Refused by the loop breaker | 452 | 137 | 337 | 0 |
| Applied | 2 | 11 | 6 | 12 |
| Leading whitespace on `old`: 4 spaces / tab / 8 spaces / none | 425 / 64 / 16 / 2 | 67 / 105 / 0 / 11 | 68 / 304 / 0 / 11 | 0 / 0 / 0 / 21 |
| Runs with a trigger here | 14 | 12 | 10 | 0 |
| Recovered here within 5 turns | 0 | 7 | 1 | – |

**The sequence in failing runs** (A seed 301, E seeds 303 and 309, C seed 305, inspected turn by turn):
1. The model edits the indented lines 174–175.
2. Its first line-181 attempt carries four spaces.
3. The tool refuses it. Its error says the text matches apart from whitespace, at line 181, and shows the line as
   `181<TAB>) -> SessionMiddleware[…]:`.
4. The model reads the file (`read_file`, which uses the same `number<TAB>text` layout) and retries. Some runs keep
   four spaces; others switch to a leading tab.
5. The loop breaker refuses each repeat from the third identical call on, telling the model the call was already
   answered.
6. The model repeats until the 60-turn cap. A ends at the turn cap in 13 of 15 runs, E in 9.

**Was the error feedback useful?**
- It was accurate: whitespace-only, the right line, the exact text.
- **It is the same in every arm.** The edit tool's message is unchanged by C and E.
- Whether it was "useful" can't be measured apart from the model's response. Within 5 turns, it never led to recovery
  at line 181 in A, and once in E.

### 6.2 Arm E

| Check | Result |
|---|---|
| Indentation kept in PRISM outputs | **Verified for the gateway's quoted source lines:** E's quoted lines show leading whitespace (p3-hidden-03: 75 of 75; p3-hidden-15: 24 of 124, where many quoted lines are module-level). A and C show it on 0 lines. |
| Indentation in `prism_find_references` rows | **[UNVERIFIED] from the Kaggle transcripts:** the 300 characters kept show only each answer's first row, a module-level import with no indentation. Verified offline before the launch: pre-run test 2, 338 answers. |
| Fewer mismatch triggers | Verified. p3-hidden-15: 50 in A against 12 in E. p3-hidden-03: 3 against 0. |
| Fewer whitespace mismatches | Verified, per 100 edits on p3-hidden-15: 12.4 against 6.4. |
| On both tasks | Yes, in the same direction [DESCRIPTIVE]. On p3-hidden-03 every arm except the baseline resolved 5/5, so there is no room to differ. |

**Were E's remaining failures outside its design scope? [POST-HOC]**
- **Yes, as far as the evidence shows:**
  - line 181 has no indentation, so A already showed it correctly, and E has nothing to restore there;
  - E's refused attempts carry either four spaces (copied from neighbouring lines) or a tab (matching the numbered
    layout of `read_file` and the edit tool's error);
  - E changes neither of those formats.
- **This is an interpretation from reading transcripts, not a pre-registered finding.** The tab hypothesis in
  particular is untested.

## 7. PRISM against BASELINE_LSP [DESCRIPTIVE / EXPLORATORY]

A, C and E are different arms and are compared separately. "PRISM" below means a specific arm.

### 7.1 p3-hidden-15

| | A | C | E | BASELINE_LSP |
|---|---|---|---|---|
| Resolved | 2/15 | 11/15 | 6/15 | **12/15** |
| Run-level failed | 14 | 5 | 9 | **0** |
| Target places done at end (of 16): mean | 12.9 | 14.9 | 14.0 | **15.4** |
| Edits applied / refused (all kinds) | 191 / 545 | 222 / 211 | 198 / 379 | 301 / 186 |
| Of which loop refusals | 452 | 137 | 337 | 86 |
| Reference search per run | 1.0 `prism_find_references` + gateway worklist | 1.0 + worklist | 1.0 + worklist | 9.9 `grep`, 0.5 `find_references` |
| Gateway interventions per run | 31.8 stall, 12.7 worklist, 0.6 done-block | 14.8 worklist, 14.1 note, 9.7 stall | 23.1 stall, 13.2 worklist | – |

**Why the baseline's 3 unresolved runs failed to resolve:**
- seeds 306 and 311 never attempted line 181 (the place was missed);
- seed 314 left 7 places, and introduced a syntax error and an undefined name.

### 7.2 p3-hidden-03

| | A | C | E | BASELINE_LSP |
|---|---|---|---|---|
| Resolved | **5/5** | **5/5** | **5/5** | 2/5 |
| Target places done at end (of 17): mean | **17.0** | **17.0** | **17.0** | 8.2 |
| Reference search per run | 1 `prism_find_references` + worklist | same | same | 18.2 `grep` |
| Mean turns | 22.2 | 21.2 | 21.0 | 48.6 |

**Why the baseline's 3 unresolved runs failed to resolve:** each hit the turn cap with 14–15 of the 17 places
unedited, and one introduced a syntax error.
- **On the cause [POST-HOC]:** `from_header` has many look-alike methods in werkzeug. The gateway counted 96 per run
  and blocked none. So a plausible cause is that grep-based discovery struggled. That is a hypothesis.

### 7.3 Combined [DESCRIPTIVE]

These are pooled over two different tasks, for orientation only. They are not a test, and not a single PRISM
configuration.

| Arm | Resolved | Run-level failed | Mean turns | Mean tokens |
|---|---|---|---|---|
| A | 7/20 | 14/20 | 48.7 | 506k |
| C | 16/20 | 5/20 | 31.1 | 279k |
| E | 11/20 | 9/20 | 38.7 | 386k |
| BASELINE_LSP | 14/20 | 0/20 | 50.0 | 265k |

### 7.4 Gateway blocks and false positives

- **Edits:** the gateway never blocked an edit (`prevented_edits` 0 in all 60 PRISM runs). On p3-hidden-03 it
  listed look-alikes without blocking them.
- **`done`:** it blocked `done` 16 times (A 9, C 3, E 4). At every one, the audit's reconstruction shows target places
  still pending. **No false-positive block was found.**

### 7.5 The questions

| Question | Answer [DESCRIPTIVE] |
|---|---|
| Did the original gateway (A) outperform the baseline? | On p3-hidden-03, yes: 5/5 against 2/5. On p3-hidden-15, no: 2/15 against 12/15. |
| Did exact replacement guidance (C) outperform the baseline? | p3-hidden-03: 5/5 against 2/5. p3-hidden-15: 11/15 against 12/15, about equal. Combined: 16/20 against 14/20. Not tested; no claim of superiority. |
| Did indentation preservation (E) outperform the baseline? | p3-hidden-03: yes, 5/5 against 2/5. p3-hidden-15: no, 6/15 against 12/15. |
| Which task gave the strongest PRISM benefit? | p3-hidden-03: all three PRISM arms edited all 17 places in every run, in fewer than half the baseline's turns. |
| Which task is the counterexample? | p3-hidden-15: the baseline resolved most runs, and A and E lost runs to an edit loop at one line. |
| Does the benefit depend on the task? | **The evidence is consistent with that, but cannot show it.** Two tasks, and differences of this size, cannot separate task characteristics from chance. |

**No causal claim is made.** Different tasks also differ in repository, size, look-alikes and line shapes.

## 8. Efficiency analysis [DESCRIPTIVE]

| Task | Arm | Mean turns (median) | Mean tokens (median) | Mean minutes | Resolved runs: mean turns / tokens |
|---|---|---|---|---|---|
| p3-hidden-15 | A | 57.5 (60) | 621k (653k) | 10.4 | 41.0 / 391k |
| p3-hidden-15 | C | 34.4 (25) | 321k (174k) | 6.8 | 25.1 / 175k |
| p3-hidden-15 | E | 44.6 (60) | 463k (670k) | 8.9 | 21.5 / 140k |
| p3-hidden-15 | BASELINE_LSP | 50.5 (48) | 242k (220k) | 6.3 | 48.1 / 225k |
| p3-hidden-03 | A | 22.2 (23) | 158k (170k) | 3.0 | 22.2 / 158k |
| p3-hidden-03 | C | 21.2 (23) | 152k (167k) | 3.1 | 21.2 / 152k |
| p3-hidden-03 | E | 21.0 (21) | 156k (159k) | 3.1 | 21.0 / 156k |
| p3-hidden-03 | BASELINE_LSP | 48.6 (60) | 330k (378k) | 11.4 | 31.5 / 165k |

- **Where PRISM's arms resolve:** they take fewer turns than the baseline on both tasks. On p3-hidden-15, a resolved
  C run takes 25 turns against the baseline's 48.
- **Tokens per turn are higher in PRISM's arms:** their answers carry gateway text. A resolved C run on p3-hidden-15
  uses 175k tokens against the baseline's 225k.
- **The loop dominates the unresolved PRISM runs on p3-hidden-15.** In A, 452 of 545 refused edits were loop
  refusals, and the mean is 621k tokens per run, 2.6× the baseline's.
  - Whether the gateway's repeated stall messages (31.8 per run in A) prolonged the loop cannot be told from this
    design [POST-HOC].

## 9. Threats to validity

| Threat | Detail |
|---|---|
| Small samples | 15 runs per arm on the primary task and 5 on the recovery task. The primary test's power is low: 0.30–0.85 over the amendment's simulated effects, and 0.45 at the observed rates (14/15 against 9/15; exact enumeration [EXPLORATORY]). |
| Two tasks only | Every task-dependence statement rests on one primary and one recovery task. |
| One place decides the primary outcome | Every run-level failure on p3-hidden-15, in every arm, is at line 181. The primary result says more about that line than about whitespace errors in general. |
| The model's files | Phase 3b pinned the registry's files on 2026-11-26. POC 2 recorded none, so whether the two ran the same bytes is unknown. A's failure rate on p3-hidden-15 (14/15) is far above POC 2's (2/3), which may reflect this, the seeds, or chance. |
| Seed variability | Two pairs of C runs produced identical transcripts despite different seeds (seeds 302/313 and 311/314; all four resolved). Deduplicated, C is 9 resolved and 5 failed of 13 distinct runs. A and E have none, so the primary test is unaffected. |
| Transcript truncation | The turn logs keep 300 characters per answer, so some checks rely on the gateway's records or on the pre-run tests (sections 2 and 6.2). |
| Post-hoc interpretation | The line-181 mechanism, the tab hypothesis and the baseline's discovery difficulty on p3-hidden-03 all come from reading transcripts after the outcomes were known. |
| Different tool use | The baseline searches with grep (10–18 calls per run); PRISM's arms make one reference call and follow the gateway's worklist. The arms differ in more than one way, so differences between them are not attributable to one feature. |
| Environment | No inconsistency found across the 6 sessions. Jedi's Kaggle behaviour differed from this container's before the launch (pre-run test 6); that affects only BASELINE_LSP's `find_references`, used 0.5 times per run. |
| Analysis mistakes | The first analysis call used a wrong glob and matched nothing. It was re-run correctly; the results never depended on it. The audit found no further analysis error. |
| Run-level failure ≠ resolution | The primary outcome measures recovery from mismatched edits. An arm can have 0 failed runs and still fail to resolve tasks, as BASELINE_LSP does in 6 runs. |

## 10. Supported conclusions

1. **The primary test:** on p3-hidden-15, E failed fewer runs than A (9 against 14 of 15). The difference is not
   significant (two-sided Fisher p = 0.080) [PRE-REGISTERED].
2. **E's effect on mismatches:** E reduced mismatch triggers and whitespace mismatches on both tasks, and showed
   indented quoted lines as designed [DESCRIPTIVE].
3. **C's effect:**
   - C's recoveries followed its notes, with the exact text, in 42 of 43 cases [DESCRIPTIVE].
   - C failed 5 runs against A's 14 [DESCRIPTIVE; Fisher p = 0.0017, labelled descriptive by the amendment].
4. **The failures:** every run-level failure is at one unindented line, `tests/conftest.py:181`.
5. **The gateway's blocks:** no false-positive block, and no edit blocked.
6. **PRISM against the baseline:** PRISM's arms resolved all p3-hidden-03 runs, against the baseline's 2 of 5. On
   p3-hidden-15, the baseline resolved more runs than A or E, and about as many as C [DESCRIPTIVE].

## 11. Unsupported claims to avoid

- "Indentation preservation (E) works" or "has no effect": the primary test is inconclusive.
- "C is significantly better than A" or "better than the baseline": every C comparison is descriptive.
- "PRISM outperforms the baseline": true on one task and false on the other, with different arms behaving
  differently.
- "The notes caused C's recoveries": the sequence is shown; the counterfactual is not.
- "The failures are caused by the numbered `N<TAB>text` format": a post-hoc hypothesis, untested.
- "PRISM's benefit depends on task characteristics": consistent with the data, not shown by it.
- "BASELINE_LSP never fails": it has 0 run-level failures but 6 unresolved runs.
- Pooled figures across tasks, presented as one configuration's performance.

## 12. Recommended next research questions

These are questions only; nothing is designed or run.

1. **Does the numbered `N<TAB>text` listing format** (`read_file`, the edit tool's error) induce leading-tab errors,
   separately from PRISM's formatting? Testable offline on these transcripts and in a pre-registered arm.
2. **How general is line 181?** How often do target lines lack indentation while their neighbours have it, and does
   the edit loop recur there on other tasks?
3. **What does C's exact text do across a wider task set?** C's notes gave every recovery its text here. Its effect
   needs a pre-registered test, beyond two tasks.
4. **Why does the baseline under-discover places on p3-hidden-03,** and does PRISM's advantage track look-alike
   density?
5. **Do the gateway's repeated stall messages during a loop help or prolong it?**
6. **Which model files did POC 2 run?** POC 2's high-versus-low A failure rate (2/3 against 14/15) can't be explained
   without them.
