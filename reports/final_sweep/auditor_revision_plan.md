# Auditor revision plan: scorer rigor, recomputations, narrative pivot

Branch: `claude/prism-final-empirical-8arms-kp3tpt` (verified with
`git branch --show-current`). `develop` untouched.

Sources:
- Every number below is produced by `scripts/auditor_recompute.py` and
  stored in `reports/final_sweep/auditor_numbers.json`.
- Per-arm scores under all three scorers are in
  `reports/final_sweep/supplementary_triple_scoring.md`.
- Draft manuscript audited: `docs/research_paper_empirical_synthesis.md`
  at `033c68c`.

## 0. Corrections to the revision brief itself

Recomputation did not confirm every premise of the brief. These points
differ from it and are reflected throughout.

1. **Pitfall C.** Moving from partial credit to binary TSR **does not**
   reverse the Oracle-vs-PRISM ordering in any pooled comparison. Every
   reversal found is between **exact-match** and the two containment
   scorers (§1.3).
2. **gpt-4o-mini tRPC taxonomy-vs-lexical (+0.096).** It is binary, but its
   task-clustered 95% CI is [−0.020, +0.228]. It is **not significant**
   (§2.3).
3. **Scaffolding effect on all 90 tasks (+0.072).** Significant uncorrected
   (p = 0.016) but **not after Holm** (p = 0.082). Earlier reports called it
   significant.
4. **"Oracle 0.764 beats PRISM 0.724" on tRPC.** A point estimate only:
   +0.040 [−0.072, +0.148], not significant. The deficit claim is withdrawn
   because it is unsupported, not because the reverse is established.
5. **Token savings on the 80 clean tasks:** **33.4% [25.7%, 40.6%]**, with
   per-corpus values of 20–49%. The brief's "32–37%" is replaced by this.
6. **The non-inferiority margin (−5 pp) was chosen after seeing the data.**
   The claim is valid as stated but must be labeled post hoc (§3.1).

---

## Task 1: Scorer definitions and triple scoring

### 1.1 Definitions

Let `P = [p1..pn]` be the adjudicated `pipeline_symbols` and `A` the
answer's `symbols` list (both compared by bare name). Let `X` be the
arm's rendered context (`selected_symbols`).

- **Exact-match** (`score_debug`): 1 iff `A == P` as lists. Same
  members, same order, no additions, no omissions, no duplicates.
- **Strict binary TSR** (`tsr`; `score_debug_causal(A, P, X) == 1.0`):
  1 iff both of these hold:
  - `P` is an ordered, not necessarily contiguous, subsequence of `A`;
  - every element of `A` not in `P` is in `X`.

  In words: the whole causal pipeline is recovered in order, and any extra
  symbol named is real and available in context.
- **Partial credit** (`tsr_partial`): `LCS(P, A) / n`, forced to 0 if any
  named symbol is in neither `P` nor `X`.

Binary TSR is therefore exact-match with one relaxation: it accepts
**additional real intermediate symbols from the context**.

### 1.2 Why `prism_full` is 0.572 exact but 0.724 binary, while the oracle is 0.764 on both (gpt-4o-mini, tRPC, 250 cells)

| arm | exact | binary | cells binary=1 but exact=0 | cause of every such cell | mean context size | mean pipeline length |
|---|---|---|---|---|---|---|
| `prism_full` | 0.572 | 0.724 | 38 | answer names ≈1.05 extra real symbols, all from its own context | 4.72 | 3.48 |
| `ablation_signature_only` | 0.576 | 0.796 | 55 | same | 4.76 | 3.48 |
| `baseline_bfs_bidirectional` | 0.600 | 0.644 | 11 | same | 12.08 | 3.48 |
| `pragmatic_oracle` | 0.764 | 0.764 | **0** | — | 3.48 | 3.48 |

**Mechanism:**
- **PRISM's context includes real helpers.** Its Turn-2 context contains
  intermediate helpers the model requested in Turn 1 (for example
  `createNewBuilder` between `procedure` and `createResolver`). The model
  names them as part of the chain.
- **Exact-match scores those answers 0.** It treats a semantically
  valid, more detailed trace as a failure.
- **Binary scores them 1.** The extra names are real and present in
  context.

**Why the oracle is identical under both, necessarily:** the strict
`pragmatic_oracle` context is exactly the pipeline (`X = P`).
- **Extras are impossible in a passing answer.** Any symbol beyond `P` is
  outside the context, so the hallucination gate zeroes it.
