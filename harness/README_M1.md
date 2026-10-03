# Eight-arm SLM harness — Milestone 1

M1 delivers the shared foundation (config, interfaces, canonical model,
tokenizer, unified scorer, metric registry, bootstrap, reporting) and four
engines: **Arm 0** (no retrieval), **Arm 1** (Advanced RAG, built from
scratch), **Arm 5** (PRISM, wrapped) and the **Oracle**. Arms 2 and 3 arrive
in M2 and Arm 4 in M3; for now they are declared stubs that raise
`NotImplementedError`.

## Layout

```
harness/
  config.py            every constant: model, 13,000/18,432 budgets, seeds 42-44, T=0.4, fidelity grades
  tokenizer.py         HF Qwen2.5 tokenizer (Hub id, local dir or GGUF), llama-server /tokenize,
                       Ollama prompt counter, verify_tokenizer_parity()
  llm.py               OpenAI-compatible chat client (Ollama /v1 or llama-server); same call for every arm
  pipeline.py          one cell: retrieve -> finalize_context -> prompt -> generate -> adapter -> score
  ast_splitter.py      tree-sitter Python splitter (Arm 1 now, Arm 2 in M2)
  kaggle_m1.py         the M1 GPU gate (Gate A type coverage + Gate B 5 real FastAPI T2 tasks)
  smoke_test_cpu.py    CPU smoke test, PASS/FAIL/BLOCKED table
  arms/                base.py (RetrievalArm, prompt contract), arm0..arm5, oracle, registry
  tasks/               schema.py (T1..T5 discriminated union), loaders.py (legacy YAML -> schema),
                       synthetic.py (one task per type)
  scoring/             canonical, registry, scorer, adapters, bootstrap, latency, hallucination,
                       fairness, noise (gated off), agent_diagnostics
  reporting/           output_schema (Parquet), summary (lift/efficiency, CIs), hypotheses
kaggle/m1_smoke.ipynb  the Kaggle notebook (GPU part of M1)
```

## Install and run the CPU tests

```bash
pip install -e .
pip install rank-bm25 sentence-transformers onnxruntime onnx onnxscript pandas pyarrow statsmodels
# ripgrep (rg) must be on PATH: hallucination detection uses it

# Tokenizer: the Hub id is used by default. Offline, point at a local copy
# (a tokenizer directory, or a GGUF file such as llama.cpp's ggml-vocab-qwen2.gguf):
export HARNESS_TOKENIZER_PATH=/path/to/qwen2-tokenizer

python -m pytest tests/test_harness_foundation.py tests/test_scorer_dispatch.py tests/test_bootstrap.py \
  tests/test_arm1_chunking.py tests/test_latency.py tests/test_hallucination.py tests/test_ranking_metrics.py \
  tests/test_fairness.py tests/test_noise.py tests/test_agent_diagnostics.py tests/test_output_schema.py \
  tests/test_summary.py tests/test_hypotheses.py tests/test_adapters.py tests/test_arm1_rag.py \
  tests/test_arms_m1.py tests/test_pipeline_m1.py -p no:warnings

python -m harness.smoke_test_cpu            # exits non-zero on FAIL or BLOCKED
```

`BLOCKED` means a check could not run in the environment (for example, model
weights unreachable). It is not a pass. `--allow-blocked` changes only the
exit code.

## Model and token envelope (decided after the d087d1b Kaggle run)

- **Model: `qwen2.5-coder:14b-instruct-q8_0`**, not the originally specified
  q4_K_M. q8_0 is what that run actually served, it fits on Kaggle's 2 × T4
  (about 10.9 GB peak per GPU observed, split across both GPUs), and it is
  cached on Kaggle.
- **Generation cap: 4,096 tokens** (was 2,500). Two cells hit the old cap in
  repetition loops.
- **Context window: 18,432 tokens.** It grew instead of the retrieval budget
  shrinking, so every arm keeps its 13,000 retrieval tokens. The envelope is
  system 500 + task 300 + retrieval 13,000 + generation 4,096 + margin 536.
- **The window is requested on every call.** With `--ollama-url`,
  `harness.kaggle_m1` calls Ollama's native `/api/chat` with
  `num_ctx = 18,432` and `num_predict = 4,096`. Ollama's OpenAI-compatible
  `/v1` endpoint ignores per-request options, so the window would otherwise
  depend on how the server was started.
- **Truncation guard:** if the server counts clearly fewer prompt tokens than
  the harness sent (below 95%), the cell is flagged
  `server_prompt_shortfall` and fails the gate rather than being scored.

## Run on Kaggle

