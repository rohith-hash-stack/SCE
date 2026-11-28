# Phase 1 step 1f: results, reply format (2026-11-16)

**The run:**
- Kaggle, devstral-small-2, code `a74b431f`.
- 680 calls: 17 turns × 10 seeds × 4 conditions.
- Results in `reports/r4/phase1f/`, with Ollama's server log.
- Pre-registration: `AMENDMENTS.md`, "Phase 1 step 1f".
- **Prompt fidelity is as in step 1e:** 7 of 17 turns faithful to Phase 0 (delete-05: all 6; feature-08: turn 8).

## Parse-error rate per condition

| Condition | All 17 turns | 7 faithful turns | Mean seconds per call |
|---|---|---|---|
| `before` (JSON mode, Phase 0 prompts) | 119/170 = **70.0%** | 39/70 = **55.7%** | 11.5 |
| `free` (JSON mode off) | 127/170 = **74.7%** | 46/70 = **65.7%** | 11.9 |
| `native` (native tools, Phase 0 prompts) | 68/170 = **40.0%** | 11/70 = **15.7%** | 8.9 |
| `native_p1` (native tools, Phase 1 prompts) | 15/170 = **8.8%** | 15/70 = **21.4%** | 13.4 |

**Per turn: parse errors out of 10 (before / free / native / native_p1):**

| Run | Turn | Faithful | Parse errors |
|---|---|---|---|
| delete-05 | 18 | yes | 1 / 2 / 0 / 0 |
| delete-05 | 19 | yes | 10 / 10 / 4 / 0 |
| delete-05 | 20 | yes | 9 / 10 / 0 / 3 |
| delete-05 | 21 | yes | 2 / 6 / 0 / 5 |
| delete-05 | 24 | yes | 7 / 7 / 0 / 0 |
| delete-05 | 27 | yes | 9 / 9 / 0 / 0 |
| feature-08 | 8 | yes | 1 / 2 / 7 / 7 |
| feature-08 | 15 | no | 0 / 1 / 0 / 0 |
| feature-08 | 19–27 | no | 80 / 80 / 57 / **0** (out of 90) |

## Against the decision rule recorded before the run

- **The rule:** a format replaces JSON mode only with ≤ 5% over all 17 turns, and below `before` on both turn sets.
- **`native`: 40.0%. Does not qualify.**
- **`native_p1`: 8.8%. Does not qualify** (above 5%).
- **The rule's outcome:** "neither qualifies: JSON mode stays, with the step 1c history cut."

**The intent check** (the rule for future pre-registrations, AMENDMENTS "Override"):
- **The 5% bar** was set to find a format that removes the problem.
- **Native comes close.** Both native conditions sit far below JSON mode on both turn sets.
- **The realistic configuration is the Phase 1 harness:** `native_p1` at 8.8% against JSON mode with the same harness
  (step 1e `after`, same prompts, earlier session) at 37/170 = 21.8%. On the faithful turns: 21.4% against 48.6%.
- **This is a label-versus-intent case, so it goes to the user.**

## What the data says about the cause

- **JSON mode is not the cause.** With JSON mode off (`free`), the failures are as frequent (74.7% against 70.0%) and
  look the same: short replies that stop inside the `old`/`new` string of an `edit_file`, with no stop reason and no
  token counts.
- **What fails is the model writing a tool call as JSON text.** In its native format (`[TOOL_CALLS]name[ARGS]{...}`),
  most failures disappear.
- **The server log (`ollama_11434.log`):**
  - **What it shows:** the stopped replies are ended on Ollama's side. The engine logs a normal "stop processing" and
    Ollama logs "cancel task" (201 times). The HTTP response is 200, without a final chunk.
  - **The exact rule** in Ollama v0.34.0 that ends them is not identified.
  - **Ruled out:** devstral-small-2 has no builtin output parser configured, so that cancellation path does not
    apply.

**The remaining native failures:**
- **Empty replies** (no content, no call; 12 cases).
- **Prose instead of a call** (feature-08 turn 8: "I need to create the skills utilities module…").
  - **Why it can happen:** Ollama has no `tool_choice: required`, and the system prompt still asks for a JSON object.
- **Copies of an earlier broken reply that sits in the history** (feature-08 turns 19–27 with Phase 0's history: 57
  of 90). The history cut removes those entirely: 0 of 90 in `native_p1`.

## Candidate next steps (none built)

1. **Adopt native tools with the Phase 1 harness for the re-baseline.** This needs the user's decision, as the rule
   was not met.
2. **A native-mode system-prompt line:** "call exactly one of the given tools each turn; never reply in prose". It is
   aimed at the prose and empty replies, and is identical in both arms. Testable with the same rebuilt prompts (about
   30 min on Kaggle), or measured inside the re-baseline's first runs.
3. **Live residual:** a parse error costs one turn plus a reminder; it does not end the run. In step 1e's live runs
   (JSON mode), the rate was 1 in 643 turns.