- **So binary reduces to exact.** Binary = 1 requires `A` to contain `P`
  in order with no extras, which is `A == P` up to duplicates.
- **The data confirm it:** the 250 oracle cells show zero cells where the
  two disagree.

**Consequence:** exact-match systematically penalizes every arm whose
context is richer than the answer key. It penalizes the oracle not at all.

### 1.3 Where the metric changes an ordering

| model | scope | comparison | exact | binary | partial |
|---|---|---|---|---|---|
| qwen-7B | pooled, 4 corpora | oracle − PRISM | **+0.189** | −0.053 | −0.075 |
| qwen-7B | pooled | scaffolded − pragmatic oracle | **−0.133** | +0.072 | +0.082 |
| qwen-7B | Express | oracle − PRISM | **+0.170** | −0.350 | −0.403 |
| gpt-4o-mini | tRPC | PRISM − baseline | **−0.028** | +0.080 | +0.036 |
| gpt-4o-mini | pooled | PRISM − baseline | **−0.016** | +0.061 | +0.065 |

In every row, exact-match disagrees in sign with binary and partial,
while binary and partial agree. Full tables: `supplementary_triple_scoring.md`.

---

## Task 2: Historical numbers under uniform binary TSR

### 2.1 tRPC `prism_full` failure breakdown (binary)

Every binary failure was classified with a deterministic rule (code:
`classify_failure`), applied in this order:

1. **Unparseable answer:** the answer could not be parsed.
2. **Hallucination gate:** the answer names a symbol that is in neither
   the pipeline nor the context.
3. **Missing stage.** If a stage is missing and was never in the context,
   it is a **Turn-1 retrieval omission**. If it was in the context but not
   named, it is a **Turn-2 generation omission**.
4. **All stages named, wrong order.** If some misordered pair `(a, b)`
   has a call path `a → b` in the static call graph, it is a **Turn-2
   sequencing violation** (the ground-truth order is causally forced).
   Otherwise it is an **annotation artifact**: the stages are causally
   independent siblings, and the key's order is a source-order
   convention, not a dependency.

| category | gpt-4o-mini (69 failures / 250 = **27.6%**) | qwen-7B (77 / 250 = 30.8%) |
|---|---|---|
| Turn-1 retrieval omission (stage never reached context) | 13 (18.8%) | 4 (5.2%) |
| Turn-2 sequencing violation (causally forced order broken) | 7 (10.1%) | 5 (6.5%) |
| Ground-truth / annotation artifact (independent siblings reordered) | 19 (27.5%) | 22 (28.6%) |
| Turn-2 generation omission (stage in context, not named) | **30 (43.5%)** | **45 (58.4%)** |
| Hallucination gate | 0 | 1 |

The brief's three buckets don't cover the largest category. For gpt-4o-mini:
- **In context but not named:** 30 of 69 failures had the required stage
  in context; the model simply did not name it. That is neither retrieval
  nor ordering, so it is reported as its own category.
- **Retrieval is a minority cause:** only 13 of 69 (5.2% of all cells) are
  retrieval omissions.
- **Annotation artifacts:** 19 of 69 fail only because the model ordered
  causally independent sibling calls differently from the key. Example:
  `trpc_t02_001`, where `createTRPCInner` builds `createBuilder`,
  `createMiddlewareFactory`, `createRouterFactory` and
  `createCallerFactory` as independent properties of one object literal.

### 2.2 Express delta under uniform binary TSR

The historical Express cells (the source of +67.5 pp strict-vs-partial and
+43.0 pp partial-vs-partial) were never committed, so they cannot be
re-scored. **Both figures are retracted.** The replacement numbers come
from the sweep:

| model | tasks | ΔTSR binary (PRISM − BFS), 95% cluster CI |
|---|---|---|
| qwen-7B | all 20 | **+0.405 [+0.170, +0.640]**, Holm-adjusted p = 0.002 |
| qwen-7B | 10 internal (no external package) | **−0.050 [−0.200, +0.090]** |
| gpt-4o-mini (933/1,600 cells, 12 tasks paired) | 12 | +0.025 [−0.258, +0.333] |
| gpt-4o-mini, internal only | 9 | −0.122 [−0.322, +0.000] |