The Kaggle script maintained outside the repository (pilot-4 pattern, 2 × T4)
is what runs M1. It installs onnxruntime/onnx/onnxscript, runs
`python -m harness.smoke_test_cpu --json /kaggle/working/m1_smoke/smoke_cpu.json`,
then `python -m harness.kaggle_m1 --out /kaggle/working/m1_smoke --model ...
--llm-url ... --ollama-url ...`, and pushes the results to
`reports/harness_m1/kaggle_smoke/`. `kaggle/m1_smoke.ipynb` predates that
script (single GPU, q4_K_M) and is superseded by it.

`harness.kaggle_m1` runs:
- **tokenizer parity**: the HF tokenizer against the serving model's own
  prompt counts. A divergence above 2% is logged, and later runs must count
  with the server-side tokenizer;
- **Gate A**: 5 synthetic tasks (T1–T5) × Arms 0/1/5/Oracle, run once;
- **Gate B**: 5 FastAPI T2 tasks × the same arms.

After indexing, right before Gate A, it sends a warm-up request and then takes
the first GPU reading, so the model is resident when memory is measured. It takes another reading
before each gate and a final one before exit. Each arm indexes on its own: an
arm that fails to index is recorded once in `index_failures`, its cells fail
with that cause, and the other arms still run. Arm 1 loads its reranker
first in `index()`, so a missing package fails there in seconds.

Every FAIL row carries a `failure` record:
`{type, message, traceback_tail (last 5 lines), step, cmd}`.
`step` is one of index, retrieve, budget, prompt, generate, adapt, score,
validate. `gate_report.json` also holds:
- the PASS/FAIL table (`table`) and `totals`;
- `exit_code`;
- the GPU readings (`gpu_mb_after_warmup`, `gpu_mb_per_gate`, `gpu_mb_final`);
- `index_failures`;
- `wall_seconds` (the whole harness run);
- `fatal`, if the run itself crashed (exit code 2).

`summary.parquet` holds one row per (arm, task_type, corpus): means with CIs,
`retrieval_lift` (N/A on strict T2), `retrieval_lift_any_gold` and
`retrieval_lift_note`.

**Reranker threads:** ONNX Runtime uses the CPUs this process may actually use,
i.e. the affinity set capped by any cgroup CPU quota, not `os.cpu_count()`
(which inside a container reports the host). The smoke test gates a warm
(second) 50-pair call at 3 s and reports the cold first call and the thread
count beside it.

**Arm 1 T2 diagnostic** (one-off, off by default; enable with
`HARNESS_ARM1_T2_DIAGNOSTIC=1`): writes `arm1_t2_diagnostic.json` with the
gold and Arm 1's top-5 chunks per T2 task, plus a verdict that separates a
symbol-mapping bug from a retrieval failure. The same analysis runs
offline on pushed bundles:
`python -m harness.scoring.arm1_t2_diagnostic --bundles <dir>`. On the
1fd53af bundles the verdict is a retrieval failure: every delivered gold
definition is in `symbols` (3 of 3), and the top 5 mention 1 of the 20 gold
names.

Expected output: one PASS/FAIL line per (gate, arm, task_type), 24 table cells over 40 rows. It
also prints the within-type bootstrap check and the tokenizer parity status,
and writes these files to `/kaggle/working/m1_smoke/`:
- `gate_report.json`
- `smoke_cpu.json`
- `cells.parquet`
- `bundles/*.json` (the exact delivered context and prompt)
- `completions/*.json` (raw model output)

The run makes about 52 model requests: 40 answer calls, 8 PRISM Turn-1 calls
and 4 parity probes. Expected time is about 25 minutes of GPU inference, plus
about 10–20 minutes of setup and CPU indexing.

## Definitions that matter for reading results

