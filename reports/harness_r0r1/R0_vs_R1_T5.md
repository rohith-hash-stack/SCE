# R1 vs R0 — T5 ablation (does the LLM's Turn-1 selection add value?)

Run: Kaggle, commit `bcb1197`, model `qwen2.5-coder:14b-instruct-q8_0`, Arm 5 only, T5 only, seeds 42/43/44,
fastapi / django / express / trpc. 96 cells per config, 192 total. B0M4 is the stored M4 data (no re-run).
Generated tables: `report/ablation.md`, `report/ablation.json`, `report/per_cell.csv`
(`python -m harness.experiments.production_routing.ablation_report`).

| config | Turn 1 |
|---|---|
| R1 | the pipeline as shipped: the model selects from the Design C manifest |
| R0 | identical pipeline; on T5 the Turn-1 selection is "every `caller` row", no model call |

R2 (routing) is parked as exploratory and was not run. No T2 cells were run.

## Finding

**Replacing the LLM's Turn-1 selection with "take every caller row" raised T5 TSR in all four corpora
(+0.14 to +0.29). The gain is significant after Holm on tRPC (+0.29, p_holm < 0.001) and not significant on
FastAPI, Django or Express. So the LLM's Turn-1 selection adds no measurable value over the rule anywhere,
and measurably costs on tRPC.**

R0 does not tie R1; it is ahead in every corpus. On the independent semantic gold (4 Django seeds) the gap
narrows to a small one: answer recall 0.355 vs 0.335.

## 1. T5 matrix

Mean TSR [95% CI]: per-task 3-seed mean, 10,000-resample cluster bootstrap over tasks, seeded as in M4.

| config | fastapi (n=8) | django (n=8) | express (n=2, anecdotal) | trpc (n=14) |
|---|---|---|---|---|
| B0M4 (M4, stored) | 0.281 [0.179, 0.375] | 0.190 [0.088, 0.307] | 0.250 [0.000, 0.500] | 0.151 [0.080, 0.240] |
| **R1** (LLM Turn 1) | 0.375 [0.240, 0.514] | 0.407 [0.172, 0.656] | 0.333 [0.000, 0.667] | 0.372 [0.224, 0.535] |
| **R0** (rule Turn 1) | 0.518 [0.392, 0.633] | 0.558 [0.280, 0.809] | 0.500 [0.000, 1.000] | 0.666 [0.524, 0.802] |

B0M4 reproduces the published M4 T5 matrix exactly (means, CIs, per-seed means, counts).

Secondary metrics (cell means; gold coverage = gold delivered to the answer model):

| config | gold coverage | selection precision | selection recall | TSR |
|---|---|---|---|---|
| B0M4 | 0.197 | 0.312 | 0.196 | 0.199 |
| R1 | 0.461 | 0.507 | 0.460 | 0.379 |
| R0 | 0.878 | 0.681 | 0.874 | 0.592 |

The rule is both more complete and more precise than the model's picks. The model still spends picks on
downstream rows, as in M4. The answer turn loses part of what is delivered under both configs (R0: 0.878
delivered vs 0.592 named in the answer).

## 2. B0M4 comparison (context)

Holm over these 8 tests:

| corpus | R1 − B0M4 | p_holm | R0 − B0M4 | p_holm |
|---|---|---|---|---|
| fastapi | +0.094 [−0.034, +0.243] | 0.560 | **+0.237** [+0.092, +0.404] | **<0.001** |
| django | +0.217 [−0.000, +0.452] | 0.207 | **+0.368** [+0.151, +0.569] | **0.006** |
| express (anecdotal) | +0.083 [0.000, +0.167] | 0.986 | +0.250 [0.000, +0.500] | 0.986 |
| trpc | **+0.221** [+0.103, +0.369] | **<0.001** | **+0.515** [+0.360, +0.667] | **<0.001** |

B0M4 vs R1 is not a controlled comparison. It mixes everything shipped since M4 (call-resolution fixes,
Design C for T5, the three manifest fixes) with a fresh model sample. R1 vs R0 is the controlled one: same
commit, same session, same manifests, only Turn 1 differs.

## 3. R0 vs R1, Holm over the four corpora

