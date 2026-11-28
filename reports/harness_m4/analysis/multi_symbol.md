# M4 T2 — multi-symbol subset (gold size > 1)

_Source: `reports/harness_m4/<corpus>/cells.parquet` at `8353a24`. 3-seed (42/43/44) mean TSR. 95% CI: task-level cluster bootstrap, 10,000 resamples, percentile. Corpora are never pooled._

Subset: T2 tasks whose gold (`pipeline_symbols`) has more than one symbol. A single-symbol gold is the seed that the prompt itself names, so any arm that echoes the question passes.

| Corpus | T2 tasks | single-gold (excluded) | multi-symbol (kept) | Oracle uniform_cpi = 1 on all cells |
|---|---|---|---|---|
| fastapi | 24 | 7 | 17 | True |
| django | 20 | 0 | 20 | True |
| express | 20 | 9 | 11 | True |
| trpc | 25 | 0 | 25 | True |


| Arm | fastapi (n=17) | django (n=20) | express (n=11) | trpc (n=25) |
|---|---|---|---|---|
| A0 no retrieval | 0.137 [0.020, 0.294] | 0.250 [0.100, 0.433] | 0.273 [0.061, 0.515] | 0.200 [0.040, 0.360] |
| A1 RAG | 0.373 [0.176, 0.588] | 0.433 [0.233, 0.633] | 0.424 [0.152, 0.697] | 0.400 [0.240, 0.573] |
| A2 packing | 0.412 [0.235, 0.608] | 0.433 [0.250, 0.633] | 0.697 [0.424, 0.939] | 0.413 [0.240, 0.587] |
| A3 LSP | 0.745 [0.549, 0.922] | 0.383 [0.200, 0.583] | 0.273 [0.061, 0.515] | 0.253 [0.120, 0.413] |
| A4 agent | 0.294 [0.137, 0.471] | 0.233 [0.100, 0.400] | 0.303 [0.061, 0.576] | 0.240 [0.107, 0.400] |
| A5 PRISM | 0.902 [0.765, 1.000] | 0.650 [0.450, 0.833] | 0.909 [0.727, 1.000] | 0.840 [0.707, 0.947] |
| Oracle | 0.902 [0.765, 1.000] | 0.767 [0.600, 0.917] | 1.000 [1.000, 1.000] ‡ | 0.907 [0.827, 0.973] |
| **Best non-Oracle** | A5 0.902 | A5 0.650 | A5 0.909 | A5 0.840 |
| **PRISM rank (of 6)** | 1 | 1 | 1 | 1 |
| **PRISM gap to Oracle** | 0.000 | 0.117 | 0.091 | 0.067 |

‡ degenerate CI (every resample gives the same mean).

## Per-seed means (42 / 43 / 44)

| Arm | fastapi | django | express | trpc |
|---|---|---|---|---|
| A0 no retrieval | 0.118 / 0.176 / 0.118 | 0.200 / 0.300 / 0.250 | 0.273 / 0.182 / 0.364 | 0.200 / 0.200 / 0.200 |
| A1 RAG | 0.353 / 0.353 / 0.412 | 0.500 / 0.350 / 0.450 | 0.455 / 0.364 / 0.455 | 0.320 / 0.440 / 0.440 |
| A2 packing | 0.471 / 0.294 / 0.471 | 0.400 / 0.400 / 0.500 | 0.727 / 0.636 / 0.727 | 0.400 / 0.440 / 0.400 |
| A3 LSP | 0.706 / 0.706 / 0.824 | 0.350 / 0.450 / 0.350 | 0.273 / 0.273 / 0.273 | 0.280 / 0.320 / 0.160 |
| A4 agent | 0.118 / 0.353 / 0.412 | 0.200 / 0.350 / 0.150 | 0.273 / 0.364 / 0.273 | 0.280 / 0.240 / 0.200 |
| A5 PRISM | 0.882 / 0.941 / 0.882 | 0.550 / 0.650 / 0.750 | 0.909 / 0.909 / 0.909 | 0.880 / 0.800 / 0.840 |
| Oracle | 0.941 / 0.882 / 0.882 | 0.800 / 0.750 / 0.750 | 1.000 / 1.000 / 1.000 | 0.880 / 0.960 / 0.880 |

## Difference from the full T2 matrix (subset − full)

| Arm | fastapi | django | express | trpc |
|---|---|---|---|---|
| A0 no retrieval | -0.252 | +0.000 | -0.327 | +0.000 |
| A1 RAG | -0.183 | +0.000 | -0.259 | +0.000 |
| A2 packing | -0.172 | +0.000 | -0.136 | +0.000 |
| A3 LSP | -0.074 | +0.000 | -0.311 | +0.000 |
| A4 agent | -0.206 | +0.000 | -0.314 | +0.000 |
| A5 PRISM | -0.029 | +0.000 | -0.041 | +0.000 |
| Oracle | -0.029 | +0.000 | +0.000 | +0.000 |

## Excluded single-gold tasks

- **fastapi**: `fastapi_t02_013_swagger_ui_html_generation`, `fastapi_t02_018_application_setup_docs_registration`, `fastapi_t02_019_orjson_response_render`, `fastapi_t02_020_ujson_response_render`, `fastapi_t02_021_openid_connect_extraction`, `fastapi_t02_023_redoc_html_generation`, `fastapi_t02_024_get_value_or_default_utility`
- **django**: none
- **express**: `express_t02_007_etag_external_dependency`, `express_t02_012_cookie_signing`, `express_t02_014_top_level_dispatch`, `express_t02_015_path_to_regexp_alias_mismatch`, `express_t02_016_send_file_streaming`, `express_t02_017_content_disposition`, `express_t02_018_type_is_alias_mismatch`, `express_t02_019_range_parser`, `express_t02_020_query_string_parsing`
- **trpc**: none

