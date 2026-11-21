# Phase 1: architecture and implementation plan (draft, 2026-11-15)

**Status:** a plan only. Nothing is built.
- **What it covers:** the five priorities the user set after Phase 0 passed (`PHASE0_RESULTS.md`, 7 of 20 solved).
- **Before any live run:** each part gets an `AMENDMENTS.md` entry, as in Phase 0.

**Facts below come from the 40 Phase 0 runs** (`reports/r4/phase0/`) unless marked otherwise.

## 0. Framing: what Phase 0 does and does not say about PRISM

**Phase 0 does not show that PRISM is a net negative.**
- **The arms overlap:** PRISM solved 4 tasks and BASELINE 6. Both solved 3 (delete-08, delete-10, feature-02).
- **The discordant tasks are 4:**
  - BASELINE only: delete-06, delete-09, feature-05;
  - PRISM only: feature-09.
- **3 against 1 is not significant.** A two-sided sign test gives p = 0.625, with one seed per task.
- **Phase 0 was a capability check, not a comparison.**

**What Phase 1 must therefore do:**
1. **Explain the four discordant tasks run by run** (from the logs; no model calls).
2. **Fix the harness problems that hit both arms** (items 2–4). Their noise would swamp any PRISM effect.
3. **Change PRISM only where step 1 finds a cause.**
4. **Measure the comparison with enough runs to mean something:** several seeds, and more tasks than 20.
   - **"PRISM beats BASELINE" becomes a claim only then.** It needs a pre-registered test and a power calculation
     (§6).

**The order follows from this:** items 3, 4 and 2 (harness hygiene) first, then item 1's interventions, then the
comparison. The item-1 diagnosis runs first of all, because it needs no code.

## 1. Item 1: the PRISM / BASELINE gap

### 1.1 What the data already shows

| | PRISM arm | BASELINE arm |
|---|---|---|
| Mean prompt per turn | 8,311 tokens | 7,534 tokens |
| Mean tokens used per run | 216,851 | 200,025 |
| Token-budget endings | 5 | 4 |
| Mean output of a PRISM call | **2,207 tokens** (38 calls) | – |
| Mean output of `read_file` | 762 | 720 |
| First edit (mean turn) | 13 | 14 |

**What this suggests:**
- **PRISM's outputs are large:** about 3× a file read.
- **The PRISM arm carries ~10% more prompt per turn,** from those outputs and its longer tool menu.
- **The direction fits "context cost",** but the size of the effect is small.
- **The symbol-format error** hit only the pilot (3 of 38 calls), so it cannot explain the gap.

### 1.2 Hypotheses, and how each is tested (Phase 1a: analysis only)

| # | Hypothesis | Test on the existing logs | Supports it if |
|---|---|---|---|
| H1 | **Context cost:** PRISM output pushes useful history out (stubbing) or spends the budget | for each discordant task: tokens from PRISM outputs, turns stubbed, budget and turn of ending, against the BASELINE run of the same task | PRISM runs stub or exhaust budget earlier, and the lost material was needed later (re-reads of stubbed files) |
| H2 | **Symbol format** | rejected or not-found PRISM calls per run | rejections concentrate in the losing runs (Phase 0: they do not) |
| H3 | **Noise or misdirection:** PRISM points to the wrong code | files and functions named in PRISM output, against the gold files and what the agent then edited; turns from a PRISM call to the first useful edit | after PRISM calls, the agent edits non-gold files, or wanders, more than BASELINE |
| H4 | **Fixed cost:** the PRISM system prompt and tool menu on every turn | system-prompt tokens per arm, times turns | the difference explains the budget endings by itself |
| H5 | **Seed noise:** the discordant tasks are coin flips | re-run the 4 discordant tasks × 3 seeds × 2 arms (24 runs, ~2.5 h, Kaggle) | discordance flips direction across seeds |

**Deliverable:**
- **A narrative per discordant task.** For each one: what PRISM returned, what the agent did with it, where the two
  arms diverged, cited by turn.
- **A table of H1–H5,** each marked supported, refuted or open.

**Decision rule, recorded before the H5 runs:**
- **If H5 shows the discordance is noise:** item 1 becomes "no PRISM-specific fix yet". Phase 1 goes straight to the
  powered comparison after items 2–4.
- **Otherwise:** the supported hypotheses pick the intervention (§1.3).

### 1.3 Candidate PRISM interventions (built only if 1.2 supports them)

