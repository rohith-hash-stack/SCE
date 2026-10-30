# Round R1: is it PRISM's context, or just a longer prompt? (pre-registered before any padded prompt was built)

The suite (`../suite/README.md`, results in `reports/suite/REPORT.md`) found three wins for PRISM: forward
(callers updated), blast (callers named) and newfn (helpers reused). In each, PRISM's prompt was also longer:

| type | baseline prompt | PRISM prompt |
|---|---|---|
| forward | FILE 2,270 tokens | 4,243 |
| blast | GREP 2,356 | 5,443 |
| newfn | FILE 2,174 | 6,144 |

R1 asks whether the wins survive when the baseline gets the same number of tokens of ordinary code, picked without
PRISM's call graph. It also re-runs the existing prompts with two more seeds.

## What is fixed

- **The tasks and PRISM's contexts are the suite's frozen ones** (`../suite/repos/*/{forward,blast,newfn}.json`),
  unchanged. They were built from the code at `793c40a`. Nothing in R1 changes PRISM.
- **This tests the existing claims on the existing tasks.** It is not evidence that PRISM generalises; that needs
  fresh repositories (the next round).
- Model and settings as in the suite: `qwen2.5-coder:14b-instruct-q8_0`, temperature 0.2, a 20,480-token window,
  answers capped at 4,096 tokens. Seeds 42, 1001 and 2002.

## The padded versions

Each padded prompt is the baseline's prompt plus one block of code from the repository at the task's revision,
filled until the whole prompt has PRISM's real token count for that task (Qwen2.5 tokenizer), within ±5%.

| version | type | base | padding |
|---|---|---|---|
| `FILE_BM25PAD` | forward, newfn | FILE | the functions a BM25 search over the repository's functions ranks highest |
| `FILE_GREPPAD` | forward | FILE | every production line mentioning the changed function's name, then the code of the functions those lines are in, then BM25 functions if there is room |
| `GREP_PAD` | blast | GREP (its grep lines) | the code of the functions those lines are in, then BM25 functions if there is room |

- **BM25 query:** forward: the changed function's name and its new code. newfn: the new function's signature and
  description.
- **Shown code:** each function as in the bugs type's BM25 version: its name and location, then up to 60 lines.
- **Excluded:** the changed or anchor function itself, and functions already visible in the file window.
- **What the padding never uses:** the call graph, PRISM's contexts or the answer key. Function boundaries come from
  the parser.
- **Filling:** whole functions in rank order while they fit, then the first lines of the next one to reach the target.
- **When the base prompt is already at least 95% of PRISM's size:** no padding; the task is counted and reported.
- **Audit:** the builder (`pad.py`) records every padded prompt's real token count and the share within ±5%.

## Runs

| type | versions | seeds |
|---|---|---|
| forward | FILE, PRISM, FILE_BM25PAD, FILE_GREPPAD | 42, 1001, 2002 |
| blast | GREP, PRISM, GREP_PAD | 42, 1001, 2002 |
| newfn | FILE, PRISM, FILE_BM25PAD | 42, 1001, 2002 |

- Seed 42 for FILE, PRISM and GREP is the suite's existing answers. They are not run again, except a
  determinism check.
- **Determinism check:** 5 tasks per type, drawn with seed 5501, base and PRISM versions, run again with seed 42
  (30 calls). This counts identical answers. If they differ, seed effects are reported as run-to-run variation.
- **Order:** the padded versions at seed 42 come first, then the determinism check, then seed 1001, then seed 2002.
  A partial run still answers the main question.

## Tests and decisions

**Primary tests (family C, Holm-corrected together, α = 0.05, two-sided):**

| id | type | comparison | measure | test |
|---|---|---|---|---|
| H-TM1 | forward | PRISM vs FILE_GREPPAD | share of changed callers found per task | Wilcoxon signed-rank |
| H-TM2 | blast | PRISM vs GREP_PAD | traced-caller recall per task | Wilcoxon signed-rank |
| H-TM3 | newfn | PRISM vs FILE_BM25PAD | helper reuse per task | Wilcoxon signed-rank |

- **Per-task value:** the mean over the seeds run. If a seed is incomplete, only seed 42 is used, and the report says so.
- **Interval:** bootstrap 95% over tasks, 10,000 resamples, seed 5501.
- **"PRISM's context helps" holds for a type only if all of these hold:**
  1. Holm-adjusted p < 0.05;
  2. the interval's lower end is above 0;
  3. the difference is at least the floor: forward +0.10, blast +0.05, newfn +0.05;
  4. for newfn, PRISM's invented calls per task are no more than FILE_BM25PAD's + 0.05.
- **If it fails:** that type's win is reported as explained by prompt size (or as unresolved, if the difference is
  positive but below the floor or not significant). Nothing is tuned and re-run on these tasks.

**Secondary:**
- forward PRISM vs FILE_BM25PAD; each padded version vs its unpadded base (does more code alone help?);
- each primary per seed (McNemar over callers for forward);
- wrong functions per task (forward), functions named (blast), parse rate with the outer fence removed (newfn);
- results per language.

**Validity:** truncation per version as in the suite's analysis. A comparison is invalid when a version cuts more
than 10% of its answers or the two differ by more than 5 points. It is then also reported without the cut tasks.

## Files

- `pad.py`: builds `pads/<type>/<repo>.json`. It is resumable and commits per repository.
- `run.py`: generates and scores the answers (`reports/r1/<repo>/<type>/answers.json`; keys `<task>/<version>@<seed>`).
- `analyze.py`: writes `reports/r1/RESULTS.json`.
- `kaggle/r1_cell.py`: the Kaggle cell. It checks the pads' sha256 (`pads/manifest.json`) before any model call.