| corpus | Δ TSR (R0 − R1) | 95% CI | p_raw | p_holm | significant |
|---|---|---|---|---|---|
| fastapi | +0.143 | [−0.013, +0.320] | 0.076 | 0.229 | no |
| django | +0.151 | [−0.046, +0.391] | 0.179 | 0.358 | no |
| express (anecdotal) | +0.167 | [0.000, +0.333] | 0.493 | 0.493 | no |
| trpc | **+0.294** | [+0.159, +0.435] | <0.001 | **<0.001** | **yes** |

Significant = p_holm < 0.05 and the CI excludes 0. Corpora are not pooled (M4 rule).

## 4. Turn-1 source distribution

| config | llm | rule |
|---|---|---|
| R1 | 96 | 0 |
| R0 | 0 | 96 |

From each bundle's `turn1_source` / Turn-1 model field: R1's Turn 1 is always the Qwen model, R0's is
always `rule_callers`.

## 5. FAIL rows and anomalies

* **No FAIL rows.** 192/192 cells checkpointed, `rows_fail` 0 in every corpus. Tokenizer parity OK; no index
  failures.
* **Over budget, 3 R1 + 3 R0 cells:** `django_t13_004_blast_options_get_field`, all seeds, both configs
  (TSR 0.75 in each). The harness re-count (Qwen tokenizer) trimmed lowest-ranked items from this hub
  seed's 34-symbol pack. It affects both configs identically.
* **One degenerate Turn 1 in R1:** `django_t13_002_blast_queryset_get` seed 42. The model's Turn-1 output
  did not parse (1 pick, TSR 0). This is a model failure on the LLM path and is counted as such.
* **Run-procedure deviations** (no effect on the data):
  * The session did not keep the cell's isolation markers (`ablation_config.json`).
  * R1 FastAPI was re-run rather than resumed. The re-run reproduced the first session's results exactly:
    24/24 cells with identical TSR and identical Turn-1 text; only latencies differ.
  * Isolation was therefore verified from the data instead:

| check | result |
|---|---|
| every cell is Arm 5, T5, seeds 42–44, the stated model | yes |
| Design C on in every cell; routing off in every cell | yes |
| R1 and R0 got the same manifest size, per paired cell | 96/96 |
| R0 requested exactly the manifest's caller rows | 96/96 |

## Caveats

* **T5 gold is PRISM-derived.** It was built from PRISM's original call graph, and R0 selects PRISM's
  callers, so this gold structurally favours R0.
* **Independent check: change-based semantic gold (4 Django blast seeds).** Gold in the delivered context /
  gold named in the answer, 3-seed means:

| seed | n gold | B0M4 | R1 | R0 |
|---|---|---|---|---|
| `reverse` | 21 | 0.048 / 0.048 | 0.937 / 0.873 | 1.000 / 0.921 |
| `QuerySet.get` | 29 | 0.000 / 0.000 | 0.057 / 0.046 | 0.138 / 0.069 |
| `Field.clean` | 6 | 0.222 / 0.111 | 0.667 / 0.167 | 1.000 / 0.167 |
| `Options.get_field` | 79 | 0.008 / 0.008 | 0.278 / 0.253 | 0.278 / 0.262 |
| **mean** | | 0.070 / 0.042 | 0.485 / 0.335 | 0.604 / 0.355 |

  On independent gold, R0 ≥ R1 on every seed (context and answer), but the answer-level gap is small
  (+0.02). The large gain is pipeline vs M4 (0.04 → 0.34–0.36), not rule vs LLM. With n = 4 this is
  descriptive, not tested.
* **Express has n = 2 T5 tasks**: anecdotal; no conclusion is drawn from that column.
* **R2 (routing) is parked** as exploratory, default-off in the repo, and not part of this run.

## What this settles

For blast-radius retrieval, the LLM's Turn-1 choice is not where PRISM's value comes from. The deterministic
caller slice is at least as good everywhere and significantly better on tRPC. The architecture can hand the
caller slice to the answer model directly and drop the Turn-1 LLM call for impact queries. The evidence
supports "the LLM adds nothing measurable at Turn 1"; it does not support a large "the LLM hurts" effect
on independent gold.
