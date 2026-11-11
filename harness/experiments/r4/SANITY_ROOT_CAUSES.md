# R4 sanity: root causes (2026-11-11)

**Sources:** the 40 runs in `reports/r4/sanity/`: turn logs (`*.jsonl`), scores, and the task files. No re-runs and no
code changes.

## The hypothesis tested

**The hypothesis (user):** after the first edit, the harness sends upstream/downstream node and edge information. The
model does not know what to do with it, and fails.

**The evidence says no:**
- **Nothing is sent after an edit.**
  - `notice_turns` is 0 in all 40 runs.
  - `prism_check_changes` is not built.
  - A successful edit returns one line: "edited FILE at line N: replaced a lines with b".
- **The failures come before any edit succeeds.** 154 of the 223 failed `edit_file` calls happen before the run's
  first applied edit, and 14 runs never apply an edit at all.
- **BASELINE fails the same way.** It gets no PRISM node or edge information, yet it has 77 failed edits before its
  first applied edit; PRISM has 77 too.

**The spirit of the hypothesis holds:** the model fails where it is not told explicitly what to do. That happens in
the task message and in the tools' replies, not in graph information.

## Turn sequences (one letter per turn)

`r` read, `g` grep, `l` list, `P` a PRISM tool, `k` related_code, `T` run_tests, `D` done, `E` applied edit, `x` edit
"not found", `d` edit "occurs N times", `c` edit "file exists" (an empty `old` on an existing file).

```
delete-01  B rgD   P rgD                      delete-06  B rgD   P rgD
delete-02  B rxgxEdxgxdxD   P rgxgEdxgdxD     delete-09  B rdrdrdrdrdrdrdrdrdrdrdrdr   P rgdxxxxxxxxxxxxxxxxxxxxxxxxx
feature-02 B rggD   P rgrcxcxccccccccccccccccccccc
feature-04 B rlllrxgD   P rllllrrrPPPPPPPPPPPPPPPPPPPP
feature-07 B gddxxxxxxxxxxxxxxxxxxxxxxxxx   P gPD
feature-09 B rllrcrcrcrcrcrcrcrcrcrcrcr   P PPcrEPcrETPrcrccccccccc
```
(all 40: `reports/r4/sanity/*.jsonl`)

## Root causes, by runs affected

| # | Root cause | Where | Runs | Evidence |
|---|---|---|---|---|
| R1 | **The delete prompt names the target by its index name**, not as it is written. It asks to remove `object.shape` or `GenerateJsonSchema._generate_definitions`. The model greps that literal string, finds nothing, and calls done ("No matches found… No changes were made.") | task message (`sanity_tasks.prompt_delete`) | 4 (delete-01, -06, both arms) | `_generate_definitions` is at line 375 of a 3,265-line file; zod's `shape` at line 1100 of 2,205 |
| R2 | **A cut `read_file` claims to be whole.** With no range, a long file is cut at 4,000 tokens, but its header still says "(lines 1-3265 of 3265)". The marker "... [cut at 4,000 tokens]" says neither where it stopped nor how to read on. The model believes it has seen the file, never sees the target region, and writes edits from memory | tool output (`shared_tools._read_file` + the cap) | most of the edit failures | of 98 "not found" edits, 47 were in a file never read; 38 more start with a line that is not in the file; 15 differ only in whitespace |
| R3 | **Edit errors give no way to recover.** "not found" shows no nearest match; "occurs 3 times" shows no line numbers; "FILE exists: give the exact text" answers an attempt to rewrite the whole file (`old` empty, 82 times). The model repeats the same call | tool output (`_edit_file`) | 14 loop or stuck runs | 223 of 279 edit calls failed; repeats of one call up to 27 times |
| R4 | **"Done" is accepted with failing tests.** Nothing tells the model to check its change, or to keep going while a test fails | task message / loop | ~8 | delete-03, both arms: removes the class, sees `run_tests` fail on an import, calls done. delete-02: done with 58 tests broken |
| R5 | **The feature prompt is misread as "check the tests".** "Tests … are already in the working tree and fail now" | task message (`prompt_feature`) | ~4 | feature-02 B: "The tests … are already present and fail as expected." Done after 4 turns |
| R6 | **The delete scorer** (`SANITY_RESULTS.md` finding 1): the developer's test diff does not apply over the agent's test edits | scorer | 5 | "hidden tests do not apply" |
| R7 | **PRISM's "symbol not found" gives no hint.** In a feature task the function is new, so there is nothing to look up. The model guesses names and retries | PRISM tool output | 1–2 (PRISM only) | feature-04: 20 calls, 3 guessed names, every one "not found" |
| R8 | **The window and the budget.** Long files fill the 20k window; earlier reads are stubbed, and the model re-reads the same files | loop window | 4 (budget endings) | feature-01 P: `context.ts` and `streaming.ts` read 6 times each; 17 outputs stubbed |

**R1–R5 are explicitness gaps.** R1, R4 and R5 are in the task message; R2 and R3 are in the tool replies. Fixing them
changes what both arms see identically. **R6** is a scoring bug. **R7** is PRISM's own (it belongs with the
`prism_check_changes` and tool-message work). **R8** is the agreed compaction rule's subject (`COMPACTION_DESIGN.md`).

## The fixes this points to (none made yet)

| Fix | Arms | For |
|---|---|---|
| Delete prompt: the bare name, its kind and its line ("the method `_generate_definitions` of class `GenerateJsonSchema`, `pydantic/json_schema.py` line 375") | both | R1 |
| A cut read says so and how to continue: "lines 1-212 of 3265 shown (cut at 4,000 tokens); read on with start=213" | both | R2 |
| Edit errors that help: "not found" → the closest matching lines with their numbers; "occurs N times" → the line numbers; an empty `old` on an existing file → "to change part of it, give the exact lines to replace; read them first" | both | R3 |
| An explicit workflow in the task message: read the target, edit, run the tests, fix failures, then done; "do not call done while a listed test fails" | both | R4, R5 |
| Feature prompt: "Change the source code so these tests pass. Do not edit the tests." | both | R5 |
| Delete scoring: restore the hidden patch's files to the start state before applying it | both | R6 |
| PRISM "not found": the nearest symbols by name, or "this symbol does not exist yet: for a new function use `prism_new_function_context`" | PRISM | R7 |

R1, R2, R4 and R5 are what "ask explicitly" means here: say what to do and where, and say what was not shown.
