# PRISM: required behaviour (spec, adopted 2026-11-09; the gate before R4)

**Purpose.** What PRISM must do so that a coding agent, using it, makes complete and consistent changes, and the
user does not have to re-verify the code, adjust it elsewhere, or explain what the codebase has. It is written from
the agent's side: what the agent asks, and what it must get back.

**Rule (adopted by the user, 2026-11-09):** every requirement below is fixed or implemented in PRISM **before R4
starts**. R4 then tests this PRISM. Each requirement has acceptance criteria that are automated tests (§9).

**Architectural constraint:** PRISM never calls a model at query time. Every answer comes from the index, the parse
trees, the graph and version control. Whether an offline, model-written summary index may be used is an open
decision (`ROADMAP_R4_R6.md` §8, item 5).

## 0. Why: where agents fail today

From the agent's side, and seen in the R3 pilot (`r3/PILOT_LOOP_INVESTIGATION.md`,
`r3/PILOT_INTERFACE_INVESTIGATION.md`). Each failure is why a user re-verifies:

1. Touch points missed: callers through re-exports or aliases, overrides, registrations, type definitions, docs.
2. Assumed behaviour: whether a function can return nothing, what it raises, what it changes.
3. Existing behaviour re-implemented: a helper that already exists is not seen.
4. Its own new calls broken: a name not imported, arguments in the wrong order, an error not handled (pilot B-02,
   B-08, B-09).
5. Partial updates: some callers, or not the types, docs or tests.
6. Premature "done": after "no matches" or a passing test file that does not exercise the change.
7. Unknown unknowns: a dynamic call missed without knowing it was missed.

## 1. Answer quality (applies to every requirement)

| ID | Requirement | Acceptance |
|---|---|---|
| **Q1 Honest** | No fact is stated as certain unless it is. Each fact carries `certain` / `possible` / `not followed (reason)` | a receiver-less call never resolves to a method; a scored choice among 2+ candidates is `possible` (fixes D1) |
| **Q2 Complete about gaps** | What PRISM could not resolve is listed, never dropped: unresolved names, dynamic calls, reflection | every call to a name neither defined nor imported in its file is reported, embedded calls included (fixes D2) |
| **Q3 Current** | Every answer reflects the working tree as it is now, the agent's edits included | after an edit, the next answer reflects it (the R3-5 live index, E1–E3 equivalence) |
| **Q4 Short and actionable** | Statements an agent can act on ("this call does not match: …"), not dumps; no machine formats (XML, raw JSON) served to the agent | each tool's output is readable text within its token cap |
| **Q5 Askable** | Any symbol and any relation can be asked about, not one fixed report | every touch-point query (§2) takes a symbol argument and a relation |
| **Q6 Deterministic** | The same tree and the same question give the same answer | repeat-call tests |

## 2. Touch points of a symbol (before changing anything)

| ID | The agent asks | PRISM returns | Status today |
|---|---|---|---|
| **T1** | What is it? | signature; what it does, returns, raises and changes (the digest), with certainty | **exists** (`summaries/digest.py`), with D1/D2 defects |
| **T2** | Who calls it, and how? | direct callers and, per call site, the arguments passed, the result's use, the errors caught; transitive callers by distance | **exists** (`impact/dependencies.py`, blast radius) |
| **T3** | What does it call? | callees, with their returns, raises and state changes | **exists** (change context §5) |
| **T4** | What overrides or implements it, and what does it override? | the class-family members with the same method | **partial** (virtual-dispatch expansion in the graph); **not served** as a touch point |
| **T5** | Where is it imported or exported? | every import, `from … import`, `require`, re-export and barrel file | **missing** |
| **T6** | Where is it registered? | routes, CLI commands, plugin and entry-point tables, DI and config strings naming it | **partial** (`scanner/entrypoint_gate`, registered entry points); **not served** per symbol |
| **T7** | Where is its type declared? | `.d.ts` / stub / interface declarations of it | **missing** |
| **T8** | Which tests reach it? | test functions that call it, directly or within N hops | **exists, not served** (`impact/testcontext.py`) |
| **T9** | Which docs mention it? | doc files and docstrings naming it | **missing** |
| **T10** | All of the above as one checklist | the touch points of the anchor, each with certainty | **partial** (`change_context`) |

## 3. Before writing new code

