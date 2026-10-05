# M2 closure

M2 added Arm 2 (Cursor-style Priompt packing) and Arm 3 (Copilot-style
Pyright LSP) to the harness, ran them on Kaggle with Arms 0, 1, 5 and the
Oracle, and then fixed two defects that the run exposed. This document
records the results, the fixes, the methodology caveats, and how M3 uses
them. Design and fidelity details live in `harness/README_M2.md`.

## Runs

| Run | Code | Artifacts | Arms |
|---|---|---|---|
| M2 gate | `df42d29` | `reports/harness_m2/kaggle_smoke/` (`b00035d`) | 0, 1, 2, 3, 5, Oracle |
| Arm 5 post-fix baseline | `10b7a9f` | `reports/harness_m2/arm5_only/` (`19f5df3`) | 0, 5, Oracle |

Both runs: `qwen2.5-coder:14b-instruct-q8_0` on Ollama's native API,
context window 18,432, generation cap 4,096, seed 42, tokenizer parity
0.000%, every row executed (PASS means executed, not correct), exit code 0.

## Gate B results (real FastAPI T2, TSR, strict "all gold" rule)

M2 gate:

| Task | Arm 0 | Arm 1 | Arm 2 | Arm 3 | Arm 5 | Oracle |
|---|---|---|---|---|---|---|
| t02_001 | 0 | 0 | 1 | 1 | 1 | 1 |
| t02_002 | 0 | 0 | 0 | 1 | 0 | 1 |
| t02_003 | 0 | 0 | 1 | 1 | 1 | 1 |
| t02_004 | 0 | 0 | 0 | 0 | 0 | 1 |
| t02_005 (excluded, see caveats) | 0 | 0 | 0 | 0 | 0 | 0 |

Means with 95% bootstrap CIs (1,000 replicates, seed 42), with and without
t02_005:

| Run | Arm | n = 5 | n = 4 (t02_005 excluded) |
|---|---|---|---|
| M2 | Arm 0 | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] |
| M2 | Arm 1 | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] |
| M2 | Arm 2 | 0.40 [0.00, 0.80] | 0.50 [0.00, 1.00] |
| M2 | Arm 3 | 0.60 [0.20, 1.00] | 0.75 [0.25, 1.00] |
| M2 | Arm 5 | 0.40 [0.00, 0.80] | 0.50 [0.00, 1.00] |
| M2 | Oracle | 0.80 [0.40, 1.00] | 1.00 [1.00, 1.00] |
| Arm 5 post-fix | Arm 0 | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] |
| Arm 5 post-fix | Arm 5 | 0.60 [0.20, 1.00] | 0.75 [0.25, 1.00] |
| Arm 5 post-fix | Oracle | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] |

Arms 0, 1, 5 and the Oracle reproduced M1 byte-for-byte in the M2 run (40/40
completions identical). Arms 2 and 3 broke M1's "0 on everything" pattern
for the retrieval arms, though only Arm 3's interval excludes zero at n = 5.

## Fixes after the M2 run

