# M3 closure

M3 implemented the last arm, Arm 4 (Claude-Code-style agent loop), and ran
the first gate with all seven arms. It is the clean FastAPI baseline: the
fence-parse fix (`54b127c`), the Arm 3 FQN fix (`be97008`) and the t02_005
exclusion are all in. M1 and M2 remain as historical, pre-fix runs (see
`docs/m2_closure.md`). Arm 4 closes at 0.00 on Gate B; the sections below
explain why that number is the agent's own and not a harness artefact.

## 1. Runs

| Run | Code | Artifacts | Arms | Rows |
|---|---|---|---|---|
| M3 gate | `a171e07` | `reports/harness_m3/kaggle_smoke/` (`16049ed`) | all 7 | 63/63 executed |
| Arm-4-only re-run | `6955c51` | `reports/harness_m3/arm4_fix/` (`71047ba`) | arm0, arm4, oracle | 27/27 executed |

Both runs: `qwen2.5-coder:14b-instruct-q8_0` on Ollama's native API,
context window 18,432, generation cap 4,096, seed 42, tokenizer parity
0.000%, no index failures, exit code 0. All 13 CPU smoke checks passed in
both, including `arm3_lsp_ready` (editable-install check on) and
`arm4_agent_tools`. Ablation flags were off (`PRISM_MANIFEST_STRICT`,
`PRISM_TURN1_STRICT_PROMPT`). PASS means the cell executed, not that the
answer was right.

## 2. Results

Gate B, real FastAPI T2, n = 4 (t02_005 excluded), TSR under the strict
"all gold" rule. Arm 4 is from the re-run; every other arm from the M3
gate.

| Task | Arm 0 | Arm 1 | Arm 2 | Arm 3 | Arm 4 | Arm 5 | Oracle |
|---|---|---|---|---|---|---|---|
| t02_001 | 0 | 0 | 1 | 0 | 0 | 1 | 1 |
| t02_002 | 0 | 0 | 0 | 1 | 0 | 1 | 1 |
| t02_003 | 0 | 0 | 1 | 1 | 0 | 1 | 1 |
| t02_004 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| **Mean** | **0.00** | **0.00** | **0.50** | **0.75** | **0.00** | **0.75** | **1.00** |
| 95% CI | [0, 0] | [0, 0] | [0, 1] | [0.25, 1] | [0, 0] | [0.25, 1] | [1, 1] |
| answer_gold_recall | 0.35 | 0.40 | 0.61 | 0.95 | 0.30 | 0.95 | 1.00 |

Gate A (synthetic; checks that every task type runs): T2 is 1.0 for every
arm; T5 is 0.0 (Arms 0 and 4), 0.5 (Arms 1 and 3) and 1.0 (Arm 2, Arm 5,
Oracle). T1, T3 and T4 have no scorer yet (NaN by design).

With one seed and four tasks, only the Oracle's interval and Arms 3 and 5's
lower bounds separate from zero; the means are a single-seed baseline, not
a ranking (section 5).

## 3. Arm 5: the fence fix reproduces

Arm 5's prompts and completions in the M3 gate are byte-identical to the
post-fix Arm5-only run (`reports/harness_m2/arm5_only/`) on all 9 cells.
The Turn-1 fence-parse fix moved Arm 5 from 0.50 to 0.75 (n = 4): t02_002
flipped 0 → 1, and the t02_004 repetition loop that hit the generation cap
in M1 and M2 is gone.

## 4. Arm 3: the FQN fix shifts cells, not the mean

The FQN mapping fix relabels some hover items, and the labels appear in
Arm 3's item headers, so its prompts changed. Per cell: t02_001 went
1 → 0 (its shorter M3 answer dropped `get_sub_dependant`, which M2's
longer answer listed) and t02_004 went 0 → 1. The mean is unchanged at
0.75. A two-cell swap on four tasks is a reminder of how sensitive a single
seed is to prompt changes.

## 5. Cross-session drift

Three cross-session drift instances across M3 and the Arm-4 re-run (Oracle
t02_001; Arm 0 t02_002; Oracle t02_004), all with unchanged TSR. This is the
third independent confirmation of single-seed non-determinism and the
primary motivation for multi-seed M4.

- Oracle t02_001 (M3 gate against M2): same prompt, different completion.
- Arm 0 t02_002 (re-run against M3): `finish_reason` `length` → `stop`,
  `hallucination_rate` 0.948 → 0.75. Arm 0 receives no context, so its
  prompt cannot have changed.
- Oracle t02_004 (re-run against M3): `hallucination_rate` 0.0 → 0.294,
  Oracle code unchanged.

