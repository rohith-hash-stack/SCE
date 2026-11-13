# Phase 0 model candidates on Kaggle 2× T4: capability test (2026-11-13)

**Status:** the `load` and `generate` stages have both run on Kaggle 2× T4 (2026-11-13). **The measured results are
in §9, which replaces the estimates in §5 and the recommendation in §7.** Raw data:
`results/model_capability_results.json`.

The original pre-measurement text follows unchanged.
- **Verified from the Ollama registry,** without a GPU: model IDs, quantisations, download sizes and architecture
  (from each GGUF header, read through 16 MB range requests).
- **Computed, not measured:** VRAM fit and the single-T4 test. Those numbers come from the cell's `load` stage on
  Kaggle.

**Two corrections to the brief:**
- **The Groq block** was a limit of 8,000 tokens per minute (from Groq's own headers), not a per-request cap. Its
  effect is the same: a 16K turn cannot run.
- **"200K tokens a day"** was my recollection, never confirmed.

## 1. Availability (verified, registry.ollama.ai, 2026-11-13)

| Candidate (brief) | Exact Ollama ID (default tag = Q4_K_M) | Q4_K_M download | Q8_0 download | Architecture (GGUF) |
|---|---|---|---|---|
| Qwen3-Coder-30B-A3B | `qwen3-coder:30b-a3b-q4_K_M` (= `qwen3-coder:30b`) | **18.56 GB** | 32.48 GB | `qwen3moe`: 48 layers, 128 experts (8 active), 4 KV heads × 128 |
| "Qwen3.8-27B" | `qwen3.8:27b-q4_K_M` (= `qwen3.8:27b`) | **16.81 GB** + 0.93 GB vision projector | 29.05 GB | `qwen35`: 65 layers, **hybrid** (full attention on 1 layer in 4: 17), 4 KV heads × 256. "Qwen3.8 27B 0814" |
| "Devstral 2 24B" | `devstral-small-2:24b-instruct-2512-q4_K_M` (= `devstral-small-2:24b`) | **15.18 GB** | 25.91 GB | `mistral3`: 40 layers, 8 KV heads × 128, dense, vision tower. (`devstral-2` itself is a 123B model) |
| Ornith-1.0-35B (4th) | `ornith:35b-q4_K_M` (= `ornith:35b`) | **21.17 GB** | 36.90 GB | `qwen35moe`: 40 layers, hybrid (10 attention layers), 256 experts (8 active), 2 KV heads × 256. "Ornith 1.0 35B" |

**Not usable on T4:** the other quantisations offered (`nvfp4`, `mxfp8`, `mlx`) need FP4/FP8 hardware (newer than
Turing) or Apple silicon. **Pull commands:** `ollama pull <ID>` with the IDs above.

## 2–3. Load and context fit (computed; the cell's `load` stage measures it)

KV cache at the Phase 0 loop's `num_ctx` = 20,480 (16,384-token prompt window plus the answer), with the sanity cells'
q8_0 cache and flash attention:

| Model | Attention layers with a KV cache | KV q8_0 @ 20,480 | KV f16 | Weights | Estimated total on 2× T4* | Fits 2× T4 (~29 GB) |
|---|---|---|---|---|---|---|
| qwen3-coder 30B-A3B | 48 of 48 | 1.07 GB | 2.01 GB | 18.56 GB | ~21.6 GB | **yes** (~7 GB spare) |
| qwen3.8 27B | 17 of 65 | 0.76 GB | 1.43 GB | 17.74 GB (with projector) | ~20.5 GB | **yes** |
| devstral-small-2 24B | 40 of 40 | 1.78 GB | 3.36 GB | 15.18 GB | ~19.0 GB | **yes** |
| ornith 35B | 10 of 40 | 0.22 GB | 0.42 GB | 21.17 GB | ~23.4 GB | **yes** (~5 GB spare) |

\* Weights + q8_0 KV + ~2 GB of compute buffers (estimated). At 20,480 tokens none is near the limit; all four would
also fit a longer context.

**Q8_0:**
- **devstral-small-2** at Q8_0 (25.9 GB + 1.8 GB KV + buffers ≈ 29.7 GB) is at the edge of 2× T4: likely to spill
  to the CPU.
- **The other three** at Q8_0 do not fit.

## 4. Two lanes: none fits on one T4

A T4 has ~15 GB, ~14.5 GB usable after the CUDA context. **Every candidate's Q4_K_M weights alone exceed that:**
devstral-small-2 15.18 GB (not the brief's ~14.3), qwen3.8 16.8 GB, qwen3-coder 18.6 GB, ornith 21.2 GB. No smaller
T4-compatible quantisation is published for them.

So all four are **single-lane** (split across both T4s), at any context. The cell's single-T4 test skips a model whose
weights exceed one T4, and says so.

**The only two-lane route** would be a smaller model, such as `ornith:9b` or the Qwen3.5 small sizes: a different
capability class.

## 5. Estimated Phase 0 wall time (40 runs, single lane; estimates, to be replaced by the measured tok/s)

**Reference points, measured in this project on 2× T4:**
- **Non-thinking, dense ~15–16 GB model** (Qwen2.5-Coder-14B q8, sanity 2): about 5.2 min per gated run
  (3.4 h for 40).
- **Thinking, dense 15.7 GB model** (Qwen3-14B q8, S4): about 42 min per run, from ~880 thinking tokens a turn at
  ~14 tok/s.

| Model | Mode | Generation (estimate) | Per run | 40 runs | Sessions |
|---|---|---|---|---|---|
| qwen3-coder 30B-A3B (MoE, ~3.3B active) | non-thinking | ~30–50 tok/s | ~4–6 min | **~3–4 h** | 1 |
| devstral-small-2 24B (dense) | non-thinking | ~10–14 tok/s | ~6–9 min | ~4–6 h | 1 |
| qwen3.8 27B (dense, hybrid) | thinking off | ~10–13 tok/s | ~6–9 min | ~4–6 h | 1 |
| qwen3.8 27B | thinking on | same | ~40–50 min | ~27–33 h | 3 |
| ornith 35B (MoE, hybrid) | thinking off | ~30–50 tok/s | ~4–6 min | ~3–4 h | 1 |
| ornith 35B | thinking on | same | ~12–15 min | ~8–10 h | 1–2 |

- **Every candidate is expected to clear the brief's 5 tok/s bar.**
- **What dominates a run is prompt processing and the length of the answers** (thinking), not raw tok/s.
- **These estimates are the least reliable numbers here.** The `generate` stage replaces them.

## 6. The cell: `kaggle/model_capability_test.py`

**Settings:** GPU T4 × 2, Internet ON. No GitHub token; no repository.

**Stages:**
- **`STAGE = "load"` (the default; no generation):** for each model:
  - pull it (time, size);
  - read its architecture;
  - load it with `num_ctx` = 20,480 by an empty-prompt load request. That allocates the cache and generates
    nothing;
  - report load time, the GPU/CPU split (`ollama ps`) and the VRAM per GPU (`nvidia-smi`);
  - try one-T4 loads at 20,480, 8,192 and 4,096 when the weights could fit one T4 (here: none, reported as
    skipped).
- **`STAGE = "generate"` (only after review):**
  - **Q3/Q4:** a ~16K-token prompt with a 500-token answer, thinking off. Reports: completed or not, the prompt and
    answer token counts, the generation and prompt tok/s, the VRAM peak per GPU (sampled every second), and the GPU
    share during the call.
  - **Q6:** a 10-turn tool-calling loop on a synthetic repository: a two-bug `calc.py`, its tests, and a ~9K-token
    `notes.py` that makes prompts large once read. Same JSON tool-call protocol as the harness. Reports: turns
    completed, infrastructure failures, parse failures, ended with done, tests passing at the end, the largest
    prompt, seconds per turn.
- **Resumable:** results are saved to `/kaggle/working/model_capability_results.json` after every step, and a rerun
  skips finished steps.
- **Session limits:** no step starts within 30 minutes of 11.5 h.
- **Disk:** an already-tested model is removed before the next pull if disk space is short.
- **Send back:** the printed summary table, or the results file.

**Checked locally (no GPU):** the cell compiles; the synthetic repository fails its two tests at the start and passes
after the two fixes; the parser, the tools and the KV-cache formula behave as described.

## 7. Recommendation (before measurement)

1. **`qwen3-coder:30b-a3b-q4_K_M` first.**
   - **Fit:** it fits 2× T4 with ~7 GB spare at 20,480 tokens.
   - **Speed:** it is a mixture-of-experts model (~3.3B active), so likely the fastest of the four.
   - **Fit for the task:** it is coding-specialised and does not think by default, which keeps runs short.
2. **`devstral-small-2:24b-instruct-2512-q4_K_M` as the second.**
   - **For it:** a dense agentic-coding model, and the smallest download.
   - **Against it:** the largest KV cache of the four, and slower generation.
3. **`ornith:35b-q4_K_M`: test, but flag it.**
   - **Its headers:** a Qwen3.5-MoE derivative ("Ornith 1.0 35B").
   - **What I can't verify:** its provenance and the 75.6% SWE-bench claim. Treat any result as provisional.
4. **`qwen3.8:27b-q4_K_M`: viable only with thinking off.**
   - **With thinking on,** it repeats S4's 40-minute runs.
   - **It also loads a vision projector** the harness does not use.

**The viable set is not too thin:** all four fit at 16K on 2× T4. The real limitation is that **none can run two
lanes.**

**Running Phase 0 on any of these needs an amendment before the run:**
- **What changes:** the model (the pre-registration named `openai/gpt-oss-120b`) and the client (`OllamaChat`, which
  exists, instead of the API client).
- **What it means:** like every model change so far, comparability with earlier rounds is partial.

## 8. Stop point

- **What may run immediately:** the `load` stage (no generation).
- **What waits for the user's go-ahead after the `load` results:** the `generate` stage (Q3/Q4/Q6).
- **Generation-heavy step:** none expected to exceed 10 minutes per model. The 16K call is one prompt plus 500
  tokens; the loop is at most 10 short turns.

## 9. Measured results (Kaggle 2× T4, `STAGE = "generate"`, cell commit `548b9d2a`, 2026-11-13)

Ollama 0.34.0, flash attention, q8_0 KV cache, `num_ctx` 20,480, one model loaded at a time. **One run per model,
seed 42**: each row is n = 1.

### Load and the 16K call (Q2–Q4)

| Model | VRAM on load (GPU0 / GPU1, MB) | Peak during the 16K call | On GPU | Prompt tokens | Prompt tok/s | **Gen tok/s** | 16K + 500 wall |
|---|---|---|---|---|---|---|---|
| qwen3-coder 30B-A3B | 9,979 / 9,517 | 10,017 / 9,549 | 100% | 14,845 | 251 | **20.9** | 83.7 s |
| devstral-small-2 24B | 9,257 / 8,219 | 9,293 / 8,255 | 100% | 15,683 | 399 | **12.1** | 81.7 s |
| qwen3.8 27B (think off) | 9,415 / 8,861 | 9,441 / 8,879 | 100% | 16,013 | 410 | **11.3** | 83.8 s |
| ornith 35B (think off) | 10,869 / 10,353 | 10,901 / 10,385 | 100% | 16,071 | 1,336 | **54.6** | 21.8 s |

- **All four complete a ~16K prompt plus a 500-token answer** (`done_reason` = length: the full 500 tokens), entirely
  on the GPUs. The peak is ≤ 40 MB per GPU above the loaded size, leaving ≥ 4.4 GB free per T4.
- **All four clear the 5 tok/s bar.**
- **Estimates that were wrong:**
  - **qwen3-coder:** I estimated 30–50 tok/s; it measured 20.9 tok/s, with the slowest prompt processing of the four
    (251 tok/s).
  - **ornith:** at the top of its estimate, and 3–5× faster than the others at prompt processing.
- **Two lanes:** confirmed impossible. Every model's weights exceed one T4 (the single-T4 load was skipped for all
  four).

### The 10-turn tool loop (Q6)

| Model | Turns | Tools used | Parse failures | Ended with `done` | **Tests pass at end** | s / turn |
|---|---|---|---|---|---|---|
| qwen3-coder 30B-A3B | 10 (cap) | read ×7, run_tests ×3, **edit ×0** | 0 | no | **no** | 1.1 |
| devstral-small-2 24B | 5 | read, edit, edit, run_tests, done | 0 | yes | **yes** | 5.4 |
| qwen3.8 27B (think off) | 5 | read, edit, edit, run_tests, done | 0 | yes | **yes** | 3.5 |
| ornith 35B (think off) | 10 (cap) | read ×8, run_tests ×2, **edit ×0** | 0 | no | **no** | 1.0 |

- **devstral-small-2 and qwen3.8 solved the task optimally:** one read, one edit per bug, one test run, then done.
- **qwen3-coder and ornith never attempted an edit in 10 turns.** Each produced valid JSON on every turn (0 parse
  failures) but cycled through reading `calc.py` and running the tests.
- **Why they stalled cannot be told from this data.** The cell logs neither the reply texts nor the tool outputs.
  A plausible cause: Ollama's grammar-constrained `format: "json"` combined with how these two models were trained
  to emit tool calls. That is a hypothesis, not a finding.
- **This loop never stressed long context:** no model read the 9K-token `notes.py`, and the largest prompt was
  1,042 tokens. Long context is covered by the single 16K call only.
- **Infrastructure:** no failures or timeouts in any of the 30 loop turns, or in the four 16K calls.

### What this changes

- **The pre-measurement recommendation (qwen3-coder first) is withdrawn.** It is the only candidate that is both
  slower than expected and failed Q6.
- **Ornith is the fastest by far but failed Q6,** and its provenance is still unverified.
- **The two models that passed Q6, devstral-small-2 and qwen3.8, are also the two slowest** (11–12 tok/s, about
  400 tok/s prompt). One trivial task cannot separate them.
- **Phase 0 wall time with either of them,** estimated from the measured rates: about 5–9 min per run, about 4–6 h
  for 40 runs on one lane. That fits one Kaggle session. It is still an estimate: the harness's turns are larger than
  Q6's.

**Phase 0 on either model still needs the amendment described in §7** (model and client) before any run.
