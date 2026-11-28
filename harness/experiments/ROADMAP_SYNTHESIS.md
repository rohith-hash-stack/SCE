# Roadmap synthesis after R3a's launch (2026-11-09; planning only — no code, no rule change, no model call)

**Scope.** Every deferred item in the record, grouped by dependency, with options and trade-offs. Nothing here
pre-commits a round. Each item cites the file and section it comes from. Items not found in the record are marked
**not in the record**. Costs marked *(guess)* are estimates. Costs marked *(measured)* come from logs.

**Three corrections to the premises, from the record:**
1. **R4 is already named, and it is not the confidence work.**
   - In the record, R4 is "user query → plan": the agent starts from a natural-language request
     (`r3/README.md`, Amendment R3-1 item 7; R3-2 item 4; `r4/README.md`). The base+delta divergence-detection
     study is filed under it (`r4/README.md`; `AMENDMENT_R3-5.md` lines 7, 166).
   - R5 is the cross-model round (`r3/README.md`, Decisions table, "model"; R3-2 item 4; `reports/r2/REPORT.md`,
     line 84).
   - My `CONFIDENCE_DESIGN_STUDY.md` §7 used "R4a/b/c", and `INTERPRETATION_NOTES.md` §4 says "R4 plans to
     address". Both collide with the named R4. This document does not use round numbers for new work: it uses
     labels (A, B, C …). Renumbering is your call.
2. **The model was not chosen for Kaggle fit alone.** It is fixed so that R3 is comparable with R1, R2 and the
   suite (`r3/README.md`, Decisions table: "R3 must be comparable with R1 and the suite"). R3's task window starts
   after the model's release date (2024-11-12) to rule out memorization (`r3/README.md`, "The model's release
   date"). That second fact constrains any cross-model round heavily (§5).
3. **The "46 turns trimmed" are 46 tool outputs replaced by one-line stubs.** No turn was trimmed and the goal was
   not dropped.
   - The window rule always keeps the system prompt and the task message (`loop.py`, the docstring "The window
     rule"). All 23 windows of B-09 contain the task message.
   - 46 of the 49 stubs are in the three looping PRISM runs (13, 17 and 16), and all from turn 13 on, after each
     loop had started (`reports/r3/pilot/*.json`, `turns_stubbed`).
   - Trimming is a consequence of the loop, not its cause. What remains open is whether a goal pinned at the top,
     about 10,000 tokens above the newest turn, still steers the model (§3).

**And one correction to my earlier estimate:**
- R3a's runs average **428 s** so far (30 runs), against 239 s in the pilot *(measured,
  `reports/r3/r3a/pilot_runs.jsonl`)*.
- 320 runs × 428 s ≈ **38 hours, about 4 Kaggle sessions**, not the 2–3 I said.

## 1. Inventory

**Status key:**
- **open:** deferred, not started;
- **designed:** studied, with a proposal on paper;
- **closed:** resolved;
- **new:** not in the record before this request.

