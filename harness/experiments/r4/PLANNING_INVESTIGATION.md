# Planning investigation: does planning or thinking help the sanity failures? (2026-11-11)

**This is an investigation, not R4 design.** R4's protocol is unchanged, and the verification gate's design continues
separately (it is needed whatever this shows).

## The question

Does planning alone, without a verification gate, fix the sanity failures? The failures it targets:
- edits retyped from memory, so they are rejected;
- done claimed with the task unfinished;
- loops on failed edits.

## The runs

The arm is PRISM: the model plans from the context PRISM gives it. The sanity runs used seed 42, turn cap 28 and R3a's
loop; these runs keep all three.

| Variant | Model | What differs | Runs |
|---|---|---|---|
| Qwen 14B, current prompt | qwen2.5-coder:14b-instruct-q8_0 | none | **not re-run:** the sanity runs (`reports/r4/sanity`, the same seed, code and loop) |
| Qwen 14B + planning (A) | the same | every reply states "I will [action] because [reason]. Then [next step]." The plan sits inside the JSON, since replies are constrained to JSON, and stays in the history | 3 |
| Qwen3-14B, thinking off | qwen3:14b-q8_0 | a different model, no thinking: **the control for the next row** | 3 |
| Qwen3-14B, thinking on (B) | the same | native thinking; the answer limit is 12,288 tokens, thinking included; the prompt window is unchanged | 3 |
| QwQ-32B (B) | qwq:32b (Q4_K_M) | native reasoning; answer limit as above. Run last, so it can be cut for time | 3 |

**Tasks:**
- `r4s-delete-02`: instructor delete. 1 of 5 edits applied, then done.
- `r4s-delete-06`: pydantic delete. Grepped the dotted name and found nothing, then done in 3 turns.
- `r4s-feature-04`: typer feature. PRISM looped on "symbol not found" until the turn cap.

**Why a Qwen3 thinking-off control:** comparing Qwen 14B with QwQ-32B changes the model, its size and thinking all at
once. Qwen3-14B with thinking on vs off isolates thinking itself, on the same weights.

**Measured per run** (`planning_report.py`):
- a plan or thinking before the first action, and the share of turns that have one;
- edit success: applied over attempted edits;
- hallucinated done: done called, run not resolved;
- verified before done: `run_tests` after the last applied edit and before done;
- the ending, thinking tokens per turn, and minutes.

**Today's rows (from the sanity logs):**

| Variant | Task | Plan first | Edits applied | Hallucinated done | Verified before done | Ending |
|---|---|---|---|---|---|---|
| Qwen 14B, current prompt | delete-02 | no | 1/6 | YES | no | done (11 turns) |
| Qwen 14B, current prompt | delete-06 | no | 0/0 | YES | no | done (3) |
| Qwen 14B, current prompt | feature-04 | no | 0/0 | no | no | turn cap (28) |

## Fit and time on Kaggle's T4 × 2 (2 × 15 GB, about 320 GB/s each)

| Model | Weights | KV cache at 28,672 tokens (q8_0) | Total | Fits? | Speed | Per turn with thinking |
|---|---|---|---|---|---|---|
| qwen2.5-coder 14B q8_0 (today) | 15.7 GB | ~2.5 GB | ~19 GB | yes | 10.2 tok/s measured (sanity) | ~4 s (39-token answers) |
| qwen3 14B q8_0 | 15.7 GB | ~2.5 GB (40 layers × 8 KV heads × 128) | ~19 GB | yes | ~10 tok/s | ~1–3 min (500–2,000 thinking tokens) |
| QwQ-32B Q4_K_M | ~20 GB | ~4 GB (64 layers × 8 KV heads × 128) | ~25–26 GB with buffers | **yes, with ~3–4 GB spare**, split across both GPUs | ~7–8 tok/s (the weights' size and today's efficiency) | ~2–11 min (1,000–5,000 thinking tokens) |

- **Session time:**
  - planning and thinking-off: about 15 minutes each;
  - Qwen3 thinking: about 1–3 hours;
  - QwQ: about 2–8 hours, since its runs end at the token budget after roughly 15–20 turns.
- **Total: about 4–12 hours,** so possibly 2 sessions. The cell resumes, and QwQ runs last.
- **The original ~30 minute estimate holds only for the non-reasoning rows.**
- **Thinking counts against the run's 300k token budget,** as answer tokens always have. A reasoning model can end at
  the budget sooner; this is reported, not adjusted.

## The decision rule (set before the runs)

- **Fixes the failures** means: on at least 2 of the 3 tasks, no hallucinated done, and edit success above today's.
- **Readings:**
  - Qwen 14B + planning fixes them → add planning to R4, which is cheaper than switching models.
  - Only a reasoning model fixes them, and Qwen3 thinking-on beats thinking-off → R4 may need a model switch. That
    breaks comparability with R3a, and the turn time grows several-fold.
  - Neither fixes them → planning is not the answer. Proceed with the verification gate.
- **These are 3 tasks at 1 seed:** enough to tell "changes the behaviour" from "does not", not enough for a rate.

## Code (all opt-in; R3a and the sanity run are unchanged by default)

- `r3/loop.py`:
  - `system_prompt(version, plan)` and `AgentRun(plan=...)`: the plan field, logged and kept in the history;
  - `OllamaChat(think, answer_tokens, json_format)`: thinking is logged, a `<think>` block in the answer is stripped,
    and the prompt window is kept.
- `r3/runner.py`: the `--model`, `--plan`, `--think`, `--answer-tokens` and `--no-json-format` flags.
- `r4/planning_run.py`: the driver (resumable). `r4/planning_report.py`: the table.
- `kaggle/r4_planning_cell.py`:
  - each model is pulled, smoke-tested (one JSON tool call with its thinking setting), run on the 3 tasks, then
    removed;
  - if forcing JSON breaks a model, its runs go without the JSON constraint, recorded in `smoke_<variant>.json`.
- **Tested locally against a fake Ollama server** (no model calls): the plan prompt and history, the think flag and
  `num_ctx` (28,672), thinking logged, and the report rows. R3's tests pass (48), and the loop has a new plan-mode
  test.
