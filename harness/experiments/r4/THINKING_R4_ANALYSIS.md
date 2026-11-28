# Should R4 use a thinking model alongside the gate? (2026-11-12)

**This is an investigation and suggestions only.** No code, protocol change or amendment. It is written while sanity
3 runs, so its decision tree is conditional on sanity 3.

**Sources:**
- the planning investigation's 12 runs: `reports/r4/planning/`, turn logs with thinking text (`8f936f2e`);
- sanity run 2: `reports/r4/sanity2/`, for per-run time with the gate.

**Evidence strength:** 3 tasks, 1 seed, one arm (PRISM). Every number below from those runs is an observation on 3
tasks, not a rate. **Known** marks what the logs show; **predicted** marks extrapolation.

## 1. Architecture: thinking + gate

**Known: they solve different problems, with almost no overlap.**

| | Thinking off (Qwen3-14B) | Thinking on (Qwen3-14B) |
|---|---|---|
| Edits applied | 5/24 (21%) | 14/18 (78%) |
| Done claimed with the task unfinished (no gate in these runs) | 2/3 | 3/3 |

**The false dones were made with the failure in view.**
- **Pydantic:** the last `run_tests` before done reported "0 passed, 1 failed". It had been run on a source file, not
  a test.
- **Typer:** "9 passed, 1 failed".
- **The thinking text at the done turn knows it is unfinished.** Typer: "the test is still failing … However, given
  the information available, the correct approach is …", then done.
- **So reasoning does not catch incompleteness: it recognises it and stops anyway.** That is the gate's problem (the
  done escape), and thinking does not shrink it.

**Thinking does include planning.** Every turn's thinking in typer, and 13 of 16 in pydantic, lays out steps ("First,
I'll check … Let me start by …"). So the user's point holds: a thinking model plans without being told to.

**Interactions:**

| Possible bad interaction | Assessment |
|---|---|
| Thinking eats the budget, leaving no room for the gate's "continue" | **Not a problem.** The loop does not keep thinking in the history, so it adds only the answer tokens of each turn: about 0.7–1.2k against a ~10–16k prompt. The thinking runs used 98k–154k of the 300k budget by turns 16–19 |
| A long think truncates the JSON answer | **Low risk.** The largest observed think was 3,317 tokens (Qwen3), against the 12,288-token answer limit |
| The model call times out | **Low for Qwen3** (longest call 278 s against 900 s). **Real for QwQ** (871 s, and one run ended on a timeout) |
| The gate makes runs longer, and thinking makes each turn longer | **Real, and multiplicative: this is the main cost.** With the gate, the median run lasts 24 turns (sanity 2), at 0.9–1.7 min per thinking turn (§5) |
| Thinking is dropped between turns, so the model forgets its own reasoning | **Known and real.** The typer done turn says "the user has already tried that" about its own earlier edits. This argues for carrying a short plan across turns (§2) |

**Verdict: "both in R4" is sound.**
- **Thinking addresses execution:** finding the target, and edits that apply.
- **The gate addresses honesty:** it refuses a done the evidence contradicts.
- **The evidence says they are orthogonal, not redundant.**
- **The caveat is not technical:** adding thinking changes the model, which matters for comparability (§8).
  **Whether thinking is needed at all depends on sanity 3:** if the tool fixes already raise applied edits, thinking's
  main measured benefit is delivered without it.

## 2. A test of planning + thinking (designed, not run)

**The design:**
- **Variant:** Qwen3-14B with thinking on **and** the plan field. The plan is the JSON field written after the thinking
  block in the same reply; it stays in the history, while the thinking does not.
