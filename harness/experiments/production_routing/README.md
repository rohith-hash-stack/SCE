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
| `ablation_report.py` | per-cell table and T5 matrix / pairwise / anomaly report for B0M4, R1 and R0 (M4 statistics) |
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

## Configs

**Current run: R1 vs R0, T5 only** (192 cells). R2 (routing) is parked as
exploratory: it stays in the repo, default off, and is not part of this run.

| config | flags | cells |
|---|---|---|
| **B0M4** | stored M4 cells, `python -m harness.experiments.production_routing.b0m4` | 0 (no GPU) |
| **R1** | none: the pipeline as currently shipped | 32 T5 tasks × 3 seeds = 96 |
| **R0** | `HARNESS_PRISM_T5_RULE_SELECTOR=1` | 96 |
| R2 (parked) | `HARNESS_PRISM_PRODUCTION_ROUTING=1` | not run |

R1 is not "M4 + three fixes". It is everything shipped since M4:
* the call-resolution fixes and best-effort edge pricing;
* Design C for T5 (`PRISM_BLAST_MODE`, on by default);
* the three manifest fixes: hop-count admission, full signatures, `lines=N`.

R0 differs from R1 only at Turn 1 on T5 seeds: it requests every `caller`
row of the same manifest and makes no model call. The manifest call,
manifest text, Turn-2 hydration arguments and answer turn are identical.

Run commands (Kaggle; same model and server arguments as M4), one process
per config per corpus, each with its own `--out` directory:

```bash
for c in fastapi django express trpc; do
  HARNESS_ACTIVE_ARMS=arm5 python -m harness.kaggle_m1 --corpus $c --seeds 42,43,44 --task-types T5 \
      --out /kaggle/working/r1/$c --model qwen2.5-coder:14b-instruct-q8_0 \
      --llm-url http://localhost:11434/v1 --ollama-url http://localhost:11434
  HARNESS_ACTIVE_ARMS=arm5 HARNESS_PRISM_T5_RULE_SELECTOR=1 python -m harness.kaggle_m1 --corpus $c --seeds 42,43,44 \
      --task-types T5 --out /kaggle/working/r0/$c --model qwen2.5-coder:14b-instruct-q8_0 \
      --llm-url http://localhost:11434/v1 --ollama-url http://localhost:11434
done
```

Report (offline, after copying `/kaggle/working/r1` and `/kaggle/working/r0`
back, e.g. to `reports/harness_r0r1/{r1,r0}/<corpus>/`):

```bash
python -m harness.experiments.production_routing.ablation_report \
    --config R1=reports/harness_r0r1/r1 --config R0=reports/harness_r0r1/r0 \
    --out reports/harness_r0r1/report
```

This writes `per_cell.csv`, with one row per cell:
* config, corpus, seed, task_id, TSR;
* gold coverage, selection precision, selection recall;
* turn1_source, plus diagnostics.

It also writes `ablation.json` and `ablation.md` with:
* the T5 matrix with 95% CIs;
* R0 vs R1, Holm-corrected over the four corpora;
* the comparisons against B0M4;
* the anomaly scan.

Gold is read only here, at analysis time; arms never see it.

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
