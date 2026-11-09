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

## 1. The touch-point workflow, as PRISM stages

One workflow for every task type. PRISM supplies a stage's facts; the agent decides and edits.

| Stage | Question | Touch points | PRISM today | Gap |
|---|---|---|---|---|
| **S1 Exists already?** (before writing) | Does code with this behaviour already exist? Inside the anchor's scope, and outside it | functions with the same purpose, signature or structure | `prism_ask` (lexical search, no model); `new_function_context` §3 "Helpers" (ranked by search over name, signature and purpose) | **No same-behaviour detection.** Search finds name and word matches, not "does the same thing" |
| **S2 Reuse** (while writing) | Which existing nodes should the new code call, and how is code written here? | helpers, siblings, the class's methods, conventions | `new_function_context` (group, class, helpers, style); `change_context` §5–§6 for an existing anchor | ranking ignores the graph (what similar code calls) — minor |
| **S3 Upstream check** (after writing) | Does the new or changed code use the nodes it calls correctly? | each callee: exists? imported? arguments bind to its signature? return value used the way it returns? its errors handled? | digest facts for callees (returns, raises), with certainty; section 5 "what it depends on" | **No callee binding check.** The break check binds *callers → anchor*, never *anchor → callees*. Plus D1 (wrong targets) and D2 (missing names silent) |
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
| B5 | **The post-edit check tool** (working name `prism_check_changes`, no arguments): S3 + S4 over every function changed in the working tree against `HEAD`, with each issue's certainty; empty when consistent | S5 | 2 days |
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
