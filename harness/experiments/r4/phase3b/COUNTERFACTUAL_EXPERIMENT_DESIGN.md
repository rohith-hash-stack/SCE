# Phase 3b follow-up: a controlled counterfactual investigation of the line-181 whitespace failure (design, 2026-11-28)

**Status:** a research design only.
- Nothing here is implemented or run.
- It is not a pre-registration: a frozen amendment would follow approval of this design and of the pilot's results.
- No source code, prompt, tool, task, configuration, score file, transcript, ledger or result was changed.

**Labels on every statement:**
- **[EVIDENCE]:** established in `RESULTS.md`, `EVIDENCE_AUDIT.md`, `WHITESPACE_FAILURE_INVESTIGATION.md` or their
  verification files.
- **[HYPOTHESIS]:** an explanation the design is meant to test.
- **[DECISION]:** a proposed experimental decision, pending approval.
- **[UNRESOLVED]:** an open question.

## 1. Research objective and questions

**Objective [DECISION]:** separate four candidate influences on the repeated refused edit at `tests/conftest.py:181`
(p3-hidden-15):
1. the numbered source-line layout;
2. the edit tool's refusal message;
3. their interaction;
4. the gateway's isolated-line place presentation.
The study does this by changing one displayed text at a time from an identical starting state.

**The research questions:**

| RQ | Question | Factor(s) |
|---|---|---|
| RQ1 | Does showing source lines as `N<TAB>text` raise the probability that the model submits the separator as part of `old`? | A |
| RQ2 | Does the refusal message's content affect whether the model rebuilds the exact line or repeats its refused edit? | B |
| RQ3 | Is failure under the current layout with the current message greater than either factor alone predicts? | A × B |
| RQ4 | Does quoting the target line alone favour one-line edits that fail more often than block edits? | C |
| RQ5 | Which feedback conditions help the model recover within the 5-turn window? | A, B (and C, descriptively) |

## 2. Evidence baseline

| # | Finding | Label |
|---|---|---|
| E1 | Primary Phase 3b test: A failed 14/15 runs, E 9/15; two-sided Fisher p = 0.0801, not significant. | [EVIDENCE] |
| E2 | Line 181 is `) -> SessionMiddleware[ClientSideSessionBackend]:`, with no leading whitespace. It sits between two lines indented by 4 spaces, and the file has no tab. | [EVIDENCE] |
| E3 | 1,093 line-181 attempts. 976 of the 1,073 A/C/E attempts repeat an earlier call exactly. | [EVIDENCE] |
| E4 | First attempts carried 4 spaces in 40 of the 45 A/C/E runs. Before them, line 181 had been shown numbered in only 2 runs. | [EVIDENCE] |
| E5 | All 15 attempts that first introduced a tab came after line 181 had been shown as `181<TAB>text`: 9 right after an edit error, 4 right after `read_file`/`prism_read`, 2 after a loop refusal. | [EVIDENCE] |
| E6 | The refusal shows the file's line numbered. It doesn't repeat the submitted text or say what differs. The loop breaker's later answers repeat nothing. | [EVIDENCE] |
| E7 | In all 80 runs, no one-line edit of line 181 was accepted except C's 10, each after its note gave the exact text. Every other accepted edit was multi-line, anchored on line 179 or 180. | [EVIDENCE] |
| E8 | C recovered within 5 turns in 7 of 12 runs with a trigger there; A in 0 of 14 (95% CI for A: 0–0.23, Clopper–Pearson). | [EVIDENCE] |
| E9 | The baseline edited line 181 in multi-line blocks in 18 of 20 attempts, and never had a whitespace mismatch there. | [EVIDENCE] |
| E10 | The harness samples at temperature 0.2. Two pairs of C runs gave identical transcripts despite different seeds. | [EVIDENCE] |
| E11 | Whether `read_file`, the edit error, or both produce the tab; why the PRISM arms isolate the line; why refused calls are repeated: not established. | [UNRESOLVED] |

## 3. Hypotheses and competing explanations

