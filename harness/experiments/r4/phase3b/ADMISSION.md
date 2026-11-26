# Phase 3b, before the amendment: POC 2 reconciliation and recovery-task admission

**Run:** 2026-11-26. No code, prompt, task file or historical result was changed.
- **Code:** the harness and `src/` are unchanged since `d769d27c`.
- **Working files:** the clones, a scratch task file with the approved turn correction, and the dry-run output are
  in the session scratchpad only.

## 1. POC 2 counts, recalculated from the 45 score files

**Source:** `reports/r4/phase3_poc2_seed{201,202,203}/*.score.json`, 15 per arm, each read as `score.resolved`.

| Arm | Resolved | Runs |
|---|---|---|
| BASELINE_LSP | 5 | 15 |
| PRISM_LSP | 9 | 15 |
| **PRISM_GW** | **13** | 15 |

**Cross-checks:**
- **The ledger** (`reports/r4/phase3_poc2_ledger.md`) agrees row by row: 45 of 45 runs, 0 mismatches.
- **The report** (`reports/r4/phase3_poc2_report.json`) agrees: `solve_rates`, and `per_task` (PRISM_GW: hidden-06
  3, hidden-13 3, hidden-15 1, hidden-16 3, delete-09 3).

**The source of the 12: an error in `FINAL_REPORT.md` (`e501e8cf`).**
- **The error:** the verdict paragraph says PRISM_GW solved p3-hidden-06, -13 and -16 "in 8 of 9 runs". The score
  files give **9 of 9**.
- **How it produces 12:** counting 8 + 1 (hidden-15) + 3 (delete-09) gives the 12.
- **No other number in the report is affected:**
  - its tables (10 of 12 hidden-task runs; +8; McNemar 9 against 1, and 7 against 1 collapsed) are computed from the
    score files and are right;
  - the per-run outcomes are unaffected.
- **The correction (not applied; awaiting approval):** an erratum commit changing "8 of 9" to "9 of 9", with a note
  saying what was wrong and when it was found. The historical score files and the report JSON are untouched.

## 2. Recovery-task admission (POC 2 configuration)

**Checks run on the current code:**
1. **The scoring check** (`derive_scoring_check`, in a fresh worktree with the repository's environment): the gold
   tree resolves and the gate accepts it; the unchanged start does not resolve and the gate rejects it.
2. **POC 2's dry run** (`phase2_dryrun --guards`, the real runner):
   - the gold replay through BASELINE_LSP, PRISM_LSP and PRISM_GW must end `resolved`;
   - the empty script must end unresolved or stuck.

**Inputs:** both tasks carry the approved turn correction, and nothing else differs. Checked field by field: only
`prompt` differs from the source task, and only at "28" → "60".

### p3-hidden-03 (werkzeug): **admitted**

| Check | Result |
|---|---|
| Scoring check | gold resolved and accepted; empty unresolved and rejected |
| Gold replay, BASELINE_LSP | resolved (23 turns) |
| Gold replay, PRISM_LSP | resolved (21 turns) |
| Gold replay, PRISM_GW | resolved (20 turns) |
| PRISM_GW's gateway on gold | target inferred (`Range.from_header`); 17 places; 0 blocks; 0 pending at the end |
| Empty script | stuck at the done gate (as required) |

### p2-rename-04 (pydantic-ai): **fails; excluded** (the user's rule: no replacement task is mined)

| Check | Result |
|---|---|
| Scoring check | passes (gold resolved and accepted; empty unresolved and rejected) |
| Gold replay, BASELINE_LSP | resolved |
| Gold replay, PRISM_LSP | resolved |
| **Gold replay, PRISM_GW (arm A)** | **`stuck_done`**: the gateway refused 16 of the gold edits, and 4 uses were left |
| Empty script | stuck at the done gate |

**Why it fails, on two independent grounds:**

**1. The answer key renames code that is not the target.**
- **What it renames:** `soup.find()` in `docs/.hooks/algolia.py` (BeautifulSoup), and three `content.find(...)` calls
  in `pydantic_ai_slim/pydantic_ai/_thinking_part.py` (`str.find`). It renames them to `find_impl`, which breaks that
  code.
- **Why it scores resolved anyway:** no scored test runs those lines.
- **The prompt matches it:** it says the check is that "no code still uses the old name `find`". Unlike the Phase 3
  rename tasks, it has no "leave other objects of the same name unchanged".

**2. Arm A cannot complete it.**
- **What happened:** PRISM resolves 5 references, in 1 file. The 13 test calls `tree.find(...)` in
  `tests/evals/test_otel.py` are genuine uses of `SpanTree.find`, but PRISM cannot determine `tree`'s type.
- **The effect:** the gateway refuses those edits as look-alikes. It also refuses the 4 wrong renames above (that
  refusal is correct).
- **Consequence:** the correct answer is blocked in arm A, and the task would measure that block rather than
  whitespace recovery.

**Recorded as a finding (outside Phase 3b; nothing changed):**
- **What it shows:** where PRISM cannot type a receiver, the gateway's look-alike block can refuse genuine references.
- **Prior exposure:** POC 2's tasks did not expose this (0 look-alike blocks in 15 PRISM_GW runs).
- **Where it matters:** it is a risk for Phase 4.
- **A second, small point:** the block message also prints the line stripped (`docs/.hooks/algolia.py:51:
  first_element = soup.find()`), one of the gateway messages E covers.

## 3. Effect on the approved allocation

**Without p2-rename-04:**

| Task | Seeds | Arms | Runs |
|---|---|---|---|
| p3-hidden-15 | 10 | 4 | 40 |
| p3-hidden-03 | 5 | 4 | 20 |
| **Total** | | | **60 runs, about 9.5 GPU hours** |

**Alternative, for the user's decision: keep the approved 80 runs, and give the 20 freed runs to the primary task.**
- **The allocation:** p3-hidden-15 at 15 seeds per arm (60 runs), with p3-hidden-03 unchanged (20).
- **Simulated power** of the single test (two-sided Fisher, α = 0.05):

| True failure rate, A → E | 10 seeds per arm | 15 seeds per arm |
|---|---|---|
| 0.67 → 0.10 | 0.59 | 0.85 |
| 0.67 → 0.20 | 0.36 | 0.61 |
| 0.50 → 0.10 | 0.29 | 0.51 |
| 0.67 → 0.33 | 0.17 | 0.30 |

- **Caution:** A's rate (0.67) is estimated from 6 POC 2 runs, so these figures are rough.
