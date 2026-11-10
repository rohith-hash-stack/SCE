# Roadmap R4 → R6, and going live (revised 2026-11-09 after the user's direction; a plan, nothing built yet)

## The user's direction (as given, 2026-11-09)

1. **R4 is the final PRISM.** Everything PRISM has left, including the task families earlier deferred to R3b, is
   built and fixed **before** R4 runs. Nothing is parked.
2. **Whatever the query or task type, the LLM should respond correctly with PRISM's help.**
3. **PRISM helps by working through the anchor node's touch points**, whatever the task. For example, writing a new
   function or method:
   - **before writing:** check, inside and outside the function's scope, whether existing nodes or edges already
     have the same behaviour;
   - **while writing:** use existing nodes and edges inside the anchor;
   - **after writing:** check that the nodes it uses are used correctly (upstream), and whether the nodes below it
     are affected (downstream);
   - **if anything is affected:** update the anchor, or the downstream nodes, as needed.
4. **R5:** a double check of R4. **R6:** cross-model verification. **PRISM goes live after R5 or R6.**

Changes from the first version of this file: the "parked" list (§7 there) is gone. Every task family and leftover
item is in R4's scope.

## Spec: PRISM's required behaviour (accepted by the user, 2026-11-09)

**Stages S1–S5 below are PRISM's behaviour definition, and they drive everything before R4.** The full spec, with
requirement IDs and acceptance criteria, is `harness/experiments/PRISM_REQUIRED_BEHAVIOR.md`:
- Q1–Q6: answer quality;
- T1–T10: touch points;
- N1–N3: before writing;
- V1–V5: check my changes;
- L1–L2: limits;
- R1–R4: regression safety;
- the pre-R4 gate.

**Approved now (2026-11-09):**
1. **Correctness fixes:** D1, D2, and the break check against the committed version, including the `async`
   arrow-function parse bug.
2. **Golden-output tests:** two purpose-built fixture repositories (Python, JS/TS) with every touch point plus the
   D1/D2 traps. The outputs of `change_context`, `blast_radius`, `digest`, `new_function_context` and `prism_ask`
   are pinned. An intended change updates the saved copy in the same commit.
3. **Cross-feature tests:**
   - the tools agree on callers;
   - formatting never turns "possible" into "certain";
   - no test code leaks when tests are excluded;
   - the index rebuilt after edits equals a fresh build.
4. **Suite hygiene:** the environment failures become skips; fixture caches move to temporary folders.

**Investigate first, then decide:** the 5 drift failures (`COVERAGE_BASELINE.md`; findings in
`DRIFT_INVESTIGATION.md`).

**Deferred until R3a finishes:**
- the order of task families;
- 40 vs 80 tasks per family;
- the third arm;
- going live after R5 or R6;
- every build beyond the correctness fixes (the post-edit check, same-behaviour detection, import sites, and the
  rest).

## 1. The touch-point workflow, as PRISM stages

One workflow for every task type. PRISM supplies a stage's facts; the agent decides and edits.

| Stage | Question | Touch points | PRISM today | Gap |
|---|---|---|---|---|
| **S1 Exists already?** (before writing) | Does code with this behaviour already exist? Inside the anchor's scope, and outside it | functions with the same purpose, signature or structure | `prism_ask` (lexical search, no model); `new_function_context` §3 "Helpers" (ranked by search over name, signature and purpose) | **No same-behaviour detection.** Search finds name and word matches, not "does the same thing" |
| **S2 Reuse** (while writing) | Which existing nodes should the new code call, and how is code written here? | helpers, siblings, the class's methods, conventions | `new_function_context` (group, class, helpers, style); `change_context` §5–§6 for an existing anchor | ranking ignores the graph (what similar code calls) — minor |
| **S3 Upstream check** (after writing) | Does the new or changed code use the nodes it calls correctly? | each callee: exists? imported? arguments bind to its signature? return value used the way it returns? its errors handled? | digest facts for callees (returns, raises), with certainty; section 5 "what it depends on" | **No callee binding check.** The break check binds *callers → anchor*, never *anchor → callees*. Plus D1 (wrong targets) and D2 (missing names silent) Each callee's origin decides the fix: internal (update the callee or adapt the call), external (never edit the library; adapt the call), unresolved (define or import): `PRISM_REQUIRED_BEHAVIOR.md` V1a |
| **S4 Downstream check** (after writing) | Which nodes below are affected? | direct and transitive callers; tests reaching it; import sites; overrides | blast radius (callers); break check (callers against the new signature); `testcontext` "tests that reach it" (not served as a tool) | the break check has no pre-change version in the agent setting (B2); **no import-site list** (needed for move, rename, delete); tests-that-reach not served |
| **S5 Update and re-check** (repeat until clean) | After the agent's updates, is everything consistent? | every function changed so far (the working tree against `HEAD`) | the live index (`WorkingIndex.refresh()`, R3-5) rebuilds after edits | **no tool runs S3 + S4 over all changed functions** |