| Code | Explanation | Label | What it predicts in this design |
|---|---|---|---|
| H1 | The model copies the numbered layout's separator into `old`. | [HYPOTHESIS] | A2 lowers separator-derived leading characters (tab, or A2's marker), relative to A1. |
| H2 | The model misreads the refusal; it does not grasp that only leading whitespace differs. | [HYPOTHESIS] | B2 raises recovery and lowers exact repeats, relative to B1. |
| H3 | The model does not separate displayed formatting from source content in general. | [HYPOTHESIS] | Under A2 the model copies A2's marker into `old`: a non-whitespace, display-derived error. |
| H4 | The isolated-line place listing favours narrow one-line edits. | [HYPOTHESIS] | C2 raises the share of block-anchored first attempts, and lowers first-attempt mismatches. |
| H5 | Retrying is independent of presentation and feedback: a generic loop. | [HYPOTHESIS] | No factor changes the exact-repeat rate or recovery. All effects are null with narrow intervals. |
| H6 | The tab distribution is partly chance; for example E's 304 of 383 against A's 64 of 507. | [HYPOTHESIS] | Replicates from one state show variable tab incidence within a cell; between-cell differences don't exceed it. |
| H7 | The four leading spaces come from the neighbouring lines or the model's previous edit, not from any display. | [HYPOTHESIS] | First-attempt 4-space submissions persist under A2 and B2 alike. |

## 4. Factorial design and condition matrix

### 4.1 Why not one 2×2×2 full-run factorial

**[DECISION]** A single 2×2×2 factorial of full runs is not the efficient first step.

| Reason | Detail | Label |
|---|---|---|
| The factors act at different moments | C acts before the first line-181 attempt, where the gateway lists the place. A and B act from the first refusal on. A full run mixes both stages with everything before them, which differs from run to run. | [EVIDENCE] (timings, E4–E5) |
| The cost of full runs | A main-effect comparison at the effect sizes of interest needs on the order of 30 or more runs per factor level (section 8). Interactions need about 4 times that. At about 10 minutes a run, a confirmatory 2×2×2 would need several hundred runs. | [HYPOTHESIS] (assumption-dependent; section 8) |
| Path dependence | Whether a run reaches line 181 in a given state depends on earlier choices, so the factors' effects are diluted by unrelated variation. | [EVIDENCE] (A's first attempts at turns 15–19) |

**[DECISION]** Instead, a staged design:
- **Stage 0:** offline checks; no model.
- **Stage 1:** branching counterfactuals from identical recorded states, as a pilot. It has two parts: 1a, the A × B
  factorial after the first refusal; and 1b, factor C before the first attempt.
- **Stage 2:** a confirmatory full-run experiment, only for the factors Stage 1 supports, frozen in a new amendment.

**The branching method [DECISION]:**
- **The prefix:** the recorded tool calls of a Phase 3b arm-A transcript are replayed, unchanged, through the real
  runner up to a branch point, with the condition's display rules in force. This is Phase 3b's `ReplayModel` replay:
  the calls are fixed, and the answers are regenerated by the tools.
- **The continuation:** from the branch point, the real model continues with the real tools for a fixed window.
- **Why this works:** every condition then starts from the same file state and the same model calls. Only the
  manipulated displayed text differs.

### 4.2 The factors, defined exactly

**Factor A: the source-line layout.** It applies everywhere the harness shows numbered source lines:
- `read_file`;
- `prism_read`;
- the edit tool's whitespace-refusal listing;
- its "closest lines" listing.

| Level | What the model sees for lines 180–182 | Label |
|---|---|---|
| **A1** (current) | `180<TAB>    cookie_session_backend: …`<br>`181<TAB>) -> SessionMiddleware[ClientSideSessionBackend]:`<br>`182<TAB>    return SessionMiddleware(…)` | [EVIDENCE] (`shared_tools.py`, `tools.py`) |
| **A2** (alternative) | `180│    cookie_session_backend: …`<br>`181│) -> SessionMiddleware[ClientSideSessionBackend]:`<br>`182│    return SessionMiddleware(…)`<br>The separator is U+2502 (│), with no whitespace character between the number and the text. | [DECISION] |

**Why A2 is unambiguous [DECISION]:**
- the separator is not whitespace, so no whitespace character stands between the number and the source text;
- every character after `│` is the source line, exactly;
- the explanatory phrases change with the layout: "each is its number, a tab, then the text" becomes "each is its
  number, a │, then the text". Nothing else in any message changes.