| If | Intervention | Where |
|---|---|---|
| H1 / H4 | **A compact output mode.** A token cap per PRISM call (e.g. 800, against 2,207 now), with ranked callers and code snippets only for the top-k; the rest by name, retrievable on request | PRISM tool layer (`harness/experiments/r3/tools.py` / `ToolLayer`), PRISM's `token_budget` parameter |
| H1 | **PRISM context pinned once:** the first `change_context` output kept as a compact pinned block, not re-sent in full every turn of history | harness window rule (both arms keep identical rules; only the PRISM arm has the block) |
| H3 | **Relevance fixes in PRISM ranking** (which callers and dependencies are shown) | `src/prism`; would need its own investigation |
| H4 | **A shorter PRISM tool menu:** 5 tools → 2–3 (e.g. `change_context`, `ask`) | `loop.PRISM_DOC` |

**Each intervention is tested against the same tasks and seeds, PRISM arm only:** before against after.
- **Primary measure:** solves.
- **Secondary measures:** tokens per run, budget endings, turns to the first gold-file edit.

## 2. Item 2: the token budget

### 2.1 Facts

- **What the budget counts:** `BUDGET_TOKENS` = 300,000 (`loop.py:39`) is the sum, over turns, of each call's
  prompt plus answer.
- **Why it runs out:** every turn re-sends the history. A run with 15K prompts spends 300K in ~20 turns.
- **The window rule** (`loop.AgentRun.window`): with the prompt over 16,384 tokens (`NUM_CTX` − `ANSWER_TOKENS`),
  older tool outputs are replaced by one-line stubs, oldest first, then the oldest turns are dropped. It acts only at
  the edge of the window, so prompts sit at 12–17K late in a run.
- **9 of 40 runs ended on the budget.** Two were cut off mid-progress: feature-04 PRISM (4/6) and feature-06 PRISM
  (15/41).
- **A related accounting bug:** the 17 degenerate replies (item 3) came back with no token counts, so they were never
  charged. `used` undercounts.

### 2.2 Options

| Option | Effect | Risk |
|---|---|---|
| A. **Tighter window:** stub older tool outputs from a lower threshold (e.g. a 10K working window), keeping the last N tool outputs in full | prompts ~10K instead of 15K, so ~50% more turns per budget | the stale-read problem returns (S4: 74 of 105 failed edits repeated an earlier one). The model edits from text it can no longer see |
| B. **Deduplicated reads:** a newer read of the same file range replaces the older copy with a pointer ("superseded by turn N") | removes the most common waste without losing information | small; needs range tracking |
| C. **Compressed history:** old tool outputs replaced by structured summaries (what was read, which lines; test results as pass/fail counts and failing names), building on the progress ledger | large savings, and the facts are kept | summary quality; it must be deterministic (no model calls), so it is harness-built, like the ledger |
| D. **Budget semantics:** count only tokens not already in the model's KV prefix cache (what Ollama actually computes) | a cost model closer to reality | **not compression.** It changes what "budget" means and comparability with every earlier round. **The user's decision** |
| E. **Raise the budget** | simplest | hides the problem; comparability |

**Recommendation:**
- **B + C:** deterministic, no information lost that the ledger does not keep.
- **A only as a fallback.**
- **D and E only by explicit decision.**
- **Fix the accounting bug** whatever is chosen: estimate tokens with the tokenizer when Ollama returns none.

### 2.3 How it is tested

1. **Offline, exact, no model:**
   - **The tool:** the Phase 0b replay (`r4/replay.py`) re-runs a logged run with its recorded replies through the
     real loop.
   - **Counterfactual accounting:** run with the new window rule, it gives the exact prompt sizes and budget use the
     same trajectory would have had.
   - **Applied to:** the 9 budget-ended runs. Did they reach turn 28? How much prompt was saved per turn?
   - **The limit:** this measures accounting only; the model cannot react differently in a replay.
2. **Unit tests:**
   - the deduplication and summary functions on fixed histories;
   - the ledger and the latest tool output never stubbed;
   - identical rules in both arms (the asymmetry test, as in `test_r3_openai_client.py`).
3. **Live:**
   - **A mini-run:** the 9 budget-ended tasks' arms (9 runs).
   - **Then the full re-baseline (§5).**
   - **Measured:** budget endings, stale-edit repeats (the S4 metric), re-reads of stubbed files, solves.

## 3. Item 3: the trailing-off parse errors

### 3.1 Facts

- **What they are:** 17 of the 18 parse errors start a well-formed `edit_file` call, then emit runs of whitespace or
  `\n` until the JSON is invalid (up to 5,404 characters).
- **Ollama's answer for these:** no `prompt_eval_count`, no `eval_count`, no `done_reason`, with 9–105 s of model
  time. That fits a generation that was cut off rather than one that finished. The cause is not established.
- **Where:** 11 of them are one run, feature-08 PRISM, the same broken reply eleven times.
  - **The harness amplifies it:** the full degenerate reply goes back into history, followed by the format reminder,
    and the model copies it.