- **Tasks and compared against:** the same 3 tasks (instructor delete, pydantic delete, typer feature), PRISM arm,
  seed 42, and the same task versions (sanity 1's, no gate) as the existing thinking-on runs, so the only difference
  is the plan.
- **Cost:** 3 runs, about 1–1.5 hours on Kaggle (the existing thinking runs took 1.8, 18 and 34 minutes, plus the
  model pull and setup).
- **Code:** one new entry in `planning_run.VARIANTS` (`--think on --plan`). The cell can then run it.
- **Measured:** as before (edits applied, false done, verified before done, ending), plus two new measures:
  - **plan–thinking agreement:** does the plan state what the thinking concluded?
  - **cross-turn anchoring:** does a later turn's thinking re-justify the previous plan in the face of contrary
    evidence?
- **Optional second half (3 more runs):** the same comparison on sanity 3's gated tasks. This is only worth it if R4
  might use thinking.

**Predictions** (none of this is measured):

| Measure | Thinking on (measured) | + plan (predicted) | Why |
|---|---|---|---|
| Edits applied | 78% | **about the same (70–85%)** | the plan is written after the thinking, so it cannot steer that turn's reasoning |
| False done (no gate) | 3/3 | **unchanged, 2–3 of 3** | honesty is not a planning problem: the model knew it was unfinished |
| Remembering its own earlier work | lost ("the user has already tried that") | **better** | the plan in the history is the only memory of intent the model gets |
| Repeating a failed approach | present | **slightly worse or the same** | a stale plan in the history can anchor later turns |

**Overall prediction:**
- **Neutral on the measures that matter (solves, false done).**
- **A small plus for continuity, and a small risk of anchoring.**
- **Planning neither spoils thinking much nor rescues honesty.**

## 3. Does planning spoil thinking? Three proposed mechanisms

| Mechanism | Plausibility | Reasoning |
|---|---|---|
| **Premature commitment** (the "I will do X" text anchors the model) | **Low within a turn, moderate across turns** | Within a turn, the plan is generated after the thinking ends, so it cannot anchor that reasoning. Across turns, the plan stays in the history while the thinking is dropped, so a stale plan can anchor the next turn's thinking. Qwen 14B + planning showed exactly this: "Since there were no matches … it appears the method does not exist" carried to done. That was a non-thinking model reasoning from a false premise (R1), though |
| **Compression loss** (one sentence replaces 1,000–5,000 tokens) | **Low as a harm** | The model re-thinks every turn, and the thinking is discarded from the history anyway. The plan reduces the loss rather than causing it: it is the one piece of intent that survives |
| **Double reasoning overhead** | **Negligible** | A plan is ~30 tokens against ~700–2,200 of thinking, under 5% of the answer. Its value as a checkpoint is unknown |

**Other mechanisms:**
- **Rationalisation (plausible):** the plan field invites the model to assert success ("I will finish because tests
  pass"), adding false confidence before done. The false done happens anyway, so this would not change the count, only
  make it look surer.
- **Format pressure (low):** an extra JSON field could raise parse errors. Every smoke and run parsed; no evidence.

**Most plausible:** cross-turn anchoring, a variant of premature commitment, because the history keeps plans and drops
thinking. Expected size: small.

## 4. R4's model, conditional on sanity 3

| Sanity 3 outcome | Recommendation | Reasoning | Comparability with R3a |
|---|---|---|---|
| **Edits ≥70%, solves rise clearly** (baseline ≥ about 4/20) | **The current model for R4.** No thinking model | the tool fixes delivered what thinking would have; R4 stays cheap (§5) and on R3a's model | **partial:** the same model and PRISM, but different families, gate and tools (§8) |
| **Edits 55–65%, solves marginal** (baseline 2–3/20) | **The current model for R4, plus a 40-run thinking sanity first** (the same 20 tasks, both arms; ~13–28 GPU-hours, 2–3 sessions). Switch to thinking (or add R4b) only if that sanity clearly lifts the baseline (≥ +3 tasks) | this is the expensive-ambiguous zone. A marginal baseline means R4 risks R3a's floor (2/160 solved), where no PRISM effect is detectable. The test is whether thinking lifts the baseline off the floor, measured cheaply before committing 100+ GPU-hours | the current model: partial. A thinking model: none at the level of numbers (§8) |
| **Edits <50%, solves flat** | **Do not run R4 on the current model.** Run the 40-run thinking sanity; if it clears the floor, R4 on Qwen3-14B with thinking. If not, the problem is task difficulty: calibrate the tasks, not the model | the current model fails the pipeline. Thinking is the only lever with evidence (21% → 78% edits on the same weights), but that evidence is 3 tasks. It must be confirmed on the 20 before 100+ GPU-hours | none at the level of numbers |

**When thinking is worth 4–7× the GPU time:** only when it moves the baseline's solve rate off the floor. A PRISM
effect cannot be measured if neither arm solves. Better edits alone are not enough; solves must rise.

**R4 now on the current model and R4b on thinking later, or commit to one?**
- **Prefer committing to one model for R4's pre-registered run**, chosen by sanity 3 and, if needed, the thinking
  sanity.
- **R4b is worth it only as its own question:** "does PRISM's effect depend on the model?". That needs R4's exact
  tasks and protocol on the second model, and its own pre-registration.
- **Not as a rescue:** running a floor-bound R4 first, then R4b, spends the cheap run on a null that was predictable
  from sanity.

## 5. Cost

**Per run:**
- **Current model, measured:** 5.2 minutes (sanity 2's 40 runs, gated, including setup and scoring; 3.4 hours in
  total).
- **Qwen3-14B with thinking, observed:** 0.9–1.7 minutes per turn; the 3 runs took 1.8, 18 and 34 minutes.
  - **The 2-minute end is not realistic for R4:** it was a 2-turn false done, which the gate now rejects.
  - **With the gate, runs last a median of 24 turns** (mean 20; 62% reach 20 or more).
  - **Estimate:** about 1.3 minutes of setup and scoring plus 20–24 turns × 0.9–1.7 minutes, giving a **low of about
    20, a middle of about 30 and a high of about 42 minutes per run**.

**R4's run count** depends on its size (ROADMAP §4a: delete and feature at 40–80 tasks each), the arms (2) and the
seeds (1–2):

| Runs | Current (5.2 min) | Thinking, low (20 min) | Thinking, middle (30 min) | Thinking, high (42 min) |
|---|---|---|---|---|
| 80 (40 tasks × 2 arms × 1 seed) | 7 GPU-h · 1 session · <1 week | 27 h · 3 sessions · 1 week | 40 h · 4 · 1.3 weeks | 56 h · 6 · 2 weeks |
| 160 (80 × 2 × 1, or 40 × 2 × 2) | 14 h · 2 · <1 week | 53 h · 5 · 1.7 weeks | 80 h · 8 · 2.7 weeks | 112 h · 11 · 3.7 weeks |
| 320 (80 × 2 × 2) | 28 h · 3 · 1 week | 107 h · 10 · 3.3 weeks | 160 h · 15 · 5 weeks | 224 h · 21 · 7 weeks |
| 640 (160 × 2 × 2, R4's upper size) | 55 h · 6 · 2 weeks | 213 h · 20 · 6.7 weeks | 320 h · 30 · 10 weeks | 448 h · 42 · 14 weeks |

- **Assumptions:** sessions of ~10.75 usable hours (11.5 minus the stop margin); calendar time at 3 sessions a week.
- **Kaggle's weekly GPU quota** (about 30 hours on a free account, to my knowledge) caps at about 3 sessions a week
  anyway, so the calendar figures hold.
- **A thinking sanity** (40 runs) costs 13–28 GPU-hours, 2–3 sessions.
- **The ratio is 4–8× the current model**, not 5–10×: gated runs are long for both.

## 6. The gate with a thinking model

| Question | Prediction | Basis |
|---|---|---|
| Does "at least one file changed" fire differently? | **Less often.** Thinking runs edit more (78% applied), so rejections will be about failing tests and the target, not "no change" | the planning runs: the thinking runs changed files in 2 of 3 |
| Does the "continue" feedback fit the budget? | **Yes.** Thinking is not kept in the history; the budget hits come from long prompts, as for the current model | known: 98k–154k used by turns 16–19 |
| Do thinking models call done less or more often? | **About as often, and with false confidence:** 3/3 called done, against 2/3 for thinking off. The reasoning sees the failure and concludes anyway | known, from the done-turn thinking |
| `stuck_done` | **Rarer.** A thinking model is likelier to make some edit between rejections, which resets the count. More runs would end at the turn cap or the budget instead | predicted |
| Changes needed in the gate | **None to its logic.** Operationally: the per-call timeout (900 s) is fine for Qwen3, not for QwQ. Session planning must use the longer runs (§5) | — |

**One thinking-specific risk the gate covers:**
- **It runs tests on the wrong file.** The pydantic thinking run ran `run_tests` on a source file and read the result
  as a check.
- **The gate runs the right tests itself,** so this mistake cannot pass as verification.

## 7. What would change each recommendation

| Recommendation | Unless |
|---|---|
| **The current model for R4** | sanity 3's baseline solves stay at the floor (≤ 2/20) even with applied edits ≥ 60%. R4 would then repeat R3a's undetectable-effect problem |
| **A thinking model for R4** | a 40-run thinking sanity shows no clear solve gain (< +3 tasks over the current model on the same 20); or its runs take > 45 minutes (R4 at 320 runs would then exceed ~240 GPU-hours, ~8 weeks); or its "gate accepted, scorer unresolved" count is high (the gate not containing false dones) |
| **R4 on the current model and R4b on thinking** | the current-model R4 would sit at the floor (R4 wasted: go straight to thinking); or the calendar cannot carry both (together > ~8–10 weeks at R4's size); or the question "does PRISM's effect depend on the model?" is not one we want answered now |
| **Do not add the planning prompt** (from the planning investigation) | the §2 test shows plan + thinking clearly beats thinking alone on applied edits or continuity, with no rise in false dones |

## 8. Comparability with R3a

**The comparability R4 already loses, even on the same model:**

| | R3a | R4 |
|---|---|---|
| Model | Qwen2.5-Coder-14B | Qwen2.5-Coder-14B, or a thinking model |
| Task families | Family B (caller updates) | delete and feature (Family B directional) |
| Protocol | no gate; old tool messages | gate; explicit tool messages; new prompts |
| PRISM | R3a's tool layer | the same, plus K4 and the planned fixes |

So R4 on the current model is already only **partly** comparable with R3a. It shares the model and PRISM, but changes
the families and the protocol. Its numbers cannot be set beside R3a's as a replication.

**Switching the model removes the one shared element.**
- **The honest claim** would be: "PRISM's effect was measured on Qwen2.5-Coder-14B in R3a (null on Family B) and on
  Qwen3-14B-thinking in R4 (result X on delete and feature)."
- **That is two separate experiments.** Any difference between them cannot be attributed to the model, the families or
  the protocol, since all three changed. It tells a reader what PRISM did in each setting, not why the settings differ.
- **It can still mean something as a statement of scope:** "PRISM's effect, on these families, with this protocol, on
  this model". R4 is a new experiment with its own pre-registration in either case.
- **A real model comparison needs R4 and R4b** on identical tasks and protocol.

**The honest weight of the argument:**
- **Comparability is not the strongest argument against switching.** Most of it is gone already, through the family
  and protocol changes.
- **The stronger arguments against are:**
  - cost, at 4–8× (§5);
  - weak evidence: 3 tasks, and solves did not rise in any thinking run;
  - confounding inside R4 if the model choice is made after seeing sanity results without pre-registering it.

## 9. Recommendation, conditional on sanity 3

- **If sanity 3 lifts applied edits to ≥ 60% and the baseline solves at least ~4 of 20:** run R4 on the current model.
  It is cheap (about 1 week for 320 runs) and shares the model with R3a. A thinking model is not needed. Keep it as an
  optional R4b with its own question.
- **If edits rise but the baseline stays at 2–3 of 20 or below:** run a 40-run thinking sanity first (2–3 sessions).
  Switch to Qwen3-14B with thinking only if it lifts the baseline by ≥ 3 tasks. That moves R4 at 320 runs from ~28 to
  ~107–224 GPU-hours (3–7 weeks).
- **If edits do not rise:** diagnose before any model decision.
- **Under every branch, the gate stays,** and the planning prompt is not added unless the §2 test shows a clear gain.
- **The strongest argument for thinking:** it is the only lever that raised editing on the same weights (21% → 78%),
  and editing was sanity's main failure.
- **The strongest argument against:** that rise did not produce a single solve in 3 tasks, and it costs 4–8× the GPU
  time on 3-task evidence. It should be confirmed on the 20 sanity tasks before R4 pays for it.
