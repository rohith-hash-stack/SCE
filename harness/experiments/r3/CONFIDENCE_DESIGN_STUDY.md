# Design study: confidence signals in PRISM (2026-11-09; investigation only — no code, no rule change, no model call)

**Hypothesis under study:** the five defects seen in the R3 pilot (D1, D2, the loop, the missing function, the
break-check results) are one design failure: PRISM states as fact what it should state with a certainty level. A
confidence-signal mechanism inside PRISM would fix the root cause, and would break the loop by making repeat
responses different.

**Hard architectural constraint.**
- **PRISM never calls a model.** Every signal below is computed deterministically from PRISM's own index, parse
  trees, graph and version control.
- An "internal verification loop" can only mean PRISM's own passes cross-checked before it returns: never a model
  called in a loop.
- A model call would break the architecture, add token cost, and destroy the fairness of any R3-style comparison
  with the baseline.
- Any signal that would need a model ("ask the model how sure it is", "sample twice and compare") is **out of
  scope**, and is marked so where it comes up.

**Evidence base:**
- the replayed pilot windows (`PILOT_LOOP_INVESTIGATION.md`, `PILOT_INTERFACE_INVESTIGATION.md`);
- a fresh offline probe of B-09's start state (index build, graph edges, digest facts, timings);
- reading the code paths named below.

## 0. The main finding: PRISM already has a confidence model; it is broken in three places

The hypothesis is half right. PRISM is not a system that states everything as fact. It already carries certainty
end to end:
- **Graph:** a call linked by the unique-name fallback (G44) is stored with `kind="TENTATIVE_CALL"`
  (`src/prism/graph/concrete_builder.py`, `_resolve_ambiguous_call`, line 3622 onward; weight 0.60). Dynamic or
  ambiguous calls get sentinel nodes (`<ambiguous:…>`, dynamic-edge sentinels).
- **Digest:** every call fact has a certainty (`CERTAIN`, `POSSIBLE`, `RUNTIME`, `UNKNOWN`;
  `src/prism/summaries/digest.py`, line 68). It is rendered as `-> X`, `-> one of: …` or `-> ?`. `_resolve` (line
  1096) turns a tentative edge into `POSSIBLE`, and `_check_receiver` (line 1250) downgrades a name-only match on
  an unknown receiver to `POSSIBLE`.
- **Dependencies:** section 5 of `change_context` marks such lines "(possible target)", and `_callers` tags
  tentative callers (`src/prism/impact/dependencies.py`, line 370).
- **Break check:** severities `fails`, `changes meaning` and `check`, plus a per-caller certainty
  (`src/prism/impact/breaks.py`).

The defects are where this model **is bypassed, discarded, or never fed**:
1. **Bypassed (D1).** `_resolve_ambiguous_call`'s scored path, used when 2 or more repo symbols share the name,
   adds the winner as a plain `CALLS` edge with no `TENTATIVE_CALL` (line 3799). It also lets a method compete for
   a call with no receiver.
   - In B-09 the edge to `aiohttp.client_proto.ResponseHandler.set_exception` is `{'relation': 'CALLS'}` and nothing
     else. The genuine `set_result` edge carries call-site metadata.
   - The digest then sees one non-tentative target, and `_check_receiver` returns early for a one-segment call
     ("A target stays certain only when the receiver is … a bare function"). So the guess is rendered **certain**.
2. **Discarded (D2).** `_call` (line 1105 onward) keeps an embedded call (inside a return, an assignment or another
   call) only if it resolved to an index target: `if certainty != UNKNOWN and (index …)`. An embedded unresolved
   call such as B-02's `getMessage(...)` is dropped by design, to avoid noise. A top-level one is kept as `-> ?`.