- **The other parse error** hit the 4,096-token answer limit (`done_reason` `length`).

### 3.2 Hypotheses

- **H-a: grammar-constrained decoding.** `format: "json"` allows unlimited whitespace between tokens, and the model
  falls into a whitespace loop. This is a known failure mode. It is not a stop-token problem, since the JSON never
  closes.
- **H-b: Ollama aborts the request** (an internal whitespace or timeout guard), which would explain the missing
  counts.
- **H-c: the content itself.** Long `new` strings, with multi-line code containing quotes, are where it happens
  (all 17 are `edit_file`).

### 3.3 Candidate fixes

| Fix | What | Where |
|---|---|---|
| F1 | **Stream and cut:** stream the reply; stop as soon as the first complete JSON object closes (brace balance, string-aware), or after N consecutive whitespace tokens; close the stream | `OllamaChat` (`stream: true`) |
| F2 | **A schema-constrained format:** Ollama's `format` accepts a JSON schema; constrain to `{"tool": enum, "args": object}` | `OllamaChat` |
| F3 | **No degenerate reply in history:** keep at most the first ~300 characters plus "[reply was not valid JSON]" before the reminder | `AgentRun` (both arms) |
| F4 | **Accounting:** when Ollama returns no counts, count the reply and prompt with the tokenizer | `OllamaChat` / `AgentRun` |

**Recommendation:** F1 + F3 + F4. F2 as an experiment.

### 3.4 How it is tested

1. **Unit tests against a fake streaming Ollama server** (as `tests/test_r3_openai_client.py` does for the API
   client):
   - a reply that closes then trails whitespace is cut at the brace;
   - a reply that never closes is cut after the whitespace limit and reported as a parse error with its counts;
   - strings containing braces are handled;
   - a normal reply is unchanged.
2. **Reproduction on Kaggle,** about 30 min, no new tasks:
   - **The prompts:** rebuild the exact prompts of the 17 failing turns with the replay tool. Prompts are
     deterministic from the turn log, and each turn's `prompt_sha` confirms it.
   - **The calls:** call devstral 10 times per prompt with the current client and with F1(+F2).
   - **Measured:** the degeneration rate before and after. This is the test of H-a / H-b.
3. **Live:** the re-baseline (§5) reports parse errors per turn, aiming well under Phase 0's 1.80%.

## 4. Item 4: the PRISM symbol error

### 4.1 Facts

- **Where it is rejected:** `src/prism/mcp/security.py:88`, `validate_symbol_name`, allows `^[\w.\-]+$`. devstral
  sent `packages/zod/src/v4/classic/schemas.object`, and it was rejected before any lookup.
- **The message** says only "invalid symbol name".
- **In Phase 0:** 3 of 38 calls, all in the pilot.

### 4.2 Fix (two steps)

1. **The message (do first):** state the accepted form and an example.
   - **For instance:** "invalid symbol name 'packages/zod/src/v4/classic/schemas.object': use a dotted name such as
     `module.Class.method`, or the bare function name `object`. File paths are not accepted."
   - **Reach:** this touches PRISM's error text only. The validator, a security boundary, is unchanged.
2. **The resolver (optional, after the message):** accept `path/to/file.ext::name` and `path/to/file.ext.name`.
   - **How:** map the path, once it has passed a containment check inside the repository, onto PRISM's dotted
     module name, then validate the dotted result with the unchanged validator.
   - **Why carefully:** the validator exists to stop path traversal, so the mapping must reject `..`, absolute paths
     and anything outside the indexed root.

### 4.3 How it is tested

- **Unit tests in `tests/`:**
  - the message for a slash name, an empty name and a too-long name;
  - for step 2: path forms resolve to the same symbol as the dotted form;
  - traversal attempts (`../x`, `/etc/passwd`, `a/../../b`) are still rejected.
- **The PRISM suite:** the existing test suite must stay green. It is the security boundary.
- **A replay:** of delete-01 PRISM turns 5–7, to confirm the new message, or the resolved output.

## 5. The Phase 1 re-baseline (after items 2–4)

**Why it is needed:** items 2–4 change the harness for both arms, so Phase 0's numbers become historical.

**The run:**
- the same 20 tasks, both arms, seed 42;
- the same model and the same cell, in a new stage;
- about 4 h.

**Pre-registered expectations:**
- parse errors < 0.5% of turns;
- budget endings ≤ 3 of 40;
- no loss of solves against Phase 0's 7 (both readings reported);
- infrastructure deaths ≤ 2.

**If solves drop:** the harness changes are checked first (stale-edit repeats, stubbed re-reads) before anything else
proceeds.

## 6. The comparison: PRISM against BASELINE (after item 1's interventions)

**Why 20 tasks × 1 seed cannot answer it:** as §0 shows.

