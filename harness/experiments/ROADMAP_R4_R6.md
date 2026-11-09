# Roadmap R4 → R6, and going live (adopted by the user, 2026-11-09; planning document, nothing built yet)

## The user's direction (recorded as given)

- **R4:** PRISM helps an LLM in multi-turn tasks, with a loop-aware protocol. Any fix or implementation PRISM needs
  is done **before** R4 runs.
  - The problem R4 targets: coding agents do not understand the surrounding, related code (callers, callees,
    dependents), so they give improper responses, and the user has to re-verify.
  - PRISM already holds those relationships (the nodes and edges of its graph), so it should resolve this.
- **R5:** a double-check, a verification run of R4's result, after R4.
- **R6:** cross-model verification, once R5 holds.
- **Going live:** PRISM goes live after R5 or R6. *(Noted in §6, and in `IMPLEMENTATION_LOG.md`.)*

## Renumbering (this replaces the earlier assignments)

| Earlier name | Source | Now |
|---|---|---|
| R4 = "user query → plan" | `r3/README.md` R3-1 item 7; R3-2 item 4; `r4/README.md` | **parked**, after going live (§7). The R4 name is reassigned |
| R4 notes on base+delta divergence detection | `r4/README.md`; `AMENDMENT_R3-5.md` | **go-live prerequisite** (§6), not a research round |
| R5 = cross-model | `r3/README.md` Decisions table "model"; R3-2 item 4 | **R6** |
| R3b (Family N, refactor, delete, move, tests) | `r3/README.md` R3-1 item 6 | **unscheduled breadth track**, after going live (§7) |
| "R4a/b/c" | `CONFIDENCE_DESIGN_STUDY.md` §7 | folded into R4's build (§2) |
| "R4 plans to address" | `INTERPRETATION_NOTES.md` §4 | now correct as written: the new R4 is that round |

`r3/README.md` and `r4/README.md` are not edited: the R3 README is hash-committed, and the old R4 file is history.
This document is the current plan.

## 1. What R4 claims, in measurable terms

The agenda's problem has three parts, and R3's scoring already measures each:

| The problem | Measured by | Where (already in the code) |
|---|---|---|
| The agent misses related code (a caller left unchanged) | **resolve rate** (the primary); file F1 against the developer's change | `runner.score`, `analyze.compare` |
| The agent breaks related code (collateral damage) | **pass-to-pass tests broken** | `runner.score` (`pass_to_pass_broken`) |
| The user has to re-verify (the agent says done, but it is not) | **unresolved after done** (a false "done"), and early done | `analyze` secondaries; `turn_flags` |
| The agent gets stuck (loops) | **looping runs per arm** | `analyze.turn_flags` / `diagnostics` |

**R4's primary claim** (same form as R3a's, for comparability):
> With a loop-aware protocol applied to both arms, an agent with PRISM resolves more Family B tasks than the same
> agent without PRISM. Per-task means over two seeds; Wilcoxon; α 0.05; +0.10 floor.

**Pre-registered secondaries:**
- fewer false "done" runs;
- fewer pass-to-pass breaks;
- fewer loops;
- the missing-function split.

## 2. What must be built before R4 runs

Ordered by dependency. Costs are *guesses* unless marked.