**PRISM Turn-1 fence parse (`54b127c`).** The model wraps its Turn-1 JSON in
a ```` ```json ```` fence. The parser ran `json.loads` on the raw text, which
failed on 5 of 5 tasks, so the regex salvage always ran and sorted the
selection alphabetically. The selection itself survived (salvage equals the
model's list intersected with the manifest), but the model's request order,
which sets PRISM's delivery order, was lost. The parser now reads the fenced
block, and the salvage keeps first-mention order.

Result (Arm 5 post-fix baseline against M2; `diff_vs_m2.json`):

| Task | TSR | answer_gold_recall | finish_reason | Notes |
|---|---|---|---|---|
| t02_001 | 1 → 1 | 1.0 → 1.0 | stop → stop | |
| t02_002 | **0 → 1** | 0.25 → 1.0 | stop → stop | gold moved from ranks 7–9 to 4–6; no invented seed members |
| t02_003 | 1 → 1 | 1.0 → 1.0 | stop → stop | |
| t02_004 | 0 → 0 | 0.2 → 0.8 | **length → stop** | repetition loop gone; misses `get_typed_return_annotation` (named under the seed) |
| t02_005 | 0 → 0 | 0.67 → 0.67 | stop → stop | excluded task |

`turn1_parsed_ok` went from False to True on all 5 tasks; every hydrated set
is unchanged, only the order moved. Arm 5's mean went from 0.40 to 0.60
(n = 5), or 0.50 to 0.75 (n = 4). The attribution is strong on t02_002 but
not airtight: one task flip, n = 5, single seed, and the cross-session drift
below.

**Arm 3 FQN mapping (`be97008`).** LSP locations were mapped to outline
symbols by line only, so a one-line `def f(call):` was labelled `f.call`.
Mapping is now by (line, character) containment. Re-scoring the stored M2
Arm 3 cells: `delivered_symbols_resolved` unchanged (mean 8.6),
`delivered_symbols_named_only` 67.0 → 64.8, TSR unchanged. Arm 3's item
headers carry these labels, so its prompt text changes from M3 on.

**Scaffolds, off by default (M3 ablation levers).**
`PRISM_MANIFEST_STRICT` (`79b39b2`) filters PRISM's Turn-1 manifest to
distance ≤ 2 in the harness wrapper; `PRISM_TURN1_STRICT_PROMPT`
(`62269cc`) adds a soft cap of 20 symbols to the Turn-1 system prompt. The
gateway investigation found the manifest 50–85% distractors, but most
hydrated distractors sit within 2 hops: on t02_004 the model kept 25 of 33
manifest distractors, on t02_002 only 4 of 44. LLM-side selection is the
bigger lever.

## Methodology caveats

### Cross-session reproducibility

Same-prompt, same-seed cells produced different completions on 2 of 25
cells in the Arm5-only run compared to M2 (8%). Notably, the Oracle's
t02_005 TSR flipped 0 → 1 across the two runs because it named
`ValidationException.errors` in one and `RequestValidationError.errors` in
the other. This confirms that single-seed, single-run comparisons are not
byte-reproducible and should not be treated as such. The M4 benchmark will
use multiple seeds; M3 reports a single seed with this caveat noted.

(The other drifting cell is the Oracle on t02_001, whose TSR stayed 1. The
M1 → M2 comparison happened to reproduce 40/40 completions; that does not
generalise across Kaggle sessions.)

### Task-set caveats

t02_005 (`request_validation_error_response`) is excluded from Gate B. Its
gold symbol, `fastapi.exceptions.ValidationException.errors`, is defined on
the base class and invoked at runtime through the subclass
(`exc.errors()` on a `RequestValidationError`, which does not override it).
The model treats the name as ambiguous: the Oracle named the subclass form
`RequestValidationError.errors` in one run and the parent in the other, and
Arm 3 named the subclass form in its one run. The subclass form is not a
definition anywhere in the repository, so no static-retrieval engine can
deliver it; relabeling the gold to it would make the gold undeliverable,
and keeping the parent makes the strict answer check depend on which name
the model happens to choose. (The parent's definition itself was delivered:
the Oracle, Arm 3 and Arm 5 all had it in context.) The task is out of
scope for a retrieval benchmark in its current form and moves to the M4
annotation backlog, for re-design with an explicit accepted-alternatives
field.

Mechanics: `config.GATE_B_EXCLUDED_TASKS` lists the task and the reason;
the gate runner drops it from Gate B and records it in `gate_report.json`
(`gate_b_excluded`). The task file is unchanged, and so is scoring: the
strict "all gold" rule applies to every other task. The
`answer_names_inherited_gold` diagnostic still reports both forms.

### Other known caveats

- **Arm 2 signature stubs on decorator-heavy code** (`harness/README_M2.md`):
  on t02_004 the `routing.py` stubs, with `Annotated[..., Doc(...)]` on
  every parameter, consume the budget; Arm 2 delivered 1 of 5 gold.
- **Generation cap:** on t02_002, Arms 0, 1 and 2 hit the 4,096-token cap
  in M2 (Arms 1 and 2 in repetition loops, `repetition_count` 362 and 447).
- **Timing tests:** `test_fuzzy_match_performance_on_real_corpus` and
  `test_scan_performance_on_django_sized_repo` fail intermittently under
  container load; both pass in isolation and are left unchanged.

## M3

The M3 gate is the clean baseline for every arm on FastAPI: it includes the
fence-parse fix, the Arm 3 FQN fix, the t02_005 exclusion (Gate B n = 4)
and Arm 4, with the ablation flags off unless an ablation run sets them.
The paper reports M3 numbers; M1 and M2 remain as historical, pre-fix
baselines.
