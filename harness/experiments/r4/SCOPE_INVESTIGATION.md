# Two-direction scope analysis (bounded-task detection): investigation (2026-11-10)

**Question:** can PRISM tell, before the agent starts, whether a "change the anchor, update its callers" task is
bounded? The analysis walks two directions from the anchor:
- **downstream:** the callers that break, and how many hops the update must travel;
- **upstream:** whether the anchor's new code fits the functions it calls.

**Constraints:** investigation only. No PRISM code, no new tool. It uses R3a's 80 tasks and git history.

**Script and outputs:** `scope_inv/`.
- `scope.py`: per task, two static PRISM indexes. P is the parent (the old anchor); S is the start state (the new
  anchor).
- `scope2.jsonl`: the raw records.
- `final.json`: the classification below.

## 1. Downstream (cascade) analysis: feasible and cheap; hop 1 predictable, hop 2+ only after edits

| Measure | How | Result on R3a's 80 |
|---|---|---|
| Callers by hop, files by hop | reverse BFS over the index's caller edges (`DependencyAnalyzer._callers`), production code only | always computable; 0.001 s median on an existing index |
| Which callers break at hop 1 | the break check (`BreakChecker.check(anchor, new_code)`), the old code from P | flagged 83 callers, 72 of them changed by the developer (precision 87%). It caught 72 of the developer's 131 changed direct callers (recall 55%) |
| Hops the update really needed | the developer's changed functions, placed on the hop map | 59 tasks: hop 1 only; 13: up to hop 2; 4: hop 3; 2: hop 5 (B-04 pydantic, B-70 execa); 2: no caller changed |
| Graph depth (upper bound) | the BFS's deepest hop | 16 tasks have callers 8+ hops away; the developer stopped at hop 1–2 in 72 |

- **The break check is blind when the signature does not change.** It flagged nothing in 23 tasks. Those are changes
  of meaning: return values, behaviour, a removed normalization (B-43). Recall there needs tests, or a digest diff of
  the anchor.
- **Hop 2 and beyond cannot be predicted before the edit.**
  - Whether a hop-1 caller's update propagates depends on that edit: does the caller's own signature change?
  - Graph depth is only an upper bound. It is not the cascade: 16 tasks reach 8+ hops in the graph, while the
    developer's real changes stop at hop 1–2 in 72 tasks.
  - The cascade is found by re-running the break check on each edited function. That is S5 (`prism_check_changes`,
    roadmap B5).
- **Cost:**
  - **The index:** 15.6 s median (p90 84 s, max 149 s), the same index PRISM already builds.
  - **The incremental analysis:** under 0.1 s per anchor (break check 0.015 s, BFS 0.001 s).
  - **For comparison,** R3a's `prism_blast_radius` took 0.30 s median (p90 18 s) per call on a built index.

## 2. Upstream (callee) analysis: feasible; the call extraction must be syntax-based