| # | Build | What it fixes (from the record) | Where in the code | Effort *(guess)* |
|---|---|---|---|---|
| **B1** | **Correctness: D1**: a receiver-less call never resolves to a method; a scored winner among 2+ candidates is tentative | wrong facts (`CONFIDENCE_DESIGN_STUDY.md` §0–§1) | `src/prism/graph/concrete_builder.py` `_resolve_ambiguous_call`; `src/prism/summaries/digest.py` `_check_receiver` | 0.5 day |
| **B2** | **Correctness: the break check** compares against the committed version (`git show HEAD:`); fix the arrow-function `signature()` parse | wrong, empty or half-right checks (§0) | `src/prism/impact/breaks.py`; `context.py` / `mcp/server.py` | 1 day |
| **B3** | **Honest resolution, D2:** calls to names the file neither defines nor imports are listed ("not defined or imported here"; "exists in X, not imported") | silence on missing helpers: all three pilot loops | `digest.py` `_call`, `_unresolved_target`, renderer | 0.5 day |
| **B4** | **The relational summary in `change_context`**: the closing block "What this means for the change": each caller whose call no longer fits, each missing callee | the agent does not see what the relationships imply; the core of the agenda | `src/prism/impact/context.py` `render` (from B2 and B3) | 0.5 day |
| **B5** | **Post-edit verification, a new PRISM tool** (working name `prism_check_changes`, no arguments): after the agent's edits, every changed function (the working tree against `HEAD`) checked against its callers and callees. It reports calls that still do not fit, callers not yet updated, missing names, and which edits are consistent | the user having to re-verify: PRISM does that check. Its output changes as the tree changes, so it is **not identical on repeat**, and a re-query brings new information (the fixed-argument loop) | new tool over `WorkingIndex.refresh()` (the R3-5 live index, which already rebuilds after edits) + `BreakChecker` + B3; served in `harness/experiments/r3/tools.py` and `src/prism/mcp/server.py` | 2 days |
| **B6** | **Loop-aware protocol, both arms** (a protocol change, pre-registered): (a) a repeated identical call gets a short notice ("you already made this call; its result is unchanged; the last test result was …"); (b) the last test result stays pinned in the window | the model repeating itself, and early done after a nil result | `harness/experiments/r3/loop.py` (`AgentRun.run`, window rule) | 1 day |
| **B7** | **Readable tool formats:** blast radius as a hop list with code, without the XML envelope; `prism_ask` as a ranked list, not raw JSON | machine formats served to an agent (`PILOT_LOOP_INVESTIGATION.md` §4) | `harness/experiments/r3/tools.py` | 0.5 day |
| **B8** | Analysis additions: the false-done rate and pass-to-pass breaks as named secondaries; a repeat-notice count per arm | measuring the agenda | `harness/experiments/r3/analyze.py` | 0.5 day |

**Verification before R4** (the same discipline as before R3a):
- unit tests for B1–B8;
- E1–E3 on all repositories, because B1 changes graph edges;
- the blast-radius test-code check;
- the dry run (gold resolves, empty does not) on all tasks;
- offline renderings of B4 and B5 on the three pilot loop tasks, checked against the developer's fix;
- an **R4 pilot**: the 10 pilot tasks × 2 arms under the new protocol, 20 runs, about 1.3 GPU-hours *(measured
  pilot pace)*.

**Total:** about **7 days of code** plus about **3–4 days of verification and the pilot** *(guess)*.

**Parity check:**
- B1–B5 and B7 change PRISM's tools only. That is the treatment, and the arms differ there by design.
- B6 changes the loop for **both** arms identically.
- B5 has no baseline counterpart, which is the point of R4. Baseline keeps `run_tests`, which also changes with the
  tree, so both arms keep a "what is the state now?" tool.

## 3. R4's design

- **Arms:**
  - PRISM (B1–B7) + protocol, against BASELINE + protocol. Same model (Qwen2.5-Coder 14B q8), seeds 42 and 1001,
    the turn cap from R4's pilot, the same budget.
  - R3a (the old tools, the old protocol) is the reference for "what changed", as a cross-round comparison.
- **Optional third arm,** to separate the tools' effect from the protocol's:
  - PRISM-old + protocol, i.e. R3a's PRISM under R4's protocol.
  - It costs +50% runs, but it makes it attributable which part helps.
  - **Decision needed.**
- **Tasks:** R4 is designed after R3a's result is read, so R3a's 80 tasks are no longer unseen.
  - Two options. **Decision needed.**
    - (a) **R4 on R3a's 80 tasks, labelled development.** The confirmation is R5 on fresh tasks, the R2 pattern
      (development, then held-out).
    - (b) R4 on fresh tasks too, which means two mining efforts.
  - Option (a) matches "R5 = double-check".
- **Size:** 80 tasks × 2 arms × 2 seeds = 320 runs, about 38 GPU-hours *(measured R3a pace: 428 s per run)*, about
  4 Kaggle sessions. That is about 480 runs with the third arm.
- **Interim look:** as R3-1, at 40 tasks, with R3-9's α 0.05 (interim p < 0.0031, final p < 0.0490).

## 4. R5: the double check

- **What it verifies:** R4's primary, with the same frozen code, protocol, model, seeds, cap and analysis, on
  **fresh held-out tasks** never used for design.
- **The claim:** R4's result replicates. The same direction, and significant at α 0.05; or, if R4 was not shown,
  R5 tests the same pre-registered hypothesis once more. No new hypothesis.