S5 is also the answer to the loop finding. Its output depends on the current tree, so it changes after every edit.
A re-check after a real update returns new information, unlike re-asking the fixed-argument context tools.

## 2. Task types: what each needs

"Whatever task type" means a task family per type. Each family needs a PRISM entry point, a source of real tasks
(mined commits), and an automatic oracle (how a run is scored).

| Task type | PRISM entry stages | Tasks: source | Oracle (scoring) | Status |
|---|---|---|---|---|
| **Update callers of a changed function** (Family B) | S3, S4, S5 on the anchor | mined (`mine.py` B) | hidden tests: fail-to-pass and pass-to-pass | **ready** (R3a's 80) |
| **Write a new function or method** (Family N) | S1, S2, then S3, S4, S5 | mined (`mine.py` N) | hidden tests | tools ready; **pool 23** (needs about 10–15 more repositories; `r3/pilot/README.md`) |
| **Multi-function feature** | S1, S2 per new function, then S3–S5 across them | **no miner**: commits adding 2 or more production functions with tests | hidden tests | new miner |
| **Refactor** (behaviour kept) | S4, S5 (every caller still fits; nothing else changes) | **no miner**: commits with no test change, or tests changed only mechanically *(guess)* | pass-to-pass only (no fail-to-pass), plus "the refactor is done": a structural check against the developer's change *(design needed)* | new miner + **new oracle** |
| **Delete** | S4, S5 (every caller and import updated or removed) | **no miner**: commits removing a function | pass-to-pass, plus the symbol gone, plus no dangling references (the index answers that) | new miner + oracle |
| **Move** (between files or modules) | S4 with **import sites**, then S5 | **no miner**: commits moving a definition | pass-to-pass, plus the symbol at its new path, plus old imports updated | new miner + **new PRISM tool** (import sites) |
| **Write tests** | `testcontext` (target, callers, existing tests, conventions), then S5 | mined: commits adding tests for existing code | the written tests pass on the developer's code and **fail on the parent** (or on a seeded mutant) | `testcontext` exists, not served; new oracle. The suite's earlier test task was invalid through truncation (`reports/r1/REPORT.md`) |
| **(Plain-language request → plan)** | S1 first (what exists), then the matching type | needs a task design (the developer's commit message as the request?) | as the matching type | **decision needed**: "whatever query type" may include it. It was the old R4 (`r4/README.md`) |

## 2a. Task families: templates, done criteria and signal gates (the R4 design contract; adopted 2026-11-10)

**The user's decision, as given (2026-11-10, after `r3/R3A_FLOOR_INVESTIGATION.md`):**

"Each family requires its own task template, done criterion, and signal gate. R3a's single template worked only for delete tasks. Refactor, move, and test families have 'tests pass at start' as the correct starting state — treating that as 'no signal' was a task-design failure, not an agent failure. R4 does not use a single template."

| Family | Prompt template | Done criterion | Signal gate |
|---|---|---|---|
| Delete | "Remove [target]. Remaining tests must still pass." | Delete completed, project tests pass | Tests pass at end, target absent |
| Refactor | "Refactor [target] to [goal]. Behavior must not change; all tests must still pass." | Code changed, all tests pass | Tests pass at start AND end; diff is structural |
| Move | "Move [target] to [destination]. Callers must be updated. Tests must pass." | Moved, callers updated, tests pass | Tests pass at end; old path gone, new path exists |
| Tests | "Write tests for [target]. They must pass on current code, fail on [prior version]." | Tests pass on current, fail on prior | Score by fail-to-pass on the prior version |
| Feature | "Implement [feature spec]. Tests for the feature must pass." | Feature tests pass | Visible failing test at start |
| Family B (callers) | "Function [X] changed [diff]. Update callers. Hidden tests will verify." | Fail-to-pass tests pass, no pass-to-pass broken | At least one visible failing test at start (per G1) |

**Notes:**
- **"G1" here is the signal gate in Family B's row:** at least one fail-to-pass test exists in a test file the agent
  can see at the start. It is not `ROADMAP_SYNTHESIS.md`'s G1 (PRISM correctness).
- **G1 applies to Family B only.** It is the wrong gate for refactor, move and tests, whose correct start state has
  the tests passing.
- **R3a's 80 tasks under G1:** 57 pass, and 23 would be excluded (18 with all visible tests passing, 5 with no
  visible tests; `r3/R3A_FLOOR_INVESTIGATION.md` §2).
- **How the gates are implemented on the mined candidates** (test selection, "structural", "prior version"):
  `r4/GATES.md`.
- **The same contract is in `PRISM_REQUIRED_BEHAVIOR.md` §10** (task families).

## 3. The build list (everything PRISM has left)

PRISM never calls a model. Every new capability below is deterministic, from the index, the parse trees, the
graph and git. Costs are *guesses*.

**A. Correctness (needed by every stage)**

| # | Build | Effort |
|---|---|---|
| A1 | D1: a receiver-less call never resolves to a method; a scored winner is tentative (`concrete_builder._resolve_ambiguous_call`, `digest._check_receiver`) | 0.5 day |
| A2 | Break check against the committed version (`git show HEAD:`); arrow-function `signature()` parse fix (`impact/breaks.py`) | 1 day |
| A3 | D2: calls to names the file neither defines nor imports are listed, embedded ones included (`digest._call`) | 0.5 day |

**B. New capabilities (the stages' gaps)**

| # | Build | Stage | Effort |
|---|---|---|---|
| B1 | **Same-behaviour detection** — candidates with evidence, never a verdict. (i) signature and name and purpose search (exists); (ii) structural match: the same callee set and the same digest facts (returns, raises, state changes); (iii) a normalized-AST fingerprint (identifiers and literals abstracted) for near-clones. Inside the anchor's scope (file, class) and outside (repository) | S1 | 2–3 days |
| B2 | **Callee binding check (upstream)**: for each call in new or changed code: the target resolved (A1, A3), the arguments bound to the callee's signature (the break check's `bind`, in the other direction), the return value's use against what it returns, its raises against the enclosing catches | S3 | 2 days |
| B3 | **Import-site list**: every import of a symbol (Python `import` / `from`; JS/TS `import` / `require` / re-exports) | S4 (move, delete, rename) | 1–1.5 days |
| B4 | **`testcontext` served as a tool**, plus "tests that reach this function" in S4 | S4, tests | 0.5 day |
| B5 | **The post-edit check tool** (working name `prism_check_changes`, no arguments): S3 + S4 over every function changed in the working tree against `HEAD`, with each issue's certainty; empty when consistent | S5 (**core R4**: the multi-turn loop; runs after every edit; four-section facts, under 800 tokens, at most 0.5 s: `r4/CHECK_CHANGES_DESIGN.md`) | 2 days |
| B6 | **The relational summary** ("What this means for the change") closing `change_context` and `new_function_context`: the S3/S4 facts for the anchor in one block | S2–S4 | 0.5 day |
| B7 | Readable formats: blast radius as a hop list with code (no XML envelope); `prism_ask` as a ranked list (no raw JSON) | all | 0.5 day |

**C. Protocol (both arms, identical; pre-registered)**

| # | Build | Effort |
|---|---|---|
| C1 | The repeat notice: an identical repeated call is answered with a short notice ("already called; result unchanged; last tests: …") | 0.5 day |
| C2 | The last test result pinned in the window | 0.25 day |
| C3 | The system prompt names the workflow neutrally, for both arms ("check what exists, make the change, check what it affects, run the tests"), with no PRISM-specific wording | 0.25 day |

**D. Task families (miners, gates, oracles)**

| # | Build | Effort |
|---|---|---|
| D1 | Family N: mine about 10–15 more repositories to reach about 80 | 1–1.5 weeks (mining + gating) |
| D2 | Multi-function feature: miner + gate | 1 week |
| D3 | Refactor: miner + gate + the "refactor done" oracle | 1–1.5 weeks |
| D4 | Delete: miner + gate + oracle | 1 week |
| D5 | Move: miner + gate + oracle | 1 week |
| D6 | Tests: miner + gate + the "fails on the parent" oracle | 1–1.5 weeks |
| D7 | Per-family pilots (10 tasks × 2 arms each) | about 1 GPU-hour each |

**E. Analysis and verification**

| # | Build | Effort |
|---|---|---|
| E1 | Per-family scoring paths; the named secondaries (false done, pass-to-pass broken, loops, S1 duplicates written: a new function that duplicates an existing one, detected by B1 after the run) | 1 day |
| E2 | Re-verification after A1 (graph edges change): E1–E3, the blast-radius test-code check, dry runs on every family | 2–3 days |

**Totals *(guess)*:**
- PRISM and protocol (A–C): about **12–14 days of code**.
- Task families (D): about **6–8 weeks**. Mining and gating is the long pole: R3a's Family B took about a week.
- Verification and pilots: about **1 week**.
- **About 2.5–3 months of single-builder work before R4 runs.**

## 4. R4's design (final PRISM)

- **Arms:** PRISM (A + B) + protocol (C), against BASELINE + protocol (C). Same model, seeds 42 and 1001, the cap
  set by R4's pilots.
  - Optionally a third arm, PRISM as in R3a + protocol, to separate the new PRISM from the protocol.
- **Families:** 7 (B, N, multi-function, refactor, delete, move, tests), plus the request→plan type if included.
- **The primary hypothesis**, one test, so Holm does not split α:
  > Pooled over all families, the agent with PRISM resolves more tasks than without (per-task means over two
  > seeds; Wilcoxon; α 0.05; +0.10 floor).

  Per-family results are pre-registered secondaries, each with its own power statement.
- **Size and power *(guess)*:**
  - 80 tasks per family × 7 families × 2 arms × 2 seeds = 2,240 runs, about 270 GPU-hours (428 s per run, R3a's
    pace). That is about 25 Kaggle sessions, **about 9 weeks of quota** (about 30 GPU-hours a week).
  - At 40 per family: 1,120 runs, about 4.5 weeks. The pooled primary keeps +0.10 power (280 tasks). Each family
    alone detects only about +0.20.
  - **Decision needed:** tasks per family.

## 4a. R4's validation in three phases (decided 2026-11-10; restructured the same day, smaller first)

| Phase | Question | Families | Arms | Tasks / runs | Criteria |
|---|---|---|---|---|---|
| **Sanity 1** | Does PRISM's loop work? | the healthiest gated now: delete and feature, plus Family B re-gated, whatever it yields | **PRISM only, one arm** | about 30 tasks, 1 seed: about 30 runs | **none:** observation |
| **Sanity 2** | Does PRISM beat the baseline? | the same | PRISM and the baseline | about 30 tasks: about 60 runs | **none:** observation |
| **R4** | The comparison at scale | the Sanity families, plus refactor, move, tests and Family N as each passes gating | PRISM and the baseline | 40–80 per family: 240+ runs | **pre-registered** (`r4/PREREG_DRAFT.md`) |

**The same in every phase and every arm:**
- the boolean-gated post-edit check (`r4/CHECK_CHANGES_DESIGN.md` §8–§9);
- one fixed compaction rule (`r4/COMPACTION_DESIGN.md`). It is a protocol setting, not an arm variable;
- the model, the prompt template per family, the caps and the seeds.

**The rules:**
- **Sanity 1 has one arm, PRISM.** It has no baseline: comparing before PRISM's loop works measures the wrong thing.
- **The sanity phases are development iterations:** no pass or fail criteria, no pre-registration.
  1. run;
  2. observe;
  3. diagnose (which tasks, which mechanism);
  4. fix;
  5. re-run.
  - A phase is left when its behaviour looks right, judged by observation.
- **Pre-registration applies to R4's full run only.** It is frozen after Sanity 2. The R3 discipline applies
  unchanged: hash-committed before the run; amendments recorded and never used to lower a threshold.
- **Task selection stays a gate:** `r4/GATES.md` and the pre-flight scope check (`r4/SCOPE_INVESTIGATION.md`). The
  scope check is the former sanity check's Layer 1. Its Layers 2 (calibration) and 3 (signal) need the baseline, so
  they are part of Sanity 2 (the user, 2026-11-10: "the correct split").
- **Families enter in sequence, not by permanent exclusion.** Refactor, move, tests and Family N join R4 only after
  they pass gating and Sanity 1 has shown the loop works.
- **Family B may be small, and that is an expected outcome, not a blocker.**
  - Real caller-update tasks are rare in commit history (about 1 in 20 of R3a's tasks).
  - If re-gating yields 15, R4 measures 15 for Family B and 40–80 for delete and feature.
  - Family B is mined from what the repositories offer, not forced.
- **Dropped:** a separate deep-cascade presentation (hops 2+). It cannot be predicted before the edit, and it emerges
  from the re-check after each edit.

**What Sanity 1 observes.** **Fact uptake is the primary observation, ahead of the solve rate:**

| Signal | What | How it is read |
|---|---|---|
| **Fact uptake (primary)** | of the checks that reported issues, the share whose next edit fixes the named fact: the named caller updated, the undefined name defined or imported, the mismatched call changed. An edit elsewhere counts as no uptake. Also: on which turn after the report (the next turn, or the one after)? | **near zero:** the model is the bottleneck, not PRISM. Escalate before any comparison: the 32B model (R6), or a protocol change. **Meaningful (about 30% or more):** the feedback is used and the loop functions. Go to Sanity 2. Readings, not gates |
| Solves | solved tasks (at least one?), which ones; partial (file and function F1); pass-to-pass broken | |
| Convergence | facts per check over the turns: falling (converging) or rising (diverging); the same feedback repeated after an edit | |
| The check | fires after every edit; `[check: clean]` against warnings-only against full feedback; facts per check; latency (target at most 0.5 s); `[check: unavailable]` events | |
| Stopping | done while the last check reported issues (a false done); done right after `[check: clean]`; turns to the first edit and to the first clean check | |
| Context | peak and mean prompt as a share of the window (expected below about 75% with compaction); compaction events; overflow-fallback events; budget and turn-cap stops | |
| Re-request rate | stubbed outputs called again, and how soon | immediate re-calls mean compaction is wasting turns |
| Loops | identical calls 3+ times with no edit; early done (within 5 turns, no edit) | |
| External safety | edits to vendored, stub or library paths | expected none |

**After Sanity 1:**
- uptake meaningful and edits converging: Sanity 2;
- uptake near zero: escalate (the model or the protocol);
- mechanism errors: fix and re-run.

**After Sanity 2:**
- PRISM ahead of the baseline: the full R4;
- tied or behind: investigate the tasks, the model, or whether the baseline's text-only feedback is good enough.

**Cost** *(estimate)*:

| Phase | Runs | Sessions (11.5 h) |
|---|---|---|
| Sanity 1 | about 30 (one arm) | 1–2 |
| Sanity 2 | about 60 (two arms) | 2 |
| Full R4 | 240+ (more families, two arms) | 8+ |

- **The working figures:** 5–7 min per run, plus environment setup of 10–15 min per repository, paid again in each
  session (Kaggle's disk resets).
- **Before knowing whether PRISM works:** about 90 runs, 3–4 sessions, about a week.
- **Disk:** the gating run's safeguards apply. Check before each repository, clean between repositories, stop under
  2 GB, clear the package caches.

**R4's scope, from the gated pools (confirmed 2026-11-10, after the K4 diagnosis):**

| Family | Pool (gated) | Role in R4 |
|---|---|---|
| delete | 71 | **primary**: the conclusive comparison, 40–80 tasks |
| feature | 23 (gating paused; more on resume) | **primary**: 40–80 tasks as the pool allows |
| Family B | 4 (`r4/FAMILY_B_REGATE.md`); re-gated again after the K4 fix, and over R3's full mined pool | **directional by default**, analysed separately at its real size and stated as directional. **The promotion rule:** Family B becomes primary only if its honest pool after K4 reaches 40 under G1, G2 and the scope check, as for the primary families. There is no expansion to reach it: no new mining, no relaxed gates. The pool is the re-gated R3a 80 plus R3's full mined pool; the new repositories (pydantic-ai, fastmcp) were mined only for the other families |
| move, tests, Family N | 10, 11, 9 | **deferred** to R4b or later: not enough volume to answer |
| refactor | not gated yet | resumes gating after Sanity 1; becomes an R4 family if it yields 80+ |

**Sanity 1's shape:**
- about 13 delete, about 13 feature, and Family B's 4–10: 30–36 tasks;
- delete and feature exercise `prism_check_changes` the same way Family B does, so the loop is validated on the
  three together;
- fact uptake is measured across all three.

**Code fix order (2026-11-10):**
1. K4 diagnosis (done: `r4/K4_DIAGNOSIS.md`);
2. the K4 fix (import roots), with the golden K4 lines updated, and the scope check re-run on the 50 tasks on
   affected repositories (`r4/K4_DIAGNOSIS.md` §5);
3. parsed-code call detection;
4. D1;
5. internal/external classification;
6. `prism_check_changes`.

Sanity 1 waits for all six.

**After R4, if at all:** a side test of proactive against reactive compaction (2 families × 10 tasks × 2
configurations; `r4/COMPACTION_DESIGN.md` §6). It is not part of R4.

## 5. R5 (double check) and R6 (cross-model): unchanged in shape, larger in size

- **R5:** R4's frozen code and protocol, on fresh held-out tasks from repositories never used, in every family.
  The same pooled primary.
  - This needs a second round of mining per family. That is the most expensive part of R5 *(about 4–6 weeks)*.
  - A smaller R5 (fewer families, or 40 per family) is a decision for after R4.
- **R6:** Qwen2.5-Coder 7B and 32B (same release, so clean on any task after 2024-12), both arms, on R5's tasks.
  - A newer model family needs tasks after its own training cutoff, so R5's mining window should start late
    enough.

## 6. Going live (noted, as asked)

**PRISM goes live after R5 or R6.** The proposed criterion, for your decision: after R5, if R4's and R5's pooled
primaries agree; or after R6, for a model-general result.

**Engineering prerequisites** (not research, but they gate going live):
- an incremental index (base+delta, `r4/README.md`'s notes; a cold build is 2–43 s *(measured)*);
- divergence warnings;
- a real tokenizer (tiktoken falls back to a regex behind proxies);
- test hygiene (`BACKLOG.md`);
- packaging and serving the MCP server as a product.

## 7. Honest scale

| Phase | Time *(guess)* |
|---|---|
| R3a finishes | 1–1.5 weeks (running) |
| A–C (PRISM + protocol) + E2 verification | about 4 weeks (in parallel with mining) |
| D (six families' mining, gating, oracles) | about 6–8 weeks (the long pole) |
| R4 runs | about 4.5 weeks (40 per family) to about 9 weeks (80 per family) |
| R5 (mining + runs) | about 6–10 weeks |
| **Going live after R5** | **about 5–7 months from now** |
| R6 | about 6–8 weeks more |

This is much larger than the earlier R4. That follows directly from "every task type before R4", and it is the
right call only if a staged release is not wanted.

**The alternative, if time matters:** run R4 family by family (B first, which is ready), with the same frozen PRISM.
That spreads the GPU cost and gives early results. **Decision needed.**

## 8. Decisions needed

1. **Task families in R4:** all 7, and whether the plain-language request → plan type is one of them.
2. **Tasks per family:** 40 (pooled power kept, per-family only about +0.20) or 80.
3. **One R4, or staged by family** (with the same frozen PRISM).
4. **The third arm** (R3a's PRISM + the new protocol): yes or no.
5. **The offline summary index** (`prism summarize` writes one-line summaries with a model, offline; search can use
   them). Allowed as a pre-built asset, or excluded so PRISM stays model-free end to end?
6. **Going live:** after R5 or after R6.

**What can start now without a decision:** A1–A3 (correctness), B2 (callee binding check) and B5 (post-edit check).
Every option needs them.

## Test hygiene per round (standing practice, adopted 2026-11-09)

**The policy (user):** whenever a round clones a repository for its own execution, the repository's real-codebase
test files run against it as a supplementary check. This applies to R4, R5, R6 and every later round; not to R3a,
which runs unchanged.

**What these tests prove, and what they do not:** they show that PRISM does not crash on real code, and that the
real parsing and linking paths execute. They do **not** show that tool outputs are correct; the golden tests do
that (`GOLDEN_TESTS_PLAN.md`). Both are needed, and neither substitutes for the other.

### 1. Test → repository mapping (the 18 real-codebase test files)

All 18 index one of four benchmark corpora, pinned by commit (`benchmarks/corpora/resolver.py`; checked out under
`.benchmarks/corpora/`). Times and results are from 2026-11-09, run one file per process on this sandbox (4 CPUs),
without coverage.

| Test file | Tests | Corpus | Time | Result |
|---|---|---|---|---|
| `tests/benchmarks/test_harness_metrics.py` | 113 | django, express, fastapi, trpc | 310 s | 1 failed (§4) |
| `tests/phase_c/test_traversal_layer.py` | 14 | django | 40 s | pass |
| `tests/phase_d/test_causal_path.py` | 10 | django | 161 s | pass |
| `tests/test_causal_path.py` | 8 | django | 196 s | pass |
| `tests/test_corpora_resolver_subdir.py` | 3 | (resolver unit) | 3 s | pass |
| `tests/test_feature_masks_cache_parity.py` | 3 | django | 131 s | pass (1 xpassed: a stale xfail marker) |
| `tests/test_fuzzy_seed_suggestion.py` | 9 | django | 24 s | 1 failed (timing) |
| `tests/test_index_cache_consistency.py` | 2 | django | 1,381 s | pass (1 xfailed) |
| `tests/test_index_cache_decorated_parity.py` | 2 | django | 663 s | pass |
| `tests/test_layer4_runner.py` | 54 | django, express | 23 s | pass |
| `tests/test_mtime_invalidation.py` | 8 | django | 4 s | 1 failed (timing) |
| `tests/test_prism_selection_regressions.py` | 22 | django | 110 s | pass |
| `tests/test_smoke_corpus.py` | 6 | django, express, fastapi, trpc | 2 s | pass |
| `tests/test_task_reachability.py` | 9 | django | 20 s | pass |
| `tests/test_three_pass_integration.py` | 7 | fastapi | 49 s | pass |
| `tests/test_two_pass_benchmark.py` | 31 | django | 60 s | pass |
| `tests/test_two_pass_engine.py` | 12 | django | 60 s | pass |
| `tests/test_two_pass_root_imports.py` | 13 | express | 13 s | 4 failed (environment: express's npm dependencies, e.g. `etag`, not installed) |
| **Total** | | | **3,250 s (54 min)** | **7 failed** |

**Which planned rounds use these corpora:** none.
- R3a's 16 repositories (`r3a_tasks/tasks_B.json`) are aiohttp, execa, h3, litestar, nitro, packaging, poetry,
  prettier, pydantic, scrapy, sqlglot, svelte, tornado, typer, unstorage and yarl. None is Django, FastAPI,
  express or tRPC.
- R4/R5/R6's repositories are not chosen yet. Unless a round mines one of the four corpora, **the policy as worded
  runs none of the 18 files.**

**So the policy has two parts, to serve its intent** ("the clone exists, so the coverage is free"):

| Part | What runs | Against | Status |
|---|---|---|---|
| **A. Corpus suite** | the 18 files above | their own pinned corpora, fetched by the resolver | exists; runs once per PRISM code commit a round uses (it does not depend on the round's clones) |
| **B. Round-repository smoke** | for every repository the round clones, at the round's own revisions: index build; each served tool once on 3 anchors from the round's tasks; the cross-feature invariants (callers agree; no "possible" rendered "certain"; no test code with `include_tests=False`; the live index after a scripted edit equals a cold build) | the round's clones | **to build**, alongside the golden tests' cross-feature tests (the same invariant code, parametrized over real clones) |

**Where each part runs (decided 2026-11-09, replacing the default below):**
- **Part A runs on GitHub Actions:** `.github/workflows/real-codebase-tests.yml`, manually triggered with the PRISM
  commit to test.
  - One job per corpus (django, fastapi, express, trpc), then a multi-corpus job for the three files that index
    several corpora, then an aggregation job. That is all 18 files.
  - The jobs run one after another on standard `ubuntu-latest` runners (`needs:` chain, `if: always()`); a fresh
    runner per job cleans the disk.
  - The test steps are `continue-on-error`. Each job uploads its JUnit files, and the aggregation job writes a
    per-file table and a failure list to the run summary.
  - The install list includes express's npm dependencies (`npm install --omit=dev --ignore-scripts`). That
    resolves today's 4 environment failures, the missing `etag`. Express has no lockfile, so npm picks versions
    within express's own ranges.
  - GitHub's runners have network access, so `tiktoken` loads the real encoding there.
  - No container image: setup (pip and the corpus clones) takes minutes, not enough to justify one.
- **Part B stays in the Kaggle GPU session** of each round, on the round's own clones.

### 2. Mechanics

| Option | What breaks | What does not | Verdict |
|---|---|---|---|
| **Parallel to model runs** (CPU, while the GPU serves the model) | The round's own runs use the CPU too: `run_tests`, index builds, installs. Heavy indexing (Django: 2–3 GB RSS, 100% of a core) slows them, and can change test timeouts inside runs, i.e. **the measurement**. The suite's own timing tests fail under load (two of today's failures are wall-clock thresholds) | No GPU interference | **rejected** |
| **Once per repository per session** (in the round's cell, on first clone) | It adds minutes to the start of every session, before model work, on the GPU clock | Isolation from runs (it finishes before they start) | acceptable for part B only, but it wastes GPU time |
| **End of session** (in the round's stop margin) | Part A takes 54 minutes; the margin is 45. If the session hits its limit, the final push is at risk | It uses otherwise idle time | acceptable for part B only, and only if it fits the margin |
| **A separate cell on the same revisions** (a CPU-only Kaggle notebook) | It cannot share the round's `/tmp` clones, so it re-clones the same URLs at the same revisions (minutes) | Full isolation from the measurement; no GPU quota used (CPU notebooks do not draw the weekly GPU allowance); can run any time during the round | **default** |

**Default:**
- one CPU-only hygiene cell per round, run once at the round's start, or whenever the round's pinned PRISM commit
  changes;
- it runs part A, and part B over every repository in the round's task list, at the task revisions;
- it pushes a `hygiene/` report next to the round's results;
- the round's GPU cell does not change.

### 3. Failure policy

- **Log, never interrupt.** A hygiene failure is recorded as a defect. The round continues: its protocol measures
  PRISM, and these tests are hygiene.
- **Categorize** each failure:
  - **environment:** a missing package, tool, network or data;
  - **real bug:** PRISM's behaviour is wrong;
  - **drift:** a recorded expectation is out of date with an intended change.

  Environment failures are noise. Real bugs are findings.
- **Report at round end**, in a supplementary "Test hygiene during this round" section, never in the primary
  results.
- **Never pause a round** because one fires. If a failure makes the round's results uninterpretable (for example a
  real bug in caller linking on a repository the round measures), the round's report says so plainly, but the
  round is not stopped.

### 4. Before the policy goes live: today's 7 failures and the 46 skips

**The 4 "unexplained" failures from the first, interrupted run:**
- The interruption was a **sandbox restart** (machine uptime reset), not PRISM. The first run's log was lost with
  it.
- The file-by-file rerun found 7 failures in all:
  - 4 in `test_two_pass_root_imports.py`. This is environment: express's `node_modules` (`etag`) was never
    installed, already listed in `BACKLOG.md`. They are almost certainly the first run's 4 (the same
    four-in-a-row pattern).
  - 2 wall-clock thresholds, which are noise: fuzzy matching 83 ms against a 50 ms target, a warm scan 102 ms
    against 100 ms.
  - 1 real behaviour change, `test_harness_metrics.py::test_all_four_existing_blast_seeds_pass_the_richness_screen`:
    `django.db.models.query.QuerySet.get` now has 2 direct and 5 transitive callers. At `bc53e055`, where the
    test was added, it had 34 and 8.
  - **Bisected: drift, not a regression.** `934f83c8` (2026-10-07, "universal call-resolution fixes") took it
    from 34+8 to 0+0. Its rule, "builtin method names (dict/list/str/…) are never guessed onto repo methods for
    receivers of unknown type", removed false links.
  - All 34 old "callers" were `dict.get` or request lookups, not `QuerySet.get`: `kwargs.get("using")`,
    `request.GET.get(...)`, `self.options.get("proxy")`, the test `RequestFactory`'s `self.factory.get(...)`.
    The commit's own runtime check says the same: "QuerySet.get callers never seen at runtime 18 → 0".
  - So the seed's "20 or more callers" was only ever true because of those false links. **It does not affect
    R4's measurement**: it is a correctness gain.
  - **Recommended action (your decision; not done):** replace `QuerySet.get` in that test's seed list with a
    Django seed that passes the 20-caller screen on today's graph.
  - **A real limit it exposes** (spec T2 / L1): Django's real `QuerySet.get` calls go through managers
    (`Model.objects.get(...)`, a descriptor-returned proxy). PRISM does not link them, and does not say so. That
    is a "not followed (dynamic)" case the spec requires PRISM to report.

**The 46 skips:**

| Group | Count | Under this policy |
|---|---|---|
| tiktoken encoding unavailable | 5 | fixed by tokenizer option (b), once the asset is committed |
| Python package not installed (onnx, orjson ×4, ujson, httpx ×4, jinja2) | 11 | environment: the hygiene cell installs them, so **they run** |
| external tool not installed (typescript-language-server ×9, aider-chat) | 10 | environment: the hygiene cell installs them, so **they run** |
| `PRISM_LIVE_NETWORK_TESTS=1` live clone suites | 16 | can run in the hygiene cell (network), but they are heavy; **opt in per round** |
| real gin clone absent | 1 | environment: runs if the cell clones it |
| by design (R3 equivalence needs round clones; the live LLM judge needs a model call, out of scope; a data condition) | 3 | **stay skipped** |

### 5. Cost for R4

- **GPU sessions:** **0 added** under the default (a separate CPU-only cell).
- **The hygiene cell, once per PRISM commit:**
  - part A, about 54 minutes (measured, this sandbox; Kaggle's 4-CPU notebooks are comparable *(guess)*);
  - part B, about 1–2 minutes per repository *(guess: a cold index build is 2–43 s measured, plus about 10 tool
    calls at under 1 s each, plus the invariant checks)*, so about 20–35 minutes for 16 repositories;
  - re-cloning, about 5 minutes;
  - total, about **1.5 hours of CPU**, once per PRISM commit, not per session.
- **If it ran inside the GPU cell instead:** part B alone is about 20–35 minutes per session, on the GPU clock.
  That is about 5–8% of an 11.5-hour session. Part A does not fit the 45-minute stop margin. This is why the
  separate cell is the default.
