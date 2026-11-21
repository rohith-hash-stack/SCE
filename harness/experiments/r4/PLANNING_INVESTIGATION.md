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

## Results (2026-11-12)

**The run:** 12/12 runs, code `f7a7a0f9`, PRISM arm, seed 42. Results in `reports/r4/planning/`.
- **Every smoke check passed with JSON forced on**, so no variant needed the fallback.
- **QwQ's feature run** ended on a model timeout: one call ran over 900 s, at about 11 tok/s with about 2,200 thinking
  tokens a turn.
- **These runs used the first sanity run's tasks:** no gate, and the index-name delete prompt.

| Variant | Plan or thinking first | Edits applied (all 3 tasks) | Hallucinated done | Verified before done | Resolved | Minutes per run |
|---|---|---|---|---|---|---|
| Qwen 14B, current prompt (sanity 1) | 0/3 | 1/6 (17%) | 2/3 | 0/3 | 0/3 | 0.2–3 |
| Qwen 14B + planning | 3/3 (every turn) | 0/0 | 2/3 | 0/3 | 0/3 | 0.5–8.5 |
| Qwen3-14B, thinking off | 0/3 | 5/24 (21%) | 2/3 | 2/3 | 0/3 | 2.4–3.8 |
| Qwen3-14B, thinking on | 3/3 (~700–1,200 thinking tokens/turn) | 14/18 (78%) | **3/3** | 0/3 | 0/3 | 1.8–34 |
| QwQ-32B | 3/3 (~1,000–2,200 thinking tokens/turn) | 9/22 (41%) | 1/3 (the other two hit the turn cap or a model timeout) | 1/3 | 0/3 | 33–87 |

**What each one did:**
- **Planning (A) changes nothing that matters.**
  - The plans are fluent, but they are built on the wrong facts. In delete-02: "I will first identify all usages of
    `AnthropicHandlerBase._register_streaming_from_kwargs`…". It greps that dotted name (R1), finds nothing, and plans
    "it appears the method does not exist". Done after 3 turns.
  - So a plan does not make the model check its premises.
- **Thinking (B) helps with editing and finding, not with finishing.**
  - **Editing:** on the same weights, edit success rises from 21% to 78% with thinking on.
  - **Finding:** Qwen3 found the pydantic target despite the bad prompt; QwQ found both delete targets.
  - **Finishing:** every thinking run that called done claimed success, while 639 tests (pydantic) or 69 (instructor)
    were broken. "Ensuring tests pass" and "Tests pass after removal" were written without running the tests, or
    against their output.
  - **Cost:** 5–30× the time per run, with QwQ at 33–87 minutes a run.

## Verdict, by the rule set before the runs

- **The rule:** "fixes" means no hallucinated done on at least 2 of 3 tasks, and edit success above today's.
- **No variant meets it.** Thinking-on raises edit success but has 3 of 3 hallucinated dones. QwQ's 1 of 3 comes from
  runs that ended at the cap or on a timeout, not from verifying.
- **So planning is not the answer, and the verification gate is needed regardless.**

**For R4:**
- **Do not add the planning prompt.**
- **Keep the model** for now. Thinking is the one lever that clearly raised edit success, though on 3 tasks at 1 seed.
  It is the candidate model change if sanity 3 lands in "applied edits rise but solves don't" (task difficulty or
  model):
  - Qwen3-14B with thinking: 14B, fits as today, about 10–30 minutes a run;
  - not QwQ-32B: 30–90 minutes a run and call timeouts make 240+ R4 runs impractical, at about 240 GPU-hours.
- **A switch would break comparability with R3a** and needs its own pre-registered decision.