**What the pre-registration must fix before the run:**
- **Unit of analysis:** the task, paired across arms.
- **Primary measure:** solve rate. Each task's score is the share of its seeds solved.
- **Test:** a paired sign or Wilcoxon test over tasks, or McNemar on task-seed pairs, chosen and recorded before the
  run.
- **Size:** chosen from a power calculation. The inputs are Phase 0's solve rates (PRISM 20%, BASELINE 30% of tasks)
  and the effect size that would matter to the user.
  - **Rough scale:** 20 tasks × 3 seeds × 2 arms = 120 runs (~12 h, two Kaggle sessions) is the minimum. Detecting a
    10-point difference will very likely need more tasks.
  - **The task pool:** the pending family gating (task #17 on the list) is where more tasks would come from.
- **Known-broken tasks** are excluded or repaired before the run; the list is in §8.

## 7. Item 5: ornith with native tool calling (backlog)

- **Why:** 4.5× faster generation would cut a 120-run comparison from ~12 h to ~3 h.
- **What it needs:**
  - `OllamaChat` with a `tools` parameter. Ollama's `/api/chat` returns `message.tool_calls`; map the first call to
    the loop's text form, as `OpenAIChat` does now;
  - the same tests as `test_r3_openai_client.py`'s native section;
  - an amendment.
- **Gate before use:**
  - the diagnostic rig with the harness client (ornith native 3/3 in §10.1 used the rig, not the harness);
  - then the first10 tasks on ornith, against devstral's checkpoint-3 numbers.
- **Not scheduled.**

## 8. Known issue for future task mining: broken pass-to-pass tests

Recorded at the user's direction (2026-11-15).

| Task | Pass-to-pass tests that fail in the run environment | Evidence |
|---|---|---|
| delete-01 (zod) | 1 (`recursive type with \`id\` meta`; its path-prefixed duplicate ID passes) | 4 unchanged runs, plus every run of every round |
| delete-02 (instructor) | 57 (`test_request_local_streaming.py`) | 2 unchanged runs |
| delete-04 (hono) | 4 (`c.redirect()` URL cases), flagged | every edited run, identical set |
| delete-05 (multidict) | 13, flagged | every edited run, identical set |
| delete-07 (scrapy) | 10, flagged | every edited run, identical set |

**The cause, in all five:** tests that passed at gating (start and commit) fail in the Kaggle run environment.

**For future mining:**
- **Gating must run in the same environment as the runs.**
- **Score the unchanged start state per task,** and drop or repair a task whose start state breaks any
  pass-to-pass test.
- **Treat duplicate test IDs** (zod's bare and path-prefixed names) as one test.

## 9. Implementation order and checkpoints

| Step | Content | Model calls | Output |
|---|---|---|---|
| 1a | Item 1 diagnosis on the Phase 0 logs (H1–H4) | none | the discordant-task narratives and the H1–H4 table |
| 1b | Item 4 step 1 (the message) + tests | none | PR-sized change to `src/prism`, tests green |
| 1c | Item 3 F1, F3, F4 + fake-server tests | none | `OllamaChat` streaming, tests |
| 1d | Item 2 B + C + the accounting fix + tests; counterfactual replay of the 9 budget runs | none | window and summary code, replay report |
| 1e | Kaggle: the item-3 reproduction (§3.4.2) and H5 (4 tasks × 3 seeds × 2 arms) | yes (~3 h) | degeneration before/after; seed-noise reading |
| ✓ | **Checkpoint:** review 1a–1e; pre-register the re-baseline | | |
| 1f | The re-baseline (§5), 40 runs | yes (~4 h) | Phase 1 baseline |
| ✓ | **Checkpoint:** choose PRISM interventions from 1a/1e; pre-register the comparison (§6) | | |
| 1g | PRISM interventions, PRISM arm before/after | yes | |
| 1h | The powered comparison | yes (≥ 12 h) | the PRISM vs BASELINE result |

**What is built without further approval:** 1a–1d need no model calls and can start on the user's go-ahead.
**What waits for its own go-ahead:** every Kaggle step, after its amendment entry.

## 10. Decisions for the user

1. **Budget semantics (§2.2 D):** keep "every prompt token counts" (recommended for comparability), or count only
   non-cached tokens.
2. **Item 4 step 2:** accept file-path symbol forms in PRISM's resolver, or stop at the better message.
3. **The comparison's effect size of interest** (§6): it sets the number of tasks and seeds. And whether to resume
   task mining (#17) to grow the pool.
4. **H5 (seed noise) before or after the harness fixes:** before measures noise on Phase 0's harness; after, on the
   one the comparison will use. **Recommended: after,** inside step 1e only if step 1c/1d are done.
