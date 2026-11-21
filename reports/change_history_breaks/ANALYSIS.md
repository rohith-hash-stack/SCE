# Run 3 (the break check): analysis

19 frozen tasks from new commits; 32 callers the developer had to change. Model
`qwen2.5-coder:14b-instruct-q8_0`, run on Kaggle at `55534fa`; 57 answers with no errors. Descriptive only:
no significance test. Tokens are counted with the model's own tokenizer.

## Results

| | FILE | PRISM | PRISM_BREAKS |
|---|---|---|---|
| callers found | 7 / 32 | **26 / 32** | **26 / 32** |
| tasks with every caller found | 3 | **15** | 13 |
| wrong functions named | 11 | 3 | **1** |
| answered "nothing to change" | 5 | 3 | 3 |
| prompt tokens (mean) | 2,307 | 4,275 | 4,428 |
| answer tokens (mean) | 334 | 558 | 465 |
| seconds per answer (mean) | 25.3 | 41.6 | 35.5 |

## Reading

- **PRISM against FILE:** the largest gap of the three runs. PRISM found 26 callers against 7, and named
  3 wrong functions against 11.
- **PRISM_BREAKS against PRISM:** a tie on callers found (26 each), but on different tasks:

| where | tasks | effect of the break section |
|---|---|---|
| the check flagged the callers | ch07, ch15 | **+4**: PRISM answered "nothing to change"; PRISM_BREAKS fixed both callers each time |
| the check found nothing and said so ("No call site breaks…") | ch09, ch12, ch13 | **−3**: PRISM found these callers; PRISM_BREAKS left them out |
| the check flagged 2 of 3 callers | ch17 | **−1**: PRISM_BREAKS fixed exactly the 2 flagged callers and dropped the third (`parse_unary`), which PRISM had fixed |

- **The check works when it fires.** Where it stated a break, the model acted on it every time.
- **The model trusts the section too much.** It reads "No call site breaks" as "nothing to change", and it
  reads a list as complete. The check covers only signatures, raised errors and empty returns. In ch09,
  ch12 and ch13 the callers had to change for reasons no signature shows.
- **Smaller effects:** PRISM_BREAKS named fewer wrong functions (1 against 3) and was faster (35.5 s
  against 41.6 s), for about 150 more prompt tokens.

## What this suggests (not yet tested)

Two wording changes, both decided after seeing these results, so they need fresh tasks to test:

1. **When the check finds nothing, leave the section out.** A clean result must not read as an all-clear.
   On the six tasks where the check found nothing (ch02, ch09, ch10, ch12, ch13, ch14), the prompt would
   then be exactly PRISM's prompt.
2. **When it finds breaks, say the list is not complete.** For example: "these calls break for certain;
   other callers may still need changes for other reasons; check section 4".

If both work as intended, the expected result on this set is the 4 gained callers without the 4 lost
(about 29–30 of 32). That is an estimate, not a measurement.

## Decision

- Keep `PRISM` as the default package.
- Revise the break section (the two changes above).
- Test the revised section on fresh commits before making it the default when new code is known.
- Fresh tasks are scarce: runs 1–3 used most of the forced candidates (only one hard commit is left). The
  next test needs newly mined commits, ideally from more repositories. That is also the bigger test.
