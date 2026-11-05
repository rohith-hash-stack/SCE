# R2 result: which sections of the change context do the work (forward)

**Pre-registration:**
- R1-A, A6 (`reports/suite/REPORT.md`);
- the amendments in `harness/experiments/r2/AMENDMENT_R2-1.md` (R2-1 to R2-5);
- R3-3 and R3-4 (`harness/experiments/r3/README.md`).

**Run:**
- Model `qwen2.5-coder:14b-instruct-q8_0` on Kaggle, temperature 0.2, seeds 42 and 1001.
- Code `415a931` (all 157 result commits); frozen tasks and pads `0e64bffc`.
- 2,670 of 2,670 calls in 3 sessions; 0 errors.
- 9 answers hit the answer cap. Their tasks are excluded per comparison (A1): at most 3 of 192 development tasks and
  1 of 75 held-out tasks per comparison. No comparison is near the 20% limit.
- Analysis: `harness/experiments/r2/analyze.py`, committed before any answer was opened. Full output:
  `reports/r2/RESULTS.json`.

## Model-free context (reported first; frozen before any call)

- Sections 4 and 7 each list nearly all changed callers: section 4 lists 100%, section 7 lists 99.7% on
  development and 90.5% on held-out.
- Removing both leaves 4-9% of the changed callers listed.
- The pre-run note predicted that **single-section effects would be small**, because each of 4 and 7 still names the
  callers when the other is removed. **That prediction was wrong** (below).

## Primary: callers found (share of the changed callers updated), FULL minus the version

| removed | development (n) | drop [95% CI] | held-out (n) | drop [95% CI] | seed 42 / 1001 (dev) |
|---|---|---|---|---|---|
| §4 who depends on it | 191 | **+0.213** [+0.161, +0.268] | 74 | **+0.213** [+0.132, +0.297] | +0.23 / +0.19 |
| §6 style to follow | 192 | **+0.089** [+0.039, +0.139] | 74 | **+0.089** [+0.009, +0.172] | +0.11 / +0.07 |
| §7 callers' code | 189 | **+0.166** [+0.107, +0.226] | 74 | **+0.217** [+0.122, +0.318] | +0.18 / +0.15 |
| §4 and §7 | 191 | **+0.424** [+0.358, +0.490] | 74 | **+0.578** [+0.473, +0.679] | +0.45 / +0.40 |

- **Full context:** 0.65 of the changed callers found on development, 0.85 on held-out.
- Every drop is positive at both seeds. None is unstable.
- Every drop is positive in every language: Python and JavaScript on development; Python and TypeScript (by
  repository) on held-out.

## A6 hypotheses (development, Holm across the four)

| id | prediction | result | label |
|---|---|---|---|
| R2-H4 | removing §4 lowers callers found by ≥ 0.05 | +0.213, Holm p 1e-10, CI above 0 | **pass** |
| R2-H6 | §6 is dead weight for forward (within ±0.03) | +0.089, TOST p 0.99 | **equivalence not shown** (§6 helps instead) |
| R2-H7 | §7 is redundant once §4 is present (within ±0.03) | +0.166, TOST p 1.0 | **equivalence not shown** (§7 helps) |
| R2-H47 | removing both lowers it more than either alone | +0.21 below NO4, +0.26 below NO7, both CIs above 0 | **pass** |

## Sections (A6 "load-bearing"; R3-3 and R3-4 pruning)

| section | load-bearing (dev pass, held-out same direction) | dead weight | harmful | pruned |
|---|---|---|---|---|
| §4 | **yes** | no | no | no |
| §6 | **yes** (drop 0.089, above the 0.05 floor) | no | no | no |
| §7 | **yes** | no | no | no |
| §4 + §7 (pair, R3-4) | **yes** | no | no | — |

**Pruning decision (R3-3, R3-4): none.** R3's PRISM tools keep the full change context.

## Secondary

**Wrong functions per task** (functions named that did not need changing):

| | FULL | NO4 | NO6 | NO7 | NO47 |
|---|---|---|---|---|---|
| development | 0.58 | 0.46 | 0.58 | 0.59 | 0.80 |
| held-out | 0.57 | 0.56 | 0.65 | 0.59 | 0.71 |

- Removing §4 lowers wrong functions slightly on development (0.58 to 0.46). The model names fewer functions without
  the caller list, right and wrong alike.
- Removing both sections raises wrong functions.

**All callers found** (the task fully solved) falls with every removal, in step with the primary.

## What this allows us to say

- "On these 267 tasks (10 development and 15 held-out repositories), with this model, each of sections 4, 6 and 7 of
  PRISM's change context helps the model update the callers a change breaks. Sections 4 and 7 together carry most of
  the effect: without both, callers found falls by 0.42-0.58."
- **§4 and §7 are not redundant in effect,** although both name the callers. The per-call-site facts (§4) and the
  callers' code (§7) each add to the other.
- **The style section (§6) helps forward too** (+0.09), contrary to R2-H6's prediction. That prediction is
  falsified.
- **Scope:** one model, single-shot prompts, forward only. It says nothing about blast, newfn, agents (R3) or other
  models (R5).
- R2's FULL numbers are not pooled with R1's (R2-4: digest v2).