| # | Item | Source (file: section) | Status | What it is | Why deferred |
|---|---|---|---|---|---|
| 1 | **R3b: Family N** (write a new function to a spec) | `r3/README.md` R3-1 item 6; `AMENDMENT_R3-6.md` line 24; `AMENDMENT_R3-8.md` line 8; `r3/pilot/README.md`; `INTERPRETATION_NOTES.md` §3 | open | Family N's own round with "a proper mining plan: different repos, or a different window" | Only 23 gated (`r3a_tasks/README.md`), under the 50 minimum for its power statement; R3-1 says "decided and built only after R3a's result is read" |
| 2 | **R3b: refactor, delete** | `r3/README.md` R3-2 item 2; Tool audit table | open | Task flavors whose tools exist (change context, blast radius) but which have no tasks | Not R3a's families; decided with R3b |
| 3 | **R3b/new: move code** | `r3/README.md` Tool audit ("no tool lists import sites") | open | Needs a new tool, an import-site list | No tool |
| 4 | **R3b/new: add tests** | `r3/README.md` Tool audit (`prism.impact.testcontext` "exists but is not given"); `reports/r1/REPORT.md` type table ("tests … R3, if included") | open | A test-writing family; the tool exists but is not wired | Test writing is out of R3 |
| 5 | **R3b/new: multi-function features** | `r3/README.md` Tool audit ("a feature spanning several new functions: none") | open | Several new functions per task | No tasks; Family N adds one |
| 6 | **R4 (named): user query → plan** | `r3/README.md` R3-1 item 7, Scope "Limit"; `r4/README.md` | open, named, not scheduled | The agent starts from a natural-language request and must decide *what* to change | Needs its own pre-registration; A3 did not test planning (`r3pre/README.md`) |
| 7 | **R4 (named): base+delta divergence detection** | `r4/README.md`; `AMENDMENT_R3-5.md` lines 7, 14, 166 | open, notes only | An incremental index with an equivalence invariant against cold builds; divergence warnings | "Base+delta is not tested in R3" |
| 8 | **R5 (named): cross-model** | `r3/README.md` Decisions table "model"; Cost section ("a stronger model is a separate round"); `reports/r2/REPORT.md` line 84 | open, named | The same rounds on another model | "Never a substitution into R3" |
| 9 | **D1: bare call resolved to an unrelated method, reported as fact** | `PILOT_LOOP_INVESTIGATION.md` §4/§7; `CONFIDENCE_DESIGN_STUDY.md` §0–§1; `INTERPRETATION_NOTES.md` §5 | designed | `concrete_builder._resolve_ambiguous_call`'s scored winner is untentative, and methods are allowed for receiver-less calls | Not fixed before R3a (user decision, `IMPLEMENTATION_LOG.md` 2026-11-08) |
| 10 | **D2: unresolved embedded calls dropped** | `CONFIDENCE_DESIGN_STUDY.md` §0–§1 (`digest._call`) | designed | Completeness of the digest's call list | As 9 |
| 11 | **Break check: no pre-change version in R3; arrow-function parse bug** | `CONFIDENCE_DESIGN_STUDY.md` §0 (and the correction in `PILOT_LOOP_INVESTIGATION.md`); related: `r4/README.md` ("on applied edits it needs the current text as `new_code`") | designed | Old code from `git show HEAD:`; `breaks.signature()` reads `const f = async (…)` as `async` | As 9 |
| 12 | **Confidence signals** (resolution, verification, completeness) | `CONFIDENCE_DESIGN_STUDY.md` §2–§3 | designed | Make the existing certainty model honest and complete; deterministic cross-checks | Design study only |
| 13 | **Descriptive closing block in `change_context`** | `PILOT_INTERFACE_INVESTIGATION.md` §7; `CONFIDENCE_DESIGN_STUDY.md` §5 | designed | "What this means for the change": caller mismatches and calls to missing names | "Do not add before R3a" (user, 2026-11-08) |
| 14 | **Interface limitation: fixed symbol argument, identical repeat** | `PILOT_INTERFACE_INVESTIGATION.md` §5, §7; `INTERPRETATION_NOTES.md` §4 | recorded as a finding | PRISM's context tools have no free argument | Measured by R3a's counters, not fixed |
| 15 | **Loop-aware protocol** (repeat notice, workflow line, goal restatement) | `PILOT_LOOP_INVESTIGATION.md` §5; `PILOT_INTERFACE_INVESTIGATION.md` §7; `CONFIDENCE_DESIGN_STUDY.md` §4; `INTERPRETATION_NOTES.md` §4 | open | A harness-level change to the agent loop | The loop is pre-registered for parity (R3-1, R3-5) |
| 16 | **Tool output formats** (blast radius as XML envelope; `prism_ask` as raw JSON) | `PILOT_LOOP_INVESTIGATION.md` §4; `CONFIDENCE_DESIGN_STUDY.md` §5 | open | Machine formats served to an agent | Outside the questions asked; a treatment change |
| 17 | **Early-done runs** (done after "no matches" or "all pass") | `PILOT_LOOP_INVESTIGATION.md` §6; `PILOT_INTERFACE_INVESTIGATION.md` §6 | recorded; counter in R3a | Model reasoning, the same in both arms | No item addresses it (§3) |
| 18 | **Tasks whose visible tests already pass** (pilot B-06) | `PILOT_LOOP_INVESTIGATION.md` §6; `IMPLEMENTATION_LOG.md`, pilot gate notes ("some visible tests (pilot-B-08: 2 of 11)") | recorded | The agent has no failing signal; hidden tests only | A task-design property |
| 19 | **Scaling test** (resolve rate against depth) | `INTERPRETATION_NOTES.md` §2; `R3A_TASKS_HASH.md` | closed for R3a: "not run" | Needs 30 deep tasks; 18 exist | Insufficient depth variance |
| 20 | **Representativeness of deep tasks** | `INTERPRETATION_NOTES.md` §1; `AMENDMENT_R3-8.md` | recorded caveat | The repositories added under R3-8 were chosen partly on deep share | — |
| 21 | **Vitest browser mode** | `AMENDMENT_R3-8.md` line 27 | open | About 3 more deep tasks; needs a browser install on Kaggle | Cost against yield |
| 22 | **Rollup install** (wasm-pack postinstall behind the proxy, Rust) | `IMPLEMENTATION_LOG.md`, R3-8 week | accepted attrition | — | Environment |
| 23 | **Section-level attribution for `new_function_context`** | `r3/README.md`, R2 sign-off item 4 | open | R2 ablated `change_context` only | "Deferred to a future round" |
| 24 | **Skill-file round** | `r3/README.md`, Decisions table "skill file" | open, out of scope | Agents with a repository skill file | "A skill-file round is separate" |
| 25 | **"bugs" task type** (search, read, narrow down) | `reports/r1/REPORT.md`, type table: "not scheduled [TBD team]" | open; the only unresolved TBD outside blast2 | A debugging family | Never scheduled |
| 26 | **Blast2 explanatory test** | `r3/README.md` line 54 ("demoted to a post-R3 explanatory test"); `blast2/README.md` (two `[TBD team]` markers: hop limit, repositories) | open | A test of the retrieval mechanism | Demoted |
| 27 | **Joint R3a + R3b claims need cross-round correction** | `AMENDMENT_R3-9.md`, Caveat | recorded | Each round at α 0.05; a combined claim needs correction | — |
| 28 | **Post-R3 engineering backlog** (25 pre-existing test failures, `test-d` files, a flaky wall-clock test) | `harness/experiments/BACKLOG.md` | open | Hygiene | Not R3 work |
| 29 | **tiktoken unavailable behind this environment's proxy** (PRISM falls back to a regex tokenizer) | not in the record. Seen in the replay's blast-radius envelope (`TOKENIZER_FALLBACK`); Kaggle's own pilot budgets differ (1,443 vs 1,829 tokens) | **new, unassigned** | Local checks and Kaggle runs can count PRISM's budget differently | — |
| 30 | **Workflow verification** (post-write integration check) | **not in the record** | **new, unassigned** | After an edit, check the change integrates: a break check on applied edits, tests | Named in your request only. It overlaps item 11 (break check on applied edits) and `r4/README.md`'s note |
| 31 | **Duplicate-behaviour detection** (pre-write check) | **not in the record** | **new, unassigned** | Before writing new code, find existing code that already does it | Named in your request only. It overlaps `new_function_context`'s "helpers to reuse" (Family N) |
| 32 | **Model selection beyond "one other model"** (capability as a variable) | `r3/README.md` Decisions table (R5 as "a second model") | **new framing** | Two or more models to test whether PRISM's effect depends on capability | R5 was framed as a substitution check, not a capability axis |
| — | *Closed, for completeness:* memtest late growth (`BUILD_PLAN.md` §7: rerun under R3-7, PASS); JS/TS one-week rule (the builder passed; `r3/README.md` R3-1 item 6); R2's held-out TBDs (resolved by `AMENDMENT_R2-1.md`, R2-2); all R3 `[TBD — team]` markers (R3-2; framing note in `IMPLEMENTATION_LOG.md`, 2026-11-06) | | closed | | |