**Delivered**: only what reached the prompt.
- Every item is re-counted with the harness tokenizer (engine-reported counts,
  e.g. PRISM's cl100k, stay in provenance only).
- Above 13,000 tokens, items are dropped from the lowest rank and
  `over_budget=True` is logged.
- Each item's header line is part of its content, so it counts against the
  budget.

**Item symbols**: the definition(s) an item belongs to. This applies to
every arm:
- a RAG chunk carries its function, method or class;
- a PRISM node carries its id;
- an Oracle item carries its gold symbol;
- module-level blocks carry none.

**Gold set** for ranking metrics: `pipeline_symbols`, i.e. the T2 pipeline
or the T5 affected set.

**Relevance at rank r**: an item counts as relevant only if it adds gold
coverage not already provided by a higher-ranked item. This keeps P, nDCG
and MAP within [0, 1] when an engine repeats a symbol.

**Empty delivered set** (Arm 0): cleanliness, context precision and relevant
token density are NaN (shown as N/A), never 1.0. Recall-type metrics are 0.

**Answer matching**: an answer identifier names gold symbol `g` when it
equals `g` or is a dotted suffix of it (`get_dependant`,
`utils.get_dependant`). This is the legacy harness's bare-name rule.

**`tsr`** is the per-type success score. `task_success = tsr >= 0.5` is a
secondary, binarised view.

| Type | `tsr` | Type-specific metrics |
|---|---|---|
| T1 | judge-scored: min(faithfulness, answer relevancy). NaN without a judge; the judge must not be a Qwen model. | `faithfulness`, `answer_relevancy` |
| T2 | **answer-based**, same rule for every arm: 1 if the answer names all gold symbols (`config.T2_ANSWER_RULE = "all"`; `"any"` is available), else 0. Arm 0's T2 rate is the parametric floor. | `acc_at_5_retrieval` (all gold in the top-5 items; retrieval diagnostic, not part of success), `answer_names_gold`, `answer_names_any_gold`, `answer_names_inherited_gold` (every gold named, also accepting a subclass's inherited member: `RequestValidationError.errors` for gold `ValidationException.errors`), `answer_gold_recall` |
| T3, T4 | stub (NaN) until a sandboxed test runner exists | `stub: true` |
| T5 | **primary**: fractional recall of the gold affected set named in the answer (8 of 10 gives 0.8) | `recall_at_5` (gold affected in the top-5 delivered items; retrieval diagnostic, not part of success), `false_negative_rate` = 1 − tsr |

**Hallucination**: answer identifiers of at least 4 characters that are not
in G*_universe and do not resolve in the repository. The candidates are
G*_universe for every arm; the arm's own retrieved set is never used.
Resolution:
- a file path resolves if the file exists;
- a dotted name resolves when any of these holds:
  - it, or its own dotted suffix, is a known symbol: `utils.get_dependant`;
  - its longest module prefix is a repository module that binds the last
    name at top level, by import, re-export, def, class or assignment
    (including inside top-level if/try blocks). So
    `fastapi.dependencies.utils.get_path_param_names` resolves (imported
    from `fastapi.utils`), and `fastapi.responses.JSONResponse` resolves
    (re-exported from starlette);
  - it is `Class.member` and the member is defined on an ancestor class:
    `RequestValidationError.errors` resolves via `ValidationException.errors`.
- otherwise the dotted name is a hallucination. Its last word alone never
  rescues it: `solve_dependencies.values` (a function's local) and
  `exception_handlers.exc.errors` stay hallucinations;
- a plain name resolves through the symbol table, then ripgrep.

On the d087d1b/1fd53af data, the Oracle's rate fell from 0.571 to 0 (t02_001)
and from 0.333 to 0 (t02_005) under these rules; no cell's rate rose.

External-library names that the repository does not bind, such as
`typing.get_type_hints` or `pydantic.fields.ModelField...`, cannot be
checked from the repository and count as unresolved unless they are in
G*_universe.

**Generation diagnostics** (per cell, never bootstrapped):
- `finish_reason`: the server's value; "length" means the 4,096-token cap was
  hit;
- `generation_capped`: true when `finish_reason` is "length";
- `repetition_count`: symbol mentions minus distinct symbols. It is counted
  on the JSON `symbols` list, or on the quoted dotted names of an answer cut
  off mid-list. A 219-repeat loop of `APIRoute.__init__` is what this catches.

Sampling is identical across arms, so repetition is model behavior and is
not penalised.

**Retrieval Lift on T2**: Arm 0's strict T2 `tsr` is 0 (it has no context
and must name every gold symbol), so the primary `retrieval_lift` is N/A
(NaN). The summary table explains this in `retrieval_lift_note` and adds
`retrieval_lift_any_gold`, which is computed on `answer_names_any_gold`.
Read it with caution: in every real T2 task the seed symbol is gold and is
named in the prompt, so any-gold is 1.0 for every arm in all 15 real-task
cells of the d087d1b run, and this secondary lift is likely 0.

**Bootstrap**: repositories are fixed and tasks are resampled within each
repository; seeds are averaged per task.
- Paired: one resampling plan serves every arm.
- Runs within a task type only.
- Holm–Bonferroni across all arm pairs.

**Latency**: `perf_counter_ns`; warm p50/p95/p99, with the first (cold)
sample kept separately. Layers are L_index, L_retrieve, L_generate and L_e2e,
plus per-arm sub-components.

## M1 finding: docs_src/tests pollution in Arm 1