**Rejected alternatives [DECISION]:**
- `181: text` and `181 | text`: the space after the separator could itself be copied as a leading space.
- An unnumbered block: it also removes the per-line numbers that other behaviour may rely on, so it changes more than
  one thing.

**A2's own risk [HYPOTHESIS]:** the model may copy `│` into `old` (H3). That is measured, not assumed away.

**Factor B: the whitespace-refusal message.** It applies only to the edit tool's "matches apart from whitespace"
refusal.

| Level | Text | Label |
|---|---|---|
| **B1** (current) | `error: the text to replace was not found in F: it matches once apart from whitespace (indentation), at line N. These are the file's lines there (each is its number, <sep>, then the text; copy the text exactly, with its spaces):` + the numbered lines | [EVIDENCE] |
| **B2** (diagnostic) | The same text, with one sentence inserted before the listing: `Your old text's first line starts with <k spaces / k tabs / no whitespace>; line N of the file starts with <…>.` | [DECISION] |

**The limits on B2 [DECISION]:**
- **Its content:** it states only the leading-whitespace counts of the first differing line, the submitted and the
  file's. For a multi-line `old`, that is the first line whose stripped text matches but whose leading whitespace
  differs.
- **What it never does:** propose replacement text, change the file, or mention any other place.
- **Everything else stays:** the loop breaker and every other message are unchanged in B1 and B2. So B acts only on
  non-repeated refused calls.

**Factor C: the place presentation in the gateway's listing.** It applies to the gateway's pending-places listing,
and only in PRISM_GW.

| Level | What the model sees for line 181 | Label |
|---|---|---|
| **C1** (current) | `  tests/conftest.py:181: ) -> SessionMiddleware[ClientSideSessionBackend]:` (the line alone, stripped) | [EVIDENCE] (`gateway.py`, `_listing`) |
| **C2** (block) | The same row, followed by the place's complete enclosing statement, when that statement spans 2–5 lines: a fenced, unnumbered code block of lines 179–181, exactly as in the file:<br>`def cookie_session_middleware(`<br>`    cookie_session_backend: ClientSideSessionBackend, mock_asgi_app: ASGIApp`<br>`) -> SessionMiddleware[ClientSideSessionBackend]:` | [DECISION] |

- **The statement:** the smallest enclosing statement, from Python's AST, here the function signature `def …:`. Places
  whose statement is a single line are listed exactly as in C1.
- **A confound C2 cannot avoid [HYPOTHESIS]:** C2 necessarily shows the neighbouring lines' real indentation, and the
  exact text of a block that could be used as `old`. So it tests block presentation as a whole. It cannot separate
  "context shown" from "copyable block offered".

**Held off in every condition [DECISION]:**
- C's exact-fix note (`exact_fix`): it supplies the corrected text and would dominate B;
- E's `keep_indent`: it would change listing text beyond factor C.

### 4.3 The condition matrix

