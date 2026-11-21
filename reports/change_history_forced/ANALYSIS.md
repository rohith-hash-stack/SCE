# Run 2 (forced changes, three versions): analysis

12 frozen tasks, 20 callers the developer had to change. Model `qwen2.5-coder:14b-instruct-q8_0`, run on
Kaggle at `d6cdec8`. There were no failed calls. Descriptive only: no significance test.

## Results

| | FILE | PRISM | PRISM_MAP |
|---|---|---|---|
| callers found | 3 / 20 | **9 / 20** | 8 / 20 |
| tasks with every caller found | 1 | **6** | 5 |
| wrong functions named | 7 | 0 | 0 |
| answered "nothing to change" | 3 | 6 | 7 |
| prompt tokens (mean) | 4,204 | 7,073 | 7,675 |
| answer tokens (mean) | 870 | 313 | 243 |
| seconds per answer (mean) | 42.4 | 18.0 | 15.2 |

Writing the map and second-ring sentences took 539 s for the 12 tasks (about 45 s per task).

## Reading

- **PRISM against FILE:** run 1's result holds. PRISM found 3× more callers (9 vs 3) and named no wrong
  functions (0 vs 7). FILE was slower because it wrote long answers for the wrong functions; ch12 alone
  took 215 s for four wrong functions.
- **PRISM_MAP against PRISM:** no gain. The answers are the same on 11 of 12 tasks. The one difference
  is ch03, where PRISM found the caller and PRISM_MAP answered "nothing to change". The map adds about
  600 tokens per prompt and about 45 s of preparation per task.
- **Why callers were missed:** PRISM's context contained the code of all 20 callers, so none of the 11
  misses was a visibility problem. In 6 tasks the model answered `{"changes": []}` even though the change
  breaks the call it was shown. Examples:
  - **ch09:** `parse_tuple(no_condexpr=True)` after the parameter `no_condexpr` was removed (a TypeError);
  - **ch10:** `pull_locals(frame, True)` after `no_indent=False` became `indent=True` (the meaning flips);
  - **ch02:** `signature(node, frame, False, extra_kwargs)` after `have_comma` was removed (the arguments shift);
  - **ch03:** a new required parameter `poolmanager`, with a caller passing 3 arguments.
- **Questionable tasks:**
  - **ch05, ch08 (express):** the "caller" is a wrapper rewritten as part of a larger API change. No
    signature check flags it, so these two are more about intent than forced breaks.
  - **ch12:** the developer's changes to the callers are not mechanical either.

## What this suggests

The model sees the broken call and still does not conclude that it breaks. A mechanical **break check**
could state it outright: compare the new signature with each call site's arguments (the call sites are
already in section 4) and write, for example, "`Parser.parse_for` line 12 passes `no_condexpr=`, which no
longer exists: this call fails".

A rough offline version of this check (`select._param_names` on each caller's old code) flags 14 of the
20 callers, including 8 of PRISM's 11 misses. Two cautions:
- its rate of false alarms on callers that did not need changing has not been measured;
- the ch04 flag is weak: the parameter was only renamed, so the call does not break.

## Decision

- Keep `PRISM` as the package.
- Do not take `PRISM_MAP` to a bigger test.
- The next candidate is the break check, tested the same way: a frozen set, the same model, and its false
  alarms measured on unchanged callers.