| ID | The agent asks | PRISM returns | Status today |
|---|---|---|---|
| **N1** | Does this behaviour already exist? (inside the target scope, and anywhere) | candidates **with evidence**, never a verdict: matching signature and words; the same callee set; the same digest facts (returns, raises, state); a near-identical normalized structure | **missing** (only word search) |
| **N2** | What should I reuse? | helpers ranked by relevance and nearness, with signatures and digests | **exists** (`impact/newcode.py`) |
| **N3** | Where should it live, and how is code written here? | the group's API, the class's methods, conventions, the closest similar functions | **exists** (`impact/newcode.py`, `impact/style.py`) |

## 4. After editing: check my changes

| ID | The agent asks | PRISM returns | Status today |
|---|---|---|---|
| **V1** | Do my calls fit what I call? (upstream) | per call in new or changed code (found from the parse tree, never by text pattern): the target exists and is imported (Q1, Q2); its **origin** (internal, external, unresolved: V1a); the arguments bind to its signature; the return value's use matches what it returns; its raises are handled. Facts, never verdicts | **missing** (the break check binds callers to the anchor only) |
| **V2** | Do callers still fit what I changed? (downstream) | per caller: fails, changes meaning, or check, compared with the **committed** version | **partial** (`impact/breaks.py` needs the pre-change version from `git show HEAD:`, and an arrow-function parse fix) |
| **V3** | What have I not updated? | touch points (T2, T4–T9) of each changed symbol that the working tree has not touched yet | **missing** |
| **V4** | Check everything I changed | V1–V3 over every function changed in the working tree against `HEAD`, with certainty. **Empty when consistent.** It changes as the tree changes, so it is never an identical repeat | **missing** (a new tool) |
| **V5** | Which tests should I run? | the smallest set of test files that reach the changed functions (from T8) | **missing** |

**V1a: the callee's origin (adopted 2026-11-10).** The right fix for a mismatch depends on where the callee comes from:

| Callee | Fact | What the agent should do |
|---|---|---|
| a downstream caller | "`caller` passes 3 arguments; `anchor` now takes 2" | update the caller |
| internal (defined in, or imported from, the repository) | "internal `Y` now requires `strict`" | the agent's choice: update the callee, or adapt the call |
| external (a third-party package, the standard library, a system module, a runtime builtin) | "you call `requests.get` with `timeout=None`; the library expects a positive number. Do NOT edit the library: adapt this call" | adapt the call only, never the library |
| unresolved (defined nowhere, imported from nowhere) | "`X` is called but defined nowhere: define or import it" | define or import |

- **External** includes type stubs (`.pyi`, `.d.ts`) of external APIs, and vendored code (`vendor/`,
  `third_party/`, `node_modules/`): physically present, logically external.
- **Ambiguous imports** (star imports, namespace packages, a name that is both a repository root and an installed
  package) default to external, and the fact names the ambiguity.
- **The same name internal and external:** the file's import decides. If it cannot, both are reported.
- **External arguments** are checked only against a known signature (installed package headers, stubs). Otherwise
  the fact says the arguments were not checked (L1).
- **Design and order:** `r4/CHECK_CHANGES_DESIGN.md`.

**V4 is `prism_check_changes`, the core of R4's multi-turn loop (2026-11-10).**
- **When it runs:** after every edit, it returns facts in four sections: downstream; upstream internal; upstream
  external (do not edit, adapt the call); warnings.
- **Shape:** empty sections omitted; under 800 tokens; at most 0.5 s.
- **When the tree is clean,** it returns one line: "No downstream or upstream issues detected from the latest
  edit."
- **It never gives a verdict.** The agent decides what to do with the facts.

## 5. What PRISM must say it cannot do

| ID | Requirement | Acceptance |
|---|---|---|
| **L1** | Name what static analysis cannot see: runtime-only behaviour, external services, reflection, string-built calls | listed as `not followed (reason)` in T2 and V4; never silently omitted |
| **L2** | Say when a check was not possible | V2's `cannot determine (reason)`: a splat or spread argument, an unknown receiver, an unparsed signature |

## 6. Serving (tool surface for the agent)

Not a change to the R3 loop or protocol. The protocol (repeat notice, pinned test line) is R4's, applied to both
arms (`ROADMAP_R4_R6.md` §3 C).

| Tool | Serves | Status |
|---|---|---|
| `prism_change_context(symbol)` | T1–T3, T10, plus a closing "what this means for the change" block (V2 and Q2 facts for the anchor) | exists; block **missing** |
| `prism_touch_points(symbol, relation?)` | T2–T9, one relation or all | **missing** (new) |
| `prism_blast_radius(symbol)` | T2 transitive, as a readable hop list | exists; XML envelope to remove (Q4) |
| `prism_digest(symbols)` | T1 | exists |
| `prism_ask(question)` | orientation search | exists; raw JSON to remove (Q4) |
| `prism_new_function_context(...)` | N1–N3 | exists; N1 **missing** |
| `prism_test_context(symbol)` | T8, plus tests to write against | exists as a module; **not served** |
| `prism_check_changes()` | V1–V5 | **missing** (new) |