**Stage 1a** (the A × B factorial; branch point **BP2**: right after the first whitespace-only refusal at line 181,
before the model's next reply):

| Condition | Numbered layout (A) | Whitespace refusal (B) | Place listing (C) | Exact-fix note | keep_indent | Everything else |
|---|---|---|---|---|---|---|
| **1a-11** (= Phase 3b arm A) | A1 `N<TAB>` | B1 current | C1 | off | off | constant |
| **1a-21** | **A2 `N│`** | B1 current | C1 | off | off | constant |
| **1a-12** | A1 `N<TAB>` | **B2 diagnostic** | C1 | off | off | constant |
| **1a-22** | **A2 `N│`** | **B2 diagnostic** | C1 | off | off | constant |

- **The prefix:** under A2, every numbered listing in the replayed prefix is regenerated in A2. Under B2, the refusal
  at the branch point carries the diagnostic.

**Stage 1b** (factor C; branch point **BP1**: right before the model's reply that, in the recorded run, was its first
line-181 attempt):

| Condition | Numbered layout (A) | Whitespace refusal (B) | Place listing (C) | Exact-fix note | keep_indent | Everything else |
|---|---|---|---|---|---|---|
| **1b-1** (= Phase 3b arm A) | A1 | B1 | C1 | off | off | constant |
| **1b-2** | A1 | B1 | **C2 block** | off | off | constant |

- **The prefix:** under C2, every gateway listing in the replayed prefix is regenerated in C2.

**Constant in every condition [DECISION]:**
- the task and its prompt;
- the code commit, apart from the switch for the manipulated display;
- the model files (pinned digests), Ollama 0.34.0, 2 × T4;
- temperature 0.2, context 20,480, answer tokens 4,096;
- the tool set and edit semantics, the loop breaker, compaction and feedback;
- the continuation window.

**Why the factors aren't fully crossed in Stage 1 [DECISION]:**
- C is not crossed with A × B in Stage 1, because the factors act at different branch points.
- A × B × C is deferred to Stage 2, and only if 1a and 1b both show effects.

### 4.4 Stage 2 (confirmatory; only if Stage 1 supports it)

**[DECISION]** Stage 2 is a full-run factorial, from the task's start, of the factors Stage 1 found to matter:
- a 2×2 if only A and B matter;
- a 2×2×2 if C also matters and the budget allows.
- It has its own frozen amendment, primary outcome and sample size (section 8).

**[UNRESOLVED]** Generalising beyond line 181 needs more tasks with an unindented continuation line between indented
lines. Mining them needs separate approval.

## 5. Controls and randomization

**Experimental manipulations [DECISION]:** only A, B and C, each implemented as a display rule behind a switch that
defaults to the current behaviour.

**Controlled variables [DECISION]:**
- **For Stage 1:** the prefix's calls and file state are identical across conditions; the same pinned environment
  record is checked per session, as in Phase 3b; the same turn cap, retry rules and loop breaker apply.
- **Prefixes:** only arm-A (PRISM_GW) transcripts, so that no C or E behaviour is in a prefix.
  - Stage 1a: the 14 runs with a trigger at line 181.
  - Stage 1b: all 15 runs.

**Outcome variables:** section 6.

**Potential confounders, and their control:**

| Confounder | Control | Label |
|---|---|---|
| Prefix calls were generated under A1 and C1 (a history consistency gap in A2 and C2) | Recorded and reported. The arm-A prefixes contain few numbered views of line 181 before BP2 (E4), which limits the gap. | [HYPOTHESIS] (size unknown) |
| Environment-dependent prefix answers (`run_tests`) | Stage 0 checks that the prefixes before BP1 and BP2 contain no `run_tests`; if any do, they are listed and the prefix is excluded or kept by a rule frozen beforehand. | [DECISION] |
| Seed and condition order | Each continuation gets its own seed, derived from (prefix, replicate) and identical across conditions, so conditions are paired. The order of continuations is randomized within each session. | [DECISION] |
| Low temperature | Identical continuations are expected. They are collapsed per (prefix, condition) for the primary analysis, as in Phase 3b, with n reported before and after. | [DECISION] |
| Gateway carry-over into the baseline | BASELINE_LSP is not in Stage 1; none of A, B or C is defined for it. If Stage 2 adds a baseline reference to test A and B outside the gateway, it gets only the A and B levels. It never gets the gateway, its listings or its notes. | [DECISION] |
| Answer-key leakage | Nothing in A, B or C reads the answer key or the hidden tests. B2 reports only the submitted and file whitespace counts; C2 shows only the start file's own statement. | [DECISION] |

**Blinding [DECISION]:**
- Outcomes are scored by a frozen script from the turn logs, with conditions coded.
- Any human reading of transcripts is done on copies with the condition code removed.
- [UNRESOLVED] Full blinding is impossible: A2's `│` and B2's sentence reveal the condition.

## 6. Metrics and operational definitions

**Units [DECISION]:**
- **Stage 1:** the unit is a **continuation**, nested in its **prefix** (the recorded run).
- **Stage 2:** the unit is a **run**.
- **Never a unit:** an attempt. Attempts within a continuation or a run are never treated as independent.

| # | Metric | Definition | Stage | Label |
|---|---|---|---|---|
| M1 | First-attempt whitespace mismatch | The continuation's first line-181 attempt is refused as whitespace-only (BP1); or, from BP2, the first new attempt is. | 1a, 1b, 2 | [DECISION] |
| M2 | Leading-whitespace distribution | The first line-181 attempt's leading characters, classed none / spaces (k) / tabs (k) / mixed / **display-derived** (A2's `│`, a digit-and-separator gutter). Reported per unit as "any", and as the share of attempts. | all | [DECISION] |
| M3 | Exact-repeat rate | The share of a unit's line-181 attempts identical to an earlier call in that unit. | all | [DECISION] |
| M4 | Edit refusal rate | The share of a unit's `edit_file` calls refused: whitespace-only, other not-found, or by the loop breaker, reported apart. | all | [DECISION] |
| M5 | **Recovery within 5 turns** | Line 181 holds the new name with the old-name count allowed, within the 5 model turns after the trigger (1a: after BP2), by the Phase 3b analysis's definition. | 1a primary; 2 | [DECISION] |
| M6 | Retries before recovery | Line-181 attempts between the trigger and the recovering edit; censored at the window's end. | 1a, 2 | [DECISION] |
| M7 | Target-file modification | Line 181 is correctly changed by the window's end (Stage 1) or the run's end (Stage 2). The form is recorded: one-line or block-anchored. | all | [DECISION] |
| M8 | Run-level failure | Phase 3b's definition: any target place's trigger not recovered within 5 turns. | 2 primary | [DECISION] |
| M9 | Task resolution | `score.resolved`, kept distinct from M8. | 2 | [DECISION] |
| M10 | Tokens and turns | Per unit, as Phase 3b recorded them. | all | [DECISION] |
| M11 | Edit form | The first line-181 attempt is one-line or block-anchored. | 1b primary | [DECISION] |

