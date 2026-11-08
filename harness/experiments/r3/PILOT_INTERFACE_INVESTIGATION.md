# R3 pilot: are PRISM's tool responses single-shot by design? (investigation, 2026-11-08; no code changes)

**Hypothesis under test:** PRISM's responses present information without saying what to do next. In a multi-turn
loop that leaves an ambiguous "what now?", which causes loops or stalls.

**Data:**
- the replayed pilot windows (see `PILOT_LOOP_INVESTIGATION.md`: the recorded replies, run through the real
  harness);
- every tool's real output at the B-09 start state, captured offline;
- the R1/R2 prompt code (`harness/experiments/change_history/run.py`, `harness/experiments/r2/build.py`).

No model calls.

## 1. Turn structure (R3 pilot, both arms identical)

- **One system prompt**, the same in every call. It is the same text in both arms apart from the tool list (`loop.py`, `SYSTEM`):
  > You are a software engineer working in a code repository. You work in turns: each turn, reply with exactly one
  > JSON object calling one tool: {"tool": "<name>", "args": {...}} No other text. When your changes are complete,
  > call "done". Tools: (one line per tool) … Symbols are fully qualified names as the repository's index writes
  > them.
- **One task message**, the same in every call (the task's `prompt`):
  > Function `X` in `file` was changed as shown: (the diff) Update the rest of the project so its tests pass. The
  > change to `X` is already in the working tree. When your changes are complete, call done.
- **Then the raw history.** It alternates the model's own call, re-serialized as JSON, with the tool's output
  verbatim. The window rule stubs the oldest outputs first, then drops turns.
- **No per-turn instruction.** In all 71 windows of the three looping runs there is no user message other than a
  tool output. The system prompt and task message are byte-identical in every window.
  - The goal is stated once, at the top. By turn 4 in B-08 and B-09 it sits above about 9,000 to 10,000 tokens of
    tool output.
- **The system prompt gives no workflow** (for example read, then edit, then test). It names only the format and
  the stop condition.

## 2. Tool response endings (real outputs, B-09 start state; run_tests from the replayed windows)

| Tool | Arm | Ends with | Framing |
|---|---|---|---|
| `run_tests` | both | the runner's raw tail (`# tests 25 / # pass 2 / # fail 38`, or pytest's `FAILED …` lines) | data |
| `read_file` | both | the last numbered line of the requested range | data (code) |
| `grep` | both | the last `path:line: text` match, or `no matches for 'X'` | data |
| `list_dir` | both | the last entry | data |
| `edit_file` | both | `edited F at line N: replaced A lines with B` / `error: the text to replace was not found in F` | confirmation / error |
| `done` | both | `done` | — |
| `related_code` | BASELINE | the last function's code, or `... [cut at 4,000 tokens]` | data (code) |
| `prism_change_context` | PRISM | `## 8. Wider impact` / `15 transitive callers; by distance: 1 hop: 1, …` | data (a summary line) |
| `prism_blast_radius` | PRISM | `</prism_context>` (XML), or `... [cut at 4,000 tokens]` | data (machine format) |
| `prism_ask` | PRISM | a raw JSON dump (`"question": "…"\n}`) | data (machine format) |
| `prism_digest` | PRISM | the last digest line (`L934 raise (re-raises the current exception)`) | data |
| `prism_new_function_context` | PRISM | the last similar function's code | data (code) |

- **No tool in either arm ends with an instruction, a question, or a next step.** Every one ends with data. The
  endings are symmetric across arms: the six shared tools are literally the same tools.
- `prism_change_context` ends with a summary line ("15 transitive callers; by distance …"). That could be read as
  a conclusion, but it is no more "complete-sounding" than grep's last match.

## 3. Single-shot (R1/R2) vs multi-turn (R3)

**R1/R2** (`change_history.run.prompt`, PRISM arm), one call:
> You are a software engineer working in the repository `{repo}`. The function `{anchor}` in `{file}` has just been
> changed. Before: (old code) After: (new code). Other code in the repository may have to change because of this.
> **Find every OTHER function that must change so the code keeps working, and write its complete new code.** Change
> nothing that does not need to change, and follow the style of the surrounding code. Respond with a JSON object:
> {"changes": [{"function", "file", "reason", "new_code"}]}. If no other function must change, respond
> {"changes": []}.