## Pairwise on the subset (Holm within each corpus, 21 pairs; significant only)

| corpus | task_type | arm_i | arm_j | delta | CI_low | CI_high | p_raw | p_holm | sig@0.05 |
|---|---|---|---|---|---|---|---|---|---|
| fastapi | T2 | arm5 | arm0 | 0.765 | 0.588 | 0.902 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | oracle | arm0 | 0.765 | 0.588 | 0.902 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | arm3 | arm0 | 0.608 | 0.412 | 0.784 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | arm5 | arm4 | 0.608 | 0.431 | 0.784 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | oracle | arm4 | 0.608 | 0.431 | 0.784 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | arm5 | arm1 | 0.529 | 0.333 | 0.725 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | oracle | arm1 | 0.529 | 0.333 | 0.725 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | arm5 | arm2 | 0.490 | 0.314 | 0.647 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | oracle | arm2 | 0.490 | 0.294 | 0.667 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | arm3 | arm4 | 0.451 | 0.275 | 0.647 | 0.0000 | 0.0000 | **yes** |
| fastapi | T2 | arm3 | arm1 | 0.373 | 0.176 | 0.588 | 0.0002 | 0.0022 | **yes** |
| fastapi | T2 | arm1 | arm0 | 0.235 | 0.098 | 0.392 | 0.0008 | 0.0072 | **yes** |
| fastapi | T2 | arm4 | arm0 | 0.157 | 0.059 | 0.255 | 0.0004 | 0.0040 | **yes** |
| django | T2 | oracle | arm4 | 0.533 | 0.350 | 0.717 | 0.0000 | 0.0000 | **yes** |
| django | T2 | oracle | arm0 | 0.517 | 0.333 | 0.700 | 0.0000 | 0.0000 | **yes** |
| django | T2 | arm5 | arm4 | 0.417 | 0.250 | 0.583 | 0.0000 | 0.0000 | **yes** |
| django | T2 | arm5 | arm0 | 0.400 | 0.200 | 0.600 | 0.0000 | 0.0000 | **yes** |
| django | T2 | oracle | arm3 | 0.383 | 0.200 | 0.567 | 0.0000 | 0.0000 | **yes** |
| django | T2 | oracle | arm1 | 0.333 | 0.167 | 0.517 | 0.0000 | 0.0000 | **yes** |
| django | T2 | oracle | arm2 | 0.333 | 0.150 | 0.517 | 0.0000 | 0.0000 | **yes** |
| django | T2 | arm5 | arm1 | 0.217 | 0.067 | 0.383 | 0.0032 | 0.0416 | **yes** |
| django | T2 | arm5 | arm2 | 0.217 | 0.067 | 0.383 | 0.0022 | 0.0308 | **yes** |
| express | T2 | oracle | arm0 | 0.727 | 0.485 | 0.939 | 0.0000 | 0.0000 | **yes** |
| express | T2 | oracle | arm3 | 0.727 | 0.485 | 0.939 | 0.0000 | 0.0000 | **yes** |
| express | T2 | oracle | arm4 | 0.697 | 0.424 | 0.939 | 0.0000 | 0.0000 | **yes** |
| express | T2 | arm5 | arm0 | 0.636 | 0.364 | 0.879 | 0.0000 | 0.0000 | **yes** |
| express | T2 | arm5 | arm3 | 0.636 | 0.364 | 0.879 | 0.0000 | 0.0000 | **yes** |
| express | T2 | arm5 | arm4 | 0.606 | 0.333 | 0.848 | 0.0000 | 0.0000 | **yes** |
| express | T2 | oracle | arm1 | 0.576 | 0.303 | 0.848 | 0.0000 | 0.0000 | **yes** |
| express | T2 | arm5 | arm1 | 0.485 | 0.212 | 0.758 | 0.0000 | 0.0000 | **yes** |
| express | T2 | arm2 | arm0 | 0.424 | 0.152 | 0.697 | 0.0020 | 0.0220 | **yes** |
| express | T2 | arm2 | arm3 | 0.424 | 0.211 | 0.667 | 0.0000 | 0.0000 | **yes** |
| express | T2 | arm2 | arm4 | 0.394 | 0.182 | 0.636 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | oracle | arm0 | 0.707 | 0.547 | 0.853 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | oracle | arm4 | 0.667 | 0.520 | 0.813 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | oracle | arm3 | 0.653 | 0.507 | 0.800 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm5 | arm0 | 0.640 | 0.467 | 0.800 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm5 | arm4 | 0.600 | 0.440 | 0.747 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm5 | arm3 | 0.587 | 0.427 | 0.733 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | oracle | arm1 | 0.507 | 0.333 | 0.680 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | oracle | arm2 | 0.493 | 0.320 | 0.653 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm5 | arm1 | 0.440 | 0.267 | 0.613 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm5 | arm2 | 0.427 | 0.253 | 0.600 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm2 | arm0 | 0.213 | 0.080 | 0.373 | 0.0002 | 0.0020 | **yes** |
| trpc | T2 | arm1 | arm0 | 0.200 | 0.080 | 0.347 | 0.0000 | 0.0000 | **yes** |
| trpc | T2 | arm1 | arm4 | 0.160 | 0.053 | 0.293 | 0.0020 | 0.0180 | **yes** |
