# PRISM task suite: results

Ten repositories, six task types, 742 tasks, 2,036 model calls. The design was pre-registered in
`harness/experiments/suite/README.md`. Numbers come from `RESULTS.json`, written by
`python -m harness.experiments.suite.analyze`.

**Setup**
- Model `qwen2.5-coder:14b-instruct-q8_0` (Ollama 0.34.0, Kaggle T4 ×2).
- Temperature 0.2, seed 42, 20,480-token window, 4,096-token answer cap.
- Code pinned at `793c40a`.

## Summary

| type | task | baseline | PRISM | p (Holm) | verdict |
|---|---|---|---|---|---|
| 1 forward | callers of a change updated | FILE 92/298 (31%) | **214/298 (72%)** | 1.5e-26 | PRISM better |
| 4 blast | traced callers named (recall) | GREP 0.129 | **0.302** | 2.4e-4 | PRISM better |
| 2 newfn | existing helpers reused | FILE 0.591 | **0.708** | 1.1e-3 | PRISM better, with costs (below) |
| 3 bugs | a bug function in top 5 | BM25 97/184 (53%) | 92/184 (50%) | 0.77 | no difference |
| 6 tests | all written tests pass | FILE 0/62 | 2/62 | 0.77 | no difference (format-bound) |
| 5 concepts | an answer function in top 5 | BM25 93/100 | 95/100 | 0.69 (raw) | no difference; not a decision test |

- Three of the five primary tests are significant after Holm: forward, blast and newfn.
- Bugs and tests are not. Per the pre-registration, neither losing version is tuned and re-run.
- Every interval below is a bootstrap 95% interval over tasks (10,000 resamples, seed 5501).

## Type 1: forward (update the callers a change breaks)

192 tasks × 3 versions. 298 callers must be updated.

| | FILE | PRISM | PRISM_BREAKS |
|---|---|---|---|
| callers found, per task | 0.299 | 0.693 | 0.750 |
| tasks with every caller found | 26% | 68% | 73% |
| wrong functions named, per task | 0.99 | 0.52 | 0.43 |
| prompt tokens | 2,270 | 4,243 | 4,388 |
| seconds per answer | 34.2 | 36.2 | 36.8 |

**PRISM vs FILE**
- McNemar over callers: 214 vs 92 found, p = 3.0e-27 (Holm 1.5e-26).
- Difference: +0.64 callers per task [0.50, 0.79].

**By tier** (callers found share, FILE → PRISM)
- easy (98 tasks): 0.49 → 0.65
- medium (64 tasks): 0.03 → 0.70. Here the callers sit outside the anchor's file.
- hard (30 tasks): 0.27 → 0.80. Wrong functions are lower only with breaks: 0.70 (FILE), 0.87 (PRISM), 0.57 (PRISM_BREAKS).

**By language** (callers found share, FILE → PRISM)
- Python, 120 tasks: 0.38 → 0.79.
- JavaScript, 72 tasks: 0.17 → 0.53. On JavaScript, wrong functions barely move (0.78 → 0.72).

**By repository**
- PRISM beats FILE in every repository.
- Weakest: hapi (0.00 → 0.40, wrong functions up 1.00 → 1.15) and mocha (0.20 → 0.45).

**Break check (PRISM_BREAKS vs PRISM)**
- Callers found: 227 vs 214, p = 0.098.
- Wrong functions per task: 0.43 vs 0.52, p = 0.10, difference −0.09 [−0.20, 0.01].
- Decision rule: the break section becomes the default only if it finds significantly more callers. It
  did not, so **the break section stays optional**. The direction favours it on both measures, and in
  most repositories.

## Type 2: newfn (write a new function that reuses the project's code)

128 tasks × 2 versions: 113 Python, 15 JavaScript.

| | FILE | PRISM |
|---|---|---|
| helper reuse | 0.591 | 0.708 |
| invented calls per task | 0.19 | 0.30 |
| the code parses | 96% | 88% |
| prompt tokens | 2,174 | 6,144 |

**Primary test (helper reuse)**
- Wilcoxon p = 3.5e-4 (Holm 1.1e-3).
- Difference: +0.12 [0.06, 0.18].
- Reuse rises in every repository with more than one task.