**The continuation window [DECISION]:** 10 model turns from the branch point, so a 5-turn recovery is fully
observable. The turn cap of 60 also applies.

**Primary outcomes, fixed before any run [DECISION]:**
- **Stage 1a:** M5, recovery within 5 turns after BP2.
- **Stage 1b:** M1, a first-attempt whitespace mismatch at line 181.
- **Stage 2:** M8, run-level failure.
  - p3-hidden-15 alone, unless more tasks are approved.

## 7. Statistical analysis plan

**Stage 1a, the primary analysis [DECISION]:**
- **The model:** a mixed-effects logistic regression,
  `recovery ~ A + B + A:B + (1 | prefix)`, with effect coding (−½/+½).
  - If the model is singular, or recovery is near 0 or 1, the fallback is a conditional (prefix-stratified) exact
    logistic regression, or Firth's penalized logistic regression with a prefix-clustered bootstrap.
- **The primary contrasts:** the main effects of A and B, with Holm correction over the two.
- **Secondary:** the interaction A × B (RQ3).
- **Effect sizes:**
  - marginal risk differences (A2 − A1 and B2 − B1, each averaged over the other factor), and odds ratios;
  - 95% confidence intervals from a cluster bootstrap over prefixes, 10,000 resamples.
  - p-values are reported with them, never alone.
- **Interpretation:** effects are interpreted against the smallest effect of interest (section 8), using the
  intervals. A non-significant effect whose interval excludes the smallest effect of interest is called "no
  practically meaningful effect". Otherwise it is called "inconclusive".

**Stage 1b [DECISION]:**
- M1 by C (paired by prefix and seed): conditional logistic regression, or McNemar's test on (prefix, seed) pairs;
  risk difference with a bootstrap CI.
- M11 the same way.

**Secondary outcomes, both stages [DECISION]:**
- M2: the display-derived share by A, for RQ1 and H3. M3, M4 and M6 by condition.
- They are reported with intervals, labelled exploratory, with no multiplicity claims.

**Stage 2 [DECISION]:**
- `failure ~ factors + interactions`, logistic regression with effect coding, at the run level.
- Main effects as standardized marginal risk differences with 95% CIs.
- Interactions tested only where Stage 1 pre-specifies a direction and the power supports it; otherwise reported
  descriptively.

**Never [DECISION]:**
- pooling attempts as independent observations;
- treating identical continuations or runs as separate observations in the primary analysis;
- changing a primary outcome after any data are seen.

## 8. Sample-size and power strategy

**What Phase 3b can and can't supply:**
- **[EVIDENCE]** Phase 3b's estimates are unstable. A's recovery at line 181 is 0/14 (CI 0–0.23), E's 1/10, C's 7/12
  with the exact text. The primary A-against-E comparison was not significant at n = 15, and power at the observed
  rates was about 0.45.
