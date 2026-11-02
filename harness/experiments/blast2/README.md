# Blast round B2: does PRISM name the callers a text search cannot show? (pre-registered before any data)

R1 (`reports/r1/REPORT.md`) left blast unresolved. Its pre-registered test missed Holm's correction (p = 0.059)
with about 54% power. It was judged against a key that records only what the tests happened to run:
- 86% of key callers were 3 or more hops away;
- 24 of 76 anchors had no static callers;
- 70% of PRISM's names were real callers that the key could not credit.

R1's one robust blast result was exploratory: on callers the padded prompt did not show, PRISM named 0.27 and the
padded version 0.10. The difference was +0.17 [+0.09, +0.24], stable at every seed and without the top 5 tasks.
B2 tests that result as its primary, on new tasks, against a key that is not limited to test coverage.

This file is committed before any anchor is drawn, any key is built or any model is called.

## Key

**Primary key K (the callers that can reach the anchor):** traced callers ∪ independently resolved static callers.
- **Traced callers:** R1's tracer, unchanged (`harness/experiments/suite/trace_edges.py`): production frames only,
  at any depth, from the repository's own test suite at the task's commit.
- **Static callers:** found by a resolver independent of PRISM.
  - Python: `jedi` (version pinned when this round's code is frozen), `Script.get_references` on the anchor's
    definition; a reference inside a production function makes that function a caller.
  - JavaScript and TypeScript: the TypeScript language service's `findReferences` (`typescript` npm package,
    version pinned).
  - Repeated for each caller, up to 6 hops [TBD team: confirm 6, which equals PRISM's own walk limit].
  - **PRISM's index and call graph are never used to build K.**
- Test functions are excluded everywhere: key, prompts, padding and scoring, as in R1, where the filtering was
  checked symmetric.

**Why this key, not a developer-changed key, as primary:**
- Blast asks *what a change can affect*, and every function that can reach the anchor is in that set. R1's key
  failed by being incomplete (only what the tests ran), not by containing irrelevant callers. Adding independently
  resolved static callers fixes the incompleteness without letting PRISM define the answer.
- A developer-changed key answers *what had to be edited*. That is forward's question, already tested and passed.
  It gives a median of 1 caller per task, almost all visible to grep (R1-A, A0), so a hidden-caller primary would
  have no power under it.

**Secondary key K_dev (relevance check):** for anchors changed in at least one commit in the repository's history,
the callers (any depth within K) the developer changed in the same commits, found with forward's miner
(`change_history/mine.py`). It is reported where it exists, and is never used for a decision.

**R1's traced-only key** is reported as a secondary, for comparability with R1 only.

## Tasks

- **At least 150 anchors.** R1's observed blast variance gives power 0.87 at 150 tasks for +0.10 (resampling;
  R1 post-hoc §7). The hidden-caller measure's own spread (standard deviation 0.32) needs about 93 tasks for +0.10 at
  α = 0.05 by the normal approximation. 150 covers both.
- **At least 8 repositories, at most 25 anchors from any one.**
  - At least half of the anchors come from repositories never used in the suite, R1 or earlier runs.
  - No R1 anchor is reused.
- **Languages:** Python, and JavaScript or TypeScript where the Node tracer runs the repository's tests [TBD team:
  the repository list; at least one TypeScript repository].
