# PRISM's gateway on every family of the breadth study (2026-11-28)

**Status:** implemented and tested, revalidated on all 32 tasks (section 4). Approved by the user to proceed: "Extend
and validate the gateway, correct and revalidate the six prompts, then present the updated task list and experiment
details for review before the pilot and freeze."

## 1. Why

The breadth study's admission (`TASKS.md`, finding 1) showed that PRISM's gateway tracked places on rename tasks only.
On signature, move and delete tasks it made the syntax check alone ("not a rename task: only the syntax check
applies"). So on 24 of the 32 tasks, the PRISM arm had no worklist, no stall notes and no `done` block. Without this
change, the study could not measure "PRISM's tools and its gateway".

## 2. What changed

**`prism.gateway.Gateway(..., families=True)`,** or `enable_families(prompt)` after construction. The harness flag is
`--gateway-families` (`r3/runner.py`, `r3/pilot_run.py`, `r4/phase2_dryrun.py`).

**Off by default:**
- POC 2's and Phase 3b's gateway is unchanged; their tests pass as before (`tests/test_prism_gateway.py`,
  `tests/test_phase3b.py`).
- The runner's Phase 3b construction lines are kept verbatim, and the switch is applied after them.

**What each family tracks:**

| Family | Recognised by | A place | Pending while | Look-alikes |
|---|---|---|---|---|
| rename | "Rename ..." | unchanged | unchanged | unchanged |
| delete | "Remove ..." | each line PRISM lists as a use of the target (`prism_find_references`' rows, the definition included; name-only matches excluded), as for a rename | the line still holds the name more often than its look-alike uses | tracked and protected, as for a rename (`block_lookalike`) |
| signature | "Make the parameter `P` of ... keyword-only" | the definition; each call site PRISM resolves to the target (name-only matches excluded) that passes `P` positionally at the start | the definition still takes `P` positionally; a call to the name on that line still passes more positional arguments than come before `P` (no `*args`) | none: the edit keeps the name |
| move | "Move the function `N` from `OLD` ... into a new module `NEW`" | the definition in `OLD`; each `from OLD import N` and `OLD.N` through `import OLD`, in any file with the word `N`; `N` defined in `NEW` | `OLD` still defines `N` at its top level; the line still imports `N` from `OLD`; `NEW` does not define `N` yet | none |

**Shared with the rename gateway, unchanged:** the interventions (`suggest_tool`, `search_augment`, `wrong_target`,
`worklist`, `stall`, `block_syntax`, `block_done`, `block_lookalike`), their outcomes, the `"confirm": true` override,
and `finish`.

**How the checks work:**
- Signature and move places are checked by parsing the file (`ast`), never by the name's text.
- The helpers mirror the derived tasks' static check (`r4/derived_checks.py`: `positional_calls`, `old_imports`).
  They are not imported from it: the gateway has no harness code.

**What the model is told:** the worklist and the `done` block say what is pending in each family's terms. For example:
- signature: "3 of 5 place(s) still take or pass `name` positionally for `arrow.locales.get_locale`";
- move: "... still define or import `raise_error` from `src/cattrs/fns.py`, or the new module
  `src/cattrs/fns_raise_error.py` does not define it yet".

**The exact-text note** (arm C, Phase 3b) is a rename's alone: it knows only how to rename a line. On other families,
PRISM_GW_C is PRISM_GW with families on.

## 3. Unit tests

`tests/test_gateway_families.py`, 12 tests:
- the switch off is as before;
- `enable_families` equals the constructor switch;
- a rename is unchanged by the switch;
- per family: the start state, the worklist count, the `done` block and its release;
- a look-alike edit blocked on a delete;
- a method's `self` skipped;
- name-only matches ignored;
- a call moved by an edit followed;
- relative imports;
- the syntax block and overrides in every family;
- no exact-text note outside renames.

Run with the related suites: 89 passed.

## 4. Validation on the 32 tasks

Each task through the real runner (`phase2_dryrun --guards --edit-fix --empty-every-arm --all-families
--gateway-families --partial`), in BASELINE_LSP and PRISM_GW_C, with three scripts:

| Script | Must |
|---|---|
| **gold:** the answer key, through `edit_file` | resolve, with no gateway block of a gold edit (`prevented_edits` 0) and no `done` block (`prevented_tool_calls` 0): the gateway's places all done when the gold change is |
| **empty:** `done` at once | not resolve: stuck at the done gate (baseline) or blocked by the gateway (PRISM) |
| **partial** (PRISM only): the answer key in the first half of its files (sorted), then `done` | be blocked by the gateway, listing no place in a file the script finished: no false pending place |

Results: section 5.
