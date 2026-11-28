# Phase 2 backlog: PRISM-provided reading and editing (evaluated 2026-11-17; nothing built)

**The idea (user, after the 3-arm smoke test):**
- **`prism_read_file`:** PRISM returns the exact code plus its structural map.
- **`prism_edit_file`:** PRISM applies an edit structurally, so the model need not match whitespace exactly.

**The aim:** stop the agent reading files after PRISM has already pointed at them.

## What the data says

**1. Why the agent reads after a PRISM call.**
- **`prism_ask` returns no code at all,** only ranked names, locations and scores. A read is required before any
  edit. That is the smoke test's case: `prism_ask` on turn 1, then reads.
- **`prism_change_context` does include the target's code, but not in a form `edit_file` can use.**
  - **It drops blank lines.** For `litestar.params.Dependency`, the anchor block omits the empty line inside the
    docstring that the file has. Every non-empty line is verbatim, so an `old` span copied from PRISM that crosses a
    blank line can never match the file.
  - **It has no line numbers,** which `read_file` gives.
  - **It is cut** to the 4,000-token cap, with the callers' code abridged.
- **This is a PRISM output defect,** and fixable: keep blank lines in code blocks, or mark the gaps.

**2. Would PRISM read/edit tools cut runs from about 25 turns to about 10? Not on this evidence.**

| Data (valid harness) | Runs | Failed edits per run | Main failure |
|---|---|---|---|
| Phase 0 | 40 | 1.4 | "text not found" (43 of 54) |
| Step 1e | 24 | 0.8 | "text not found" (16 of 20) |
| 3-arm smoke | 3 | 0.8 | "text not found" |

- **Where the turns go:** mostly exploration (about 14–15 reads and greps per run in both arms), and runs that reach
  the cap unsolved.
- **A structural edit tool** would recover roughly the failed-edit turns, about 1–1.5 per run.
- **A PRISM read tool** would still be a read.
- **What cutting exploration would take:** PRISM must point the agent at the right code from the start. That is what
  the INJECTED arm measures.

**3. A fairness constraint for any of these tools.**
- **The confound:** a whitespace-tolerant or structural `edit_file` given only to the PRISM arm would mix "PRISM's
  knowledge" with "a better edit tool".
- **The fair test:** give it to every arm, or test it as its own factor (edit tool × arm).

## Recommendation

**Run the 3-arm re-baseline as pinned first.** Then, in Phase 2:
1. **Fix the code blocks in `prism_change_context`:** keep blank lines; consider line numbers. This is small and
   PRISM-side.
2. **Measure from the re-baseline's logs** how often failed edits follow a PRISM call, and whether their `old` text
   matches PRISM's blank-line-stripped code.
   - **The limit:** only the first 300 characters of each output are logged. A diagnostic run with full output
     recording would settle it.
3. **If failed edits matter,** build a structural or whitespace-tolerant edit tool and test it as a separate factor
   across all arms.
