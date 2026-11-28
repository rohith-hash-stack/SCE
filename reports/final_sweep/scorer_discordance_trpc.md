# Scorer dissection: binary TSR vs exact-match on tRPC (gpt-4o-mini)

Data: `reports/final_sweep/full/trpc/cells.jsonl`, 250 ok cells per arm
(25 tasks × 10 seeds, budget 8000, T=0.4). Scorer code:
`benchmarks/tsr/scorer_debug.py`. The binary threshold is at
`benchmarks/final_sweep/runner.py:509-510`.

## 1. The two scorers

Both scorers share `extract_flat_symbols`, which reads the `"symbols"`
list from the JSON answer (any parse failure scores 0 under both), and
compare by bare name via `_normalize(symbol) = symbol.rsplit(".", 1)[-1]`.

**Exact-match**: `score_debug` (line 84). There is no function named
`score_strict`; this is it.

```python
def score_debug(response_text, pipeline):
    try:
        extracted = extract_flat_symbols(response_text)
    except ParseError:
        return 0.0
    return 1.0 if [_normalize(s) for s in extracted] == [_normalize(s) for s in pipeline] else 0.0
```

**Binary TSR**: `score_debug_causal` (line 152), thresholded at 1.0.

```python
def score_debug_causal(response_text, pipeline, candidate_symbols):
    try:
        extracted_raw = extract_flat_symbols(response_text)
    except ParseError:
        return 0.0
    extracted = [_normalize(s) for s in extracted_raw]
    pipeline_n = [_normalize(s) for s in pipeline]
    pipeline_set = set(pipeline_n)
    candidates_n = {_normalize(s) for s in candidate_symbols}
    illegitimate = [s for s in extracted if s not in pipeline_set and s not in candidates_n]
    if illegitimate:
        return 0.0
    return _ordered_subsequence_coverage(pipeline_n, extracted)   # LCS(pipeline, answer) / len(pipeline)

# runner.py:509-510
record.tsr_partial = score_debug_causal(call.content, pipeline, packed)
record.tsr = 1 if record.tsr_partial == 1.0 else 0
```

**The only difference.** Exact-match is list equality. Binary TSR equals 1
iff:

- (a) the pipeline is an ordered, not necessarily contiguous,
  subsequence of the answer; and
- (b) every answer symbol outside the pipeline is in the arm's own
  context.

Both use the same parsing, the same bare-name normalization, and the same
ordering requirement for pipeline stages.

**So exact-match adds exactly one condition: no extra symbols.** Every
exact pass is a binary pass. The data agree: no cell in any arm has
exact = 1 and tsr = 0.

## 2. Discordant cells (tsr = 1, exact = 0)

| arm | (tsr, exact) = (1,1) | (1,0) | (0,0) | tsr | exact | gap |
|---|---|---|---|---|---|---|
| `prism_full` | 143 | **38** | 69 | 0.724 | 0.572 | 38/250 = **0.152** |
| `ablation_signature_only` | 144 | 55 | 51 | 0.796 | 0.576 | 55/250 = 0.220 |
| `baseline_bfs_bidirectional` | 150 | 11 | 89 | 0.644 | 0.600 | 11/250 = 0.044 |
| `pragmatic_oracle` | 191 | 0 | 59 | 0.764 | 0.764 | 0 |

**All 38 `prism_full` discordant cells have one cause.** Each answer names
the full pipeline in the correct order, plus 1.05 extra symbols on
average, all present in the context.

- **Placement of the extra symbol:** after the pipeline in 26 cells,
  between pipeline stages in 12.
- **Not the cause in any cell:** ordering violations (they fail both
  scorers), alias or naming differences (same normalization), extra
  commentary text (both parse the same JSON field), or missing symbols
  (those fail both scorers).

**The extras are real causal steps the answer key omits.** In 36 of 38
cells, the extra symbol is directly called by a ground-truth pipeline
stage in the static call graph:

| task | extra symbol | cells | called by pipeline stage |
|---|---|---|---|
| t02_010 procedure_builder_use_middleware | `createBuilder` | 9 | `createNewBuilder` |
| t02_014 http_response_status_code | `getStatusCodeFromKey` | 9 | `getHTTPStatusCode` |
| t02_023 deprecated_procedure_migration | `_def` | 7 | `migrateProcedure` |
| t02_018 trpc_error_cause_normalization | `UnknownCauseError` | 6 | `getCauseFromUnknown` |
| t02_015 / t02_016 | `getHTTPStatusCodeFromError` | 5 | `getErrorShape` |
| t02_006 | `isRouter` | 1 | `recursiveGetPaths` |
| t02_011 | `isPlainObject` | 1 | `createInputMiddleware` |
| t02_015 / t02_016 | `initResponse`, `getStatusCodeFromKey` | 2 | no direct edge |

