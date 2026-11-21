# Re-baseline wave 1: stopped by the health check (2026-11-16)

**What ran:**
- Wave 1 (seed 42): 40 of 40 runs, code `a1d8d0dc`.
- Results in `reports/r4/rebaseline/`. Nothing was changed mid-run.

**The health check (pre-registered):** `{"runs": 40, "infra_deaths": 0, "parse_error_rate": 0.071}`.
- **Parse errors:** 7.1% of turns, against the 2% stop line. **Stopped, as designed.**
- **By arm:** PRISM 50 of 544 turns; BASELINE 26 of 527.

## What the parse errors are

- **Every one of the 76 is an empty reply:** no text and no tool call.
  - **53 ended normally** (`done_reason` `stop`), after 51–1,000 generated tokens.
  - **23 hit the 4,096-token answer limit,** taking up to 394 s each.
- **The model generated output every time.** Ollama returned none of it.

**They concentrate in a few runs, and once they start they repeat:**

| Run | Empty replies | Turns |
|---|---|---|
| feature-03 PRISM | 22 | 28 |
| delete-07 BASELINE | 21 | 28 |
| delete-05 PRISM | 18 | 28 |
| feature-01 PRISM | 6 | 23 |
| feature-01 BASELINE | 5 | 27 |
| 4 other runs | 1 each | |

**What preceded the first empty reply in each run:**
- a PRISM output (`prism_ask`, `prism_blast_radius`, `prism_change_context`);
- a failed edit;
- a large file read;
- or nothing (feature-09 PRISM, turn 1).

## Why the reply is empty: Ollama's tool-call parser (v0.34.0 source)

**In Ollama's source:**
- **`server/routes.go`, the chat handler:** with `tools` in the request, the model's text is fed to `tools.Parser`
  (`toolParser.Add`). While the parser holds text it thinks is a tool call, nothing is sent. At the end of the reply
  it sends `toolParser.Content()`.
- **`tools/tools.go`, `Parser.Content()`:** it returns the buffered text only when the tool tag is `{` or `[`. For
  devstral the tag is `[TOOL_CALLS]`, so it returns `""`.

**So:**
- **What happens:** when devstral starts a native call (`[TOOL_CALLS]…`) that the parser cannot complete (malformed
  arguments, or a call that runs on to the answer limit), the whole reply is discarded.
- **What reaches the harness:** an empty message with `done_reason` `stop` or `length`.
- **Not seen in step 1f's isolated calls:** there, 12 empty replies were 7% of native calls. Live, an empty reply
  goes into the history as an empty assistant turn plus the format reminder, and the model tends to repeat itself, so
  a single drop can take over a run (feature-03 PRISM: 22 of 28 turns).

**What we cannot see:** what the model actually wrote. Ollama discards it before the response.

## Options (none built; the user decides)

**A. Read the model's raw output ourselves** (recommended).
- **How:**
  - render devstral's chat template in the harness (the registry template is known and short);
  - call `/api/generate` with `raw: true`;
  - parse `[TOOL_CALLS]name[ARGS]{...}` in the harness.
- **What it gives:** nothing is discarded unseen. A malformed call becomes a visible parse error with its text,
  handled like any other: the history cut, the format reminder, logged in full.
- **No prompt wording changes:** the rendered prompt is the model's own template.
- **Cost:** the rendering must match Ollama's. It is checked by a Kaggle comparison of token counts and replies
  between `/api/chat` and the raw path on the same prompts.
- **Also gives:** the evidence of what the malformed calls look like.

**B. Retry an empty reply.**
- **How:** re-ask the same prompt with the next seed, at most once, not counted as a turn. The drop is treated as an
  infrastructure loss.
- **For it:** simple.
- **Against it:** it hides the failure and doubles the cost of the 4,096-token cases (about 6 minutes each). It also
  samples twice where the other arm sampled once.

**C. A tolerant parse, done by the harness instead of Ollama.** This is only possible after A, which exposes the raw
text.

**D. Return to JSON mode** with the step 1c history cut. Step 1f measured it at 22% of calls on the Phase 1 harness.
Worse.

**What a fix needs before the re-baseline restarts:**
- **The wave-1 runs are kept** as a record but **not used**: the harness would change.
- **The re-baseline restarts from wave 1** with the fixed client, under a new pin and an amendment entry.