**The inventory is larger than one builder can run.** Items 1–8, 23–26 and 30–32 are each a round or a
sub-round. At R3a's measured pace (about 38 GPU-hours, plus 1–2 weeks of building and gating per Family-B-sized
round), running all of them is more than six months of single-builder work *(guess)*. The roadmap must choose.

## 2. Dependency grouping

| Group | Items | Depends on | Enables | Independent of the others? | Changes the agent loop? |
|---|---|---|---|---|---|
| **G1. PRISM correctness** | 9 (D1), 11 (break-check plumbing and parse bug) | nothing (product fixes) | G2, and an honest treatment for every later round | yes. Can be built now, but should not land in the pinned R3a code | no |
| **G2. PRISM output for multi-turn use** | 10 (D2), 12 (confidence), 13 (closing block), 16 (formats) | G1 (the block reads the break check and resolution) | a "PRISM-v2" treatment | after G1 | no (tool output only; parity kept) |
| **G3. Loop protocol** | 15 (repeat notice, workflow line, goal restatement, pinned goal), 17 (early done, partly) | nothing technical; its *justification* depends on R3a's loop diagnostic | a protocol-level comparison | yes | **yes**: a protocol round, applied to both arms |
| **G4. Model capability** | 8 (R5), 32 | a model released before the task window, or tasks after each model's cutoff (§5) | a capability claim; decides whether G3 is needed | partly (needs the same harness) | no (if the protocol is held fixed) |
| **G5. New task families** | 1–5 (R3b: Family N, refactor, delete, move, tests, multi-function), 23, 25, 31 | mining (each needs its own pool); move needs a new tool; tests need wiring; Family N's result depends on `new_function_context` (and on 31 if added) | claims beyond updating callers | yes, mutually; each is a mining project | no |
| **G6. Planning from a request** | 6 (named R4), 30 (workflow verification, as the loop's "did it integrate?" step) | a task design in which no root is given; probably G1/G2 first (planning leans on the tools' honesty) | the product question beyond fixed roots | yes | it changes the task, not the loop |
| **G7. Index architecture** | 7 (base+delta), 29 (tokenizer) | nothing | speed; divergence warnings; with 11, the break check on applied edits | yes | no |
| **G8. Hygiene** | 28, 26, 21, 24 | nothing | — | yes | no |