In 29 of 38 cells, the extra symbol is one that PRISM's own Turn 1 had
requested.

## 3. Three cells in full

**`trpc_t02_014_http_response_status_code`, seed 43**

- **Ground truth:** `[initResponse, getHTTPStatusCode]`
- **Turn 1 requested:** `[initResponse, getHTTPStatusCode, getStatusCodeFromKey]`
- **Raw completion:**

  ```
  {"reasoning":"The pipeline begins with the `http.resolveHTTPResponse.initResponse` function ... it calls `http.getHTTPStatusCode.getHTTPStatusCode` ... This function further relies on `http.getHTTPStatusCode.getStatusCodeFromKey` to map error codes to their corresponding HTTP status codes ...","symbols":["http.resolveHTTPResponse.initResponse","http.getHTTPStatusCode.getHTTPStatusCode","http.getHTTPStatusCode.getStatusCodeFromKey"]}
  ```

- **Exact-match 0:** `[initResponse, getHTTPStatusCode, getStatusCodeFromKey]` ≠
  `[initResponse, getHTTPStatusCode]`, because of one extra trailing element.
- **Binary 1:** `getStatusCodeFromKey` is in context and is called by
  `getHTTPStatusCode`.
- **BFS, same seed:** named only the two key stages, so exact = 1.

**`trpc_t02_010_procedure_builder_use_middleware`, seed 43**

- **Ground truth:** `[createBuilder.use, createNewBuilder]`
- **Raw completion (symbols):** `[createBuilder.use, createNewBuilder, createBuilder]`.
  The reasoning reads: "Finally, `createNewBuilder` invokes
  `createBuilder`, which constructs the new procedure builder".
- **Exact-match 0:** one extra trailing element. The source does show
  `createNewBuilder` calling `createBuilder`.
- **Binary 1.**
- **BFS, same seed:** two symbols, exact = 1.

**`trpc_t02_018_trpc_error_cause_normalization`, seed 42**

- **Ground truth:** `[TRPCError.constructor, getCauseFromUnknown, isObject]`
- **Raw completion (symbols):** `[TRPCError.constructor, getCauseFromUnknown, isObject, UnknownCauseError]`.
  The reasoning reads: "a synthetic `UnknownCauseError` is created, which
  is then returned to the `TRPCError` constructor".
- **Exact-match 0; binary 1.**
- **BFS, same seed:** gave the identical four-symbol answer, so exact = 0
  for BFS too. BFS's discordant cells come mostly from this one task (7
  of its 11).

## 4. Why the baseline gap is 4.4 pp while PRISM's is 15.2 pp

- **Not about availability.** On all 8 tasks where PRISM had discordant
  cells, the extra symbol was also in the BFS context in 10 of 10 cells.
  BFS context is larger: 12.1 symbols on average vs 4.7 for PRISM.
- **BFS answers simply didn't name the extra.** Its answers named it in
  0/10 cells on t02_010, t02_011, t02_014, t02_015 and t02_016. It did
  name it on t02_018 (10/10) and t02_023 (9/10).
- **BFS matched the annotator by being terser, not by chance.** Its
  answers are shorter (3.35 symbols vs PRISM's 3.55; key length 3.48),
  and it tends to name only the chain the task prompt describes.
- **PRISM's two-pass design makes the helpers prominent.**
  - In Turn 1 the model explicitly selects intermediate helpers (29 of 38
    extras were self-requested).
  - Turn 2 then shows those helpers in a small, focused context.
  - The model reports them as causal steps, which, per the call graph,
    they are.

  `ablation_signature_only` shows this even more strongly (55 discordant
  cells).
- **Why the oracle is immune:** its context equals the answer key, so any
  extra symbol would be outside context and zeroed under binary too.

## 5. Implications

- **The 15.2-point gap is exact-match rejecting correct extra steps.** It
  is not a ranking of retrieval quality. Every discordant PRISM answer
  recovers the full annotated chain in order.
- **The extras point to incomplete annotations.** 36 of 38 are call-graph
  successors of pipeline stages (`getStatusCodeFromKey`, `createBuilder`,
  `UnknownCauseError`). The keys for t02_010, t02_014 and t02_018 are
  arguably incomplete, not the answers wrong.
- **Exact-match is biased against richer-but-correct traces.** It
  penalizes any arm whose context invites them, which is why it changes
  arm rankings (Pitfall C). Binary containment scoring, with extras
  limited to in-context symbols, is the defensible headline metric.
  Exact-match belongs in a supplement, disclosed as a lower bound.