**The costs.** PRISM's context makes the model call more names that do not exist, and break the syntax
more often.
- Invented calls rise in 6 of 8 repositories. On mocha they go from 0 to 0.38.
- Parse failures concentrate in rich (80% → 67%), scrapy (100% → 85%) and aiohttp (95% → 85%).
- So `newcode`'s helper list helps reuse but needs work on invented calls (see product issues).

## Type 3: bugs (which functions must change to fix this bug)

184 tasks × 3 versions. Context budget 6,000 tokens for each version.

| | TREE | BM25 | PRISM |
|---|---|---|---|
| top 1 | 4% | 31% | 28% |
| top 5 | 9% | 53% | 50% |
| recall@5 | 0.08 | 0.46 | 0.43 |

**Primary test (top 5, PRISM vs BM25)**
- McNemar: 92 vs 97, p = 0.38 (Holm 0.77).
- Difference: −0.03 [−0.08, 0.02].
- **PRISM's ranking adds nothing over plain BM25 on the same pool.**

**Search vs file list.** Both searches beat the file list by far: PRISM vs TREE p = 2.1e-19.

**By language**
- Python: 42% (BM25) vs 43% (PRISM).
- JavaScript: 66% vs 59%.
- PRISM is ahead in scrapy and tornado, behind in werkzeug, hapi and axios.
- rich has only 4 tasks.

## Type 4: blast (what does a change to this function affect)

76 anchors in the 6 Python repositories. The answer key is caller edges recorded by running each
repository's own tests under a tracer. JavaScript is not covered.

| | FILE | GREP | PRISM |
|---|---|---|---|
| recall of traced callers (transitive) | 0.127 | 0.129 | 0.302 |
| recall of direct callers | 0.39 | 0.43 | 0.65 |
| functions named | 4.1 | 8.1 | 21.1 |
| prompt tokens | 1,709 | 2,356 | 5,443 |
| seconds per answer | 11.1 | 39.8 | 61.8 |

**PRISM vs GREP (primary)**
- Wilcoxon p = 5.9e-5 (Holm 2.4e-4).
- Difference: +0.17 [0.10, 0.25].

**PRISM vs FILE**
- p = 1.6e-6.
- PRISM is highest in all six repositories.

**Caveats**
- The tests cannot reach every caller, so the key is incomplete. Precision is not claimed.
- The "outside traced and PRISM" count is lowest for PRISM by construction. It is shown for completeness, not as evidence.
- PRISM's answers are longer: 21 names, against 8 for GREP.
- aiohttp's async code gives a thin trace (49 edges, 11 anchors). scrapy has 5 anchors.

## Type 5: conceptual questions (hand-written; never used for a decision)

100 questions, 10 per repository.

| | TREE | BM25 | PRISM |
|---|---|---|---|
| top 1 | 17% | 74% | 72% |
| top 5 | 41% | 93% | 95% |
| recall@5 | 0.30 | 0.81 | 0.83 |

- PRISM vs BM25: 95 vs 93, p = 0.69. No difference.
- Both are near the ceiling.
- Python: 98% (PRISM) vs 95% (BM25). JavaScript: 90% vs 90%.

## Type 6: tests (write tests for a function; Python)

62 tasks × 2 versions: rich 20, aiohttp 20, scrapy 10, marshmallow 5, tornado 5, werkzeug 2. The
tests were run in each repository at the parent commit.

**Strict score (pre-registered primary)**

| | FILE | PRISM |
|---|---|---|
| all tests pass | 0/62 | 2/62 |
| the tests call the target | 61% | 66% |
| coverage of the target vs the developer's tests | 0.11 | 0.15 |
| prompt tokens | 3,016 | 4,698 |
| seconds per answer | 149 | 154 |

- All tests pass: McNemar p = 0.5 (Holm 0.77).
- Coverage: Wilcoxon p = 0.78.

**The format problem.** The strict score is bound by format, not by test quality. Of 124 answers:
- 42 hit the 4,096-token cap mid-answer;
- 67 put markdown fences inside the code;
- only 12 were clean.

