# Query-to-evidence evaluation (diagnostic only)

A small, reproducible evaluation that follows each question from query/subject
resolution through to answer grounding, and records the stage where each
required symbol or relationship is first lost. Nothing here changes Prism's
retrieval. The tracer only records what the existing stages return, and the
equivalence check below proves this for every traced run.

**What this does not show:** that Prism can answer arbitrary queries. The set
has 24 questions on two corpora, and most retrieval numbers come from a
counterfactual that supplies the gold subject.

```
TIKTOKEN_CACHE_DIR=<dir with cl100k> python -m harness.experiments.query_to_evidence.run
    -> reports/query_to_evidence/{results.json,report.md}
python -m pytest tests/test_query_to_evidence.py        # 37 tests; 5 marked slow (real FastAPI index)
```

The run is offline, calls no model, and takes about 6 minutes, most of it indexing Django.

## Files

| file | role |
|---|---|
| `dataset/questions.json` | What a system under test may see: id, corpus, category, query text. Seeded controls also get the inputs the existing benchmark already hands Arm 5 (`seed_symbol`, `task_type`). |
| `dataset/gold.json` | Annotations, read only by the scorer: answerability, expected behaviour, requirements, relationship paths, source evidence, ambiguity, unanswerable reason, provenance. |
| `dataset/manual_grounding.json` | A **labelled manual subset**: the prose claims in the 8 stored control answers, checked by hand against the stored model input. |
| `dataset.py` | Loader and validator: schema, category counts, query/gold separation, leakage of gold names into queries; with an index, also symbols indexed, edge endpoints, call-site text and caller line range. |
| `trace.py` | `Tracer` shadows `build_candidate_manifest` / `retrieve_requested` on the engine *instance* and records their results. `run_case` repeats `Pipeline.run_cell`'s order: `arm.retrieve`, then `finalize_context`, then `build_prompt`. |
| `accounting.py` | Per-symbol stage states, first loss and secondary causes. Per-edge graph status and delivery. |
| `grading.py` | Answer correctness and claim grounding for stored completions. |
| `run.py`, `report.py` | Modes, baseline comparison, equivalence check, metrics, report. |

## Dataset (24 questions: FastAPI 10, Django 14)

| category | n | ids | gold source |
|---|---|---|---|
| seeded controls | 8 | q01-q08 | Existing adjudicated tasks, with exact query text and inputs: 4 T5 (R0 run, `bcb1197`) and 4 T2 (M4 run, `e69f0ce`). |
| unseeded behavioural | 6 | q09-q14 | Existing T2 gold. The query is rewritten to describe the behaviour without naming a symbol. |
| compound | 4 | q15-q18 | Read from source at the pinned corpus commit. One requirement per independently answerable part. |
| ambiguous | 3 | q19-q21 | Interpretations, each with the symbols it would need, plus what would resolve the ambiguity. Expected behaviour: clarify. |
| unanswerable | 3 | q22-q24 | Why the repository cannot answer (runtime telemetry or customer data). Expected behaviour: abstain. |

Relationship paths are call sites with `file:line` and the exact source text.
The validator checks each one against the source.

Two nested functions that are both named `app` (in `fastapi/routing.py`) are
gold by location (`required_symbols_by_location`) and mapped to index entries
when the evaluation runs.

**Correction made during the work:** a gold edge in q04
(`TemplateResponse.__init__ → HttpResponse.__init__`) was wrong. The
`super().__init__(` call resolves by MRO to `SimpleTemplateResponse.__init__`;
Prism's graph had it right and the first run flagged it. The correction is
recorded in the question's `provenance.corrections`.

**Incomplete cases:**

- q17 r2 is an `absence` requirement: there are no direct tests of
  `get_authorization_scheme_param` (`rg` over `tests/` finds 0). The pipeline has
  no way to report absence, so it is reported as n/a, not graded.
- q21's memcached/redis interpretation has no in-repo symbol to check, because
  eviction is delegated to the external server.

## Modes

- **production**: each question's own inputs.
  - Prism has no natural-language entry point. Every non-control question
    therefore stops at subject resolution (`no_seed`, empty context).
  - That is the measured production outcome, not a scoring artefact.
- **counterfactual** (not a production path): the gold `subject` of each
  requirement is supplied as the seed. This measures the later stages given a
  perfect resolver. Turn 1 is:
  - on T5: the production rule selector (R0, every caller row);
  - elsewhere: an every-row upper bound (`AllRowsLLM`, no model).
- **stored cell**: the stored s42 bundle of each control, accounted as it was
  sent to the model. It has no trace, so manifest and hydration show as `not
  measured`.

All three modes above measure the **Arm 5 benchmark prompt**. Arm 5 sends
each node as a header plus body and drops the rest of Prism's envelope
(`<edges>`, `<contract>`, `<warnings>`, `<causal_path>`). So "edge record
delivered" is always 0 there by construction.

### Production-envelope modes (`envelope.py`)

`slice@4k`, `slice@13k`, `blast_radius@4k` and `blast_radius@13k` call the
real MCP tool functions `prism.mcp.server.prism_slice` and
`prism_blast_radius`, with production defaults apart from the seed (the gold
subject), the budget and, for slice, `task_type` (T5 → `blast`, T2 →
`debug`).

- The harness's in-memory index is registered in the server's `GraphCache`
  for the call. Those two tools read only `builder`, `contracts` and
  `repo_root`.
- The rendered XML is parsed using its real attribute names (`<edge from to
  type weight data_flow guard back_edge>`, `<contract target_id call_line>`).
