# Phase 1, steps 1a–1d: what was built and how it was tested (2026-11-15)

**Scope:** the user's go-ahead (2026-11-15) covered the four steps that need no model calls. **No model was called and
no Kaggle cell was run.**

**Off by default:** every change sits behind a switch, so earlier rounds' behaviour is unchanged:
- `--phase1` on `runner.py` and `pilot_run.py`;
- `compact` and `hygiene` on `AgentRun`;
- `stream_cut` on `OllamaChat`.

## Step 1a: the PRISM gap diagnosis

**In `PHASE1_DIAGNOSIS.md`:**
- **H1, context cost: supported.** PRISM outputs, carried in the history, account for ~22% of the PRISM arm's budget,
  and 57% in delete-06.
- **H2, symbol format: refuted.**
- **H3, misleading context: weak.**
- **H4, tool-menu cost: refuted** (+147 tokens a turn).
- **H5, seed noise: supported for 2 of the 4 discordant tasks.** In feature-09 the PRISM arm never called PRISM; in
  feature-05 the model picked a wrong name.

## Step 1b: the PRISM symbol error message (item 4, message only, as decided)

- **Where:** `src/prism/mcp/security.py`. **The validator is unchanged:** every path form is still rejected.
- **What changed:** the rejection now states the accepted form. For a path-like name it adds the dotted suggestion,
  offered only when that suggestion itself passes the validator.

  For devstral's Phase 0 call:

  > invalid symbol name: 'packages/zod/src/v4/classic/schemas.object'. Symbols are dotted names, not file paths: the
  > file's path with `.` instead of `/` and without its extension, then the class and function, e.g.
  > `pkg.module.Class.method`; a bare function or class name also works. Did you mean
  > `packages.zod.src.v4.classic.schemas.object`?

- **Checked:** on a small TypeScript repository laid out like zod's, PRISM resolves the suggested name, and the bare
  `object`, to the right function.
- **Tests:** 4 new in `tests/test_mcp_security.py`:
  - the message;
  - the suggestion for three path forms;
  - no suggestion for traversal, absolute or hostile names;
  - the validator still rejects every path form.

  The existing `test_mcp_security.py` and `test_mcp_server.py` tests pass.

## Step 1c: the trailing-off parse errors (item 3)

**Three changes:**
- **`OllamaChat(stream_cut=True)`:**
  - **Streaming:** the reply is streamed. When it ends in more than 400 characters of whitespace or escaped
    `\n`/`\r`/`\t`, the stream is closed, which stops generation. The tail is dropped and `done_reason` is
    `degenerate_cut`.
  - **Repair:** if appending one or two closing braces makes the text one valid tool call, that repaired text is used.
    This was feature-08's shape: `args` complete, the final `}` never came. The repair never changes what the model
    wrote, and is logged in `model_meta.repaired_closing_braces`.
  - **delete-05's shape** (escaped newlines inside a string) cannot be repaired. It stays a parse error, but costs
    seconds instead of up to 105 s.
- **`AgentRun(hygiene=True)`:**
  - **An invalid reply goes back into the history cut to 400 characters**, plus a note. The turn log keeps it in
    full. In Phase 0 the full reply, up to 5.4K characters, was copied back, and feature-08 repeated it 11 times.
  - **A call that came back without token counts is counted with the tokenizer** and flagged `tokens_estimated`.
    Phase 0's 17 degenerate replies were charged nothing to the budget.
- **Tests** (`tests/test_phase1_harness.py`, against a fake streaming Ollama server):
  - a normal reply is unchanged, with its counts;
  - the feature-08 shape is cut early (the client hangs up) and repaired;
  - the delete-05 shape is cut and not repaired;
  - 20 legitimate blank lines are not cut;
  - without the switch, no streaming is requested;
  - the timeout holds;
  - the history cut and the token estimate work, with and without the switch.
- **Not yet measured:** whether this lowers devstral's degeneration rate. That is the Kaggle reproduction (plan §3.4):
  the 17 failing prompts, rebuilt by the replay tool.

## Step 1d: the token budget (item 2): history compaction

**Budget accounting is unchanged,** as decided: every prompt token counts.