**Salvage sensitivity analysis (post hoc, amendment 4, not in Holm)**
- Every answer of both versions is rescored the same way: fences removed, complete tests of a cut answer kept.

| | FILE | PRISM |
|---|---|---|
| all tests pass | 3/62 | 5/62 |
| share of written tests passing | 0.36 | 0.43 |
| the tests call the target | 95% | 92% |
| coverage vs the developer's tests | 0.89 | 0.97 |

- All pass: p = 0.69.
- Pass share: p = 0.19, difference +0.08 [−0.04, 0.19].
- Coverage: p = 0.30, difference +0.08 [−0.07, 0.23].
- The direction favours PRISM, but nothing is significant.
- Coverage can exceed 1, when the AI's tests reach lines the developer's did not.
- By repository:
  - PRISM is ahead on aiohttp (coverage 0.71 → 1.00, passing tests 2.5 → 6.4) and tornado;
  - behind on scrapy (0.66 → 0.55) and werkzeug (2 tasks).

**Fix before re-testing.** For this type, the answer format must change: ask for the code without
fences and raise or remove the answer cap. That is a change to the task, so it needs new tasks, not a
re-run of these.

## Validity checks (added after the run)

These checks don't change any primary result. They test whether the results can be trusted.

**Truncation per version** (`RESULTS.json`, `validity`). A comparison counts as invalid when a version
cuts more than 10% of its answers at the answer cap, or the two versions differ by more than 5 points.

| type | baseline | PRISM | valid |
|---|---|---|---|
| forward | FILE 0.5% | 0% | yes |
| bugs, concepts, newfn | 0% | 0% | yes |
| blast | GREP 7.9% | 2.6% | no (a 5.3-point gap) |
| tests | FILE 37% | 31% | no |

- **Blast, without the 8 tasks that had a cut-off answer:** GREP 0.138, PRISM 0.301, p = 2.5e-4. The win holds.
- **Tests, without the 33 tasks that had a cut-off answer:** 0 vs 0 pass. Test writing stays invalid; fixing it needs new tasks.
- No prompt came close to the window: the prompt-near-window share is 0 for every type.

**newfn, outer fence removed** (`scores_fence.json`; a check after the fact, applied to both versions alike):

| | FILE | PRISM |
|---|---|---|
| the code parses (strict) | 96% | 88% |
| the code parses, outer fence removed | 98% | 97% (p = 0.69) |
| reuse | 0.591 | 0.708 (unchanged) |
| invented calls | 0.19 | 0.30 (unchanged) |

- 11 PRISM answers and 3 FILE answers wrapped the code in a fence.
- **Most of the parse "cost" was this fence, not broken code.** The invented-call cost is real.

**Tokens** (`TOKENS.json`, measured with the Qwen2 tokenizer):
- PRISM's estimate overcounts in every context: by 1.35× on average, 1.15× at the 5th percentile and 1.55× at the 95th.
  - By type, the average ranges from 1.20× (blast) to 1.46× (concepts).
  - So its budgets never overrun; they leave about a quarter of the budget unused.
- The harness rebuilds all 740 saved prompts exactly. Ollama's count is always the harness count + 29, the chat template's overhead. So the frozen prompts can be replayed with other seeds.