Plotted, the graph is shallow:
- **G1 → G2** is the one hard chain.
- **G3 and G4 are alternatives** for explaining the loop: is it protocol or capability? Each is more informative
  with the other.
- **G5, G6 and G7 are independent** of all of these.

The numeric order (R3b, R4, R5) does not follow the dependencies. G1 is unnumbered and is the only thing others
depend on.

## 3. The four multi-turn findings

| Finding | What kind of issue | Addressed by | Missing item |
|---|---|---|---|
| **The loop** (identical output on repeat, the model re-asks) | **mostly model behaviour, triggered by a PRISM content gap**. Both arms failed the three tasks; PRISM gave no actionable fact for a missing callee (`PILOT_LOOP_INVESTIGATION.md` §7) | prevention: G1 + G2 (the first answer has the facts); the repeat itself: G3 (protocol); capability: G4 | — |
| **The interface limitation** (fixed symbol, no repeat differentiation) | **PRISM design**, but a consequence of PRISM's statelessness: a deterministic tool answers the same way on an unchanged tree (`CONFIDENCE_DESIGN_STUDY.md` §1) | G2 (better first answer), G3 (history-aware notice, both arms) | possibly a **free-argument PRISM tool** (for example `prism_ask` scoped to the anchor's neighbourhood) so a re-query can ask something new. **Not in the record**; a PRISM-only change, so parity holds |
| **History trimming** | **not an issue as stated** (the goal is pinned; the stubs follow the loop) | — | a **goal-distance check**: does the 14B model still act on a goal about 10,000 tokens up? That would be a G3 variant ("goal restated every N turns"). Not in the record |
| **Model capability** (14B fixed for comparability, not tested for multi-turn) | **model** (and an R1/R2 comparability constraint) | G4 | a **capability axis within one model family**, so the knowledge cutoff stays fixed (§5). Not in the record |

## 4. Loop-aware protocol: the design space (no recommendation)