- For each gold edge the harness reports, independently: in graph, both ends
  selected, `<edge>` record present, contract on callee, call site in full
  caller body, and warnings.
- `first_loss` separates `caller_not_selected` / `callee_not_selected` from
  `not_in_graph` and `both_selected_no_record`.
- Denominators: slice uses every requirement with gold edges (70 edges);
  blast_radius uses impact requirements only (43).
- `BUDGET_OVERFLOW` is counted once per envelope.
- Run by default from `run.py` (`--skip-envelope-modes` to skip). Results go
  under `delivery_modes`, and `report.md` gets a "Gold-edge delivery by mode"
  table.

## Stage accounting

Per required symbol, in pipeline order:

1. resolution
2. index
3. manifest
4. selection (requested, or auto-included by hydration)
5. hydration (a node of the Turn-2 package)
6. arm (an Arm 5 item)
7. budget (kept by `finalize_context`)
8. delivery (present in the model input, as a full body or a stub)

The first stage that fails is the **first loss**. Its state is one of:
`not_resolved_from_query`, `not_indexed`, `not_discovered_in_manifest`,
`requested_but_absent_from_manifest`, `discovered_not_selected`,
`selected_not_hydrated`, `hydrated_then_excluded`, `lost_to_budget`,
`delivered_full` or `delivered_stub`.

Secondary causes are kept:

- `auto_included_by_hydration`
- `compressed_to_stub`
- for manifest losses, a read-only probe over the call graph gives one of:
  - `filtered_as_test_code` (`SymbolRole.VERIFICATION`)
  - `no_call_path_to_or_from_subject`
  - `caller_beyond_upstream_bound` / `callee_beyond_downstream_bound`
    (Prism's own `UPSTREAM_WALK_MAX_HOPS` / `CANDIDATE_INDEX_MAX_HOPS`)
  - `within_bounds_but_not_admitted`

A stage that cannot be observed is `None`, reported as `not measured`. It is
never counted as a loss.

### Edges

The graph status uses Prism's own vocabulary:

- static
- `tentative` (`TENTATIVE_CALL` / `TENTATIVE_DYNAMIC_CALL` kinds, kept with their kind)
- `confirmed_runtime`
- `ambiguous_sentinel` (`<ambiguous:NAME@…>`)
- `resolved_to_other_target`
- `unresolved`
- `endpoint_not_indexed`

Delivery is tracked separately at three points:

- both endpoints are in the manifest;
- the edge is constructed in the hydrated package;
- the call-site text is in the model input (inside the caller's full body).

Whether an *edge record* reaches the model is also checked. Arm 5 never sends
edge records.

### Outcomes by category

- **Ambiguous**: reported as `not_resolved_from_query`, because no
  clarification mechanism exists. The report shows whether each
  interpretation is indexed.
- **Unanswerable**: classified as `expected_absence`, not as a retrieval
  failure. Abstention is `not measured` (no model run, and there is no
  production abstention path).

## Answer grading

Only stored completions are graded, and always against the stored model input
that produced them.

Each named symbol is a claim:

- **T5** ("X is impacted"): supported only if a chain of Prism call edges runs
  X → … → subject, with every caller's full body delivered and the call
  visible in it.
- **T2**: supported if X's full body was delivered.

A gold symbol that is named but arrived only as a stub, or not at all, is
reported as `correct_but_unsupported`. The subject itself is reported
(`subject_named`) but not counted as a claim.

The prose `reasoning` field is not graded automatically. See the manual subset.

Correctness metrics (symbol recall and precision) and grounding metrics are
reported separately from retrieval metrics.

## Baseline, determinism, equivalence

### Controls

| | T5 controls | T2 controls |
|---|---|---|
| Replay | R0 rule (no model in Turn 1) | Stored Turn-1 text replayed |
| Run on | Current HEAD | Current HEAD |
| Compared with | Stored s42 cells | Stored s42 cells |

The comparison covers requested symbols, delivered symbols, stubs, item text
and the exact prompt string. All 8 are identical.

### Token counts

The harness trim re-counts tokens with Qwen, which is not available offline.
`Cl100kCounter` (exact cl100k) stands in. Pre-trim token counts are therefore
shown side by side (Qwen stored, cl100k replay) and not compared at ±0.01:
the definitions differ. No control is near the 13,000-token limit (largest
7,241 cl100k / 7,309 Qwen), so the trim drops nothing either way.

### Index

The index is built in memory (`build_pipeline(use_cache=False)` and
`compute_contracts`), the same two calls `PrismEngine.from_repo` makes. This
avoids stale on-disk caches.

### Tracer on vs off

Each run is repeated with the tracer on and off. The two runs give identical:

- requested symbols
- raw and kept items (symbols, kind, content, token counts)
- budget drops
- model input

Result: 21 of 21 runs identical.

## Limitations

- **Dataset size and scope**: 24 questions and 2 corpora (Python only).
  Category numbers are small-n descriptions, not estimates.
- **Counterfactual for T2**: uses an every-row Turn 1, an upper bound on
  selection. Real Turn-1 selection loss is measured only on the 4 T2 controls
  (the stored model picks).
- **Prose claims**: only an 11-claim manual subset is graded.
- **T5 support rule**: a lexical check (callee name followed by `(` in the
  delivered body) on top of Prism's own call edges.
- **Clarification and abstention**: not measured, because no model was run.
  There is also no production path that could produce them.
- **Latency**: offline retrieval only. The first Django case includes one-time
  lazy work (about 66 s).
