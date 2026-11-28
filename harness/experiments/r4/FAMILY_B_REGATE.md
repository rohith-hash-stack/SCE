# Family B re-gated: R3a's 80 tasks under R4's three gates (2026-11-10)

**No new runs:** this applies three gates to existing data.
- **The start-state test output** for G1: `r3/inv3/start_class.json`, with the full re-run for the 18 "all pass"
  tasks.
- **The two-direction scope analysis** for G2 and the scope gate: `r4/scope_inv/final.json`.

| Gate | Rule | Tasks passing (of 80) |
|---|---|---|
| **G1, signal** | at least one visible test fails at the start | 57 |
| **G2, validity** | the developer's commit is a caller update: every changed function is a caller of the anchor (no callees, no other functions in the anchor's file, no unrelated or new functions) | 12 |
| **Scope** | bounded (the developer's callers within 2 hops) and upstream-clean (no undefined call, wrong-arity call, changed callee or removed callee; the known false-positive classes excluded) | 54 upstream-clean; 74 bounded |
| G2 + scope | | 10 |
| **All three** | | **4** |

**Survivors:**

| Task | Repo | Anchor change | Gold |
|---|---|---|---|
| r3a-B-13 | packaging | `markers._eval_op` | `_evaluate_markers`, hop 1 |
| r3a-B-19 | execa | `return/early-error.handleDummyPromise` | `handleEarlyError`, and a hop-2 caller |
| r3a-B-36 | execa | `io/output-sync.writeToFiles` | `transformOutputResultSync`, hop 1 |
| r3a-B-62 | typer | `get_default_option_flag_name` drops `metavar` | 1 caller (`get_click_param`), hop 1 |

- **Close misses:**
  - **B-54 (poetry):** G1 and G2 pass, but upstream fails: `peel_sha` is defined nowhere at the start.
  - **B-61 (sqlglot):** G1 and G2 pass, but the developer's callers reach hop 3.
- **The honest Family B pool from R3a's 80 is 4 tasks.** It is recorded as such, not forced (`ROADMAP_R4_R6.md` §4a:
  a small Family B is an expected outcome).

**Caveats:**
- **G2 depends on PRISM's caller graph.** The first golden recording found that some src-layout Python imports are not
  resolved (`tests/golden/KNOWN_DEFECTS.md` K4), and packaging's caller lists never leave one file.
  - **The risk:** where a caller in another file is missed, the developer's edit to it is classed as "unrelated", and
    G2 rejects a valid task.
  - **The direction:** that can only undercount.
  - **The remedy:** re-apply the gates after the K4 investigation and the A1–A3 fixes.
- **B-06's G1** comes from the Kaggle log's head (all visible tests passing). Its suite timed out in the sandbox.
- **More Family B candidates** can come from R3's full mined pool (`r3/r3a_tasks/candidates_B.json`), not only the 80
  that were gated. They would be gated with G1, G2 and the scope check together. Not done here: the instruction was
  R3a's 80.

## After the K4 fix (2026-11-10)

- **The re-check:** R3a's poetry and packaging tasks (B-11, B-13, B-27, B-29, B-52, B-54, B-66, B-71, B-72, B-75)
  were re-run through the scope analysis with the fixed caller graph. They are the only R3a tasks K4 could affect:
  packaging was confirmed unaffected, and the other repositories keep their packages at the root.
- **The result: no change.** Every task's relations are identical before and after.
- **Family B stays at 4.** That is below the sanity floor of 5, so Family B is not sanity-checked and stays
  directional from the start (the user's rule, 2026-11-10).