That is followed by the anchor file (300 lines around the anchor), then "PRISM change context for `{anchor}`
(computed before the change):" and the context.

| | Single-shot (R1/R2) | Multi-turn (R3) |
|---|---|---|
| The question PRISM's context answers | stated, right before it: "find every OTHER function that must change" | not stated. The task says "update the rest of the project so its tests pass" |
| What the answer is | the edits themselves (`new_code` per function): reading and acting are one step | a separate `edit_file` call the model must decide to make |
| How PRISM's context arrives | labelled, as part of the prompt ("PRISM change context for X") | as a tool result in the history, unlabelled |
| Anchor file | 300 lines in the prompt | must be read (`read_file`) |
| Feedback | none | tests, edit results |
| Explicit "nothing to change" answer | `{"changes": []}` | `done` |

The hypothesis is confirmed in this precise sense. The single-shot prompt had the framing that made PRISM's
context actionable: an explicit question ("which other functions must change?") and an answer format that is the
edit. The multi-turn prompt has neither, for either arm: the task is stated in terms of tests, and acting is a
separate decision. The PRISM context itself was built for the single-shot use. Its first two sections are a
repository and group overview, a briefing rather than a step.

## 4. Turns 3, 4 and 5 in the looping runs

What each window added (the newest call and its output), and whether that output had been seen before
(test counts and temp paths ignored):

| Run | Turn 3 input adds | Turn 4 input adds | Turn 5 input adds | First call whose output is an exact repeat |
|---|---|---|---|---|
| B-02 | `prism_change_context` (new) | `prism_blast_radius` (new, but the same single caller again) | `run_tests` (same failures, flaky counts) | turn 5's call, C again: from turn 6 every addition is a copy |
| B-08 | `prism_change_context` (new) | `prism_blast_radius` (new, 14,647 chars, mostly XML) | C again: an exact copy | turn 4's call: from turn 5 every addition is a copy |
| B-09 | `prism_change_context` (new) | `prism_blast_radius` (new, cut at 4,000 tokens) | `run_tests` (same failures) | turn 5's call: from turn 6 every addition is a copy |

- At turn 4 the model had new text, the blast radius. But it added nothing actionable beyond `change_context`: the
  same caller or callers, in XML.
- From the first repeat on, **every turn adds only an exact copy of something already in the window, and no
  instruction**. The model's input changes only by growing. It never receives a reason to do something different.
- In that sense the loop is structurally induced once it starts. The window becomes a repeating pattern, which a
  14B model continues.

The cue the model was missing is the same one in all three: that the caller's call no longer matches the anchor.
- B-02: the caller passes 3 positional arguments; the anchor takes one object.
- B-08: the caller does not pass the new `isSync`.
- B-09: the caller does not pass the new `futures`.

All three callers are in the developer's fix. Plus, for each task, that the anchor calls a function that does not
exist (D1/D2 in the loop report).

## 5. The baseline's "cues"

Baseline's tools give no action cue either; they end with data. The asymmetry is in shape, not in endings:
- **Varying vs fixed queries.** grep, related_code and read_file take a free argument (a pattern, a query, a line
  range), so each new call can return something new. Baseline's model varied them: B-08 searched
  `normalizeStdioSync`, then `normalizeStdioAsync`. PRISM's two context tools take only the symbol, and the task
  names exactly one symbol, so the only "new" call is the same call. Re-asking is necessarily identical.
- **Edit-ready vs analysis.** grep and read_file return file text with line numbers, the exact substrate for
  `edit_file`'s `old`. PRISM's tools return analysis about code: sections, digests, XML, with the code inside
  fences or CDATA. Both arms had the code; baseline's came in the shape an edit is made from.
- **Every looping PRISM run used only the PRISM tools to read** (0 `read_file`, 0 `grep`). The PRISM runs that
  edited (B-04, B-05, B-07) all called `grep` or `edit_file` within their first 3 turns.

## 6. Early-done runs