**Index:** before this fix, PRISM indexed every file on disk. An initialised git submodule (aiohttp's `vendor/llhttp`) added 13 files and 164 graph nodes. That is the most likely source of the "43 phantom functions" first blamed on the cache; the cache itself checks every file's content hash. Discovery now follows git (`prism.git_files`). None of the 22 clean clones changes.

## Costs

- PRISM's contexts are 1.3–2.8× the baseline's prompt (forward 1.9×, newfn 2.8×, blast 2.3× GREP).
- They stay well inside the 20,480 window.
- Answer time rises little, except on blast (62 s vs 40 s), where PRISM's answers name many more functions.

## Product issues this run found

1. **Indexing depended on what was on disk** (fixed). Untracked files, such as an initialised submodule, were
   indexed. This was first reported as cache staleness (43 extra functions on aiohttp). Discovery now follows git; see
   "Validity checks".
2. **`prism.ask` ranking gives no gain over BM25** on bug localisation (type 3) or concept questions
   (type 5). Its value is in the structured contexts (change context, blast radius), not in search ranking.
3. **`newcode` raises invented calls** (0.19 → 0.30) along with reuse. The parse failures (4% → 12%) were mostly
   a fence around the code (98% vs 97% parse once it is removed).
   The helper list should mark which names are real and callable from the target's scope.
4. **The break section** (wording 2) points the right way but is not proven; it stays opt-in.

## Amendment R1-A: analysis rules and interpretation, committed before any R1 answer is scored

R1 (`harness/experiments/r1/README.md`) is running on Kaggle with frozen prompts. At the time of this commit, no
R1 answer has been opened, scored or summarised. Everything below is analysis or interpretation. It changes no
prompt, no task and no model call, and needs no re-run. The analysis code is `harness/experiments/r1/analyze.py`
and `run.py` (scoring only) at this commit.

### A0. Facts established from the frozen prompts and the answer key (no model output)

How many answer items each prompt shows. This is computed by `analyze.hidden` and the visibility check in the
commit message.

| type | answer items | in the padded prompt | in PRISM's prompt | hidden from the padded prompt |
|---|---|---|---|---|
| forward (changed callers) | 298 | FILE_GREPPAD 289 (97%); FILE_BM25PAD 252 | 297 | 9 callers in 9 tasks |
| forward medium tier | 70 | FILE_GREPPAD 65 | 70 | 5 |
| blast (traced callers, transitive) | 3,628 | GREP_PAD 178 (5%) | 534 listed | 3,450 in all 76 tasks |
| newfn (developer's helpers) | 358 | FILE_BM25PAD 199 | 199 | 159 in 88 tasks |

- **Forward:** the grep-padded prompt contains almost every caller's code. That includes the medium tier: a
  medium-tier caller is in another file but still names the changed function, so grep finds it. **R1's forward test
  therefore measures whether PRISM's organisation of the callers (who depends on it, per-call-site facts) beats the
  same callers given as raw code.** It cannot measure access to callers the model could not otherwise see. With 9
  hidden callers, that question has no power in R1.
- **Blast:** the reverse. The padded prompt shows few traced callers, so blast does test access.
- **Newfn:** both prompts show the same number of the developer's helpers (199 of 358).

### A1. Added reports (analysis only)

| rule | applies to | status before this amendment |
|---|---|---|
| Forward results by tier (easy / medium / hard), for PRISM against every other version | forward | **missing**: aggregate only |
| Blast recall within the first k = 5, 8, 10 names of each answer | blast | **missing** |
| Blast recall at matched length: k = the shorter of the two answers, per task and seed | blast | **missing** |
| Blast precision proxy: the share of named functions that are traced callers (a lower bound, since the trace is incomplete) | blast | **missing** |
| Truncation per version | all | already in R1's analysis (`validity`) |

- **These reports are secondary.** They are not in Holm's correction and cannot decide a type's outcome (A3).
- The blast answers are unranked (the prompt says "list every one"). Recall at k uses the order the model wrote, so
  it is labelled a proxy.

**Truncation-exclusion rule (standing, for R1 and every later run):**
1. A task is excluded from a primary comparison when either compared version's answer was cut at the answer cap,
   at any seed used.
2. The primary is computed on the remaining tasks. The all-tasks result is always reported beside it.
3. The truncation rate of every version is reported with every primary result.
4. If more than 20% of a type's tasks are excluded, that type's result is **invalid**.

This replaces R1 README's "truncated answers stay in their arm". The change is made before any R1 answer is seen.

**This rule applied to the original 742 tasks:**

| type | tasks excluded | reported | under the rule |
|---|---|---|---|
| forward | 1 (a FILE answer) | 92 vs 214 of 298, Holm p 1.5e-26 | **92 vs 212 of 296, Holm p 5.1e-26** |
| blast | 8 | 0.129 vs 0.302, Holm p 2.4e-4 | **0.138 vs 0.301 (68 tasks), Holm p 9.9e-4** (as in "Validity checks") |
| newfn, bugs, concepts, break check | 0 | unchanged | unchanged |
| tests | 33 of 62 | 0 vs 2 of 62 | **0 vs 0 of 29: invalid** (more than 20% excluded) |

Every conclusion of the suite stands.

### A2. Answers hidden from the padded prompt (the mechanism-only measure)

For each type, the primary comparison is repeated on the answer items hidden from the padded prompt (A0):
- forward: changed callers found;
- blast: traced callers named;
- newfn: helpers reused.

It is descriptive and cannot decide a type's outcome. For forward (9 items) it is reported as having no power.

### A3. Outcome labels and the negative branch

Each type gets exactly one label, from the primary alone, in this order:

| label | condition | what may be claimed |
|---|---|---|
| `invalid` | more than 20% of tasks excluded for truncation | nothing about this type |
| `padding_better` | Holm p < 0.05 and the interval lies below 0 | "the padded baseline beat PRISM"; the type's suite win is withdrawn |
| `not_shown` | Holm p ≥ 0.05, or the interval's lower end ≤ 0 | "no advantage over an equal-size baseline was shown"; the type's suite win is attributed to prompt size |
| `below_floor` | significant and the interval is above 0, but the difference is under the floor (forward 0.10, blast 0.05, newfn 0.05) | "a small advantage of X, below the pre-registered bar"; not a pass |
| `fails_invented_calls` | newfn only: otherwise a pass, but PRISM's invented calls exceed the padded baseline's by more than 0.05 per task | "more reuse at the cost of more invented calls"; not a pass |
| `pass` | everything else | A4's sentence, for that type only |

**Rules that stop a result being reframed afterwards:**
- Secondary results (tiers, recall at k, per seed, per language, hidden-item analysis) **never change a label**.
  - If forward is `not_shown`, the report may not say "PRISM still helps on medium tasks" or "grep was strong
    but PRISM still helps".
  - A secondary result that favours PRISM is reported as an uncorrected, exploratory observation, and becomes a
    hypothesis for a later pre-registered round.
- The label uses the per-task mean over all complete seeds. No seed is chosen afterwards.
- No version, measure or task subset is added after the run. No task is excluded except by the truncation rule.

**What each type's claim shrinks to if it is not a pass:**
- **forward:** "On these tasks, PRISM's change context is not more useful than the same number of tokens of grep
  results plus the callers' code." No narrower forward claim is available from R1. The only mechanism left
  (callers the model cannot see at all) has 9 items. It is deferred to a round whose tasks are selected for callers
  that grep misses (dynamic dispatch, aliases, re-exports), decided before mining.
- **blast:** "PRISM names more of the traced callers the padded prompt does not show" is allowed **only if** the
  hidden-item comparison (A2) is significant at α = 0.05 on its own. Even then it is labelled exploratory, because
  it was not a primary.
- **newfn:** "PRISM's helper list and the same number of tokens of keyword-search code give the same reuse." The
  hidden-helper comparison (A2) is reported as exploratory.

### A4. What R1 passing means, and what it does not

> If a type passes, this sentence holds for that type: on these 10 repositories, with this model, PRISM's context
> is more useful per token than an equal-size prompt of grep or keyword-search results with the matching
> functions' code.
>
> It does not show:
> - that the result holds on other repositories. These tasks come from the suite that motivated the comparison,
>   so this needs held-out repositories;
> - that it holds for other models. Only one model was tested, so this needs a second model;
> - which part of PRISM's context does the work. Its change context has eight sections, never ablated, so this
>   needs an ablation (R2).
>
> For forward in particular, a pass shows that organisation helps when the callers' code is already in the prompt
> (A0). It does not show that PRISM finds callers that text search cannot.

### A5. aiohttp and its submodule: closed, no effect on R1

- **The submodule was not in any frozen aiohttp prompt.** The suite's aiohttp tasks were built in fresh git
  worktrees, and a fresh worktree never checks out submodules. The R1 pads were built in fresh worktrees of new
  clones, with git-aware discovery.
- **Checked directly.** The stored repository-function list of `aiohttp-newfn-002` (1,040 names) equals a clean
  index at its revision. With the submodule initialised, that index has 1,093. For `aiohttp-newfn-001` the stored
  list also equals a clean index (878); the submodule could not be initialised at that old revision.
- **No trace in the files.** No frozen aiohttp task file or R1 pad mentions `llhttp`, `http-parser` or `vendor/`.
- **Rule:** aiohttp is reported with the other repositories, not separately or excluded.
- This check used the frozen prompts and a re-index only, no R1 output.

### A6. R2 skeleton: which sections of the change context do the work (not built; for review)

The eight sections (`prism.impact.context.render`):

| # | section |
|---|---|
| 1 | Codebase |
| 2 | Group |
| 3 | Anchor |
| 4 | Who depends on it: callers, with per-call-site facts (result use, errors caught, arguments, await) |
| 5 | What it depends on |
| 6 | Style to follow: conventions with counts, and two siblings' code |
| 7 | Callers' code |
| 8 | Wider impact |

**When R2 runs:** a type enters R2 only if its R1 label is `pass` or `below_floor`. A type that is `not_shown`,
`padding_better` or `invalid` has nothing to attribute.

**Hypotheses (each falsifiable):**

| id | hypothesis | falsified if |
|---|---|---|
| R2-H4 | removing section 4 lowers forward callers found by ≥ 0.05, and blast recall by ≥ 0.03 | the drop's interval includes 0, or it is below the floor |
| R2-H6 | section 6 is dead weight for forward and blast: removing it changes the primary by less than ±0.03 (equivalence test, TOST, α = 0.05). For newfn it is expected to matter: removing it lowers style match and reuse | equivalence is not shown (forward, blast), or no drop (newfn) |
| R2-H7 | section 7 is redundant once section 4 is present: removing 7 alone changes forward by less than ±0.03 (TOST) | equivalence not shown |
| R2-H47 | removing 4 and 7 together lowers forward by more than removing either alone | no larger drop |

**Versions:**
- Full context, and the full context with one section (or the 4+7 pair) removed.
- **Every version is padded back to the full context's real token count** with keyword-search code (R1's
  FILE_BM25PAD method). Otherwise removing a section would also shorten the prompt.

**Tasks:**
- Development: R1's forward, blast and newfn tasks, at seeds 42, 1001 and 2002.
- Confirmation: the held-out repositories of the next round [TBD: count and language mix; it must include
  TypeScript].
- No section, wording or budget changes between the two.

**Measures:**
- **Without a model** (reported first, never pooled with the model results): the share of answer items each section
  lists. For example, the changed callers in section 4's list and the traced callers in sections 4 and 8.
- **With the model:** forward callers found and wrong functions; blast recall and recall at matched k; newfn reuse,
  invented calls and style match.

**Freezing:**
- Versions, pads and seeds are frozen with sha256 in a manifest before any model call, and the Kaggle cell is
  pinned, as in R1.
- The truncation rule (A1) and the labels (A3) apply.

**Significance:**
- Holm across R2-H4, R2-H6, R2-H7 and R2-H47 within each type.
- Wilcoxon over tasks of per-task means across seeds.
- Bootstrap 95% interval, 10,000 resamples, seed 5501.
- TOST for the equivalence claims.

**"Section X is load-bearing"** when removing it drops the primary by at least the floor, with the Holm-adjusted p
below 0.05 and the interval above 0, on the development tasks **and** in the same direction on the confirmation
tasks. **"Dead weight"** needs the equivalence test to pass on both.

**Stopping:**
- One run per hypothesis per task set. A failed hypothesis is not re-tested by re-wording the section on the same
  tasks.
- If R2-H4 fails on the development tasks, no other section's ablation on that type is used to claim a mechanism.

**Order, and why:**
1. **Remove 4, and remove 6.** These are the two named hypotheses, and the cheapest to settle.
2. **Remove 7, and remove 4 and 7 together.** Section 7 holds the callers' code that R1's grep padding also holds
   (A0), so 4 against 7 separates organisation from raw code.
3. Sections 1, 2, 5 and 8, only if the budget allows. They carry the least answer-relevant content in the model-free
   measure. [TBD: confirm with that measure before ordering them.]
