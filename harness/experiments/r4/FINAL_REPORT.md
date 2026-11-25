# PRISM Phase 3, POC 2: final report

**Run:** 45 runs = 5 tasks × 3 arms (BASELINE_LSP, PRISM_LSP, PRISM_GW) × 3 seeds (201, 202, 203). Model devstral-small-2
(24B, q4_K_M) on Kaggle; 60-turn cap; 1M-token budget; code `d769d27c`; cell `3adb756d`. Pre-registered in
`AMENDMENTS.md` ("Phase 3, POC 2" and "POC 2 amendment 1").
**Data:** `reports/r4/phase3_poc2_report.json` (the pre-registered analysis) and `reports/r4/phase3_poc2_ledger.md`
(every run's raw metrics). Infrastructure deaths 0; parse errors 0.2% of turns; no run hit the token budget.

## Verdict

**GO, under the pre-registered rule, for both PRISM arms.** The rule is ≥3 extra solves over BASELINE_LSP on the 4
hidden-dependency tasks, ≤1 such task lost, and ≤2 infrastructure deaths.

| Arm | Hidden-task runs solved (of 12) | Extra solves | Hidden tasks lost | Go |
|---|---|---|---|---|
| BASELINE_LSP | 2 | – | – | – |
| PRISM_LSP | 6 | +4 | 1 (p3-hidden-15) | GO |
| **PRISM_GW** | **10** | **+8** | 1 (p3-hidden-15) | **GO** |

The gateway arm (PRISM's reference graph plus a per-turn validator that keeps a worklist of the places still to
change) solved hidden dependencies that the IDE-tools baseline did not: p3-hidden-06, -13 and -16, in 8 of 9 runs. The
baseline solved none of those 9 runs.

This is a **proof of concept, not a confirmed effect.** It rests on 4 hidden-dependency tasks, all renames, all in
Python, with one model. At the level of tasks, no test with 4 tasks can reach p < 0.125, and the measured p is 0.25.
A confirmatory run on a larger, pre-registered task set is needed before the result is claimed in general.

## Evidence

**1. Solves, run by run (pairs of the same task and seed).**

| Comparison | Only the first arm solved | Only the second arm solved | McNemar exact p |
|---|---|---|---|
| PRISM_GW vs BASELINE_LSP, as run | 9 | 1 | 0.021 |
| **PRISM_GW vs BASELINE_LSP, p3-hidden-06 counted once** | **7** | **1** | **0.070** |
| PRISM_LSP vs BASELINE_LSP | 6 | 2 | 0.29 |
| PRISM_GW vs PRISM_LSP (descriptive) | 4 | 0 | 0.125 |

The second row is the honest figure. PRISM_GW's three hidden-06 runs are one transcript, not three independent
results (see the caveats). Counted once, the gateway's lead is 7 to 1, p = 0.070: a strong direction, but not
significant at 0.05.

**2. Finishing against exhausting the turns** (the 12 hidden-task runs per arm).

| Arm | Ended by calling `done` | Hit the 60-turn cap | Median turns | Median turns, solved runs | Mean tokens |
|---|---|---|---|---|---|
| BASELINE_LSP | 2 | 10 | 60 | 44 | 323k |
| PRISM_LSP | 3 | 9 | 60 | 48.5 | 338k |
| PRISM_GW | 9 | 3 | 31 | **28** | 355k |

- **The gateway finished early:** its solved runs ended with `done` at a median of 28 turns, after the worklist reached
  zero.
- **The baseline ran out of turns:** in 10 of 12 runs it reached the 60-turn cap. It found the places with `grep` and
  `read_file`, and on hidden-06, -13 and -16 it never finished.
- **The gateway used more tokens per run** (about 10% more than the baseline). Its messages add about 16k characters
  per run, and its two failed runs were long.

**3. What the gateway did** (15 PRISM_GW runs, `gateway_effectiveness`).

| Intervention | Count | Followed | Ignored | Notes |
|---|---|---|---|---|
| Worklist (places still to change) | 219 | 210 | 9 | the places were correct at the end in 175 |
| Stall warning | 111 | 2 | 109 | almost always ignored: noise as built |
| Tool suggestion | 12 | 12 | 0 | |
| Blocked `done` | 2 | (2 prevented) | 0 | both in the failed hidden-15 runs |
| Look-alike or syntax block | 0 | | | none fired |

- **Prevented:** 2 tool calls (the blocked `done` calls) and 0 edits.
- **Where the value came from:** the worklist that was followed, not the blocks.

**4. r4s-delete-09 (descriptive only, as pre-registered).**
- **Scores:** all 9 runs solved it, in all three arms, and every run hit the turn cap.
- **The 21 start-state places that used the deleted name:**
  - PRISM_GW left all 21 resolved in all 3 runs.
  - PRISM_LSP left 3–5 confirmed broken in each run, and 2 still using the name in seed 202.
  - The baseline left 3–5 confirmed broken in each run, and 9–14 still using the name in seeds 202 and 203.
- **This cannot be attributed to the gateway or to the historical snapshot:**
  - the gateway acts only on rename tasks, and made 0 interventions on delete-09;
  - no agent ever asked for a historical answer (0 snapshot reads);
  - the scoring restores the test files, so the score does not reflect the leftover places.
- It is one task, reported as an observation.
- **Isolation held:** no baseline run built a snapshot. The post-run evaluation cost about 30 s and 437 MB, in its own
  process.

## Caveats

**1. The identical-seed anomaly.**
- **What happened:** PRISM_GW on p3-hidden-06 produced the same transcript in all three seeds, with identical tool
  calls and arguments, 28 turns and 223,466 tokens.
- **It is the only such case:** each of the other 14 arm-and-task groups has 3 distinct transcripts, so the seed
  does vary the model's output.
- **Likely explanation (not verified):** the worklist left the model one obvious next edit at every turn, so sampling
  never changed the path.
- **Consequence:** those three solves are one observation. All the statistics above are given both ways.
- **For future runs:** seed-level identity should be checked, and identical transcripts collapsed, before any test.

**2. The gateway is not perfect: p3-hidden-15** (rename `SessionMiddleware`, 16 places; PRISM_GW solved it 1 of 3).
- **Seeds 201 and 203 failed the same way, and not because of a wrong worklist.**
  - The worklist was right: it named the remaining places exactly, including `tests/conftest.py:181`, and there was no
    false positive.
  - Line 181 has no indentation (`) -> SessionMiddleware[ClientSideSessionBackend]:`).
  - The model's `edit_file` kept sending the line with leading whitespace:
    - seed 201 sent four spaces;
    - seed 203 sent a tab, copied from the line-number separator in the error message.
  - The edit tool answered each time that the text "matches once apart from whitespace, at line 181", and showed the
    exact line. The model did not adapt.
  - The loop breaker then refused the identical call 41 times (seed 201) and 33 times (seed 203). The model kept
    sending it until the turn cap, and the stall warnings were ignored.
  - So 45 of the 54 edits were that one call, and the 690k and 635k tokens are those turns' growing prompts.
- **Effect:** the runs never reached the hidden test file's last uses. 2 places were left (seed 201) and 1 (seed 203).
- **This is a model and edit-interface failure that the gateway did not recover from.**
- **Fixes to evaluate before the next run (not made):**
  - apply an edit when its text matches exactly one place apart from leading whitespace;
  - or have the gateway, after a refusal, give the exact replacement for the line it lists.
- **The baseline solved this task 2 of 3;** it is the one hidden task each PRISM arm lost.

**3. Scope.**
- **Tasks:** 4 hidden-dependency renames, 1 delete task.
- **Repos:** litestar, aiohttp, fastmcp, pydantic-ai.
- **Setting:** one model, one quantisation, a 60-turn cap.
- **Not tested:** other languages, other edit kinds (signature changes, moves), larger models.

**4. The gateway's cost.** About 23 interventions and 16k characters per run. The stall warning should be removed or
redesigned: 109 of 111 were ignored.

## Next step (proposed): Phase 4, productization

- **The idea:** package the gateway and `prism_find_references` as an MCP server, so that MCP clients such as Cursor
  and Claude Desktop get the reference graph and the per-edit worklist.
- **Before committing to it, three prerequisites from this POC:**
  1. Fix the hidden-15 failure mode (the whitespace-tolerant edit, or the exact-replacement hint) and the stall
     warning's noise.
  2. Confirm the effect with a pre-registered run on a larger task set: more tasks, other edit kinds and repos, and
     seed-identity checks, sized so that a task-level test can reach significance.
  3. Measure the gateway against a frontier model in an MCP client. The 24B model here may benefit more from guidance
     than the clients' own models.
