# Smoke test 3: trace of the PRISM arm (`r4s-feature-02__PRISM__42`)

Source: `reports/r4/smoke3/r4s-feature-02__PRISM__42.jsonl` (code `299e08f0`; devstral-small-2; raw tool path; turn
cap 12). The turn log keeps each reply in full and the first 300 characters of each tool output. The full `prism_ask`
answer was regenerated locally at the task's commit (litestar `6e432d0d6b4a`) through the same `ToolLayer` call.

## The task

**The prompt** (feature family, litestar): "Implement the following feature. feat: add plain text traceback to HTML debug response context", then the
upstream commit's bullet list:
- toggle UI and a textarea;
- toggle/copy JavaScript functions;
- CSS;
- tests;
- "separate copy button and view toggle".

**Then the standard tail:**
- the failing test `tests.unit.test_exceptions::test_debug_html_response_contains_javascript_functions`;
- "run `run_tests` yourself", "28 turns" (the smoke cell caps at 12).

**Gold files:** `litestar/exceptions/responses/_debug_response.py` and `litestar/exceptions/responses/templates/scripts.js`.

**Outcome, all three arms:**
- all ended at the 12-turn cap, unresolved, with no parse errors;
- PRISM is the only arm that edited a file (the right one: file F1 0.67, function F1 0.50);
- tokens: BASELINE 27K, INJECTED 67K, PRISM 92K.

## Turn by turn

| Turn | Prompt tok | Model s | Answer tok | Call | Result (tokens) |
|---|---|---|---|---|---|
| 1 | 1,371 | 4.6 | 21 | `prism_ask` "Where is the debug HTML response context code?" | ranked list, no code (1,308) |
| 2 | 2,823 | 4.8 | 20 | `read_file _debug_response.py` | whole file, lines 1–210 (2,340) |
| 3 | 5,338 | 6.3 | 16 | `list_dir …/templates` | body.html, frame.html, scripts.js, styles.css (19) |
| 4 | 5,375 | 1.8 | 19 | `read_file templates/body.html` | 20 lines (178) |
| 5 | 5,599 | 2.2 | 18 | `read_file templates/scripts.js` | 27 lines (260) |
| 6 | 5,938 | 2.8 | 18 | `read_file templates/styles.css` | 121 lines (945) |
| 7 | 6,964 | 4.5 | 16 | `read_file tests/unit/test_exceptions.py` | lines 1–341 of 353, cut at the cap (3,945) |
| 8 | 10,005 | 22.2 | 83 | `read_file` the same test file, lines 297–312 | the target test (195) |
| 9 | 7,818 | 20.0 | 101 | `prism_new_function_context` (`add_traceback_ui_elements`) | group, helpers, siblings (3,999, at the cap) |
| 10 | 12,123 | **81.5** | **892** | `edit_file _debug_response.py` | **failed:** text not found; closest lines from 128 (245) |
| 11 | 13,453 | 24.9 | 31 | `read_file _debug_response.py` lines 128–162 | the real function (495) |
| 12 | 13,743 | 48.7 | 320 | `edit_file _debug_response.py` | applied: line 151, 10 lines → 13 (27) |

**Totals:** 224 s of model time; the tools took 7.3 s, of which `prism_ask`'s first call (index sync) was 5.7 s.

## What `prism_ask` returned (turn 1)

Ten hits, each with `qname`, `location`, `kind`, `rank`, `score` and the score legs (`stems`, `action`, `capability`,
`capability_source`), plus `entry_context`, `pinned`, and the question and intent. No code, no docstring, no summary.

| Rank | Hit | Location |
|---|---|---|
| 1 | `…_debug_response.create_line_html` | `_debug_response.py:59` |
| 2 | `…_debug_response.create_exception_html` | `:113` |
| 3 | `…_debug_response.create_frame_html` | `:87` |
| 4 | `…_debug_response.create_debug_response` | `:175` |
| 5 | `…_debug_response.create_html_response_content` | `:128` (the function the gold patch changes) |
| 6–10 | `Template.to_asgi_response`, `Template`, `ExceptionHandlerMiddleware.default_http_exception_handler`, `get_symbol_name`, `Template.__init__` | |

