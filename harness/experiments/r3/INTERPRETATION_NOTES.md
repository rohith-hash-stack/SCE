# R3a: statements the interpretation must make (recorded before any main-run call)

Collected from the decisions logged in `IMPLEMENTATION_LOG.md`. These are fixed before results exist. The results
section states each of them, whatever the results are.

1. **Representativeness (R3-8):** the four repositories added under R3-8 (svelte, rollup, prettier, pydantic) were
   chosen partly on their share of deep tasks, so R3a's deep tasks are not representative of real code. Family B
   is 78% one-caller (62 of 80), close to the real change share (77% of the main window's mined candidates).
2. **Scaling: not run — insufficient depth variance after pool expansion.** There are 18 deep tasks; the scaling
   test needed 30, and the recorded rules allowed at most 28.
3. **Family N is dropped to R3b.** R3a is Family B only.
4. **The interface limitation (pilot, 2026-11-08; `PILOT_LOOP_INVESTIGATION.md`, `PILOT_INTERFACE_INVESTIGATION.md`):**
   - PRISM's context tools were built for single-shot use. In R1/R2 their output answered an explicit "which other
     functions must change?" question, and the answer was the edit. In R3's multi-turn loop the same output
     arrives as an unframed tool result.
   - The tools take a fixed symbol argument, so asking again returns an identical answer. Baseline's tools take
     free arguments and return new content on each call.
   - On hard tasks this produces structural repeats. In the pilot, 3 of 10 PRISM runs repeated identical calls
     until the token budget; no baseline run did. All 3 were on tasks both arms failed, and all 3 were
     missing-function tasks.
   - R3a reports, per arm, the looping runs and the early-done runs. For each looping PRISM run it reports whether
     baseline resolved the same task, plus the missing-function split (13 of 80 tasks).
   - If PRISM's loops concentrate on tasks baseline resolved, the loop suppresses PRISM's score, and R3a-fix
     (descriptive caller-mismatch and unresolved-call block, plus D1/D2) is the justified follow-up round. If they
     concentrate on tasks both arms failed, the loop is how PRISM's failure looks, not why it failed.
   - **R4 plans to address** the interface: action-ready output from PRISM's tools in multi-turn use, and a
     repeated-call signal applied to both arms.
5. **Known PRISM defect, not fixed for R3a (D1):** a bare call to a name not in scope can resolve, by name, to a
   method of an unrelated class (pilot B-09: `set_exception`), and that method's side effects are then reported as
   the anchor's. It was seen once in the pilot, not in the R3a probe. It is part of the treatment as measured.
6. **An invalid interim (amendment R3-10):** "An invalid interim was computed during session 1 due to a readiness
   bug. Its direction (PRISM ≤ baseline) is not a result. If the valid interim after session 2 shows the same
   direction, it warrants investigation — potentially the loop suppressing PRISM's score on the sample. The invalid
   computation itself tells us nothing about the true result."