The entire Express advantage comes from the 10 external-package tasks,
where BFS never reaches the package symbol (Pitfall B). On
internal-only Express tasks, PRISM shows no advantage.

### 2.3 gpt-4o-mini tRPC taxonomy vs lexical anchors (the "+0.096")

- **The metric is binary.** It was computed on `tsr` (binary) by
  `benchmarks/final_sweep/summary.py`.
- **Its original interval was too narrow.** That interval, [+0.044,
  +0.148], came from a **cell-level** paired bootstrap, which treats the
  10 seeds of a task as independent.
- **Under task-clustered resampling:** **+0.096 [−0.020, +0.228]**, p =
  0.112 (Holm 0.67). It is not significant on its own.
- **The pooled Qwen result stands.** Taxonomy anchors beat lexical anchors
  by +0.142 [+0.078, +0.213] across 90 tasks, and +0.151 [+0.080, +0.225]
  on the clean 80. Holm-adjusted p < 0.001 in both.

### 2.4 Historical runs re-scored from their own cells

Scope: every remote branch was fetched and scanned for checkpoints. Qwen
2.5-Coder 7B was run outside the final sweep **only on Django and FastAPI**
(8 runs, all listed below). No branch holds Qwen-7B (or any other
local-model) runs for tRPC or Express. Those corpora were only run with
gpt-4o-mini before the sweep.

**Every Qwen-7B historical run scored its single-pass arms with the strict
exact-match scorer and PRISM with partial credit.** The reported PRISM − BFS
gains (+0.35 to +0.60) shrink to +0.01 to +0.20 under uniform binary TSR.
None of the uniform-binary CIs excludes zero.

Single-pass cells were re-scored with `score_debug_causal(raw_response,
pipeline, selected_symbols)`; two-pass binary is `tsr == 1.0`. Pipelines
come from the task files at each run's own commit.

Validation of the method: for both Django pilots, whose single-pass cells
were stored causal-scored, re-scoring reproduces the stored means exactly.

| historical run | single-pass scorer as run | Δ as reported (PRISM − BFS, mixed where noted) | Δ uniform partial | **Δ uniform binary** (95% cluster CI) |
|---|---|---|---|---|
| Django qwen-7B smoke (`origin/smoke-qwen7b-progress`) | strict exact-match | +0.573 (mixed scorers) | +0.082 | **+0.100 [−0.083, +0.283]** |
| Django qwen-7B pilot, seeds 43-46 (`origin/pilot-qwen7b-progress`) | strict exact-match | +0.548 (mixed scorers) | +0.068 | **+0.090 [−0.087, +0.273]** |
| Django qwen-7B holdout, seeds 101-102 (`origin/pilot-qwen7b-holdout`) | no baseline arm in this run (single-pass arm was `prism_v11` only) | – | – | – |
| FastAPI qwen-7B smoke (`origin/smoke-fastapi-qwen7b-progress`, 5 tasks) | strict exact-match | +0.603 (mixed scorers) | +0.020 | **+0.200 [−0.267, +0.600]** |
| FastAPI qwen-7B full, seed 42 (`origin/pilot-fastapi-qwen7b-full-progress`) | strict exact-match | +0.371 (mixed scorers) | −0.020 | **+0.013 [−0.093, +0.133]** |
| FastAPI qwen-7B rerun v2 (`origin/pilot-fastapi-qwen7b-full-v2-progress`) | strict exact-match | +0.398 (mixed scorers) | +0.060 | **+0.093 [−0.027, +0.227]** |
| FastAPI qwen-7B rerun v3 (`origin/pilot-fastapi-qwen7b-full-v3-progress`) | strict exact-match | +0.345 (mixed scorers) | +0.006 | **+0.040 [−0.053, +0.147]** |
| FastAPI qwen-7B holdout (seeds 101-103); draft cites +34.47 pp | strict exact-match | +0.345 (mixed scorers) | +0.001 | **+0.027 [−0.076, +0.124]** |
| Django qwen-14B pilot-4-patched; draft cites +7.67 pp | causal | +0.077 (uniform) | +0.077 | **+0.133 [+0.033, +0.267]** |
| Django deepseek-6.7B; draft cites +7.86 pp | causal | +0.079 (uniform) | +0.079 | **+0.030 [−0.183, +0.243]** |
| tRPC gpt-4o-mini; draft cites +33.87 pp | strict (inferred, §3.1 of the investigation) | +0.339 (mixed scorers) | cells not in git | sweep: **+0.080 [−0.024, +0.192]** |