- **Tasks:** Family B, mined the same way, from repositories not used in R3a or R4 (the held-out rule of R2).
  About 80 tasks; about 16 repositories at 5–10 tasks each *(guess, from R3a's yield)*.
  - The window starts after the R6 models' training cutoffs, so R6 can reuse R5's tasks (§5).
- **Cost:**
  - mining and gating 1–2 weeks *(guess)*;
  - 320 runs, about 4 sessions;
  - no new code (frozen).
- **R5 also measures** cross-session reproducibility: a small repeat of 10 R4 runs, the same seeds, to size session
  noise before any cross-round claim.

## 5. R6: cross-model verification

- **The question:** does PRISM's effect hold, or change, with the model?
- **The training-cutoff constraint:** a model may have seen commits before its cutoff.
  - Same-family models released with the current one (Qwen2.5-Coder 7B and 32B, November 2024) are clean on any
    task after 2024-12.
  - A newer model family needs tasks after its own cutoff. So R5's held-out window should start after the newest
    model R6 will use (`ROADMAP_SYNTHESIS.md` §5).
- **Minimal R6:**
  - Qwen2.5-Coder 32B (capability up) and 7B (capability down), both arms, on R5's tasks.
  - About 640 runs, about 12 sessions *(guess; 32B about 2× slower)*.
  - 32B fitting on T4×2 with a 20k context is **unverified** and must be checked first.
- **Optional:** one other model family, restricted to tasks after its cutoff.

## 6. Going live (noted, as asked)

**PRISM goes live after R5 or R6.**
- **Proposed criterion, for your decision:** live after R5 if R4 and R5 agree on the primary. R6 then shows how far
  the result generalizes across models, but does not gate going live for the tested model.
- Waiting for R6 makes going live model-general, at about 4–6 more weeks.

**Engineering prerequisites for live use.** These are not research, but they gate going live:

| Item | Why it matters live | Source |
|---|---|---|
| **Incremental index (base+delta)** | A cold index build is 2–43 s per repository *(measured: B-09's took 33 s)*. A live agent edits continuously. | `r4/README.md` (the old R4 notes: trail-code memoization, codegraph edge drift) |
| **Divergence warnings** | Live edits and the index must not silently disagree | `AMENDMENT_R3-5.md` (R3-5's parse gate is the start) |
| **Tokenizer** | PRISM falls back to a regex tokenizer when tiktoken cannot download (seen behind this environment's proxy), so budgets are approximate | `ROADMAP_SYNTHESIS.md` item 29 |
| **Test hygiene** | 25 pre-existing failing tests, a flaky wall-clock test | `harness/experiments/BACKLOG.md` |
| **Packaging and serving** | The MCP server as a product: setup, repository registration, latency targets | not in the record (new) |

## 7. Parked until after going live

- **Planning from a request** (the old R4): the agent starts from a natural-language request.
- **New task families** (the old R3b):
  - Family N (23 gated; not viable without about 10–15 more repositories);
  - refactor, delete, move (needs an import-site tool), tests (needs `testcontext` wired), multi-function features.
- **Section attribution for `new_function_context`** (`r3/README.md`, R2 sign-off item 4).
- **A skill-file round; the "bugs" type; the blast2 explanatory test** (`ROADMAP_SYNTHESIS.md` items 24–26).

## 8. Timeline *(guess; Kaggle quota about 30 GPU-hours a week)*

| Step | Time |
|---|---|
| R3a finishes (about 4 sessions, about 38 GPU-hours) | 1–1.5 weeks; the R4 build runs in parallel |
| R4 build (B1–B8) + verification + R4 pilot | about 2–2.5 weeks |
| R4 runs (320; 480 with the third arm) | about 1.5 weeks |
| R5 mining (can overlap R4's runs) + R5 runs | about 2 weeks |
| **Go live (after R5)** | **about 6–7 weeks from now** |
| R6 (about 12 sessions) | about 4–6 weeks more → **about 3 months** if going live waits for R6 |

## 9. Decisions needed from you (none blocks starting B1–B3)

1. **R4's third arm** (PRISM-old + protocol), to separate tools from protocol: yes or no.
2. **R4's tasks:** R3a's 80 as development, with R5 as the held-out confirmation; or fresh tasks for R4 too.
3. **The protocol's components** (B6): the repeat notice plus a pinned test line, or a different set from
   `ROADMAP_SYNTHESIS.md` §4.
4. **Going live:** after R5 (model-specific) or after R6 (model-general).
5. **Whether B5** (post-edit verification) is in R4. It is the most direct answer to "the user has to re-verify",
   and it is also the largest single build.