| Measure | How | Result |
|---|---|---|
| The anchor's callees | S's call edges from the anchor | always available (0.003 s) |
| A callee exists | the new anchor's calls that resolve to nothing, are not defined anywhere in the repository at the parent, and are not imported, a parameter, a local or a builtin | 8 real cases in 8 tasks. The first pass's regex call extraction also flagged `async (`, a SQL string (`VARCHAR`) and called local variables: false positives a syntax-tree extraction removes (the A3/D2 fix does this) |
| Arguments bind to the callee's signature | the break check's `bind`, reversed (callee signature against the anchor's call) | 5 flags: 1 plausible (B-04); 4 false. Those 4 come from D1-style resolution (a bare `id(...)`/`type(...)` resolved to a repository method) and from a signature misparsed around a `# type:` comment |
| The anchor needs a callee to change | only through its symptoms before the edit (an arity mismatch, a missing name); otherwise known only from the developer's commit | the developer changed a callee in 18 tasks |
| Callees the anchor stopped calling | the old anchor's callees minus the new anchor's | dropped in 20 tasks; in 4 the dropped callee now has no caller; in 5 the developer deleted it |

## 3. The five callee cases

| Case | Signal | Detectable before the agent starts? | R3a tasks | Blocker or warning |
|---|---|---|---|---|
| U1 anchor calls an undefined function | "anchor incomplete" | **yes**, with syntax-based call extraction | 8 (B-05, B-45 (a missing import), B-46, B-54, B-57, B-67, B-70, B-71); the developer defined or imported every one | **blocker** for a caller-update task: the change needs code beyond callers. **For the agent:** certain and actionable ("define or import X") |
| U2 the anchor's call has the wrong arity | "anchor call wrong" | **yes** (`bind` reversed), only when the callee is resolved with certainty | 1 plausible | blocker when certain; warning when the target is only possible |
| U3 the anchor needs a callee to change | "not a caller-update task" | **no**, except through U1 or U2 symptoms. From the developer's commit: yes | 18 | a **task-selection filter** (mining and gating), not an agent signal |
| U4 a callee was removed | "anchor needs replacement" | not at the start: only the anchor has changed. It appears as U1 when the anchor calls the removed name | 5 (all post hoc: the commit deleted a callee the new anchor dropped) | warning |
| U5 the anchor stopped calling a callee | "dead code check" | **yes** | 4 (the dropped callee has no other caller) | warning ("X is now unused: remove it?") |

## 4. R3a's 80 tasks under the two-direction analysis (against the developer's commit)

**The classes,** first match wins:
1. **upstream-fail:** any of U1–U4, false-positive classes excluded;
2. **beyond callers:** the developer changed functions that are neither callers nor callees. These are other
   functions in the anchor's file, functions unrelated by the call graph, or new functions;
3. **cascading:** callers only, more than 2 hops;
4. **bounded + clean:** callers only, within 2 hops, upstream clean.

| Class | Tasks | |
|---|---|---|
| **bounded + clean** (a real caller-update task) | **10** | B-13, B-19, B-22, B-23, B-29, B-36, B-40, B-44, B-62, B-66 |
| upstream-fail | 27 | 18 a callee changed (U3), 8 undefined (U1), 5 removed (U4); overlapping; B-35's only flag (`VARCHAR`) is a false positive, so 26 if it is excluded |
| beyond callers | 42 | |
| cascading (callers only, > 2 hops) | 1 | B-61 (sqlglot, hop 3) |

**What the developer changed, in total:** 588 gold functions.

| Relation to the anchor | Gold functions |
|---|---|
| callers | 184 |
| other functions in the anchor's file | 172 |
| unrelated by the call graph | 162 |
| new | 46 |
| callees of the anchor | 23 |
| the anchor itself | 1 |

**Bounded + clean and G1 together:**
- of the 10 bounded + clean tasks, 5 show all visible tests passing at the start (B-22, B-23, B-29, B-40, B-66), and
  1 has no visible tests (B-44);
- **only 4 of 80 (B-13, B-19, B-36, B-62) are bounded, clean, and have a visible failing test.**
- **The task the baseline solved,** B-14, is "beyond callers": it also changes a sibling helper in the same file.

## 5. The other R4 families

| Family | Downstream | Upstream | Does the analysis hold? |
|---|---|---|---|
| Refactor | a renamed function's callers must update: the break check covers renames ("calls X, now named Y") | an extracted helper is a new callee: U1 fires if the anchor is shown in its new form | yes, given the developer's diff. For the agent, the refactor goal replaces the anchor diff, so the check runs after the agent's edits (S5) |
| Move | import sites, not call hops (roadmap B3); the call graph's hop map does not change | the moved function's callees stay the same, unless they are private to the old module (a U1-like "now unreachable from here") | partly: needs import sites (B3) |
| Delete | every caller breaks (hop 1); a cascade if callers must also be removed | the deleted function's callees may become dead (U5) | yes. U5 is the useful check here |
| Feature | no callers yet | the new code's calls must exist and bind (U1, U2) | upstream only |
| Tests | not applicable | not applicable | no |

## 6. Output format: an in-context block, plus the same computation as a gate filter

**Recommendation:** not a separate pre-flight verdict tool. Use the two parts below.

**What each part does:**
1. **For the agent:** add the facts to `change_context`'s closing block (B6, the relational summary).
   - Callers by hop, and the hop-1 callers the break check finds broken.
   - Upstream: undefined calls (U1), certain arity mismatches (U2), callees left unused (U5).
   - After edits, `prism_check_changes` (B5) re-runs the same checks on every changed function. That is how a cascade
     is found.
2. **For task selection:** run the same analysis against the developer's commit (U3, U4, the real hop depth) as a gate
   filter. Use it in R4's Family B gate and in the sanity check's Layer 1.

**Why not a separate tool:**
- R3a's model calls few distinct tools and loops on them. A new tool is one more choice and one more call.
- A "Proceed: no" verdict before the edit would often be wrong:
  - the break check misses 45% of the developer's direct callers;
  - hop 2+ cannot be predicted;
  - U3 and U4 are invisible at the start.
- The in-context facts carry certainty labels, which spec Q1/Q2 require.

## 7. The "retry" signal

- **Certain upstream facts become an instruction with a next action, not a verdict:**
  - "`limitRequestBody` is called but defined nowhere, and not imported: the change is incomplete. Define or import it
    before updating callers."
  - Under spec Q1, this is the only signal certain enough to state as a must.
- **Downstream:** state the hop-1 breaks, and the hop map as an upper bound.
  - "These 3 callers break. If you change their signatures, their own callers (hop 2: 5 functions in 2 files) need
    checking. Run `prism_check_changes` after each edit."
  - No "abandon": a real task belongs to the user, and the cascade is usually shallow (hop ≤ 2 in 72 of 80).
- **"Not a bounded task" is a benchmark-design decision, for the gate:** exclude upstream-fail and beyond-callers
  commits from Family B. It is not PRISM output to the agent.
