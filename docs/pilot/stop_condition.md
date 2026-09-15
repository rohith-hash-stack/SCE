# Pilot Pre-Registered STOP Condition

This document is the contract for the pilot. It is written and
committed *before* the pilot runs. After the pilot runs, the numbers
either clear it or they don't — this document does not get retuned,
re-thresholded, or reinterpreted after the fact to fit the result.

## 1. Primary metrics

- **TSR** (Task Success Rate)
- **CPI_strict** (Causal Pipeline Integrity, strict variant)

Both are reported with 95% bootstrap confidence intervals (10,000
resamples).

## 2. Decision thresholds

- **PASS**: ΔTSR ≥ 15 percentage points AND ΔCPI_strict ≥ 15
  percentage points, both CIs excluding zero.
  → Proceed to the full 4-repo sweep (Phase P2).

- **EXPAND**: one or both metrics land in the 5-15 percentage-point
  range.
  → Add 20 more tasks, re-run, re-evaluate against this same rule.

- **STOP**: both metrics < 5 percentage points.
  → Do not build Phase P2.

These three outcomes are exhaustive and mutually exclusive over the
two metrics; there is no fourth reading of the numbers.

## 3. Comparison baseline

- **Primary**: Prism v1.1 vs. BFS-bidirectional.
- **Secondary** (reported, not gating): BM25, BFS-forward,
  PragmaticOracle.

Only the primary comparison feeds the decision thresholds in
Section 2. The secondary engines are reported alongside for context
and are never used to justify a different PASS/EXPAND/STOP outcome
than the primary comparison produces.

## 4. Post-hoc exclusions prohibited

- No task may be dropped from the analysis after the pilot runs,
  except for the pre-declared validity reasons below. There are no
  other grounds for exclusion, regardless of how a task's result
  looks once seen.
  - Seed symbol missing from the pinned commit.
  - Parse failure on the task's seed file.
- No threshold may be adjusted after the pilot runs.
- No LLM may be swapped mid-run.

## 5. LLM pinning

- **Model**: `deepseek-v4-flash` (pinned in `benchmarks/tsr/client.py`;
  confirmed against https://api-docs.deepseek.com/quick_start/pricing -
  the legacy `deepseek-chat`/`deepseek-reasoner` names were retired
  2026-07-24 and must not be used).
- **Temperature**: 0.
- **Seeds**: 5 per cell.
- **Off-peak scheduling**: the pilot runs off-peak (outside weekday
  01:00–04:00 UTC and 06:00–10:00 UTC) to halve cost.

### 5.1 T02 debug response contract (pre-registered harness change)

- **Contract**: flat JSON object - `{"reasoning": "2-3 sentences",
  "symbols": ["fully.qualified.name", ...]}` - ordered, first symbol
  the seed, the rest the causal stages in execution order. Parsed by
  `benchmarks.tsr.scorer_debug.extract_flat_symbols`
  (`fix-prompt-flat-contract`, `fix-parser-flat`).
- **Retired**: the earlier nested `{"reasoning": ..., "pipeline":
  [{"symbol": ..., "evidence": ...}, ...]}` shape. `evidence` was never
  consumed by any downstream scoring or reporting code - only
  `entry["symbol"]` was ever read out of the old `pipeline` array -
  so dropping it costs nothing.
- **Rationale**: local SLM format-compliance testing, run before this
  pilot, against **Qwen 2.5 7B Instruct Q8_0** (Ollama, Kaggle 2x T4,
  via Cloudflare tunnel) - the flat contract passed 10/10 format
  checks; the nested contract failed at Q4. Qwen is used only for this
  pre-pilot format-compliance check, never as the pilot's own model -
  the pilot's LLM remains `deepseek-v4-flash` per this section's own
  pinning, unchanged by this switch, and Section 4's "no LLM may be
  swapped mid-run" rule is unaffected.
- **Request-side enforcement**: every chat-completions call
  (`benchmarks.tsr.client.DeepSeekClient.complete`) now sends
  `response_format={"type": "json_object"}` - a standard
  OpenAI-compatible soft constraint, honored by both DeepSeek's
  endpoint and Ollama's OpenAI-compatible one.
- **Status**: this is a pre-registered harness change to the response
  contract itself, landed and documented here *before* the pilot runs -
  not a post-hoc adjustment to fit a result, and out of scope for
  Section 4's post-hoc-exclusion/threshold-adjustment prohibitions
  (those govern the pilot's *results*, not the harness's own response
  format, which this document now pins going forward).

## 6. Failure to meet any pre-condition

If the pilot cannot be run as specified — the pinned commit changed,
the LLM is unavailable, the corpus is corrupted, or any other
precondition in this document no longer holds — the pilot is
postponed. This STOP-condition document is not modified to
accommodate the failure. The condition as written is either met or
not; it is not edited to make a compromised run meet it.

If the run fails mid-way for any reason, the partial run is discarded.
The pilot restarts from call 1 on the same model. Do not combine
partial results across runs.

## 7. Pre/post-pilot safety checklist

- **Pre-pilot**: delete `.prism/cache/` before starting. One shell
  command. Ensures no stale on-disk entries from a prior Prism
  version or a prior, uncommitted working-tree state can leak into
  the pilot run (see G43, `docs/design_formalism.md` Section 10.1 -
  the on-disk feature-bitmask cache's key is derived from `git
  rev-parse HEAD` only and does not detect an uncommitted change to
  the code that computes the cached value).

- **Post-pilot**: run 10 (task, engine, budget, seed) cells twice in
  the same process. Compare byte-for-byte. If any diverge, flag it
  in the report as a determinism concern. This is a spot-check, not
  a gate — report the divergence rate, do not retry.

G43 does not block the pilot. The pilot tasks are Python-only; G43
manifests on minified JS. These two safety steps are cheap insurance
regardless.

## 8. Sign-off

The STOP condition above is pre-registered and will not be adjusted
after the pilot runs.

Signed: ______________________  Date: ______________
