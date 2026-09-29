# tRPC Full Benchmark Debrief: 3-Engine Sweep, Gate, and 25-Task Matrix

**Status: roadmap Section 3, Step 4 (full 20-25 task, 5-seed benchmark)
complete.** Builds on `reports/trpc_pilot_debrief.md`'s own 6-task small
pilot - this run covers the full 25-task suite across all 3 engines the
gate methodology requires (floor, system-under-test, ceiling).

## 1. Setup

- **Corpus**: tRPC `v10.45.4` (pinned commit
  `2ec29bfa2e3a170901be5201962f30e9faf73f96`), indexed at
  `packages/server/src` (`benchmarks.corpora.resolver`'s own `subdir`
  support, added for this epic).
- **Tasks**: all 25 `benchmarks/ground_truth/tasks/trpc/*.yaml` debug
  tasks (`trpc_t02_001` through `trpc_t02_025`), every seed/pipeline
  symbol verified against the real indexed corpus and every stage's
  transitive reachability verified via `networkx.has_path` over
  `builder.calls_graph`, all passing the harness's real agreement gate
  (`benchmarks.ground_truth.loader.load_tasks_from_dir`).
- **Seeds**: 42, 43, 44, 45, 46 (the spec's own 5-seed default).
- **Budget**: 4000 tokens (single budget point, matching the small
  pilot's own scope - not a budget sweep).
- **Model**: `gpt-4o-mini` via OpenAI's real API - DeepSeek's own
  endpoint remains network-blocked in this container, the same
  substitution the Express pilot's own real sweep and this epic's small
  pilot both already used.
- **Engines**: `baseline_bfs_bidirectional` (floor, `benchmarks.runner`),
  `oracle` (`PragmaticOracle`, ceiling - zero-annotation-cost, real
  `dist_W`-truncated selection, `benchmarks.runner --pragmatic-oracle`),
  `prism_two_pass` (system under test, `benchmarks.run_two_pass_benchmark`,
  a separate harness/checkpoint schema normalized via
  `scripts/merge_pilot_checkpoints.py`).
- **Coverage**: 375 real cells (25 tasks x 5 seeds x 3 engines) - 250
  single-pass LLM calls (`baseline_bfs_bidirectional` + `oracle`, 1 call/
  cell) and 250 two-pass LLM calls (125 cells x 2 turns). **Total real
  cost: $0.2551** ($0.1637 single-pass + $0.0914 two-pass), on top of the
  small pilot's own $0.0214 - **$0.2765 total real LLM spend across the
  whole tRPC campaign to date**.

## 2. Real methodology finding: `fpr_gt` was never persisted for
   single-pass engines

`benchmarks.runner.run_evaluation` computes `diagnostics["fpr_gt"]` (the
false-positive rate against the task's own annotated ground-truth
universe - `|selected \ ground_truth| / |selected|`) purely for its own
printed run summary; the checkpoint cell dict it actually writes to disk
never includes it (`score`, `raw_response`, `prompt_tokens`,
`completion_tokens`, `cost_usd`, `selected_symbols`, `cpi_strict`,
`cpi_fractional`, `model` only). `benchmarks.run_two_pass_benchmark`, by
contrast, does persist it. Confirmed directly: 250/250 single-pass cells
in the raw checkpoint had `fpr_gt: null`; 125/125 two-pass cells had a
real value.

This is a real, disclosed gap in `runner.py` itself (a genuine follow-up:
`checkpoint["cells"][key]` should include `fpr_gt` and `fpr_oracle`
alongside `cpi_strict`/`cpi_fractional`, matching what the two-pass
harness already does), not something this debrief silently routed
around. Since every single-pass cell's own `selected_symbols` (the full
retrieved candidate set) *was* already saved, `fpr_gt` was recomputed
**post-hoc, at zero new LLM cost**, using the exact same formula
(`benchmarks.metrics.fpr.fpr`) and the exact same ground-truth-universe
definition (`benchmarks.runner._ground_truth_universe` - the union of
`pipeline_symbols`/`critical_callers`/`orthogonal_neighbors`/
`reference_symbols`/`required_context`/`boundary_symbols`) the harness
itself would have used - the same "recompute post-hoc from already-
collected raw data" discipline this project's own scorer-mismatch fix
(`reports/express_pilot_audit_gap_closure.md`) already established as
precedent, rather than a new ad hoc metric.

## 3. Three-engine comparison matrix

| Engine | n | Mean TSR | Mean CPI_answer | Mean Cleanliness (1 - FPR_gt) |
|---|---|---|---|---|
| `baseline_bfs_bidirectional` (floor) | 125 | 0.584 | 1.000 | 0.362 |
| `oracle` (PragmaticOracle, ceiling) | 125 | 0.760 | 1.000 | 1.000 |
| `prism_two_pass` (system under test) | 125 | **0.923** | 0.959 | 0.767 |

(Cleanliness reported as `1 - mean FPR_gt`, so higher is better/cleaner,
matching TSR/CPI_answer's own "higher is better" convention. Raw mean
`FPR_gt`: baseline 0.638, oracle 0.000, prism_two_pass 0.233.)

## 4. Gate result

```
python scripts/apply_gate.py --checkpoint checkpoint_merged.json \
  --baseline-engine baseline_bfs_bidirectional --prism-engine prism_two_pass \
  --delta-tsr-threshold 15 --delta-cpi-threshold 15
```

- **ΔTSR: +33.87pp**, 95% CI [25.20, 42.67] - clears the 15pp threshold,
  CI strictly excludes zero. A large, real, statistically confirmed
  improvement.
- **ΔCPI_answer: -4.07pp**, 95% CI [-6.13, -2.33] - a real, statistically
  confirmed **regression**, not noise (the CI is entirely negative).
  `baseline_bfs_bidirectional`'s own mean CPI_answer is already the
  ceiling (1.000, 0% headroom), so the gate's headroom-aware effective
  threshold degenerates to "any positive, CI-confirmed delta" - and this
  delta is negative.
- **Decision: MIXED** - ΔTSR clears its own bar convincingly; ΔCPI_answer
  does not. Per the gate's own five-outcome partition, one axis clearing
  while the other doesn't is MIXED, not PASS or STOP.

### Why: a real precision/recall trade-off, not noise

The Cleanliness column explains the MIXED result directly.
`baseline_bfs_bidirectional`'s wide, undirected BFS neighborhood packs
in a lot of real, structurally-reachable context - only 36.2% of what it
selects falls inside any given task's own narrow annotated ground-truth
set, but because the net is so wide, the *correct* answer is essentially
always reachable somewhere in it (hence its own perfect 1.000
CPI_answer). The model's job is then to find the needle in that noisy
haystack, in the exact right causal order - and it does so far less
reliably (0.584 mean TSR) than when handed `prism_two_pass`'s own much
more targeted, curated context (76.7% clean). `prism_two_pass` wins
decisively on the metric that actually reflects real-world usefulness
(TSR: can the model produce the right, correctly-ordered answer) at a
small, real cost on CPI_answer's own narrower "is the correct answer
merely present in context at all" question - a genuine, disclosed trade-
off between precision-oriented retrieval and recall-at-any-cost
retrieval, not a defect to explain away. `oracle`'s own middling 0.760
TSR (despite a perfectly clean, ground-truth-exact context) is itself
informative: even with the objectively best possible context, the same
Turn-2 reasoning gaps this epic's own small-pilot debrief already found
(causal-order reversals, occasional omissions on the richest pipelines)
still cap real-world TSR below 1.0 - retrieval quality alone is not the
only lever left to pull.

## 5. Status and next steps

Roadmap Section 3's own staged plan (graph-quality spike -> locator ->
small pilot -> full benchmark -> gate + audit) is now complete through
the gate. Remaining per the roadmap: **a manual construct-validity audit
of every FAIL/MIXED cell** (Section 2's own mandatory item 4 - this is
exactly how `t018`/`t019`/`t020` were found in the Express pilot), not
yet performed here. Given the MIXED outcome and the real, large residual
CPI_answer gap concentrated in specific cells, a manual audit of the
lowest-TSR and lowest-CPI_answer `prism_two_pass` cells is the natural
next step before this result is reported as final - not started in this
session, pending direction on scope.
