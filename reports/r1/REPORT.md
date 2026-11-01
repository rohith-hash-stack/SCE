# R1 results: is it PRISM's context, or just a longer prompt?

The design is in `harness/experiments/r1/README.md`. The analysis rules are Amendment R1-A (`reports/suite/REPORT.md`,
commit `feac1f4`, made before any R1 answer was scored). Numbers come from `RESULTS.json`
(`python -m harness.experiments.r1.analyze`).

**Setup**
- 3,378 new model calls, with no errors, plus the suite's 792 seed-42 answers: 4,170 answers in all.
- Seeds 42, 1001 and 2002, all complete.
- The cell was pinned to `b0ebcc9`.
- Each task's value is the mean over the three seeds.
- Primary tests exclude tasks where either version was cut off. The all-tasks result is shown beside each.

## Outcome (pre-registered labels)

| type | padded baseline | baseline | PRISM | difference [95% CI] | Holm p | label |
|---|---|---|---|---|---|---|
| forward: changed callers found | FILE_GREPPAD | 0.471 | **0.653** | +0.181 [+0.124, +0.240] | 1.5e-7 | **pass** |
| blast: traced-caller recall | GREP_PAD | 0.195 | 0.292 | +0.097 [+0.026, +0.170] | 0.059 | **not shown** |
| newfn: helper reuse | FILE_BM25PAD | 0.709 | 0.703 | −0.007 [−0.046, +0.033] | 0.99 | **not shown** |

- **forward passes.** PRISM's change context beats an equal-size prompt of grep lines plus the callers' code.
  That padded prompt held 97% of the callers' code (Amendment R1-A, A0). So the gain comes from how PRISM organises
  the callers, not from showing callers the baseline could not see.
- **blast is not shown.** The difference is above the floor and its interval is above 0, but it misses
  significance after Holm (raw p = 0.030, adjusted 0.059). By the committed rule, blast's suite win counts as not
  shown against an equal-size baseline.
- **newfn is not shown.** The equal-size keyword-search prompt gives the same helper reuse as PRISM. The suite's
  newfn gain (0.59 → 0.71) is explained by prompt size.

## forward

| | FILE | FILE_BM25PAD | FILE_GREPPAD | PRISM |
|---|---|---|---|---|
| callers found per task | 0.289 | 0.325 | 0.468 | **0.655** |
| tasks with every caller found | 25% | 30% | 43% | **64%** |
| wrong functions named per task | 0.96 | 2.10 | 1.45 | **0.51** |

- **Every seed:** +0.22, +0.17 and +0.17, each with p < 3e-5.
- **Both languages:** Python +0.19 (0.59 → 0.78) and JavaScript +0.19 (0.26 → 0.45).
- **By tier** (PRISM against FILE_GREPPAD; secondary, not corrected for multiple tests):
  - easy (97 tasks): 0.43 → 0.60 (+0.17)
  - medium (64 tasks): 0.55 → 0.68 (+0.13, p = 0.016)
  - hard (29 tasks): 0.43 → 0.77 (+0.34)
- **Medium is where grep padding helps most:** FILE alone gets 0.03, grep padding 0.55. The callers are in other
  files but name the changed function.
- **Extra code with no structure makes the model name more wrong functions:** 2.10 with keyword-search padding, 1.45
  with grep padding. PRISM halves FILE's count (0.96 → 0.51).
- **Callers hidden from the grep prompt** (exploratory, 9 callers): PRISM finds 0.70, the grep version 0. Nine items
  give this no weight.
- **Validity:** 2 tasks excluded (cut-off answers 0.3% vs 0%). The all-tasks result is the same (+0.187).

## blast

| | GREP | GREP_PAD | PRISM |
|---|---|---|---|
| traced-caller recall | 0.114 | 0.197 | **0.299** |
| direct callers | 0.41 | **0.73** | 0.64 |
| functions named | 6.8 | 8.7 | 20.4 |
| precision against traced callers (a lower bound) | 0.31 | **0.43** | 0.41 |
| recall within the first 10 names | 0.105 | 0.188 | 0.236 |

- **The difference is the same at every seed:** +0.10, p = 0.010, 0.021 and 0.021. Only Holm's correction across
  the three types puts it over 0.05.
- **At matched length** (k = the shorter answer): GREP_PAD 0.152, PRISM 0.193. The difference is +0.041
  [+0.005, +0.080], p = 0.35.
  - Within the first 5, 8 and 10 names, the differences are +0.01 to +0.04, none significant.
  - **Most of PRISM's recall advantage comes from naming more functions** (20 against 9), not from ranking better.
- **Direct callers favour the padded version** (0.73 against 0.64). It shows their code.
- **Exploratory (A3 allows this one claim, labelled exploratory):** on the 3,450 traced callers the padded prompt
  does not show, PRISM names 0.271, the padded version 0.103 (p = 4.6e-5). This is mainly the transitive callers:
  PRISM's context lists them, and grep cannot find them.
- **Validity:** 9 tasks excluded (5.3% vs 3.1% cut). With all 76 tasks the difference is +0.101, p = 0.016 before
  correction.

## newfn

| | FILE | FILE_BM25PAD | PRISM |
|---|---|---|---|
| helper reuse | 0.588 | 0.709 | 0.703 |
| invented calls per task | 0.21 | **0.21** | 0.29 |
| parses (strict) | 94.5% | 88.5% | 89.6% |
| parses, outer fence removed | 97.1% | 96.4% | 97.9% |

- **Keyword-search code at PRISM's size raises reuse exactly as much as PRISM** (+0.12 over FILE, p = 1e-5).
- **Invented calls are PRISM's own cost:** the padded version keeps FILE's 0.21, PRISM has 0.29. The difference is
  +0.076 [−0.003, +0.174], so it is unresolved.
- **Helpers hidden from the padded prompt** (exploratory, 159 helpers): 0.59 against 0.60, no difference.
- No task was excluded; the same reuse holds for every seed and both languages.

## Repeatability

- Re-running 30 seed-42 prompts gave the identical answer 22 times: forward 7/10, blast 7/10, newfn 8/10.
- The model is not exactly deterministic on this setup, so the seed results measure run-to-run variation, not seed
  alone.
- The primaries hold at each seed separately.

## What this means (Amendment R1-A, A4)

> **forward passes:** on these 10 repositories, with this model, PRISM's change context is more useful per token
> than an equal-size prompt of grep results with the callers' code. The callers' code was already in that prompt,
> so this shows that PRISM's organisation helps (who depends on it, per-call-site facts). It does not show that
> PRISM finds callers that text search cannot.
>
> **blast and newfn:** no advantage over an equal-size baseline was shown.
> - For newfn, the suite's reuse gain is explained by prompt size, and PRISM's extra invented calls remain.
> - For blast, PRISM's advantage is in naming more of the transitive callers. That is reported as exploratory.

None of this shows that the results hold on other repositories or other models, or which section of the context
does the work. Those need held-out repositories, a second model, and the R2 ablation.

**What follows, by the committed rules:**
- R2 (section ablation) enters for **forward** only, the one type labelled `pass` or `below_floor`.
- PRISM's search ranking, the newfn helper list and the blast list are not re-tuned on these tasks.