The tool output just before `done`:
- `run_tests` all passing: B-06 in both arms;
- `grep` "no matches" in the test directory: B-01 PRISM, B-04 BASELINE, B-10 in both arms;
- `prism_blast_radius` (8 callers listed): B-03 PRISM, followed by a false "ran tests to ensure they pass".

Six of the seven stop after an empty or all-clear result ("nothing to do"), not after a complete-looking briefing.
Only B-03 fits "too-complete information leads to a premature done". Mostly, then, early-done is the model reading
a nil result as "no change needed" while ignoring the failing tests it saw at turn 1. That is the same in both
arms.

## 7. Verdict: (c) partial

- **Not confirmed as stated:** no tool in either arm ends with a cue, so there is no endings asymmetry. Baseline is
  not cued to act any more than PRISM is.
- **Confirmed in a different form:**
  1. **Framing lost from single-shot.** R1/R2 attached PRISM's context to an explicit question ("which other
     functions must change?") and an answer that was the edit. R3's loop has neither, in either arm. PRISM's
     context, a briefing built for that one-shot question, arrives as an unlabelled tool result.
  2. **PRISM's context tools are fixed-argument.** Re-asking returns an identical answer, so once the answer does
     not point to an edit, the loop's only moves are repeat, test, or stop. Baseline's free-argument tools always
     offer a different next query.
  3. **What the answer lacks** is the concrete "this call no longer fits" line per caller (and D2, unresolved
     calls). That would turn the briefing into a step.

**Possible fixes:** none are implemented. Here is how they classify:
- **A workflow line in the system prompt, or a per-turn reminder of the goal:** a change to the loop. It must be
  identical in both arms, and the loop is pre-registered (R3-1, R3-5). It is a rule change, so it is out of scope
  for R3 (R4).
- **Restoring the single-shot question in the task message** ("find every other function that must change"):
  also a rule change, because the task prompt is part of the hash-committed task list.
- **A closing "What this means for the change" block in `prism_change_context`'s output**, listing:
  - each direct caller, with the concrete mismatch between its call and the anchor's current parameters (B-02:
    "passes 3 positional arguments; the anchor now takes one destructured object");
  - each call in the anchor to a name not defined or imported in its file (D2).

  This is a **tool-output change**. It touches only PRISM's tool and leaves the loop, the prompts and the tasks as
  they are. It is not a rule change, but it is a change to the treatment after the pilot. That is a decision for an
  amendment.
  - It should be descriptive ("this call does not match"), not prescriptive ("now edit X"). A prescriptive cue
    would be wrong wherever the callers are not the work (B-06, the hidden-test tasks).
  - **Testability:** the rendered text can be checked offline on every R3a start state, with no model calls (is
    it correct? is it present where it should be?). Its effect on the model cannot be tested without model calls:
    that needs at least the pilot's PRISM half re-run.
  - The obvious source, `prism.impact.breaks`, is not usable as is: on the three looping tasks it gave one wrong,
    one empty and one half-right result (loop report §4). So this is new code, about a day, plus checks.

## What this changes before R3a

- It narrows the question. The loop is not caused by tool endings, and nothing in either arm cues action. But R3
  moved PRISM from a setting where its context was the input to an explicit "which functions must change?" question
  to one where it is an unframed tool result. In that setting its fixed-argument tools are loop-prone when the
  answer does not show the edit.
- That is a property of PRISM in multi-turn use, and R3a will measure it as it stands. **Proceeding as planned is
  still sound,** if the interpretation names it: "PRISM's context tools were built for single-shot use; in
  multi-turn they answer once and then repeat."
- The per-arm loop and early-done counters (not implemented yet) and the missing-name covariate are what R3a needs
  to show it.
- **If you would rather R3a test PRISM with that gap closed:**
  - the minimal change is a descriptive caller-mismatch and unresolved-call block in `prism_change_context`, a
    tool-output change, possibly together with D1 and D2 from the loop report;
  - it needs an amendment, offline checks on all 80 start states, and a re-run of the pilot's PRISM half before
    R3a;
  - nothing in the loop, the prompts or the tasks changes.
