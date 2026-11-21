# Authentic external baselines: 5-task FastAPI retrieval smoke

Branch `baselines/authentic-external-engines` (verified with
`git branch --show-current`).

**Scope.** FastAPI `t02_001`-`t02_005`. Zero LLM cost; retrieval only.
Every token count is an exact `cl100k_base` count (`tiktoken:cl100k_base`,
from a tiktoken cache verified against the official SHA-256 hashes; see
"Tokenizer finding" below).

**Metrics.**
- **Coverage:** the share of the task's adjudicated `pipeline_symbols`
  physically present in the context. This is the "sufficiency" the brief
  asked for; in the sweep's records it is `cpi_context`, not the
  scaffold-based `sufficiency_ratio`.
- **Cleanliness:** 1 − FPR against the ground-truth universe.

Source: `scripts/authentic_baselines_smoke.py` →
`authentic_baselines_smoke.json`.

## Engines

| engine | implementation | status here |
|---|---|---|
| `prism_full` | final-sweep cells (qwen2.5-coder:7b, seed 1, Kaggle, `cl100k_base`) | measured (recorded cells) |
| `baseline_aider_official` | `aider.repomap.RepoMap` from **aider-chat 0.86.2** (isolated venv, `$AIDER_PYTHON`), called as Aider does with no chat files. Mentions come from Aider's own `Coder.get_ident_mentions` / `get_file_mentions` / `get_ident_filename_matches`. `map_tokens=4000`, git-tracked files | **measured** |
| `baseline_hybrid_dense_bm25` | AST chunks (functions/methods; class headers); `BM25Okapi(k1=1.5, b=0.75)` + dense `sentence-transformers/all-MiniLM-L6-v2` (or `text-embedding-3-small`); RRF with k=60 over top-100 each; packed to 4,000 tokens | **not run.** Encoder weights unreachable: huggingface.co and the fastembed bucket blocked by network policy; OpenAI embeddings return `insufficient_quota` |
| `baseline_agentless_hierarchical` | Agentless @ `5ce5888` vendored verbatim (`scripts/vendor_agentless.py`): `create_structure` → `filter_none_python`/`filter_out_test_files` → stage-1 file prompt (`top_n=3`) → `get_skeleton` stage-2 symbol prompt → `extract_locs_for_files`; `max_tokens=300`, T=0.0 | **not run.** Both stages are LLM calls; no LLM reachable here. Only the deterministic prompt sizes are measured |

## Retrieval table

| task | PRISM tokens | PRISM coverage | PRISM cleanliness | Aider tokens | Aider coverage | Aider cleanliness | Hybrid coverage | Hybrid cleanliness | Agentless stage-1 prompt | Agentless stage-2 prompt (if stage 1 picked the ground-truth files) |
|---|---|---|---|---|---|---|---|---|---|---|
| `t02_001` | 5,944 | 1.00 | 0.889 | 4,260 | 0.40 | 0.038 | not run | not run | 5,261 | 1,563 |
| `t02_002` | 7,570 | 1.00 | 0.583 | 3,533 | 0.25 | 0.034 | not run | not run | 5,253 | 1,555 |
| `t02_003` | 3,158 | 1.00 | 1.000 | 4,418 | 0.33 | 0.013 | not run | not run | 5,231 | 1,533 |
| `t02_004` | 7,938 | 1.00 | 0.545 | 3,421 | 0.00 | 0.034 | not run | not run | 5,213 | 25,793 |
| `t02_005` | 4,493 | 1.00 | 0.600 | 3,436 | 0.33 | 0.017 | not run | not run | 5,210 | 1,630 |
| **mean** | **5,821** | **1.00** | **0.724** | **3,814** | **0.26** | **0.028** | – | – | **5,234** | – |

**How to read the columns.**
- **What each context contains.** PRISM contexts hold full function
  bodies. Aider's map holds only definition lines in its `⋮`-elided tree,
  so "present" for Aider means the signature is visible, not the body.
- **Aider's token counts.** Aider's own count equals the `cl100k_base`
  count on 5/5 tasks. Maps of 3,421-4,418 tokens reflect its
  binary search, which accepts up to 15% over `map_tokens`.