| Approach | Where it lives | Changes the agent loop? | Both arms? | Cost *(guess)* | Trade-off | Fits where |
|---|---|---|---|---|---|---|
| Harness detects a repeat and injects "you already called this with these arguments" | harness (`loop.py` / `tools.py`) | **yes** | yes (it must be) | 0.5 day | Breaks repeats for both arms. Changes the protocol, so the results are not comparable with R3a's | a **protocol round (G3)**, not a PRISM round |
| Harness detects a repeat and ends the run as "stuck" | harness | no (a metric and an early stop) | yes | trivial | Saves tokens and time; labels the loop without breaking it. It would change R3a's `budget` outcomes into `stuck`, so the outcome mapping is a rule | could ride along with any round, if pre-registered as a stop rule |
| PRISM returns different text on repeat | PRISM / tool layer | no | **no (PRISM only)** | 0.25 day | Gives PRISM a loop aid baseline lacks; breaks parity | **out of scope** for a PRISM-vs-baseline comparison; a product feature only |
| The system prompt says "do not repeat calls; edit when you know what to change" | system prompt | yes | yes | trivial | May help both arms or neither; changes R3a's frozen prompt | G3 |
| The goal restated every N turns | harness | yes | yes | 0.25 day | Adds tokens (the budget is cumulative), and may distract; tests "lost in the middle" | G3 |
| The goal pinned, never trimmed | harness | — | — | — | **Already the rule** (`loop.py`, the window rule) | nothing to do |
| (added) Harness injects the last test result as a pinned line ("currently failing: N") | harness | yes | yes | 0.25 day | Counters the early-done pattern (stopping after "no matches" while tests fail) | G3 |

Reasoning, without deciding:
- Anything that changes what the model sees in **both** arms is a protocol change. It belongs to a protocol round,
  compared against R3a's protocol.
- Anything PRISM-only that is history-aware is out of scope for a fair comparison.
- A "stuck" early stop is the only harness change that is a measurement rule rather than a behaviour change.

## 5. Model selection

**The constraint first.**
- Every R3a task is a commit from 2024-12-01 on, chosen to post-date the model's release (2024-11-12). A model
  released later may have trained on those commits.
- So a cross-model comparison **on R3a's tasks** is clean only for models released by about November 2024. For
  later models it needs new tasks after each model's cutoff.
- Same-family models released together, the Qwen2.5-Coder sizes, share the cutoff. That makes a capability axis
  on R3a's own tasks possible.

**Measured baseline:** 14B q8_0 on T4×2, a median of 3.7 s per call (mean 7.4, p90 24.4), and 239 s per pilot run
or 428 s per R3a run.

| Model | Released | Fits T4×2 (30 GB) with a 20k context? | Ollama | Speed *(guess, relative to 14B q8)* | What it tells us | Caveat |
|---|---|---|---|---|---|---|
| **Qwen2.5-Coder 14B q8** (current) | 2024-11 | yes (15.7 GB) | yes | 1× | the R1/R2/R3 reference | — |
| **Qwen2.5-Coder 32B q4_K_M** | 2024-11 (same release) | probably: about 20 GB of weights plus about 3 GB of KV cache *(guess)* | yes | about 0.4–0.5×, i.e. about 2–2.5× the GPU-hours | **Capability up, same family, same cutoff.** Clean on R3a's tasks | q4 against q8 is also a precision change |
| **Qwen2.5-Coder 7B q8** | 2024-09 / 2024-11 | yes (about 8 GB) | yes | about 2× | **Capability down, same family.** Does the loop get worse? | weaker JSON tool-call discipline *(guess)*; the pilot's parse check is needed |
| Phi-4 14B | 2024-12 | yes | yes | about 1× | a different family at the same size | **16k native context**, below R3's 20,480 window rule; a December 2024 release overlaps the task window |
| Mistral Small 22B/24B | 2024-09 (22B) / 2025-03 (24B) | yes (q4) | yes | about 0.6–0.7× | a different family | not code-specialised; 24B post-dates the tasks |
| Llama 3.2 3B | 2024-09 | yes | yes | about 3× | a floor | likely too weak for the tool format; risks an "R3 not runnable with this model" result (`r3/README.md`, Cost) |

**The real question this answers:** "does PRISM's effect depend on model capability?"
- A single extra model answers "does it replicate?", not "does it depend on capability".
- Within one family, 7B / 14B / 32B on R3a's own 80 tasks is the only design here with a fixed cutoff.
- Its cost is about 3.5× R3a's GPU time for both new sizes and both arms *(guess)*: about 10 more sessions.