## 7. How the agent is expected to use it (the workflow PRISM is built for)

1. **Orient:** `prism_ask`, instead of blind search.
2. **Map the touch points:** `prism_change_context` / `prism_touch_points`. That list is the change's checklist.
3. **Check for existing behaviour** before writing anything new (N1), and reuse it (N2).
4. **Make the change:** the anchor plus everything on the checklist.
5. **`prism_check_changes`:** fix what it reports, and repeat until it is empty.
6. **Run the tests it names** (V5).
7. **Report with evidence:** what changed; every touch point and how it was handled; what PRISM and the tests
   verified; and what is **not** verified (L1, L2).

The task types differ only in where they enter:
- "add" enters at step 3;
- "change" and "fix" at step 2;
- "restructure" leans on T5–T7;
- "understand" stops at step 2.

## 8. Regression safety (adopted by the user, 2026-11-09)

"One implementation should not break another feature of PRISM."

| ID | Requirement | Acceptance |
|---|---|---|
| **R1 Unit coverage** | Every PRISM module has unit tests for its behaviour. The coverage baseline is measured, and gaps are closed before R4 | a line-coverage report per module (`pytest --cov=prism`); modules under a threshold listed with a plan (the threshold is decided after the baseline) |
| **R2 Golden outputs** | The served tool outputs (§6) are pinned on fixed fixture repositories (Python and JS/TS) and compared on every change. An intended change updates the golden file in the same commit | a golden-output test per tool per fixture |
| **R3 Cross-feature invariants** | Properties that hold across features are tested together: index equivalence (E1–E3); the caller set consistent between `change_context` and blast radius; certainty never upgraded by rendering; no test code listed with `include_tests=False` | invariant tests over the fixtures |
| **R4 Clean suite** | The suite passes except for clearly marked environment skips. The 25 pre-existing failures are triaged (`harness/experiments/BACKLOG.md`): environment gaps become skips, and real drift is fixed or re-recorded only when the change was intended | `pytest` green, with a skip reason on each skip |

## 9. The pre-R4 gate

R4 starts only when:
- every requirement in §1–§6 is implemented, with its acceptance tests passing;
- §8's R1–R4 are met;
- the R3 verification set passes on all task families' repositories: E1–E3, the blast-radius test-code check, and
  the dry runs (gold resolves, empty does not);
- an R4 pilot shows no harness error.

The order of work is in `ROADMAP_R4_R6.md` §3. Correctness first (Q1, Q2, V2), because every other requirement
reads those facts.

## 10. Task families: what each R4 task asks, and when it is done (the R4 design contract; adopted 2026-11-10)

**The principle (the user's decision, 2026-11-10):** "Each family requires its own task template, done criterion,
and signal gate. … R4 does not use a single template."
- **Why:** R3a's one template ("update the rest of the project so its tests pass") was wrong for 5 of 6 families.
- **The evidence:** in 23 of R3a's 80 tasks the agent saw no failing test (`r3/R3A_FLOOR_INVESTIGATION.md`).

| Family | Prompt template | Done criterion | Signal gate |
|---|---|---|---|
| Delete | "Remove [target]. Remaining tests must still pass." | Delete completed, project tests pass | Tests pass at end, target absent |
| Refactor | "Refactor [target] to [goal]. Behavior must not change; all tests must still pass." | Code changed, all tests pass | Tests pass at start AND end; diff is structural |
| Move | "Move [target] to [destination]. Callers must be updated. Tests must pass." | Moved, callers updated, tests pass | Tests pass at end; old path gone, new path exists |
| Tests | "Write tests for [target]. They must pass on current code, fail on [prior version]." | Tests pass on current, fail on prior | Score by fail-to-pass on the prior version |
| Feature | "Implement [feature spec]. Tests for the feature must pass." | Feature tests pass | Visible failing test at start |
| Family B (callers) | "Function [X] changed [diff]. Update callers. Hidden tests will verify." | Fail-to-pass tests pass, no pass-to-pass broken | At least one visible failing test at start (G1) |

**What PRISM must support per family:** the stages in §2–§4 and `ROADMAP_R4_R6.md` §2.
- delete and move need import sites (B3);
- refactor needs S4 and S5 over every changed function;
- tests needs `testcontext` (B4);
- feature needs S1 and S2.

