# Change propagation from git history: analysis of the sanity run

Run: Kaggle, code `2264d1b`, `qwen2.5-coder:14b-instruct-q8_0`, 22 calls, no errors, 1,411 s of generation.
Answers `6a54dbc`; tables `REPORT.md` (re-scored as below).

## Scoring correction (applies to both arms)

The scorer drops the model's restatement of the anchor itself. It matched the anchor by name only, so in
ch05 (anchor `App.redirect`) it also dropped PRISM's correct answer for the same-named caller
`helpers.redirect` in another file: the answer made exactly the developer's change (default `302` -> `303`).
The fix: a change is the anchor only when its stated file is the anchor's file. A regression test was added
(`tests/test_change_history.py`). Re-scoring changed one row: ch05 / PRISM, 0 -> 1 caller found. Nothing
else moved.

## Result (11 tasks, 29 callers that the developers changed)

| | FILE | PRISM |
|---|---|---|
| callers found | 13 / 29 | **23 / 29** |
| tasks with every caller found | 1 / 11 | **7 / 11** |
| wrong functions named | 4 | 1 |
| found callers' new code: developer's added lines present | 0.68 | 0.69 |
| found callers' new code: developer's removed lines gone | 0.71 | 0.66 |
| new code parses | 12 / 13 | 22 / 23 |

By tier:

| tier | FILE found | PRISM found |
|---|---|---|
| easy (4) | 3 / 7 | 3 / 7 |
| medium (4) | 1 / 5 | 3 / 5 |
| hard (3) | 9 / 17 | **17 / 17** |

## Reading

1. **PRISM helped the AI find the code to change**: 23 of 29 callers against 13 of 29, with fewer wrong
   functions named (1 vs 4). The gain is largest where the callers are outside the file the AI sees:
   - hard tasks: 17 of 17 vs 9 of 17 (httpx's seven `get` / `post` / ... wrappers of `request`, Flask's three
     `get_env` callers);
   - medium tasks: 3 of 5 vs 1 of 5.
2. **It did not make the edits themselves better.** For callers both arms found, the new code matches the
   developer's lines about equally (0.68 vs 0.69 added lines kept). The package tells the model *where*;
   *what* to write is still the model's job.
3. **Easy tasks did not move (3 / 7 each).** In these commits the callers changed because of the commit's
   wider intent, not because the anchor's change forced it:
   - ch01: type annotations tightened across a class (widening a parameter's type does not require
     callers to change; PRISM's answer was "nothing must change");
   - ch03: the `.env` loading order redesigned;
   - ch04: deprecated parameters removed everywhere.

   The tier rule measures *where* the callers are, not how forced the change is. That is a limit of this
   task set, worth fixing in a larger run (for example, keep only commits whose callers would break
   without the change: a removed or renamed parameter, a changed return or raised error).
4. **Limits:** 11 tasks, one model, one sample per arm, no significance test (as pre-registered). Two hard
   tasks share an anchor. The judgments of the easy-tier commits above are the author's.

## What it suggests next

A larger pre-registered run is worth doing, built from *forced* changes (signature, return or error
changes that break callers), with more hard and medium tasks, and two models.