- **The last Agentless column is not an Agentless result.** It is the
  stage-2 prompt size *if* stage 1 chose exactly the files containing the
  ground-truth pipeline (an upper bound on stage 2's view). t02_004 needs
  `fastapi/routing.py`, whose skeleton alone is about 24K tokens.

## Official Aider map: what it contained

| task | files in map | def tags | tags not matched to a symbol | share of tags from `docs_src/` | seed present | pipeline stages missing |
|---|---|---|---|---|---|---|
| `t02_001` | 29 | 79 | 1 | 0.18 | yes | `analyze_param`, `get_param_sub_dependant`, `get_sub_dependant` |
| `t02_002` | 19 | 59 | 1 | 0.05 | yes | `request_params_to_args`, `request_body_to_args`, `solve_generator` |
| `t02_003` | 26 | 79 | 2 | 0.11 | yes | `_get_multidict_value`, `_validate_value_with_model_field` |
| `t02_004` | 17 | 59 | 1 | 0.02 | no | `__init__`, `get_typed_return_annotation`, `get_dependant`, `get_flat_dependant`, `get_body_field` |
| `t02_005` | 18 | 59 | 1 | 0.02 | no | `request_validation_exception_handler`, `errors` |

**Aider's map is broad, not deep.**
- **Breadth:** 17-29 files and 59-79 definitions per task.
- **Coverage:** 0-40% of the annotated pipeline (mean 0.26).
- **Cleanliness:** 0.013-0.038. The task's seed function is present on
  3/5 tasks.
- **Downstream stages missing:** pipeline stages reached only through the
  seed's call chain are the ones absent.
- **Docs noise:** tutorial code (`docs_src/`) is 2-18% of the listed definitions.

## Aider's map is not deterministic across processes

With default Python hash randomization, the same query gave **three
different maps in three runs** (t02_004: 3,421 / 3,433 / 3,434 tokens,
different content hashes). Aider's identifier and file sets feed PageRank
and its tie-breaks, and set iteration order changes with the per-process
string-hash seed. With `PYTHONHASHSEED=0` all three runs were identical,
and two full smoke runs reproduce this JSON exactly.

The baseline pins `PYTHONHASHSEED=0` and records it per cell
(`pythonhashseed`). Real Aider sessions do vary this way; the paper should
state that the seed was pinned for reproducibility.

## Tokenizer finding (affects earlier results)

`openaipublic.blob.core.windows.net` (tiktoken's download host) is blocked
here. Without a cached encoding, PRISM's `count_tokens` falls back to its
regex approximation (×1.08 safety factor), with no error.

**The gpt-4o-mini sweep ran in this container on that fallback.**
- **Inflated counts:** its `context_tokens` are about 1.21× (range
  1.13-1.28×) the real counts, measured on the deterministic
  `pragmatic_oracle` package, identical across both sweeps, as rendered in
  the Kaggle Qwen sweep with real `cl100k_base`.
- **Tighter ceiling:** its 8,800-token ceiling was effectively about
  7,300 real tokens, and 146/2,000 tRPC cells had nodes dropped.
- **Unaffected:** the Qwen 7,200-cell sweep (Kaggle) used real
  `cl100k_base`.

Token counts and truncation are therefore not comparable across the two
model sweeps. TSR comparisons within a model are unaffected. The earlier
PageRank dry run (`smoke_pagerank_repomap.md`, 3,918 tokens) also used the
fallback.

**Fix used here:** the `cl100k_base`/`o200k_base` files bundled with
`litellm` (an aider-chat dependency), SHA-256-verified against
`tiktoken_ext.openai_public`, loaded via `TIKTOKEN_CACHE_DIR`. The smoke
script refuses to run on the fallback.

## To complete the pending engines

- **Hybrid RAG:** run the same script where Hugging Face is reachable
  (e.g. Kaggle; `pip install sentence-transformers`). Embeddings are
  cached per corpus under `.benchmarks/cache/hybrid_rag/`.
- **Agentless:** needs an LLM. `agentless_fl.localize(client, model, ...)`
  takes the harness client, e.g. Qwen via Ollama on Kaggle.
- **Arm registration:** none of the three is registered as a
  `final_sweep` arm yet, so LLM TSR cells are not wired. That is the next
  step once retrieval is agreed.