**Assessment:**
- **The file was right:** five of the top five hits are in the gold file.
- **The function was 5th.**
- **The answer cost 1,308 tokens** for what is, in effect, one file path. The agent's next move was to read that whole
  file.

## Which files were explored, and with what

| Tool | Calls | Targets |
|---|---|---|
| PRISM | 2 | `prism_ask` (turn 1), `prism_new_function_context` (turn 9) |
| `read_file` | 7 | `_debug_response.py` twice; the three templates; the test file twice |
| `list_dir` | 1 | the templates directory |
| `edit_file` | 2 | `_debug_response.py`: one failed, one applied |
| `run_tests` | 0 | |

Never reached: `scripts.js` (the other gold file) was read but not edited, and the test was never run.

## The failed edit (turn 10)

**What it was trying to change:** the whole of `create_html_response_content` (lines 128–160). It added a plain-text
traceback (`create_plain_text_response_content(exc)`, a real helper at line 163) and passed it to the body template.

**Why it failed:** `old` was not the file's text.

| Line | `old` said | The file says |
|---|---|---|
| 129 (docstring) | `"""Create HTML response content for an exception.` | `"""Given an exception, produces its traceback in HTML.` |
| 134 | `line_limit: Maximum number of lines to show per frame.` | `line_limit: Number of lines of code context to return, which are centered around the executed line.` |
| 136–137 | `HTML content for the exception.` | `A string containing HTML page with exception traceback.` |
| 139 | `exception_data = [...]` | `exception_data: list[str] = [...]` |

**Why `old` was wrong:**
- **Compaction had already removed the source text.** At turn 10, compaction had summarised 2 outputs: turn 1
  (`prism_ask`) and turn 2 (the whole-file read). Both were more than 6 tool outputs back (rule 4). The agent's
  only copy of the file was a one-line summary ("`_debug_response.py` lines 1–210 … read again for the text").
- **So the model wrote the function from memory:** a plausible docstring, not the real one.
- **The edit cost about 106 s**, nearly half the run's model time:
  - turn 10: 892 answer tokens, 81.5 s;
  - turn 11, the re-read: 24.9 s.
- **What followed:** turn 12 re-did the edit on a smaller span copied from turn 11's read, and it applied.

## Where the time went

| Phase | Turns | Model s | Share |
|---|---|---|---|
| Orientation (`prism_ask`, read gold file, list templates) | 1–3 | 15.7 | 7% |
| Reading templates and the test file | 4–8 | 33.5 | 15% |
| `prism_new_function_context` | 9 | 20.0 | 9% |
| Failed edit and re-read | 10–11 | 106.4 | 47% |
| Applied edit | 12 | 48.7 | 22% |

**Two drivers:**
1. **Generating edit text:** answer tokens at about 11 per second, so 892 tokens is about 80 s.
2. **Prompt size:**
   - the test-file read (3,945 tokens) and `prism_new_function_context` (3,999) took the prompt from 6K to 10–14K;
   - each later turn re-processes the whole prompt (turn 8: 22 s for 83 answer tokens).

## What this says about the PRISM tools in the loop

1. **No PRISM tool gives code the agent can copy into an edit:**
   - `prism_ask` gives locations;
   - `prism_change_context` gives code without blank lines or line numbers;
   - `prism_new_function_context` gives context for a new function.

   So every PRISM lookup is followed by a `read_file`, and the PRISM arm pays for both.
2. **Old reads are lost to compaction**, in both arms. An edit made several outputs after the read is written from
   memory. Any tool that returns exact, numbered code just before the edit avoids that.

**The response:** `prism_read_code` (amendment "A sixth PRISM tool", `AMENDMENTS.md`). It takes a symbol, a
`prism_ask` location or a file, and returns the exact numbered lines, so the path can be `prism_ask` →
`prism_read_code` → `edit_file`.