### 2.5 Claims formally withdrawn

| # | Claim (draft `033c68c`, historical debriefs) | Status | Replacement |
|---|---|---|---|
| W1 | ΔTSR "+33.87 to +43.00 pp, CI excluding zero in every corpus" (abstract, gate table) | **Withdrawn** | uniform binary deltas above: significant only for Django qwen-14B and qwen-7B Express/pooled-90 |
| W2 | tRPC ΔTSR +33.87 pp [25.20, 42.67] | **Withdrawn**: scorer asymmetry | +0.080 [−0.024, +0.192] |
| W3 | Express ΔTSR +67.46 pp and +43.00 pp | **Withdrawn**: original cells unavailable; the first figure was scorer-confounded | §2.2 |
| W4 | FastAPI ΔTSR +34.47 pp [28.50, 40.70] | **Withdrawn**: scorer asymmetry (baseline exact-match, PRISM partial) | +0.027 [−0.076, +0.124] |
| W5 | tRPC "ambient scaffolding deficit" / oracle limited to 0.760 by missing scaffolding while PRISM reaches 0.923 | **Withdrawn**: 0.760 was exact-match, 0.923 partial. Uniform binary: oracle 0.764, PRISM 0.724, difference +0.040 [−0.072, +0.148] | no detectable scaffolding deficit on tRPC |
| W6 | "Oracle Inversion Phenomenon" (oracle ≤ naive baseline) as a general finding | **Withdrawn as general**. Under uniform binary the oracle ≥ baseline on FastAPI (0.804 vs 0.747) and Django. It survives only as the Express external-package construction gap (Pitfall B) | Pitfall B |
| W7 | Any gain over unpruned baselines of +34 to +43 pp | **Withdrawn** | pooled 90 tasks: +0.133 [+0.069, +0.200]; clean 80: +0.042 [−0.004, +0.094] |
| W8 | Scaffolding effect significant on all 90 tasks | **Withdrawn after Holm** (p_Holm = 0.082) | conditional finding, Pitfall B |

---

## Task 3: Statistical rigor

### 3.1 Non-inferiority: PRISM vs the unpruned BFS baseline (qwen-7B, 80 clean tasks)

- **Hypotheses.** H0: Δ ≤ −0.05 vs H1: Δ > −0.05, where Δ =
  TSR_binary(`prism_full`) − TSR_binary(`baseline_bfs_bidirectional`),
  task-weighted, paired within task and seed.
- **Margin.** Δ_NI = −0.05 (−5 pp), **chosen post hoc**, after the
  two-sided CI had been seen. It must be reported as such. A confirmatory
  claim needs a pre-registered margin on fresh data.
- **Test.** One-sided, α = 0.025, via the 95% stratified cluster-bootstrap
  CI (10,000 resamples of tasks within corpus). Non-inferiority holds iff
  the CI lower bound is above Δ_NI.
- **Result.** Δ = **+0.042, 95% CI [−0.004, +0.094]**. The lower bound
  −0.004 > −0.05, and the bootstrap one-sided p for Δ ≤ −0.05 is < 0.001.
  **Non-inferior at the −5 pp margin; superiority not established**
  (the CI includes 0).
- **Efficiency, same tasks.** Mean rendered context is **33.4% smaller**
  (95% CI [25.7%, 40.6%]): tRPC 49%, Express 42%, FastAPI 25%, Django 20%.

### 3.2 Named tests and multiplicity

- **Point estimates.** Task-weighted mean of within-task paired
  differences (the same task and seed under both arms, averaged per task).
- **Intervals.** **Paired, stratified cluster (whole-task) percentile
  bootstrap**: resample tasks with replacement within each corpus, keep
  all seeds of a resampled task together, 10,000 resamples, 95% percentile
  interval.
- **p-values.** Two-sided bootstrap p = 2·min(P*(Δ* ≤ 0), P*(Δ* ≥ 0)).
- **Multiplicity.** **Holm–Bonferroni** across the 7 planned contrasts
  within each family (model × task scope × corpus scope).
- **Legacy intervals.** Earlier per-run summaries (`summary.py`) used a
  cell-level bootstrap. They are superseded for all inferential claims.

