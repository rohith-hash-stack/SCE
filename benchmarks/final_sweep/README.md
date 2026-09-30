# PRISM final empirical sweep: 8-arm protocol

`benchmarks/final_sweep/` runs the factorial design that answers the
external audit's "cleanliness paradox" objection (K1 vs K9). It tests
whether *clean* context causes task success, as opposed to *sufficient*
context, and which PRISM components contribute.

## Protocol

| Parameter | Value | Where |
|---|---|---|
| Model | `gpt-4o-mini-2024-07-18` (pinned snapshot; served model logged per call) | `config.DEFAULT_MODEL` |
| Temperature | 0.4 for the full sweep (the tRPC pilot ran at 0.2 and showed too little seed variance) | `config.DEFAULT_TEMPERATURE` |
| Seeds | 10 per (task, arm): 42..51 | `config.DEFAULT_SEEDS` |
| Retrieval budget | 8,000 tokens | `config.DEFAULT_BUDGET` |
| Hard context ceiling | 8,800 tokens of rendered context (every drop logged) | `config.DEFAULT_TOKEN_CEILING` |
| Corpora | FastAPI (25), Django (20), Express (20), tRPC (25) debug tasks | `config.FINAL_SWEEP_REPOS` |
| Full sweep | 90 tasks x 8 arms x 10 seeds = **7,200 cells** | `kaggle/final_sweep_8arms_cell.py` |

The planned figure was 8,000 cells, which assumes 25 tasks per corpus.
Django and Express each have 20 debug tasks that pass the agreement gate,
so the full sweep is 7,200 cells.

## Arms

