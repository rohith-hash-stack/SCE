# Eight-arm SLM harness — Milestone 3 (in progress)

M3 replaces the last stub, Arm 4, and makes the M3 gate the clean baseline
for every arm on FastAPI (see `docs/m2_closure.md`). Gate B is n = 4:
t02_005 is excluded (`config.GATE_B_EXCLUDED_TASKS`).

## Arm 4 — Claude-Code-style agent loop (fidelity MEDIUM_HIGH)

`harness/arms/arm4_agent.py`. The model explores the repository with
read-only tools and answers through an `answer` tool.

- **Tool-call format:** Qwen2.5-Coder's native `<tools>{"name": ...,
  "arguments": {...}}</tools>` (not Hermes' `<tool_call>`), parsed from the
  model's text. A block may hold one object or a list. Every block in a
  turn is parsed. The system prompt carries few-shot examples of the
  format, using a made-up repository. `<tool_call>` blocks are counted
  (`hermes_blocks`), not executed, so a format drift shows up in the
  metadata.
- **Tools:** `grep` (ripgrep), `glob`, `read`, `answer`. All paths are
  resolved with `realpath` and must stay inside the repository (`..`,
  absolute paths elsewhere and escaping symlinks are rejected).
- **Caps** (`config.AGENT_*`):
  - 10 turns, 5 calls per turn (later calls get an error result);
  - grep 30 lines: `rg -m 30` bounds each file, then the whole result is
    truncated to 30 lines with a "[N more]" note;
  - glob 30 files, read 150 lines;
  - 10 s per tool subprocess. A timeout is an error result plus a counter,
    and the loop goes on.
- **Duplicate calls**, keyed by (tool, arguments with sorted keys), are not
  re-executed; the result points to the earlier `msg_id`.
- **Compaction:** before each model call, when the conversation exceeds 60%
  of the context window, grep and glob results become digests (files and
  match counts), and every read except the 3 most recent becomes a one-line
  stub. "Most recent" is by stable `msg_id` (`r<turn>.<call>`), never list
  position.
  - Compaction is triggered, not continuous: reads added after it stay
    verbatim until the next trigger.
  - If the 3 preserved reads alone fill the window, the loop stops
    (`window_full`) and the answer is forced.
- **answer-in-batch:** sibling calls in the answering turn run first and are
  recorded, then the answer ends the loop. Their results never reached the
  model, so they are not delivered (`executed_after_answer`).
- **Errors:**
  - A turn where no `<tools>` block parses is an error turn
    (`is_error=True`), and the model is told so.
  - A turn with no block at all gets a notice.
- **Answer:** the `answer` tool's `response` argument, in the task's
  response format, scored exactly like every other arm's answer
  (`extraction_method="answer_tool"`). The pipeline makes no separate answer
  call for Arm 4 (`self_answering`).
  - If the model never calls `answer` within 10 turns, one call with the
    uniform answer prompt over the gathered context answers
    (`forced_answer=True`, ordinary extraction).
- **Delivered context:** what the model's prompt held at its final call.
  Each tool result appears once, as it was last shown: `tool_result_read`,
  `tool_result_grep`, or `tool_result_digest` (the original in provenance).
  Symbols are the Python definitions the result covers:
  - for a read, every def/class overlapping its line range;
  - for a grep, the innermost def around each match.
- **Multi-turn messages:** the LLM clients gained `chat(messages)`; their
  `__call__` delegates to it, so the other arms' requests are unchanged.
  Tool results return as a user message wrapped in `<tool_results>`.

### M3 gate finding and fixes

In the M3 gate Arm 4 scored 0/4 on Gate B with a forced answer on 3 of 4
tasks. The cause was a contradiction in its own prompt, not the agent: its
first message ended with the uniform response contract ("Respond with a
single fenced JSON object and nothing else") and then asked it to call
`answer`. When the model answered in the contract's format without a
tool call, the loop discarded the turn as "no tool call", nudged it, and
it re-grepped (on t02_002, the same 5 greps 4 times) until the cap.

1. **Direct answers accepted.** A turn with no `<tools>` block that parses
   as an answer through the same extraction every arm uses (T2/T5: the
   JSON object with a non-empty `symbols` list; T3/T4: a fenced code block)
   ends the loop as the answer (`answer_source="direct"`,
   `direct_answers`). T1 prose can't be told apart from narration, so T1
   still needs the `answer` tool.
2. **The contract belongs to the answer tool.** It now appears only in the
   `answer` tool's description in the system prompt. The task message is
   the task and "Explore the repository with grep, glob and read, then
   call answer." The system prompt says "Do not produce a final answer
   without calling the answer tool." The task texts themselves (unchanged
   task data) still ask for a JSON block, which is why fix 1 stays as the
   fallback.
3. **Every turn's raw text** is in the trajectory (`raw_text`,
   `parsed_calls`, `is_final`), so a run can be diagnosed from its bundle.

### Spec interpretations (declared in `build_meta["simplifications"]`)

1. **Old reads digested:** "preserve last 3 reads verbatim; digest grep/glob
   output" leaves older reads unspecified. They become one-line stubs
   (path and line range), so the conversation can shrink.
2. **Forced answer:** an agent that never calls `answer` still produces an
   answer, through the uniform answer prompt. Recorded, never silent.
3. **Tool results as a user message:** they go back as a user message with
   `<tool_results>` tags rather than a `tool` role, because calls are parsed
   from text rather than through Ollama's tools API.

### Agent diagnostics (`harness/scoring/agent_diagnostics.py`)

Computed per Arm 4 cell by the scorer; None for every other arm.

- **`tool_fpr`** (column `tool_fpr`, breakdown in `tool_fpr_json`):
  - counted from each dispatched call's outcome in
    `build_meta["trajectory"]`;
  - fpr = (grep_empty + read_empty + read_nonexistent + read_out_of_bounds
    + timeouts) / total_calls;
  - redundant (duplicate-suppressed) calls count in the total, not as false
    positives.
- **`digest_safety_loss`:** 1.0 when the final answer names an identifier
  that, at answer time, survived only in digested tool output.
  - Identifiers are at least 4 characters and matched by leaf name with a
    word-boundary regex.
  - The task prompt counts as preserved.
  - 0.0 when nothing was digested or the answer names no identifier.

### Verification (local)

- `tests/test_arm4_agent.py`: 17 tests. The four bug traps:
  - `rg -m` per file plus a 30-line total;
  - every `<tools>` block in a turn parsed;
  - compaction keeping the last reads by `msg_id`, with a shuffled list;
  - a tool timeout not crashing the loop.

  Plus: all blocks unparseable → `is_error`; Hermes blocks not executed;
  read caps and soft errors; path containment (including a symlink);
  duplicate suppression; the per-turn and turn caps; answer-in-batch;
  compaction at the 60% threshold; the no-tool notice; the pipeline's
  self-answer and forced-answer paths; the adapter.
- CPU smoke check `arm4_agent_tools`: a scripted agent on a real FastAPI T2
  task, using real ripgrep, read and glob, PASS.
- `harness.kaggle_m1 --dry-run --fake-encoders`: 63/63 rows (7 arms × 9
  tasks), exit code 0.

Not run on Kaggle. Arm 4 needs `ripgrep`; the hallucination scorer already
uses it, and the M1 notebook installs it (`apt-get install ripgrep`).