Primary family (qwen-7B, 90 tasks, binary):

| contrast | Δ | 95% CI | p | p_Holm |
|---|---|---|---|---|
| PRISM − BFS floor | +0.133 | [+0.069, +0.200] | <0.001 | **<0.001** |
| taxonomy − lexical anchors | +0.142 | [+0.078, +0.213] | <0.001 | **<0.001** |
| scaffolded − pragmatic oracle | +0.072 | [+0.013, +0.130] | 0.016 | 0.082 |
| 4-axis − signatures only | −0.018 | [−0.040, +0.003] | 0.096 | 0.384 |
| purity axis | −0.000 | [−0.022, +0.023] | 0.97 | 0.97 |
| no distractors − distractors | +0.016 | [−0.016, +0.046] | 0.32 | 0.65 |
| pragmatic oracle − PRISM | −0.053 | [−0.127, +0.019] | 0.15 | 0.45 |

Clean-80 family: only taxonomy − lexical survives (+0.151, p_Holm <
0.001). The raw p for pragmatic oracle − PRISM is 0.023 (+0.055), which
becomes 0.138 after Holm.

---

## Task 4: Manuscript reframe

### Title

**Right Anchors, Not More Context: Deterministic Taxonomy Anchoring and
Evaluation Pitfalls for Repository-Level Coding Agents**

### Abstract (draft)

Repository-level coding agents depend on which code reaches the model's
context. We study PRISM, a deterministic two-pass retrieval pipeline. Its
first pass anchors retrieval at a taxonomy-derived entry point and lets
the model select from a bounded k-hop manifest. Its second pass hydrates
only the selected symbols.

We evaluate PRISM in a pre-specified 8-arm factorial design across 90
debug tasks from four corpora (tRPC, Express, FastAPI, Django). The design
covers two models: a local 7B model, with 7,200 cells and 10 seeds, and
gpt-4o-mini on a subset. Every arm is scored by the same deterministic
scorer, and uncertainty comes from a whole-task cluster bootstrap with
Holm correction.

- **Anchor choice is the dominant, replicated effect.** Taxonomy anchors
  beat lexical (BM25) anchors by 14 points of task success (+0.142 [+0.078,
  +0.213]). The effect is positive in all four corpora (significant
  pooled; Express alone +0.04, not significant) and in both seed batches.
- **Against an unpruned bidirectional-BFS baseline**, PRISM is
  non-inferior at a −5 pp margin on the 80 tasks without external
  dependencies (+0.042 [−0.004, +0.094]), using 33% less context.
- **The 4-axis semantic annotations, the purity axis and injected
  distractors** show no detectable effect on task success.

We also document three evaluation pitfalls that had inflated our own
earlier results. The largest turned a +8-point effect into a reported
+34-point one. We release the harness, all cell records, and a
re-scoring script.

### Contributions

1. **A controlled 8-arm factorial protocol for context retrieval.** It
   isolates:
   - anchoring (taxonomy vs lexical);
   - manifest annotation (4-axis vs signatures);
   - the purity axis;
   - noise dose (distractors);
   - sufficiency (strict vs scaffolded oracle).

   It is instrumented per cell with sufficiency, truncation, manifest and
   context hashes, and serving metadata.
2. **Evidence that the anchor, not context volume or annotation, drives
   task success.** Taxonomy anchoring gives +0.14 over lexical anchors.
   PRISM is non-inferior to an unpruned baseline at a third less context.
3. **Null results, reported as such.** Four-axis semantic annotations, the
   purity axis and moderate distractor noise do not measurably change
   task success for either model.
4. **A catalogue of three evaluation pitfalls, with quantified impact and
   a re-scoring audit of our own prior claims:**
   - scorer asymmetry;
   - annotation-boundary oracle collapse;
   - exact-match metric sensitivity.
5. **Open artifacts:** 9,000+ scored cells with raw model outputs, the
   harness, and a deterministic re-scoring script.

### Evaluation Pitfalls in Context Retrieval for Coding Agents (section draft)

Context-retrieval systems for coding agents are usually evaluated by
giving a model each arm's context and scoring its answer against an
annotated key. Every step of that loop can manufacture or erase an
effect: the scorer, the key's boundaries, and the strictness of the match.
We report three pitfalls that affected our own earlier results. For each
we give the size of the distortion, measured by re-scoring the saved
model outputs without new model calls.