## 6. Roadmap options (unranked)

| Option | Scope | The smallest claim it can defend | Cost *(guess)* | Time *(guess)* | Doesn't test | Blocker |
|---|---|---|---|---|---|---|
| **A. Correctness only** | G1 (D1, break-check plumbing) as product fixes with regression tests; no experiment | none experimental: "known wrong facts removed" | 1.5 days code + 1 day re-verification; 0 GPU | under 1 week | any effect on agents | none |
| **B. PRISM-v2 arm** | G1 + G2; a v2 arm on R3a's 80 tasks × 2 seeds (160 runs), against R3a's PRISM and baseline | "v2 loops less than v1, and resolves no fewer" (paired) | about 3 days code + 1 verification; about 19 GPU-hours (2 sessions) | 2–3 weeks | protocol, model, other families | the v2 code must be frozen **before R3a's outcome files are read**, or fresh tasks are needed |
| **C. Protocol round** | G3 with the R3a tools: both arms under one loop-aware protocol (for example a repeat notice plus a pinned test line), on R3a's tasks | "under protocol P, the PRISM−baseline gap is X" (compared with R3a's gap) | 1–2 days harness; 320 runs, about 38 GPU-hours (4 sessions) | 2–3 weeks | PRISM's content; model | a cross-protocol comparison sits between rounds (sessions differ); the claim is about the gap, not either arm |
| **D. Capability axis** | G4: Qwen2.5-Coder 7B and 32B, both arms, R3a's tasks and protocol | "PRISM's effect on resolve rate changes (or not) with model size, within one family" | about 0.5 day harness; about 640 runs, about 130 GPU-hours *(guess)*, about 12 sessions | 4–6 weeks of Kaggle | other families; other model families; PRISM-v2 | 32B fitting with a 20k context on T4×2 is unverified; Kaggle quota of about 30 GPU-hours a week |
| **E. Multi-turn benchmark** | B + C + D (factorial: tool version × protocol × model) | "PRISM's effect depends on its output, the protocol and the model" | about 8× R3a's GPU time | 2–3 months | other families | quota; a factorial needs more tasks for interactions |
| **F. Breadth** | G5 and G6: Family N (R3b), refactor or delete, then user query → plan (named R4) | per family: "PRISM helps (or not) on X"; for planning: "PRISM helps choose what to change" | per family: 1–2 weeks mining and gating plus about 20–40 GPU-hours | 1–2 months per family | multi-turn mechanisms | Family N's pool (23); planning has no task design yet |

Notes:
- **A is contained in B.**
- **C and D answer the loop question from opposite sides.** If D shows the 32B model does not loop, C is less
  needed, and the reverse.
- **B is cheapest but the most narrowly claimed.**
- **F is a different axis** (what work), and is independent of A–E.

## 7. R3b viability

**As pre-registered (Family N alone): not viable on the current pool.** 23 gated, against the R3-2 minimum of 50
(`r3a_tasks/README.md`; `r3/README.md`, the Family N recommendation: "if fewer than 50 pass, it is dropped from R3
and run as its own round with a wider pool").

**What a viable R3b would need:**
- **Family N** with "different repos, or a different window" (`pilot/README.md`). A wider window cannot go earlier
  than the model's release date. So more repositories is the lever, at about 2–4 new tasks per repository *(guess,
  from 23 tasks over 7 repositories)*. That is about 10–15 more repositories to reach 50 to 80.
- **Refactor and delete:** the tools exist (Tool audit), but miners and gates do not. Delete has a natural oracle
  (no caller left, tests pass). Refactor's "no behaviour change" needs tests that pin behaviour.
- **Move:** needs a new import-site tool.
- **Tests:** need `testcontext` wired, and a scoring rule for written tests (the suite's attempt was invalid
  through truncation, `reports/r1/REPORT.md`).
- **Multi-function features:** no miner.

**Dependencies on the other groups:**
- Family N's result is "partly a result about `new_function_context`" (Tool audit). G2's changes do not touch that
  tool, but item 31 (duplicate detection) would.