**`harness/experiments/r3/compaction.py`, applied before the window rule** (`AgentRun(compact=True)`):
- **Deterministic:** it is rebuilt each turn from the raw history, which it never modifies.
- **Identical rules in both arms.**
- **The rules:**
  1. **Superseded reads:** a read covered by a later read of the same lines becomes a pointer.
  2. **Stale reads:** a read whose lines a later applied edit changed becomes "read it again". An edit elsewhere in
     the file leaves the read alone. (The first version marked every read of an edited file stale; replay showed that
     too aggressive, and it was narrowed.)
  3. **Repeated searches:** a `grep` or `list_dir` repeated with the same arguments becomes a pointer.
  4. **Old outputs** (beyond the 6 newest tool outputs) are summarised:
     - `read_file`: one line;
     - `run_tests`: the per-file counts and 5 failing names;
     - `grep` / `list_dir`: the first 8 / 12 lines;
     - index tools: the first 12 lines, **the same rule for `prism_*` and BASELINE's `related_code`**.
- **What is never compacted:** short outputs, errors, and the newest output of each kind.
- **What survives:** the progress ledger (files read, edits, latest tests) is unchanged, so the facts the summaries
  drop are still in front of the model.

**Tests:** 8 in `tests/test_phase1_harness.py`:
- rule by rule;
- the raw history is never modified;
- an end-to-end run in which compaction shrinks the prompt and is logged.

### The replay test (Phase 0b replay tool, real tools, recorded replies)

**Extensions to `harness/experiments/r4/replay.py`:**
- `--compact` and `--hygiene`;
- `--count-prompts`: prompt sizes counted with the tokenizer on the replay's own prompts. Without it, the replay
  returns the recorded Ollama counts, which cannot change.

**Report:** `harness/experiments/r4/phase1_budget_check.py` compares two replays turn by turn.

**Regression check:**
- **The run:** feature-04 PRISM (typer), replayed with every Phase 1 switch off.
- **The result:**
  - the same 27 turns;
  - the same end (budget) and the same 294,040 tokens used;
  - 25 of 27 tool outputs byte-identical. The other 2 are `run_tests` outputs from a different machine.
- **So the switches-off path is Phase 0's.**

**Counterfactual on two of Phase 0's nine budget-ended runs:** the same recorded replies, compaction off against on,
both counted with the same tokenizer.

| Run | Tokens used, off → on | Saving | Mean prompt, off → on | Largest prompt with compaction | Turns the window rule stubbed, off → on |
|---|---|---|---|---|---|
| delete-09 BASELINE (litestar) | 273,614 → 140,099 | **49%** | 10,883 → 5,543 | 10,481 | 6 → 0 |
| feature-04 PRISM (typer) | 277,964 → 174,206 | **37%** | 10,223 → 6,380 | 15,275 | 13 → 0 |

- **Neither run would have ended on the budget** with compaction. The 28-turn cap becomes the binding limit.
- **What a replay cannot show:** whether the model, seeing compacted history, re-reads more, or edits from text it no
  longer sees. The replies are fixed. That is what the live re-baseline measures, via re-reads of compacted reads and
  stale-edit repeats.
- **Replay reports:** `reports/r4/phase1_replays/`.

## Not done, and why

- **The other 7 budget-ended runs were not replayed.** Each needs its repository's environment built locally:
  pydantic, pydantic-ai, hono, scrapy, aiohttp. The two above cover one run from each arm.
- **The degeneration reproduction and the seed-noise runs** need the model: Kaggle step 1e, waiting for the go-ahead.

## Files

- **New:**
  - `harness/experiments/r3/compaction.py`;
  - `harness/experiments/r4/phase1_budget_check.py`;
  - `tests/test_phase1_harness.py`;
  - `PHASE1_DIAGNOSIS.md`;
  - this file.
- **Changed:**
  - `harness/experiments/r3/loop.py` (`OllamaChat` streaming; `AgentRun` `hygiene`/`compact`; history entries carry
    `args` and `turn`, never sent to the model);
  - `runner.py` and `pilot_run.py` (`--phase1`);
  - `harness/experiments/r4/replay.py`;
  - `src/prism/mcp/security.py`;
  - `tests/test_mcp_security.py`.

**Tests:** 133 pass across the loop, replay, client, gate, shared-tool, PRISM security and server suites.
