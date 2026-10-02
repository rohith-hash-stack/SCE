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
  config.py            every constant: model, 13,000/16,384 budgets, seeds 42-44, T=0.4, fidelity grades
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

## Run the Kaggle notebook

Upload `kaggle/m1_smoke.ipynb` with Accelerator **GPU T4** and Internet **ON**.
Optionally add a `GITHUB_TOKEN` secret with push access, which pushes results to
`reports/harness_m1/kaggle_smoke/` on this branch. Then Run All.

The notebook:
- clones the branch and installs the harness;
- starts Ollama on GPU 0 with `qwen2.5-coder:14b-instruct-q4_K_M`, a 16,384
  context, flash attention and a q8_0 KV cache;
- runs `harness.smoke_test_cpu` (the real jina embedder and the bge ONNX
  reranker, on CPU);
- runs `harness.kaggle_m1`.

`harness.kaggle_m1` runs:
- **tokenizer parity**: the HF tokenizer against the serving model's own
  prompt counts. A divergence above 2% is logged, and later runs must count
  with the server-side tokenizer;
- **Gate A**: 5 synthetic tasks (T1–T5) × Arms 0/1/5/Oracle, run once;
- **Gate B**: 5 FastAPI T2 tasks × the same arms.

Expected output: one PASS/FAIL line per (gate, arm, task_type), 24 rows. It
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
| T2 | 1 if Acc@5 (all gold in the top-5 items) **and** the answer names all gold, else 0 | `acc_at_5`, `answer_names_gold`, `answer_gold_recall` |
| T3, T4 | stub (NaN) until a sandboxed test runner exists | `stub: true` |
| T5 | **fractional** recall of the gold affected set named in the answer (8 of 10 gives 0.8) | `recall_at_5` (gold covered by the top-5 items), `false_negative_rate` = 1 − tsr |

**Hallucination**: answer identifiers of at least 4 characters that are not
in G*_universe and do not resolve in the repository. Resolution tries the
symbol cache, then a file path, then ripgrep. The candidates are
G*_universe for every arm; the arm's own retrieved set is never used.

**Bootstrap**: repositories are fixed and tasks are resampled within each
repository; seeds are averaged per task.
- Paired: one resampling plan serves every arm.
- Runs within a task type only.
- Holm–Bonferroni across all arm pairs.

**Latency**: `perf_counter_ns`; warm p50/p95/p99, with the first (cold)
sample kept separately. Layers are L_index, L_retrieve, L_generate and L_e2e,
plus per-arm sub-components.

## Deviations from the M1 specification (and why)

1. **`tsr` field added to `ScoreResult`** (and registered). The spec's
   ScoreResult held only the binary `task_success`, which leaves nowhere to
   put T5's fractional recall, the primary T5 metric.
2. **T5 `recall_at_5`** is read as retrieval recall of the gold affected set
   within the top-5 delivered items. The fractional *answer* recall is `tsr`.
   The spec's "recall@5 on gold_affected" was ambiguous between the two.
3. **T2 `tsr` requires Acc@5, as specified.** As a consequence, Arm 0 (no
   items) can never succeed on T2, so its T2 `tsr` is 0 by construction and
   Retrieval Lift against it is NaN (division by 0). `answer_names_gold` and
   `answer_gold_recall` are reported alongside, so an answer-only view
   exists. **This needs an owner decision before M4.**
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
