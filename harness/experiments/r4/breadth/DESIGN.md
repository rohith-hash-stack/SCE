# The breadth study: does PRISM help across tasks? (design, 2026-11-28)

**Status:**
- **Approved by the user to proceed:** "Write it up as a proper design document and go ahead", 2026-11-28, after a
  sketch.
- This is the design, not the pre-registration. The frozen amendment, with the final task list and its hash, is
  written into `AMENDMENTS.md` after the tasks are mined and admitted, and before any run of this design.
- **Done:** step 1, the edit-fix rule (section 3), is implemented and tested.
- **Not yet:** nothing has been run, and no task has been chosen.

## 1. Why this study

**What earlier phases found:**
- Phases 3–3b answered narrow questions, on 2–5 tasks each:
  - POC 2's verdict was GO;
  - Phase 3b's primary test was not significant;
  - the line-181 failure was traced to whitespace transcription, a harness friction rather than PRISM's value.
- **The effect differs by task:** on p3-hidden-03, all three PRISM arms resolved 5/5 against the baseline's 2/5; on
  p3-hidden-15 the baseline resolved 12/15, against 2–11/15 for the PRISM arms.

**What this means for the design:** PRISM's average benefit can only be known across many tasks. So this study makes
the **task** the unit of generalisation, removes the known edit friction equally for both arms, and uses tasks no
earlier phase has run.

## 2. The question and the primary hypothesis

**The question:** across a broad, held-out set of multi-place change tasks, does PRISM (its tools and its gateway)
raise an agent's task-resolution rate over an IDE-style baseline (BASELINE_LSP)?

**H₁ (primary):** the mean per-task difference in resolution rate (PRISM − baseline) is greater than 0.

## 3. The harness, the same for both arms

**Everything as in Phase 3b's cell:**
- the code and flags `--ledger --phase1 --raw-tools --feedback --guards`;
- turn cap 60, budget 1,000,000 tokens;
- devstral-small-2 with the pinned model layers, Ollama 0.34.0, 2 × T4;
- temperature 0.2.

**Plus `--edit-fix`, in both arms (new; implemented):**

| | Detail |
|---|---|
| The rule | `shared_tools.whitespace_fix`: rule v2 of `r4/phase3b_edit_replay.py`, exactly |
| Validation | Offline, on Phase 3b's refused edits: 169 intended, 0 unintended, 0 parse breaks. In-tool, it is identical to rule v2 on 4,000 generated cases (`tests/test_edit_fix.py`). |
| What it does | A one-line edit whose `old` matches exactly one file line apart from its leading whitespace is applied, with the file's own indentation for `new`. |
| What the model is told | `edited F at line N: replaced 1 lines with 1. Your old matched line N except for its leading whitespace, so the edit kept the file's own indentation for new.` |
| What it never touches | An ambiguous match (several lines), part of a line, a match that differs by more than leading whitespace, and every multi-line edit: these are refused exactly as before. |
| Switched off | Byte-identical to the current tool: tested, plus 2 Phase 3b transcripts replayed with 120 of 120 answers matching Kaggle's hashes. |
| Recorded | Each run's score file records `edit_fixes`, the number of times the rule applied. |

**The system prompt is unchanged** [decision]. The tool's answer says what it did; the prompt does not announce the
rule.

## 4. Arms

| Arm | Version | What it is |
|---|---|---|
| **Baseline** | `BASELINE_LSP` | the shared tools, jedi `find_references`, BM25 `semantic_search`, ast-grep, read-only shell; no PRISM |
| **PRISM** | `PRISM_GW_C` | PRISM's tools, its gateway and the gateway's exact-text note after a refused edit |

**Why `PRISM_GW_C`:** it was the best PRISM configuration in Phase 3b (16/20 resolved).
- **This choice was made after seeing Phase 3b's data.** That is the main reason the tasks must be held out
  (section 5).
- A, E and PRISM_LSP are dropped: each extra arm costs a third more runs.

**What PRISM's arm adds:** PRISM's tools and gateway, given that the edit friction is fixed for both. That is the
quantity a product decision needs.