- **[DECISION]** No sample size is fixed here. Stage 1 starts with a pilot, whose results feed a simulation that sets
  the confirmatory sizes.

**The assumptions a power calculation needs** [UNRESOLVED]; each pilot estimate is in brackets:

| Assumption | Needed for | Phase 3b information | Pilot estimate |
|---|---|---|---|
| p₀: recovery under A1B1 after BP2 | 1a | 0/14 (95% CI 0–0.23) | yes |
| The smallest effect of interest (SESI) | 1a, 2 | none | – (to be set by the user, see below) |
| Between-prefix variation (σᵤ, logit scale) or the ICC | 1a, 1b | none | yes (only roughly from 14 prefixes) |
| The duplicate-continuation rate at temperature 0.2 | 1a, 1b | 2 of 15 C runs were duplicates | yes |
| The A × B interaction size | RQ3 | none | yes |
| First-attempt mismatch rate under C1 | 1b | about 40 of 45 runs (E4) | yes |

**A proposed SESI [DECISION, subject to approval]:**
- **Stage 1a:** an absolute increase in 5-turn recovery of **0.25** (from about 0.05 to 0.30).
  - The reasoning: a display change achieving about half of what C's exact text achieved (7/12 ≈ 0.58) would be
    practically meaningful.
- **Stage 1b:** an absolute decrease of **0.30** in first-attempt mismatch.

**An illustrative calculation, to show scale only:**
- **Independent units:** comparing 0.05 with 0.30 at two-sided α = 0.05 and power 0.80 needs about **35 effective
  continuations per factor level**, by the normal approximation
  `n = (z₁₋α/₂·√(2p̄q̄) + z₁₋β·√(p₁q₁ + p₂q₂))² / (p₂ − p₁)²`.
- **With clustering:** with 14 prefixes, each level of A pools 2 B-levels × r replicates. So each prefix contributes
  m = 2r continuations per level, and the design effect is `1 + (m − 1)·ICC`.
  - At ICC = 0.3: r = 2 gives about 30 effective continuations per level; r = 4 gives about 36.
  - So 14 prefixes × 4 cells × 4 replicates = 224 continuations, before deduplication.
- **The interaction:** detecting an interaction of the same size needs about 4 times as many effective units. It is
  therefore planned as secondary.
- **Not a decision:** these numbers depend entirely on the unvalidated p₀ and ICC.

**The pilot [DECISION]:**
- **1a:** 14 prefixes × 4 cells × 1 replicate = 56 continuations.
- **1b:** 15 prefixes × 2 cells × 1 replicate = 30 continuations.
- **Purpose:** to estimate p₀, the cell rates, the ICC, the duplicate rate and the cost, and to check that each
  condition is implemented faithfully.
- **The pilot's limits:** it produces only estimates with intervals: no confirmatory test, and no claim. Its data are
  not reused in the confirmatory analysis.

**After the pilot [DECISION]:**
- **The simulation:** run the GLMM of section 7 with the pilot's estimates, varied over their intervals, for example
  ICC from 0.1 to 0.5 and p₀ from 0 to 0.15.
- **The confirmatory size:** the smallest replicate count r reaching 0.80 power for the A and B main effects at the
  SESI, under the pessimistic end of the intervals.
- **Frozen beforehand:** the size, with the outcomes and the analysis, in a new amendment before any confirmatory run.

**Rough cost [HYPOTHESIS]:**
- A continuation is a prefix replay (tools only) plus about 10 model turns.
- Phase 3b averaged about 11 seconds per turn.
- So the pilot's 86 continuations should take on the order of 3–5 GPU hours.
- [UNRESOLVED] The prefix-replay cost on Kaggle, in particular index building.

## 9. Threats to validity