**Pitfall A: scorer asymmetry across arms.** Our first harness had two
entry points.
- **Single-pass arms** (the BFS baseline and the oracle) were scored by
  default with an exact-match scorer: the answer list must equal the key.
- **The two-pass PRISM arm** was hard-wired to a containment scorer with
  partial credit. It accepts real extra symbols from context and credits
  partially recovered chains.

Both results were reported as "TSR" and subtracted.
- **As reported:** on tRPC with gpt-4o-mini, PRISM − baseline = +33.9 pp
  [25.2, 42.7], comfortably clearing a pre-registered 15-point gate.
- **Re-scored with one scorer for every arm:** the same comparison is
  **+8.0 pp [−2.4, +19.2] under binary containment**, +3.6 pp under
  partial credit, and −2.8 pp under exact-match.
- **Same pattern elsewhere:** the FastAPI figure fell from +34.5 pp to
  +2.7 pp [−7.6, +12.4], and the Express figure was retracted.
- **What made it hard to catch:** the fault never produced an error or a
  suspicious number. Each arm's score was individually plausible, and the
  gate's statistics were computed correctly on inputs that were not
  comparable.

The remedy is procedural. Score every arm through a single scoring call
path, and store raw outputs so the scorer can be changed after the fact.
The paper should report at least two scorer strictnesses, as in our
supplementary triple-scoring table.

**Pitfall B: annotation-boundary oracle collapse.** On 10 Express tasks,
the causal chain leads into an external npm package, for example
`createETagGenerator` → `etag`. The annotators placed the package symbol in
`required_context` rather than in `pipeline_symbols`. A strict oracle
built from `pipeline_symbols` alone therefore omitted it.
- **The model named it anyway:** the call is visible in the seed's body.
- **The scorer rejected it:** the name was not in the oracle's context, so
  the hallucination gate zeroed the answer.
- **Result:** the "ideal" oracle scored **0.06** on these tasks, against
  **0.94** for the same oracle with annotated context added and **0.98**
  for PRISM.

These 10 tasks alone produce the headline "scaffolding helps" effect
(+0.072 pooled; +0.46 on Express). Without them, the effect is −0.034
[−0.074, +0.004], and the pooled effect does not survive Holm correction.
They also account for most of PRISM's advantage over the BFS baseline:
+0.133 on all 90 tasks, but +0.042 on the other 80, where BFS reaches the
same in-repo code.

The lesson concerns the key, not the retrieval. When a key splits
"what must be named" from "what must be available", any arm whose context
follows one field but not the other can fail catastrophically, and the
failure looks like a finding. We now report such tasks as a separate
stratum and test every oracle against the answers it is expected to
accept.

**Pitfall C: metric sensitivity to legitimate detail.** Exact-match
scoring treats a more detailed but correct trace as a failure.
- **What happens:** when an arm's context includes real intermediate
  helpers, the model names them as part of the chain. On tRPC (gpt-4o-mini)
  this happens in 38 of 250 PRISM answers and 55 of 250 signature-only
  answers. Each names about one extra, real, in-context symbol, so each is
  exact-match 0 but binary 1.
- **Why the oracle is immune:** its context is exactly the key, so exact
  and binary scoring coincide for it (0.764 = 0.764).
- **The effect is directional, not noise:**
  - pooled over four corpora, exact-match ranks the oracle **19 points
    above** PRISM, while both containment scorers rank PRISM above the
    oracle (−5.3 and −7.5 points);
  - exact-match also inverts PRISM vs the BFS baseline on tRPC (−2.8 vs
    +8.0).

Containment scoring has its own blind spot. 19 of 69 tRPC binary failures
reorder causally independent sibling calls, which only the key's
source-order convention distinguishes. We therefore report binary
containment as the headline metric, publish exact and partial-credit
scores alongside it, and flag sibling-order failures as annotation
artifacts rather than model errors.

---

## Artifacts

- `scripts/auditor_recompute.py`: all recomputations; deterministic
  (seeded bootstrap), no LLM calls.
- `reports/final_sweep/auditor_numbers.json`: every number above.
- `reports/final_sweep/supplementary_triple_scoring.md`: 8 arms × 4
  corpora × 3 scorers, for both models.
- `reports/final_sweep/investigation_historical_vs_current.md`: the prior
  historical-vs-current investigation.