A documented result, not a fix. From the Kaggle run at 44e7c83 (results
commit 1fd53af), Arm 1 on the 5 real FastAPI T2 tasks:

- **57 of 93 delivered chunks came from `docs_src` or `tests`.** Only 36
  came from the `fastapi/` library itself.
- **The top 5 chunks mention only 1 of the 20 gold symbols** across the 5
  tasks, and define none of them. Arm 1's T2 `tsr` was 0 on all 5.
- **On t02_001, ranks 1–3 are `docs_src/dependencies/tutorial*.py:1-3`.**
  These are 1–3-line tutorial import snippets that the cross-encoder
  scores highest.
- **The cause is the corpus.** The pinned FastAPI checkout has 681
  `docs_src` and 382 `tests` Python files against 44 library files. Of
  Arm 1's 5,398 index chunks, only 346 (6.4%) are library code; 2,401 are
  `docs_src` and 2,461 are `tests`.
- **It is not a symbol-mapping bug.** Every delivered chunk that defines a
  gold symbol carries it in `symbols` (3 of 3). This was checked by
  `harness/scoring/arm1_t2_diagnostic.py` on the pushed bundles.

Every arm indexes the same whole checkout, so this is how plain RAG behaves
on this repository. `config.ARM1_EXCLUDE_DIRS` (for example
`["docs_src", "tests"]`) leaves those top-level directories out of Arm 1's
index. It exists as an **M4 ablation lever and is off by default (empty)**:
with it empty, Arm 1 indexes exactly the same 5,398 chunks as before. M4
will ablation-test both settings.

## Deviations from the M1 specification (and why)

1. **`tsr` field added to `ScoreResult`** (and registered). The spec's
   ScoreResult held only the binary `task_success`, which leaves nowhere to
   put T5's fractional recall, the primary T5 metric.
2. **T5 (official interpretation, confirmed by the owner):** `tsr`, the
   fractional recall of the gold affected set in the answer, is the primary
   metric. `recall_at_5` (gold affected in the top-5 delivered items) is a
   retrieval diagnostic, not part of success.
3. **T2 success is answer-based (owner decision after M1 review).** Acc@5
   moved out of `tsr` into the diagnostic `acc_at_5_retrieval`, so Arm 0 can
   score on T2 and Retrieval Lift is defined. The success rule needs ALL
   gold symbols named (`T2_ANSWER_RULE = "all"`), not any one: in all 90
   real T2 tasks the seed symbol is gold and is named in the prompt, so
   "any" is met by echoing the question. "any" is reported as
   `answer_names_any_gold`, and one config line switches the rule.
4. **Arm 0's prompt** is the query plus the same response contract every arm
   gets. Answers must be extracted identically for every arm.
5. **Extra files**: `tasks/loaders.py` and `tasks/synthetic.py` (task
   ingestion), `llm.py` and `pipeline.py` (the cell runner the notebook needs),
   `kaggle_m1.py` (the notebook's logic as a testable module), and four extra
   test files.
6. **Arm 1 indexes Python only in M1.** TypeScript chunking is needed before
   Arm 1 runs on Express and tRPC.
7. **Splitter compound statements** are if/for/while/try/with/match (sync and
   async), not only if/for/try/with: `while` and `match` are first-level
   compound statements too. A unit larger than 800 tokens is emitted whole
   and flagged `oversized`. Across FastAPI and Django that is 327 of 40,265
   chunks.
8. **Phase 3 server**: the notebook uses Ollama, the existing Kaggle pattern
   the spec allows. Ollama has no `/tokenize`, so parity is checked against
   Ollama's own prompt counts (`prompt_eval_count`) on distinct samples.
9. **PRISM rendering**: hydrated nodes are delivered in the harness's uniform
   item format, not PRISM's `<prism_context>` XML envelope, so presentation
   is not a difference between arms. This is declared in Arm 5's
   `simplifications`.
10. **Hypotheses H4–H8** are proposals marked `status: "proposed"`. They need
    sign-off before any data exists.

## What was verified where

- **This container (CPU, no model weights):**
  - every unit test;
  - the splitter over all of FastAPI and Django (0 SyntaxErrors);
  - the ONNX export and ONNX Runtime inference path, with a tiny
    locally-built cross-encoder;
  - the PRISM wrapper on real FastAPI tasks;
  - end-to-end cells with a scripted model;
  - the full Kaggle runner in dry-run mode.
- **Kaggle only:**
  - the real jina embedder and the real bge reranker (Hugging Face is
    unreachable from the dev container);
  - every real-model answer;
  - tokenizer parity against the serving model.