| engine_id | kind | What it is |
|---|---|---|
| `baseline_bfs_bidirectional` | single pass | Unpruned bidirectional AST call-graph BFS, greedy-filled to budget (floor). |
| `pragmatic_oracle` | single pass | The seed plus exactly the adjudicated `pipeline_symbols`: the strict minimal ground truth. |
| `scaffolded_oracle` | single pass | `pipeline_symbols`, then the annotated `required_context`, then the deterministic AST scaffold (below). |
| `prism_full` | two pass | Taxonomy anchor (the task's declared seed), with a 4-axis annotated manifest and k-hop (3) expansion. |
| `ablation_lexical_anchors` | two pass | Same as `prism_full`, but the anchor is the BM25 top hit for the task prompt over all callables. |
| `ablation_signature_only` | two pass | Taxonomy anchor, no 4-axis annotations anywhere. The manifest is byte-identical to the production two-pass manifest. |
| `ablation_no_purity` | two pass | `prism_full` with the Substance (purity/side-effect) axis suppressed in both the manifest and the context. |
| `prism_plus_distractors` | two pass | `prism_full`'s context plus 8 fixed, unrelated repository functions. More dose levels: `prism_plus_distractors_k<N>`. |

Notes on the definitions:

- **"Axis 2 (Purity/Side-Effects)"** maps to PRISM's **Substance** axis
  (`prism.semantics.bitmask`: bits `SINK_*`, including
  `SINK_PURE_COMPUTE`). In the codebase's own ordering it is axis 1 of
  (Substance, Form, Output, Role).
- **Axis redaction applies to both turns.** Each ablation strips its
  suppressed axis from the Turn-1 manifest, from each node's rendered
  `<features>`, and from the coverage summary.
  `ablation_signature_only` also blanks `returns.kind`, which is derived
  from the Output axis.
- **`pragmatic_oracle` differs from the legacy `oracle` engine.** The
  legacy `PragmaticOracle` also packs `boundary_symbols` and
  `required_context`. The new arm is pipeline-only, which is what makes
  the pragmatic-vs-scaffolded contrast a clean test of scaffolding.
- **The distractor arm reuses `prism_full`'s Turn-1 selection** for the
  same (task, seed), so the injected noise is the only difference between
  the two. Distractors are fixed per task, not per seed, and the sets are
  prefix-nested across doses (k=5 is the first 5 of k=10). They are
  interleaved by distance, so their position doesn't give them away, and
  the ceiling drops them first.

### The deterministic AST scaffold rule (`context_ops.ScaffoldIndex`)

For each pipeline symbol `p`, the rule collects the following, skipping
anything already in the pipeline:

1. **type**: class, interface or struct declarations whose bare name
   appears as an identifier in `p`'s own tree-sitter subtree. Comments
   and strings are excluded. When a name is ambiguous, the rule takes the
   declaration in `p`'s own module if exactly one exists there, and skips
   the name otherwise.
2. **validator**: direct CALLS/INSTANTIATES callees of `p` that carry
   `FORM_VALIDATOR`.
3. **initializer**: direct callees of `p` whose Output axis is `FACTORY`
   or `FLUENT` (router, builder or procedure construction).

Results are deduplicated in first-seen order and capped at 12. Type
declarations render from their first 40 lines.

## Per-cell record (`runner.CellRecord`; validated by `runner.validate_record`)

- **Identifiers:** `repo`, `task_id`, `engine_id`, `seed`, `budget`,
  `token_ceiling`, `status` (`ok`/`error`/`dry_run`), `error`.
- **Outcome:**
  - `tsr` is binary: 1 iff `score_debug_causal` = 1.0. That scorer is the
    deterministic pass/fail check. These debug tasks have no executable
    test suite.
  - `tsr_partial` is the raw ordered-coverage score. It is the quantity
    earlier debriefs averaged as "TSR".
  - `answer_parsed_ok` records whether the answer parsed.
- **Retrieval metrics:**
  - `cleanliness` = 1 - `fpr_gt`, against the annotated ground-truth
    universe.
  - `cleanliness_scaffold_adjusted` treats required scaffolding as
    non-noise.
  - `cpi_context` is pipeline recall in the final context.
  - `cpi_answer` is pipeline recall in the model's answer. It is the same
    formula as the legacy two-pass `cpi_end_to_end`.
  - `cpi_e2e` counts pipeline symbols that are both in context and named
    in the answer.
  - `cpi_turn1` is Turn-1 selection recall (two-pass arms only).
- **Sufficiency and truncation:**
  - `sufficiency_ratio` / `is_sufficient`: the share of required
    scaffolding present in context. Required scaffolding = resolvable
    annotated `required_context` ∪ the AST scaffold, computed identically
    for every arm.
  - `is_truncated` is true if the budget kept a wanted symbol out
    (`n_budget_dropped`), a node was downgraded below L0 by the render
    budget (`n_downgraded`), or the hard ceiling removed a node
    (`n_ceiling_dropped`).
  - `context_tokens_pre_ceiling` and `context_tokens` are also recorded.
- **Stability:**
  - `manifest_hash` is the sha256 of the exact Turn-1 manifest sent.
  - `manifest_candidate_count`, `requested_symbols`, `selected_symbols`.
  - `context_hash` is the sha256 of the rendered context.
  - `anchor_symbol`, `anchor_matches_task_seed`, `turn1_parsed_ok`,
    `turn1_degenerate`, `turn1_reused_from`.
- **LLM metadata:**
  - `system_fingerprint` comes from the answer call. `system_fingerprints`
    and `response_models` cover all calls.
  - `prompt_tokens`, `completion_tokens`, `latency_s` and `cost_usd` are
    summed over the cell's calls. `calls[]` holds the per-call breakdown.
  - The raw `turn1_response` and `answer_response` are kept.

Output is `cells.jsonl` (the append-only checkpoint), `cells.parquet`
(list and dict columns JSON-encoded), `summary.{json,md}` and
`run_config.json`.

## Running

```bash
# zero-cost wiring check: real retrieval for all arms, no LLM calls
python -m benchmarks.final_sweep.runner --repo trpc --dry-run --out /tmp/dry --fresh

# 2-seed pilot on tRPC (400 cells)
OPENAI_API_KEY=... python -m benchmarks.final_sweep.runner --repo trpc --seeds 42,43 \
    --out reports/final_sweep/pilot_trpc --workers 8 --max-cost-usd 5

# re-summarize
python -m benchmarks.final_sweep.runner --out reports/final_sweep/pilot_trpc --summarize-only
```

Runs are resumable by default: cells with an `ok` record are skipped, and
`error` cells are retried. Pass `--fresh` to start over. For the full
sweep, use `kaggle/final_sweep_8arms_cell.py`.