3. **Never fed (break check).** `BreakChecker.check` compares the anchor's **old** code (from the index) with
   proposed **new** code. In R3 the index already holds the new code, and nothing supplies the old.
   - Correction to the loop report: there I ran the check with old = new. That is why B-08 came out empty and
     B-09 named the wrong parameter.
   - The old version is available deterministically, because R3's start patch is uncommitted:
     `git show HEAD:<file>`.
   - Separately, there is one real parse bug: `signature()` reads the arrow function `const f = async (…) =>` as
     a function named `async` (B-02's wrong statement).

So most of the work is **making the existing certainty model honest and complete**, not adding a new one.

## 1. Defect-to-confidence mapping

| Defect | Root cause (code) | What fixes it | Class | Replacement text (example) |
|---|---|---|---|---|
| **D1 wrong fact** | Scored polysemy winner emitted as a full-confidence edge; a method allowed for a receiver-less call (`_resolve_ambiguous_call`); digest trusts bare-call targets (`_check_receiver`) | (a) **Correctness:** a call with no receiver never resolves to a method (Python, JS/TS). (b) **Confidence:** a scored winner among 2+ candidates is `TENTATIVE_CALL`. (c) Resolution confidence adds "not imported in this file" when the target is a module function the file does not import | **(b) correctness fix first**, then confidence | `L970 call set_exception(fut, e) -> aiohttp.helpers.set_exception? (not imported in aiohttp/connector.py)`. The section-5 claim "changes state: self._should_close = True" disappears, because it belonged to the wrong function. |
| **D2 silence** | Embedded unresolved calls dropped by design (`_call`); `-> ?` is terse | **Completeness:** every call to a name the file neither defines nor imports is listed, embedded or not | (a) confidence / completeness | `L21-29 calls getMessage(): not defined or imported in lib/ipc/get-one.js (no function of that name in the index)` |
| **Missing function** | Same mechanism as D2 plus D1: the name is either unresolved and dropped (B-02, B-08) or wrongly resolved (B-09) | D1's correctness fix, plus the D2 completeness list | (b) + (a) | In the closing block: "The anchor calls 2 names that do not exist here: `normalizeStdioSync`, `normalizeStdioAsync`." |
| **Break-check results** | No old signature in R3 (plumbing); arrow-function name parse bug | (a) **Correctness:** old code from `git show HEAD:<file>` when the working tree differs; fix `signature()` for `const f = (async)? (…) =>`. (b) **Verification confidence:** say when no old version exists ("arity check only"), and that a missing JS argument is a `changes meaning` statement, not `fails` | **mostly (b) correctness/plumbing**, plus a small confidence label | B-09: "`self._resolve_host_with_throttle(key, host, port, traces)`: `traces` (position 4) went to `traces`; it now goes to `futures`; `traces` is no longer passed." B-08: "passes nothing for the new parameter `isSync` (it will be undefined)." |
| **Loop** | The tools take a fixed symbol argument; a deterministic tool on an unchanged tree returns an identical answer | **Not a confidence problem.** Confidence is a function of the tree, which did not change, so it is identical on repeat too. What could matter: (a) the answer *preventing* the loop, by containing the actionable facts the first time (the closing block, D1/D2); (b) a *history-aware* repeat notice, which is conversation state, not confidence | **(c) confidence cannot address it** | — |

Verdict on the hypothesis:
- **D2 and the missing-function case** are confidence and completeness problems.
- **D1 and the break check** are mainly correctness fixes, in an existing confidence model.
- **The loop** is not a confidence problem.
- "Making repeat responses genuinely different" cannot come from deterministic confidence. The same tree gives the
  same confidence. It can come only from conversation state, which §4 discusses.

## 2. Confidence taxonomy (all deterministic)

| Type | Measures | Computed from (no model) | Levels | Cost | Output change | Worth building? |
|---|---|---|---|---|---|---|
| **Resolution** | for each call in the anchor, whether its target is known | Existing digest certainty, made honest (D1 fix, tentative scored edges), plus a file-scope binding check: is the name defined, imported or bound in the file? This is `covariates.py`'s logic; the digest already has `import_map` and `_resolve_import` | `certain` (one target, in scope) · `possible` (tentative or several candidates; list them) · `not in scope` (a candidate exists elsewhere but the file does not import it; name it) · `unresolved` (no candidate in the index) | under 5 ms per anchor: the digest is 12 ms for B-09's anchor, and the scope check reuses its parse | per-call suffix (exists today: `-> X`, `one of`, `?`), the new `not in scope` and `unresolved` wording, and an "Unresolved calls" line in the closing block | **Yes**: D1, D2 and missing function |
| **Completeness** | whether the caller list could be missing callers | Counts already in the graph: in-edges that are `TENTATIVE_CALL`; `<ambiguous:…>` and dynamic-edge sentinels whose candidates include the anchor; for JS, exports reachable through barrel files not followed | `complete (static)` · `N possible callers via ambiguous or dynamic calls` · `unknown` | under 10 ms (one pass over the anchor's in-edges and the sentinels) | one line under section 4: "1 caller found statically; 2 more possible (ambiguous calls in X, Y)" | **Low priority.** It addresses no observed pilot defect. Every looping task had exactly the right single caller. Cheap, so it can ride along. |
| **Consistency** | whether `change_context` and `blast_radius` agree on the direct callers | Both read the same graph (`dependencies._callers` vs the blast walk). They can differ only through filters (tests, budget) | `consistent` · `differ by N` | about 1 s, because it needs both computations | none in the normal case | **No.** They are consistent by construction. The check would cost about 1 s per call to say nothing. Useful as a unit test, not a runtime signal. |
| **Verification** | how far the break check could check each call | `BreakChecker` with old code from version control. Per call, it reports whether the binding was decidable: no splat or spread, known receiver, both signatures parsed. Self-check: compare the parameter names that `signature()` extracts with the digest's `parameters` line, and downgrade on disagreement | `fails` / `changes meaning` / `check` as today, plus `cannot determine (reason)`; plus the existing `certain` / `possible` caller certainty | under 20 ms per caller (one tree-sitter call search and a binding) | the break list (exists, but R3 never shows it) gets "cannot determine" lines and a header saying what was compared ("against the committed version") | **Yes**, once the old-code plumbing and the parse fix exist. It is the source of the closing block's "does not match" lines. |

Out of scope: any "model-estimated confidence", "ask the model whether the target is right", or "self-consistency
over two samples". All of them need a model call.

## 3. Internal verification loops (cross-checks between PRISM's own passes)

| Cross-check | Compares | Disagreement looks like | Produces | Cost | Worth it? |
|---|---|---|---|---|---|
| **Receiver-less call vs. target kind** | the linker's chosen target (`_resolve_ambiguous_call`) against the call's syntax (no receiver) | a bare `f()` linked to `SomeClass.f` | impossible: the edge is dropped, not just downgraded (this is D1's correctness rule, enforceable at link time) | ~0 (a field check) | **Yes.** It removes D1's wrong fact at the source. |
| **Call target vs. file scope** | the digest's resolved target against the file's own bindings and imports (`import_map`) | the target is `aiohttp.helpers.set_exception`, but `connector.py` does not import it | `not in scope` | under 5 ms | **Yes.** It is the missing-function signal. |
| **Signature parse vs. digest parameters** | `breaks.signature()` against the digest's `L… parameters (…)` line | different names or counts (B-02: the function was named `async`) | the verification level becomes `cannot determine` | under 1 ms | **Yes.** It would have suppressed B-02's wrong statement even before the parse fix. |
| **Break prediction vs. re-binding** | `bind()` against a second binding computed the same way | they cannot disagree (the same code) | — | — | **No.** It is not an independent check. |
| **Caller set: change_context vs. blast radius** | `dependencies._callers` against the blast walk at hop 1 | a filter difference | `consistent` / `differ` | about 1 s | **No.** See §2. |
| **Digest raise/condition facts vs. the static analyzer** | the digest's `raises` and `catches` facts against the tagger's raise sites | rare; not seen in any pilot run | — | 10–50 ms | **No** for now. No observed defect. |

**Cost of everything worth building: well under 50 ms per `change_context` call.** For comparison, a warm
`change_context` call is 675 ms and a warm `blast_radius` 1,038 ms (B-09, measured today); the index build is 33 s
once per run.

## 4. The loop-breaking hypothesis

**Is it true that a changed response changes behaviour?** That is an empirical claim about the model, and it can't
be settled offline. Two distinctions narrow it:
- **Deterministic signals do not change on repeat.** The tree is unchanged, so the second answer equals the first,
  confidence included. A response that differs on repeat needs conversation state, a "you already asked this"
  notice. That would live in the harness's tool layer (`harness/experiments/r3/tools.py`, `ToolLayer.call`), not
  in PRISM, which is stateless per call.
  - **If it is applied to PRISM's tools only,** it is a loop-detection aid for one arm. That breaks the parity
    rule (R3-1, R3-5), so it is **out of scope** for any R3-style comparison.
  - **If it is applied to both arms' tools,** it is a change to the loop: a different round's question (R4c
    below).
- **What can work within parity is prevention:** an answer that already contains the actionable facts the first
  time. These are: the caller's call does not match; `getMessage()` does not exist; `set_exception` is
  `aiohttp.helpers.set_exception`, not imported. In all three looping runs this is information the model never
  had (§0). A confidence *field* alone, such as "resolution: possible", is probably not enough. The concrete
  mismatch and missing-name sentences are the minimal change.

**Minimal change to test:** D1's correctness rule, plus the D2 unresolved list, plus a closing "What this means for
the change" block in `change_context` (§5). No repeat notice.

**Test protocol, without re-running R3a.** The pilot tasks are never reported as results, so they are fair game.
1. **Offline (no model, here).** Render the new `change_context` for the three looping tasks' start states, and
   check it by hand against the developer's fix:
   - B-02 must name `getMessage` and the argument mismatch;
   - B-08 must name the two missing functions and the missing `isSync`;
   - B-09 must name `aiohttp.helpers.set_exception` as not imported, and `futures` inserted before `traces`.

   Also confirm the output is unchanged on the 7 non-looping pilot tasks, apart from the closing block.
2. **Next-action probe (Kaggle, about 30 minutes).**
   - For each looping task, take the exact window the model saw before its first repeat, from the replay. Swap
     the PRISM outputs in that window for the new rendering, and sample the model's next call 10 times
     (temperature 0.2, seeds 42–51).
   - Control: the same with the current outputs.
   - Measure the share of next calls that are `edit_file`, `read_file` or `grep`, against a repeat.
   - Size: 3 tasks × 2 conditions × 10 samples = 60 single calls.
   - This isolates "does the output change the next action?" from everything else in a run.
3. **Small full-run check (Kaggle, about 1.5 hours)**, only if step 2 shows a shift: the 10 pilot tasks, PRISM arm
   only, the new outputs, seeds 42 and 1001 (20 runs). Measure the loop counter and the early-done counter, and
   compare with the pilot (3/10 loops at seed 42).

Steps 2 and 3 need model calls, so they are Kaggle runs for the user. Step 1 is offline.

## 5. Per-tool additions

| Tool | Current output | Proposed additions | Example |
|---|---|---|---|
| `prism_change_context` | 8 sections: codebase, group, anchor and digest, who depends on it, what it depends on, style, callers' code, wider impact | (1) honest resolution in the digest (D1 fix; `not in scope` / `unresolved` wording; embedded unresolved calls kept); (2) section 5 inherits it ("possible target" already exists); (3) a closing block (below); (4) optionally a completeness line in section 4 | see the block below |
| `prism_blast_radius` | hop list, then the `prism_context` XML envelope | resolution certainty per caller (tentative edges marked; the data exists in the graph); an optional completeness line. The XML metadata (coverage features, warnings) is noise for an agent. Dropping it is a format change outside this study's question. | `- hop 1: aiohttp.connector.TCPConnector._resolve_host (connector.py:863) [certain]` |
| `prism_digest` | digest lines with certainty suffixes | the same honest-resolution changes as `change_context`'s digest (it is the same code) | `L95 call getFileTarget(otherStdioItem): not defined or imported in this file` |
| `prism_ask` | a raw JSON dump of ranked hits | nothing from this study. Ranking confidence (score margins) addresses no observed defect. Its JSON format is a separate readability issue. | — |
| `prism_new_function_context` | helpers, conventions, similar functions | nothing from this study (Family N; no observed defect) | — |
| `prism_digest` signature field | the `L… parameters (…)` line | used as the cross-check for `breaks.signature()` (§3); no visible change | — |

**The closing block for `prism_change_context`** (descriptive, never prescriptive; built from the verification
check and the resolution check):

```
## What this means for the change
Compared with the committed version of lib/stdio/option.js (the change to `normalizeStdio` is not committed):
- `lib.stdio.handle.handleInput` (lib/stdio/handle.js:15) `normalizeStdio(options)`: passes nothing for the new
  parameter `isSync` (it will be undefined). [changes meaning; caller certain]
Calls in the new code that this file does not define or import:
- `normalizeStdioSync()` (L6): no function of that name in the index.
- `normalizeStdioAsync()` (L6): no function of that name in the index.
Not checked: callers through ambiguous or dynamic calls (0 here).
```

B-09:

```
## What this means for the change
Compared with the committed version of aiohttp/connector.py:
- `aiohttp.connector.TCPConnector._resolve_host` (connector.py:913)
  `self._resolve_host_with_throttle(key, host, port, traces)`: `traces` (position 4) went to `traces`; it now goes
  to `futures`; `traces` is no longer passed. [fails; caller certain]
Calls in the new code that this file does not define or import:
- `set_exception()` (L970): `aiohttp.helpers.set_exception` exists, and aiohttp/connector.py does not import it.
```

**How it interacts with the confidence signals:**
- The block *is* the confidence signals rendered as sentences: verification results for callers, resolution
  results for callees.
- Each line keeps its level in brackets. A `cannot determine` line says why ("the call passes `*args`").
- The block is omitted when there is nothing to report. A clean check is not an all-clear: the existing break
  renderer already avoids claiming one, and the block keeps that rule.

## 6. Cost and where the code lives

| Change | File / function | New pass or addition | ms per call | Effort | Pre-registered rule touched? |
|---|---|---|---|---|---|
| D1: no method target for a receiver-less call; scored winner tentative | `src/prism/graph/concrete_builder.py`, `_resolve_ambiguous_call` (candidate filter; the `add_edge` at line 3799 gets `kind="TENTATIVE_CALL"`) | addition (index build) | ~0 at query time | 0.5 day, plus re-verification: edges change for every repository, so E1–E3, the blast-radius test-code check, the dry run and the R1/R2 regression suites must be re-run | no (product code) |
| Digest: bare-call method guard; `not in scope` / `unresolved` wording; keep embedded unresolved calls | `src/prism/summaries/digest.py`: `_check_receiver` (line 1250), `_call` (lines 1105–1150), `_unresolved_target`, the renderer (line ~291) | addition | under 5 | 0.5 day | no |
| Break check: old code from version control; arrow-function `signature()` fix; `cannot determine` level; parameter cross-check | `src/prism/impact/breaks.py` (`signature`, `BreakChecker.check`); old code from `git show HEAD:<file>`, called from `src/prism/impact/context.py` (`ctx.breaks`, line 167) or `src/prism/mcp/server.py` `prism_change_context` | addition (plus a git read) | under 20 per caller, plus about 10 for one `git show` | 1 day | no |
| Closing block | `src/prism/impact/context.py`, `render` (a new section, built from `ctx.breaks` and the digest's unresolved calls) | addition (rendering) | under 5 | 0.5 day | no |
| Completeness line (optional) | `src/prism/impact/dependencies.py` `_callers`, plus the sentinel lookup | addition | under 10 | 0.25 day | no |
| Repeat notice | `harness/experiments/r3/tools.py` `ToolLayer.call` (harness state, not PRISM) | new behaviour | ~0 | 0.25 day | **yes, if one arm only** (loop parity): out of scope for an R3-style comparison; a loop change if both arms |

**Total for the in-scope set** (D1, digest, break check, closing block): about **2.5–3 days of code**, plus about
**1 day of re-verification**. That is over the one-day threshold, so it is significant new work.

The runtime cost is negligible: under 50 ms added to a call that already takes 0.7 s. None of it touches a scoring
rule, the task list or the agent loop. It changes what PRISM's tools say, which is the treatment, so any
experiment using it is a new round.

## 7. R4 shape

**What R3a finding justifies R4:**
- **Justifies R4:** PRISM's loops concentrate on tasks **baseline resolved** (same seed). The diagnostic is
  `prism_loops_on_baseline_resolved_tasks`. Then the loop costs PRISM resolutions it could have had, and the fixes
  have a measurable target.
  - A softer trigger: PRISM is behind baseline on the 13 missing-function tasks, with no such gap on the other
    67.
- **Does not justify an effect round:** loops concentrate on tasks both arms failed. The loop is how PRISM fails,
  not why. The fixes would not move the primary result.
- **D1 should be fixed either way.** It is a wrong fact in a product, and it needs a regression test, not an
  experiment.

**Minimal pre-registration, if R4 runs:**
- **Freeze the design before R3a's results are read.** This document is written during R3a's run. If the v2 code
  is frozen and hash-committed before R3a's outcome files are opened, R3a's own 80 tasks can be reused without
  tuning-to-test leakage. Otherwise R4 needs fresh tasks.
- **Arms:** PRISM-v2 (the in-scope set above) against PRISM-v1 (R3a's PRISM runs), on R3a's 80 tasks, seeds 42
  and 1001.
  - Only the v2 arm is new: 160 runs, about 11 hours, 1–2 sessions.
  - Baseline is unchanged, because v2 touches only PRISM's tools, so R3a's baseline runs remain the reference for
    v2-vs-baseline as a secondary.
  - Caveat: the arms run in different sessions, so session nondeterminism is part of the comparison. The pilot
    can measure its size.
- **Primary claim, the smallest testable one:**
  > On Family B tasks, PRISM-v2's runs loop less often than PRISM-v1's (paired by task and seed), and v2 resolves
  > no fewer tasks.

  - Statistics: loop rate by McNemar per seed, α 0.05. Resolve rate as a non-inferiority secondary, with a margin
    fixed in advance.
  - This claim is small, directly tied to the mechanism, and powered by the expected effect: in the pilot the
    loops were 3/10 with an all-or-nothing pattern.
- **A secondary that is stated, not tested:** the v2-vs-v1 resolve difference on missing-function tasks.

**Replace or extend; independent or contingent?** R4 **extends** R3a and is **contingent** on it, in three ways:
- R3a's result decides whether R4 is justified;
- R3a's PRISM-v1 runs are R4's control;
- R3a's baseline is R4's reference.

R4 cannot be interpreted without R3a.

**One round or several?** Several, with different scopes:
- **R4a (this study):** correctness and confidence in PRISM's existing context tools; Family B; parity unchanged.
- **R4b:** the new-function context (Family N, i.e. R3b's ground). It does not follow from R4a.
- **R4c:** loop-level aids for both arms (a repeat notice, a workflow line). It changes the agent loop, so it is a
  different question.

## 8. What this does not solve

- **The model's own judgement.** PRISM can say "`getMessage()` does not exist". Whether a 14B model then writes
  the helper is the model's behaviour. In the pilot, baseline knew and gave up.
- **The fixed-argument property.** The tools still take one symbol. A run that has used the closing block and is
  still stuck will repeat again. Only a loop-level change (R4c) addresses repeats themselves.
- **The early-done runs.** Six of seven stopped after "no matches" or "all tests pass", not after a PRISM output.
  Nothing here touches them.
- **Hidden-test tasks** (pilot B-06: the visible tests all pass). No PRISM signal can supply a failing signal the
  task does not give.
- **Other families.** R4a's claim is about Family B (updating callers). It says nothing about writing new
  functions.
- **The product question.** "Does PRISM make the model better overall?" stays with R3a's primary. R4a can show at
  most that specific defects, once fixed, stop costing runs.
- **Completeness beyond static analysis.** Dynamic dispatch, reflection and string-built calls stay outside the
  graph. The completeness line can count what it knows it missed, not what it cannot see.

## 9. Summary

The architecture is coherent, but not in the form proposed. PRISM already has a deterministic confidence model:
tentative edges, four certainty levels in the digest, "possible target" in its dependency facts, and severities in
the break check. Four of the five defects are places where that model is bypassed (D1: a scored guess emitted as
certain, a method allowed for a bare call), discarded (D2 and the missing function: unresolved embedded calls
dropped), or never fed (the break check, which in R3 has no pre-change version, plus an arrow-function parse bug).
Those are correctness and completeness fixes. Rendered as a short descriptive closing block in `change_context`,
they give the model exactly the facts it lacked in all three looping runs. The fifth defect, the loop, is not a
confidence problem. A deterministic tool on an unchanged tree answers identically, confidence included, so only
prevention, a better first answer, is within parity; a repeat notice is a loop change. Everything is deterministic
and needs no model call: about 3 days of code plus a day of re-verification, under 50 ms per call. The smallest R4
that tests it is a PRISM-v2 arm only, frozen before R3a's results are read, on R3a's 80 tasks and two seeds (160
runs), against R3a's own PRISM runs. Its claim is narrow: v2 loops less and resolves no fewer. It is run only if
R3a shows PRISM's loops falling on tasks baseline resolved. A one-hour next-action probe on the three pilot loops
can check the mechanism first. D1 should be fixed regardless, as a product defect.
