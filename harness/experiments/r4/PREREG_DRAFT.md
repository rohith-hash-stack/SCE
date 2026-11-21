# R4 pre-registration: draft (entries recorded as they are decided; frozen and hash-committed before R4's full run)

**Scope:**
- **This document pre-registers R4's full run only.** That is the comparative run at scale: the winning PRISM
  configuration against the baseline, at the full task count.
- **Sanity 1 and Sanity 2 are development iterations, not pre-registered experiments** (`ROADMAP_R4_R6.md` §4a).
  - They have no pass or fail criteria. They are observed, diagnosed, fixed and re-run.
  - They may change PRISM, the compaction design, and anything else the protocol below leaves open.
  - What R4 runs is frozen here only after Sanity 2.
- **R4's thresholds and analysis** are written here before R4 starts, as R3's were (`r3/README.md`, `PREREG.json`).
  They are not set by the sanity phases' numbers.

## 1. Task families, templates, done criteria and signal gates

`ROADMAP_R4_R6.md` §2a (the user's decision, 2026-11-10), as implemented in `GATES.md`. The pre-flight scope check
(`SCOPE_INVESTIGATION.md`; `CHECK_CHANGES_DESIGN.md` §1, the gate's consumer) is part of task selection.

## 2. Protocol: the post-edit check with a boolean gate (decided 2026-11-10)

1. **When it runs:** after every `edit_file`, in both arms, the harness runs the check on the working tree against
   `HEAD`. Always.
2. **The boolean:** is any downstream or upstream issue found?

| Result | Sent after the edit's own result |
|---|---|
| no issue | `[check: clean]` (one line) |
| warnings only (dead code; a removed callee with no other callers) | the `## Warnings` section only |
| any downstream break, internal or verified external upstream mismatch, or undefined call | the full four-section feedback (Downstream; Upstream — internal; Upstream — external (do NOT edit; adapt anchor); Warnings), empty sections omitted, at most 800 tokens |
| the check errored or timed out | `[check: unavailable]` |

3. **Same protocol in both arms.**
   - The PRISM arm's facts come from PRISM (`prism_check_changes`; `CHECK_CHANGES_DESIGN.md` §2–§3).
   - The baseline arm's come from the fixed text-only algorithm in `CHECK_CHANGES_DESIGN.md` §9.
   - The gate, the clean line, the warnings-only form, the format and the budget are identical.
4. **Facts, never verdicts.** The agent decides what to do with them.
5. **Token cost,** for a task of about 10 edits with about 3 with issues:
   - **the gate:** about 2,435 tokens (3 × 800 + 7 × 5);
   - **appending the full feedback after every edit:** about 8,000 tokens.

## 3. Protocol: compaction (decided 2026-11-10)

**One deterministic rule in every arm** (`COMPACTION_DESIGN.md`). It is a protocol setting, not an arm variable:
- compact when the next prompt would pass 70% of the window, down to 50%;
- old tool outputs dropped by age, never by tool name;
- kept: the system prompt, the task, the latest 3 turns, the latest test result, the latest check feedback, and the
  edit ledger;
- recurring before every call;
- R3's window rule only as the overflow fallback.

## 4. Families and counts (the smaller-first approach, 2026-11-10)

- **R4's primary families are delete and feature** (the user, 2026-11-10). Family B is analysed separately as a
  directional small sample (4–10), stated as directional. Move, tests and Family N are deferred (R4b or later).
  Refactor joins if its gating yields 80+.
- **Families enter in sequence.**
  - First, the families validated in Sanity 1 and 2: delete, feature, and Family B re-gated.
  - Then refactor, move, tests and Family N, as each passes gating after Sanity 1 has shown the loop works.
- **Counts:** 40–80 tasks per family, as each family's gated pool allows.
- **Family B is counted as it yields.** A small Family B (for example 15) is an expected outcome, analysed at its real
  size, not mined harder to force a count.
- **The final families and counts are entered here before R4,** from the gated pools at that time.

## 5. Still to be entered (after Sanity 2, before R4)

- **Run settings:** the turn cap, the token budget, the context window, the seeds.
- **Families and counts:** the final list and numbers under §4, and the third arm if any.
- **The test:** the primary outcome, the per-family α and thresholds, interim looks, secondaries, and the stop rules.
- **Parity rules** for anything else that differs between arms.