- **Anchor rule (R1's, unchanged):** a production function with at least 3 callers in K at any depth and at least 1
  direct one, drawn in a seeded order (seed 5501). The repository list and the draw are committed before any key
  is built.

## Versions and runs

Same as R1, with the code frozen at this round's commit:
- `GREP`: the anchor's file and every production line mentioning its name.
- `GREP_PAD`: GREP plus the code of the functions containing those lines, then keyword-search functions, then
  nearby code. It is built with R1's `pad.py` rules, to within ±5% of PRISM's real token count.
- `PRISM`: the file and `prism_blast_radius(..., include_tests=False)`, budget 6,000.
- Prompt text identical to R1's blast prompt.
- Model `qwen2.5-coder:14b-instruct-q8_0`, temperature 0.2, seeds 42, 1001 and 2002, 4,096-token answers.

## Measures

**Primary (one test): hidden-caller recall, PRISM vs GREP_PAD.**
- The hidden set is H = the callers in K that GREP_PAD's prompt does not show as one of its functions. It is
  computed from the frozen prompts and K before any model call.
- Per task: the share of H the answer names, by R1's name matcher. Then the mean over the three seeds.
- Tasks with an empty H are left out, and their number is reported.

**Secondary (reported, never decisive):**

| measure | definition |
|---|---|
| real-caller precision (RCP) | the share of the answer's named functions that are in K. R1 measured 97% for PRISM and 42% for GREP_PAD against traced ∪ PRISM-static callers, a definition B2 replaces with K. |
| name-quality breakdown | each named function is one of: in K and traced; in K and static only; an existing function that is not a caller; not found in the repository by name. Reported per version. |
| recall at matched length | recall in K within the first k names, k = the shorter of the two answers per task and seed |
| recall at 5, 10 and 20 names | recall in K within the first k names of the answer (answer order; a proxy, since answers are unranked) |
| aggregate recall | recall of all of K (R1's primary measure, here secondary) |
| K_dev recall | recall of the developer-changed callers, where K_dev exists |
| traced-only recall | recall of R1's kind of key, for comparability |
| direct vs transitive | every recall measure split at 1 hop |

**Without the model** (reported first, never pooled with the measures above): the share of K and of H listed in
PRISM's context, and the share shown in GREP_PAD's prompt.

## Tests and decisions

- **One primary**, so α = 0.05, two-sided, with no Holm correction within B2. If B2 runs in one family with other
  types, Holm's correction across that family applies, and the family is listed before the run.
- Wilcoxon signed-rank over tasks, on the per-task means across seeds.
- Bootstrap 95% interval of the difference: 10,000 resamples over tasks, seed 5501.
- **Truncation (R1-A, A1, standing rule):**
  - a task is excluded when either compared version's answer was cut at the cap, at any seed;
  - the all-tasks result is reported beside the primary;
  - more than 20% of tasks excluded makes the result invalid.
- **Floor:** +0.08. That is half of R1's exploratory effect, and just below its lower interval bound (+0.09).

**Labels (R1-A, A3, in this order):**

| label | condition |
|---|---|
| `invalid` | more than 20% of tasks excluded for truncation |
| `padding_better` | p < 0.05 and the interval lies below 0 |
| `not_shown` | p ≥ 0.05, or the interval's lower end ≤ 0 |
| `below_floor` | significant and above 0, but under +0.08 |
| `pass` | everything else |

**What each label allows:**
- **`pass`:** "On these repositories, with this model, PRISM's blast radius makes the model name more of the callers
  that an equal-size grep-and-code prompt does not show." That is all; nothing about other repositories or models.
- **`not_shown` or `padding_better` (the negative branch):**
  - R1's hidden-caller result did not replicate, and the blast access claim is withdrawn: "No advantage was shown in
    naming callers the padded prompt does not show."
  - Real-caller precision, the name-quality breakdown and any per-language, per-repository or per-depth result may
    not be used to say PRISM still helps on blast. They show what kind of names PRISM gives, not that it finds more.
  - A tie with a high real-caller precision is reported as: "PRISM names real callers, but no more of the hidden ones
    than the padded prompt."
- **`below_floor`:** "a small advantage of X in naming hidden callers, below the pre-registered bar". It is not a
  pass.

## Freezing

Committed in this order, each with its sha256 in a manifest:
1. this file;
2. the repository list and the anchor draw;
3. the key-building code with the resolver versions pinned, then K, K_dev and H;
4. the pads;
5. the Kaggle cell, pinned to the code commit.

The cell checks every hash before any model call. No answer is opened before the analysis code
(`harness/experiments/blast2/analyze.py`, to be written to this file's rules) is committed.

## Stopping

- One run per task set.
- A `not_shown` result is not followed by re-tuning PRISM's blast radius, the prompt or the key on these tasks. A
  changed version needs a new pre-registration and new anchors.
- If K turns out to be degenerate (more than 30% of drawn anchors have an empty H), the round stops before any model
  call and is reported as not run.

## What does not carry over from R1

- **R1's traced-only key as the key:** replaced by K. The traced-only result is a secondary, for comparability.
- **Aggregate recall as the primary:** it is a secondary. The primary is hidden-caller recall.
- **The matched-length cut as a decision:** it is a secondary.
- **R1's anchors and its hidden-caller numbers:** they motivate B2 and are not pooled with it.
- **R1's "named" means** (21 against 9): replaced by the name-quality breakdown. The means were carried by a few
  long lists, with medians 3.5 and 6.5.