| Threat | Detail | Label |
|---|---|---|
| History consistency | Prefix calls were generated under A1/C1. The model in an A2/C2 continuation sees a history it would not have produced itself. | [HYPOTHESIS] (direction unknown) |
| Single line, single task | All the evidence is one line of one task. Stage 1 can show that a display matters there, not in general. | [EVIDENCE] |
| One model and its temperature | devstral-small-2 at temperature 0.2. Effects may be model-specific. Low entropy yields duplicates and fewer effective units. | [EVIDENCE] (E10) |
| The loop breaker | Held constant. It withholds answers to repeats, so B can act only on non-repeated refusals. B's effect may be underestimated relative to a harness without it. | [DECISION] / [HYPOTHESIS] |
| A2's novelty | `│` may be rare in training data and change behaviour for reasons beyond the separator, for example copying it (H3). | [HYPOTHESIS] |
| C2's confound | Block presentation also reveals copyable exact block text. | [HYPOTHESIS] |
| Stage 1 against Stage 2 | Branching measures local recovery, not task resolution. Stage 2 is needed for the outcomes that matter to users. | [DECISION] |
| Blinding | Only partial (section 5). | [UNRESOLVED] |
| Model files | Must be the Phase 3b pinned layers. A different digest stops the session, as in Phase 3b. | [DECISION] |

## 10. Execution and acceptance criteria

**Stage 0** (offline, no model; all must pass before the pilot):
1. **A switch with no effect when off** [DECISION]: with every switch off, a replay of all 15 arm-A transcripts is
   byte-identical to the Phase 3b answers. This is the Phase 3b test-1 method.
2. **Isolation** [DECISION]: on the same replays, each switch changes only its own text:
   - A2: only the numbered-listing separator and the phrases naming it;
   - B2: only the one inserted sentence, on whitespace-only refusals;
   - C2: only the added statement block in listings.
   - Checked by diff rules, as in Phase 3b tests 2 and 3.
3. **Prefix integrity** [DECISION]: every A1B1C1 prefix, replayed to its branch point, reproduces the recorded
   `output_sha` of every answer. Prefixes that fail are excluded, by a rule frozen beforehand.
4. **No leakage** [DECISION]: no condition's text contains answer-key or hidden-test content (a scan, as in the
   audit).

**The pilot (Stage 1) [DECISION]:**
- **Before any continuation runs:**
  - every session passes the Phase 3b environment check;
  - every continuation is logged in full: answers recorded whole, not cut at 300 characters, so that M2 is exact on
    the displays too.
- **Stopping rules:**
  - a session whose environment differs stops;
  - more than 10% of continuations failing on infrastructure stops the pilot, with a report.
- **Acceptance:**
  - all 86 continuations complete, or the shortfall is reported;
  - the estimates go to the user with their intervals;
  - no confirmatory claim is made.

**Stage 2:** only after the user approves a new amendment built from the pilot.

## 11. Limitations and unresolved questions

- [UNRESOLVED] Whether a display change that helps at line 181 helps elsewhere. That needs new tasks (section 4.4).
- [UNRESOLVED] Why the model repeats a refused call instead of copying the shown line. B tests one remedy, the
  diagnostic, not the mechanism.
- [UNRESOLVED] Whether the read listing or the error listing matters more. A changes both together, by design, so that
  the separator is absent everywhere.
  - A follow-up could split A into "A in `read_file`" and "A in the error". That costs an extra factor.
- [UNRESOLVED] Why the baseline edits blocks. Factor C tests one candidate, the gateway's listing. Other differences
  between the baseline and PRISM's arms (tools, prompts, the worklist's pacing) remain untested.
- [UNRESOLVED] The ICC, p₀, duplicate rate and prefix-replay cost: all come from the pilot.
- [EVIDENCE] The Phase 3b transcripts kept 300 characters per answer. The design requires full answers in every new
  log.

## 12. Recommendation

**[DECISION, pending approval]:** run the **pilot** (Stage 0, then Stage 1a and 1b at one replicate), not a full
experiment.

**Why not proceed further now:**
- **The evidence is insufficient for a confirmatory experiment:** p₀, the ICC and the duplicate rate are unknown, and
  the Phase 3b rates are unstable (section 8).
- **It is sufficient to justify a pilot:** the timing evidence (E5, E7) makes H1, H2 and H4 specific and testable.

**After the pilot:** the next step is decided from the pilot's intervals:
- **Effects at or above the SESI:** a frozen confirmatory Stage 1 or Stage 2 amendment, sized by simulation.
- **Intervals excluding the SESI:** a report that these displays are not practically meaningful here.
- **Otherwise:** a report of insufficient evidence, with the sample a confirmatory test would need.

**Nothing is implemented** until the user approves this design, and then approves each stage.
