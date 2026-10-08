# Production routing experiment

Question: can PRISM decide the retrieval direction (callers vs. callees) from
the query text alone, with the Turn-1 prompt unchanged, and does that give
production-grade behaviour? Design and offline evidence:
`reports/harness_m4/analysis/production_routing_experiment.md`.

## What is here

| file | role |
|---|---|
| `intent.py` | heuristic query-intent classifier → `blast_radius` / `localization` / `mixed` / `unknown` + confidence (no model, no labels) |
| `routing.py` | pool shaping: `route_manifest(engine, seed, query, budget)` and `RoutedEngine`, an engine wrapper whose `build_candidate_manifest` routes by the current query |
| `arms.py` | `ProductionRoutingArm5` (config R2) and `RuleSelectorArm5` (config R0), both subclasses of the unmodified `Arm5Prism` |
| `b0m4.py` | B0M4: the M4 Arm 5 baseline recomputed from stored cells, checked against the published M4 matrices |
| `heldout_queries.json` | 36 generic developer queries (written before the classifier was finalised) for the classifier test |

Core PRISM (`src/prism/`) and `harness/arms/arm5_prism.py` are not modified by
this experiment. The only hook is in `harness/arms/__init__.py:build_arm`,
which builds one of the two experiment arms for `"arm5"` when a flag is on.
The experiment package is not imported when both flags are off. Both arms
keep the arm id `arm5`, so cells are scored exactly like Arm 5.

## Routing policy (pool shaping)

| query intent | Turn-1 manifest |
|---|---|
| `blast_radius` | PRISM's Design C list (transitive callers + downstream under the arm budget), downstream rows kept only at structural hop 1 |
| `localization` | PRISM's default (downstream) manifest |
| `mixed` / `unknown` | PRISM's Design C list, both directions |

Rows are filtered, never rewritten. The task-type label is never read for
routing. The base arm's label-driven `PRISM_BLAST_MODE` is ignored, and
`build_meta["prism_blast_mode"]` is set to `None` in routed cells.

## Configs (Kaggle ablation)

| config | how to run | GPU |
|---|---|---|
| **B0M4** | stored M4 cells, `python -m harness.experiments.production_routing.b0m4` | none |
| **R1** | flags off (the fixed baseline: hop-count admission, full signatures, `lines=N`) | 363 cells |
| **R2** | `HARNESS_PRISM_PRODUCTION_ROUTING=1` | 363 cells |
| **R0** | `HARNESS_PRISM_T5_RULE_SELECTOR=1`: on T5 seeds Turn 1 requests every `caller` row of R1's manifest without calling the model; T2 runs exactly as R1 | 363 cells |

Common to R1, R2 and R0: Arm 5 only (`HARNESS_ACTIVE_ARMS=arm5`), all four
corpora, seeds 42,43,44, T2 + T5, a separate `--out` directory per config.
Setting both flags is rejected at import.

### Methods note: B0M4 is not a re-run

B0M4 is the M4 Arm 5 baseline read from the stored M4 cells
(`reports/harness_m4/<corpus>/cells.parquet`, the cells behind the published
M4 matrices). It is not re-run: the M4 code (pre-fix PRISM, no Design C) is
not what R1/R2/R0 run, and re-running it would only re-sample the model.

`b0m4.py` recomputes Arm 5's mean TSR per (corpus, task type) as the M4
analysis does (per-task 3-seed mean, then the mean over tasks) and checks it
against `reports/harness_m4/analysis/summary.json`. Means, per-seed means and
task and cell counts all match exactly; this is asserted by
`tests/test_production_routing.py`.

The comparison B0M4 vs. R1 therefore spans two things at once: the code
changes since M4 (graph resolution fixes, Design C for T5, the three manifest
fixes) and a fresh sample from the same model and seeds. R1 vs. R2 vs. R0 is
the controlled comparison: same commit, same session, only the flag differs.

## Logged per cell (`build_meta`)

* R2: `routing_intent`, `routing_confidence`, `routing_policy`,
  `routing_evidence` (the impact/mechanism cue scores).
* R2 and R0: `manifest_rows`, `manifest_caller_fraction`,
  `turn1_picks_in_manifest`, `turn1_picks_caller_fraction`. The last is the
  share of the model's Turn-1 picks that are caller rows, i.e. whether a
  caller-heavy manifest moved the picks.
* R1 runs the plain arm and logs none of these. Its manifests are
  deterministic and can be regenerated offline for the same per-cell
  fractions.