The earlier instances are in `docs/m2_closure.md` (2 of 25 same-prompt
cells between M2 and the Arm5-only run, including the Oracle's t02_005
flip).

## 6. Arm 4: the contract bug and its fix (lessons learned)

In the M3 gate Arm 4 scored 0/4 with a forced answer on 3 of 4 tasks. The
model was not failing: its first message ended with the uniform response
contract ("Respond with a single fenced JSON object and nothing else") and
then asked it to call `answer`. When it answered in the contract's format
without a tool call, the loop discarded the turn as "no tool call", nudged
it, and it re-grepped until the cap (on t02_002, the same five greps four
times).

Fix (`6955c51`): the contract moved into the `answer` tool's description,
the task message no longer carries it, a no-tool turn that parses as an
answer is accepted (`answer_source="direct"`), and every turn's raw text is
stored. The re-run confirmed it: no-tool turns on Gate B dropped from 2, 5,
8 and 9 to 1, 0, 0 and 1.

Lessons: an agent's prompt must not carry two termination contracts; and an
agent loop must record each turn's raw output, because the trajectory alone
could only show that something was wrong, not what. The stored raw text is
what made the residual diagnosis below possible without another run.

## 7. Arm 4 at 0.00: residual behaviour and next steps

With the contract fixed, Arm 4 still scores 0/4 (answer_gold_recall 0.4,
0.25, 0.33, 0.2; turns 4, 11, 11, 11; forced on t02_002–004;
`digest_safety_loss` 0 on every cell). None of the residual harness gaps
could have changed a TSR: the t02_004 answer the parser missed named no
symbols, and the t02_001 bare-array answer named 2 of 5 gold. Five failure
modes, with their next step:

| Failure mode | Seen in | Next step |
|---|---|---|
| Final `<tools>` block left unclosed, so the `answer` call was not parsed | t02_004 turn 2 | Fixed in this pass (`4be6ab0`); verify on the next run |
| Direct answer as a bare JSON array (the form the task texts show) | t02_001 turn 2 | Fixed in this pass (`4be6ab0`); verify on the next run |
| The "No tool call found" notice read as a failure, triggering duplicate loops | t02_004 turns 3–10 (32 duplicate calls) | Investigate in M4: may need a different nudge message |
| Guess-and-grep on invented function names after a single short read | t02_003 (14 empty greps) | M4 agent-prompt work |
| Slow exploration, about one small call per turn, hitting the 10-turn cap | t02_002 (11 calls, no answer) | M4 cap tuning (`ARM4_MAX_TURNS`, now configurable; default 10) |

## 8. Task-set: the t02_005 exclusion held

t02_005 was excluded from Gate B in both runs and recorded in each gate
report (`gate_b_excluded`), with the reason given in `docs/m2_closure.md`.
The task file and the scoring rule are unchanged.

## 9. Oracle portability

The Oracle constructs and runs without Arm 5 in `HARNESS_ACTIVE_ARMS`: in
the Arm-4-only re-run it indexed without error and scored 1.00 on Gate B.
The gate runner builds PRISM's engine whenever the Oracle is present (the
Oracle reuses PRISM's symbol table), whether or not Arm 5 itself runs. This
matters for any future single-arm experiment: PRISM's index cost (about
25–30 s on CPU, measured locally) is paid even when only the Oracle needs it.

## 10. Open items for M4 (listed, not proposed)

- Multiple seeds (section 5).
- Arm 4: the duplicate-loop nudge, the agent prompt, and the turn-cap
  sweep (section 7); verify the two parsing fixes on a real run.
- PRISM ablations behind the existing flags (`PRISM_MANIFEST_STRICT`,
  `PRISM_TURN1_STRICT_PROMPT`).
- t02_005 re-design with an accepted-alternatives field.
- Arm 1's `ARM1_EXCLUDE_DIRS` ablation (docs_src/tests pollution).
- PRISM bug, deferred to post-M4: `symbol_table.add()` renames a demoted
  overload stub to `#N` but does not move its def-node entry, so on a fresh
  build the stub has no def-node (the cache rebuild is correct). Details and
  a 10-line reproduction in `docs/prism_known_bugs.md`. The test
  `test_index_cache_consistency::test_cache_hit_rehydrates_every_def_node`
  is `xfail(strict=True)`.
- `test_prism_selection_regressions[t02_017-_urlparse]` is a deliberate,
  accepted exception at budget 2000 (`docs/design_formalism.md`, Sec 10.4).
  M4 runs at 13,000, where the issue does not apply.