## 5. Tasks: 32 held out, 4 families × 8

**Held out:** no model has run them in any phase. They are chosen by a fixed rule before any run of this design, and
their sha256 is frozen in the amendment. Nobody inspects PRISM's behaviour on them beforehand.

| Family | Source | Count | Status |
|---|---|---|---|
| **Hidden-dependency rename** | Phase 3's 20 validated candidates (`phase3/candidates.json`). Never run in any phase: p3-hidden-05, -07, -08, -09, -10, -11, -12, -14, -17, -18, -19, -20 (12). | 8 | ready; to be chosen by the rule below |
| **Signature change** (a positional parameter made keyword-only) | to be mined: `derive_scout.py` and `derive_tasks.py`, Phase E's validated pipeline | 8 | **to mine** |
| **Module move** (a function moved to a new module) | to be mined: same pipeline | 8 | **to mine** |
| **Delete** (remove a definition and its uses) | to be mined: R4's delete miner (`mine_families.py`). `r4s-delete-01`–`10` have all been run before. | 8 | **to mine** |

**The selection rule [decision]:** within each family, candidates are ordered as Phase 3 did, with grep's precision
lowest first, then the most dependent files. They are taken in that order, at most 2 per repository per family, until
8 are admitted.

**Repositories:** at least 6 across the study.
- **Python only:** the current tools and jedi baseline are Python-specific.
- **From:** the repositories already set up (werkzeug, litestar, fastmcp, pydantic-ai, instructor, scrapy, aiohttp,
  pydantic, multidict), and others in the scouted pool (packaging, pluggy, more-itertools, pendulum, loguru, cattrs,
  sqlglot …), as the miners admit them.

**Admission:** every check must pass before the freeze.
1. The task's scoring check (`derive_scoring_check`): the reference change resolves, an empty attempt doesn't, and the
   done gate accepts the reference change and rejects the empty one.
2. The dry run (`phase2_dryrun --guards --edit-fix --empty-every-arm`) in both arms: the reference change replayed
   resolves, and the empty script does not.
3. **The gateway check:** the reference change replayed through PRISM_GW_C ends with no gateway block of a reference
   edit. This excludes p2-rename-04-style tasks, where the gateway refuses genuine edits as look-alikes.
4. The prompt's turn sentence says 60 turns.

**The development set:** the tasks earlier phases studied (POC 2's five, p3-hidden-03, Phase 2's twenty).
- They are used only for the pilot (section 10).
- They never count toward the primary result.

## 6. Runs

| | |
|---|---|
| Main study | 32 tasks × 2 arms × **3 seeds** (seeds 401–403) = **192 runs** |
| Shards | 8 shards of 24 runs (4 tasks × 2 arms × 3 seeds) |
| Arm order | alternates within each task-and-seed block, with each arm first in half the blocks |
| Expected cost | about 10 minutes a run, so about 32 GPU hours, plus setup of about 0.5 hours per session |
| Pinned per shard | environment checks as in Phase 3b's cell, plus the `--edit-fix` flag in the config sha256 |

## 7. Outcomes

**The primary outcome:** task resolution (`score.resolved`), per run.