- If G1 lands, R3b should use the fixed PRISM. Otherwise it inherits D1.

**Subsume or separate?** As a mining project it is separate from every multi-turn option. Folding it into B–E would
multiply their size. The realistic shape is **one family at a time**, each its own small round. **Delete** is the
cheapest next family *(guess: its oracle is simplest)*. **Family N** is next if more repositories are mined.

## 8. What R3a's result does to the options

| R3a outcome | More justified | Less justified | The new question it opens |
|---|---|---|---|
| **PRISM's loops fall mostly on tasks baseline resolved** | B (v2 targets exactly that), and C (a protocol aid might recover them) | A alone (the fixes matter for outcomes, not just correctness) | Do the loop-lost tasks share the missing-function property (13 of 80)? If yes, B's claim can be pre-registered on that subset |
| **PRISM's loops fall mostly on tasks both arms failed** | A (fix the wrong facts; no effect study), D (is failure here capability-bound?) | B as an effect study (it would not move resolve rate) | Why do both arms fail these tasks: missing callees, hidden tests, depth? A task-difficulty analysis, for which no option exists |
| **No meaningful loop difference** (few loops in either arm) | A; F (the multi-turn mechanism is not the bottleneck) | B, C | Was the pilot's 3/10 a small-sample artefact? |
| **PRISM ahead on the primary** (pass) | D (does it hold at other sizes?), F (other families) | — | Which sections drive it multi-turn (an R2-style ablation in the loop)? |
| **PRISM not shown, or behind** | A + B (if loops explain it), D (if the 14B model is the bottleneck) | F (extending a null to new families) | Is the null the tool or the protocol? C answers that |

## 9. Open questions surfaced

1. **Numbering.** The record's R4 (user query → plan) and R5 (cross-model) collide with the new multi-turn work.
   Do you keep the numbers and give the new work letters, or renumber?
2. **Freezing PRISM-v2 before R3a is read.** Option B's validity on R3a's tasks depends on freezing the v2 code
   before anyone opens R3a's outcome files. R3a will run for about 4 sessions. If B is wanted, that is the window.
3. **The 38-hour R3a.** The run takes about 4 sessions, not 2–3. With the weekly Kaggle quota *(about 30 GPU-hours,
   guess)* R3a alone fills more than a week, and any GPU option queues behind it.
4. **The interim look's practical timing.** The first 40 tasks include the two prettier tasks, which are retried
   in session 2. The look cannot happen before they finish.
5. **No difficulty analysis exists.** Every outcome in §8 eventually asks why both arms fail some tasks. The
   covariates in hand (depth, missing function, visible-tests-pass) are not combined anywhere.
6. **Cross-session nondeterminism** is unmeasured. Options B, C and D compare runs across sessions. Ollama with a
   seed at temperature 0.2 may not reproduce exactly. A small repeat (the same 10 pilot runs twice) would measure
   it before any cross-round claim.
7. **A free-argument PRISM tool** (§3) is not in the record, and is the only PRISM-side answer to the
   fixed-argument limitation that keeps parity.
8. **Workflow verification and duplicate detection** (items 30, 31) are not in the record. Each needs a problem
   statement before it can be placed: which observed failure would each have prevented? None of the pilot's
   failures is a duplicate-behaviour failure.

## 10. What this changes about R4 and beyond

What looked like "R4: the confidence round" is really two different things. **Correctness** (G1) is not a round:
it is product repair, needed whatever R3a says. **Multi-turn effect** is three separable questions:
- **content:** does PRISM-v2's first answer prevent loops? (B)
- **protocol:** does a loop-aware loop change the gap? (C)
- **capability:** does a stronger or weaker model change the gap? (D)

Each is one small, defensible round, and none of them is the record's R4, which is planning from a request (G6), a
different axis entirely. Meanwhile R3b is not one round but up to five mining projects, of which Family N is not
currently viable. The honest picture is a backlog of more than ten rounds for one builder. The order that respects
the dependencies is:
1. G1 now (it is cheap, and needed regardless);
2. if B is wanted, freeze PRISM-v2 before R3a is read;
3. let R3a's loop diagnostic choose between B, C and D;
4. treat new families (F) and planning (the named R4) as a separate, later track.