**Secondary outcomes (descriptive):**
- the share of a task's required places completed by the run's end;
- unintended edits (the code review's new undefined names, new syntax errors, look-alikes changed);
- run-level failure (Phase 3b's definition, for continuity);
- turns, tokens and wall time;
- `edit_fixes` per run, by arm;
- the gateway's interventions;
- every outcome per family.

**Units:**
- **A run** is an observation.
- **A task** is the unit of generalisation.
- Seeds within a task are not independent evidence about other tasks.

## 8. Analysis, power and the decision rule

**The primary analysis (to be frozen):**
- **Per task:** d_t = (PRISM's resolved runs / 3) − (baseline's resolved runs / 3).
- **The estimate:** the mean of d_t over the 32 tasks, with a 95% percentile bootstrap CI over tasks (10,000
  resamples).
- **The supporting model:** a mixed-effects logistic regression `resolved ~ arm + (1 + arm | task)`, reported beside
  it. Its arm coefficient is an odds ratio, with a 95% CI.
- **Identical transcripts** within a task and arm (temperature 0.2) count once, and n is reported before and after,
  as in Phase 3b.

**The decision rule for Phase 4** (proposed; the user sets the thresholds before the freeze):

| Result | Verdict |
|---|---|
| Mean d ≥ +0.10, and the 95% CI's lower bound > 0 | **GO** for Phase 4 |
| The CI's upper bound < +0.10 | **NO-GO** as configured: no practically meaningful gain |
| Anything else | **Inconclusive.** The report states the number of tasks a decisive test would need. |

**Power, by simulation** (`breadth/power_sim.py`; 1,000 simulated studies per cell):
- **The model:** each task's baseline log-odds is drawn from Normal(0, 1.5); PRISM's effect is drawn per task, from
  Normal(δ, sd_effect); 3 or 5 seeds per arm.
- **The test:** the bootstrap CI's lower bound above 0.

| Tasks × seeds | Mean risk difference ≈ 0.13 (δ = 0.8) | ≈ 0.19 (δ = 1.2) |
|---|---|---|
| 24 × 3 | 0.42–0.48 | 0.73–0.79 |
| **32 × 3** (proposed) | **0.49–0.59** | **0.82–0.88** |
| 24 × 5 | 0.53–0.69 | 0.84–0.95 |
| 32 × 5 | 0.63–0.80 | 0.92–0.98 |

Each range is over sd_effect from 1.0 to 0.5.

**What the power figures mean:**
- **The proposed size:** about 0.85 power for a mean gain near +0.20, and about 0.55 for +0.13. The study is sized
  for a large average effect. A small one would often come out inconclusive.
- **Why 32 × 3 over 24 × 5:** similar power at +0.20, 20% fewer runs, and more tasks, which is the study's point.

**The assumptions behind them:**
- sd_task 1.5 matches the spread of resolution across Phase 3's tasks (from 0/15 to 15/15) only roughly.
- Correlation between seeds at temperature 0.2 is ignored, so the figures are optimistic.
- **The pilot's job:** to estimate these before the freeze. If they are worse, the amendment raises the seeds to 5 (an
  extra 128 runs) or narrows the claim.

## 9. Threats to validity

| Threat | Handling |
|---|---|
| The PRISM arm was chosen from Phase 3b's data | held-out tasks; stated in the report |
| `--edit-fix` helps one arm more | recorded per run (`edit_fixes`); reported by arm; part of the harness both arms share |
| Mining favours PRISM | the miners admit tasks by static and test checks only, never by any arm's behaviour; the selection order is fixed beforehand |
| Family imbalance in difficulty | the per-family results are reported; the primary analysis weighs tasks equally |
| One model | stated; an optional second-model sub-study needs separate approval and an API budget |
| Kaggle queueing | shards are independent; resuming is built in, as in Phase 3b |
| Python only | stated as a limit of scope |

## 10. Order of work

| Step | Content | Status |
|---|---|---|
| 1 | `--edit-fix` for both arms, with tests | **done** (this commit) |
| 2 | Mine and admit the held-out tasks (section 5); write `breadth/TASKS.md` | **in progress** |
| 3 | A pilot on the development set: 6 tasks × 2 arms × 1 seed = 12 runs, with `--edit-fix`. It checks health, cost, `edit_fixes` and the variance assumptions. Results are not used for the primary claim. | needs the user's go-ahead for the Kaggle cell |
| 4 | Write and freeze the amendment: the task list and sha256, the analysis, the thresholds, the sample size from the pilot | needs the user's approval |
| 5 | The main study (192 runs) | needs the user's authorization |
| 6 | The frozen analysis, the report, an independent audit | – |

## 11. What is parked

- **The line-181 counterfactual design** (`phase3b/COUNTERFACTUAL_EXPERIMENT_DESIGN.md`): future work, not run.
- **Arms A and E,** and PRISM_LSP.
- **A second model:** optional; needs a separate approval and an API key.
