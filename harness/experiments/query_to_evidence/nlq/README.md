# NLQ P0 and P1: evaluation substrate and deterministic evidence planning

**Research harness only.** Nothing here is imported by `src/prism`. No model is called.

> **What these results are, and are not.** Every interpretation used here is gold-derived (an oracle):
> subjects, requirement kinds, scope limits, ambiguity and evidence sources all come from the gold annotations.
>
> - **Measured:** proof states (from the real tools and the existing predicates), budgets, and lexical recall.
> - **Oracle self-checks only:** every decision, clarification, abstention, answerability, decomposition and
>   routing result is true by construction. These are flagged `"oracle_self_check": true` (with a `caveat`)
>   in the JSON output and printed with `[ORACLE SELF-CHECK]`.
>
> Nothing here measures end-to-end natural-language question understanding.

This is milestone P0 of the investigation report on natural-language question understanding. Its purpose is to make later architectural changes measurable *before* production retrieval changes. It contains:

- versioned record schemas;
- gold-derived interpretation fixtures for the 24 questions;
- a tested lexical candidate search;
- metric scorers.

## Commands

```
python -m pytest tests/test_nlq_p0.py -m "not slow"          # fast: schemas, fixtures, scorers, recorded numbers
TIKTOKEN_CACHE_DIR=... python -m pytest tests/test_nlq_p0.py -m slow   # builds FastAPI + Django (~5 min)
python -m harness.experiments.query_to_evidence.nlq.gold_fixtures --check
python -m harness.experiments.query_to_evidence.nlq.run --out <scratch dir> [--index]
```

`run.py` refuses to write into `design/baseline/`, `dataset/` or `reports/`.

## Files

| file | role |
|---|---|
| `schema.py` | Validators for three versioned, closed JSON records: `QuestionInterpretation`, `EvidencePlan` and `ProofReport` (`schema_version` 1) |
| `gold_fixtures.py`, `fixtures/gold_interpretations.json` | Oracle interpretations of all 24 questions, built from gold plus a small hand annotation table. The file must equal the builder's output (`--check`) |
| `lexical.py` | BM25 subject search over the existing symbol index, plus 1-hop graph expansion, extracted from the investigation's read-only probes |
| `lexical_eval.py`, `fixtures/lexical_recorded.json` | Runs the probe measurement through `lexical.py` and diffs it field by field against the recorded probe output |
| `baseline_records.py` | Re-shapes the committed `proof_baseline.json` into `EvidencePlan` + `ProofReport` records. Read-only, losslessly; nothing is re-run |
| `metrics.py` | M1–M13 scorers. Each result is `measured` (numerator/denominator) or `not_measured` (with a reason, never 0); each is marked `deterministic` or `judgment` |
| `run.py` | Validates everything and writes the metric report to a scratch directory |

## Schemas

The vocabulary is reused from the existing evaluation:

- requirement kinds and fact types come from `design/baseline/requirements_matrix.json`;
- proof states, the worst-state rule and the state → answer contract (`state` / `qualify` / `abstain`) are imported from `design/proof_eval.py`.

**What each record does:**

- **`QuestionInterpretation`** is what the question asks. It never contains a proof state; the record is closed, so a stray `state` field is rejected. It holds:
  - answerability: `answerable` / `needs_clarification` / `unanswerable`;
  - one entry per requirement, each with:
    - a subject `mention`, which must be a verbatim span of the question;
    - `proposed_names`, which are never trusted until resolved against the index;
    - an optional `refers_to` (q15 r2, "those code paths");
    - `scope` and `evidence_source`;
  - an optional `clarification` listing interpretations, each in-repo (names symbols) or external (names none).
- **`EvidencePlan`** records, per requirement:
  - **resolution:** `resolved` / `ambiguous` / `not_found` / `not_applicable`, with `match_count` and `truncated` exactly as the MCP ambiguity payload has them;
  - **retrieval op:** `slice` / `blast_radius` / `none`, or `unsupported` with the missing capability;
  - **decision:** `retrieve` / `clarify` / `abstain`.

  Its rules forbid guessing:
  - retrieval needs a resolved subject;
  - `clarify` and `abstain` plans must not retrieve.
- **`ProofReport`** gives every fact's state and `why`. Each requirement's state is the worst of its facts; out-of-scope requirements have no state; the answer contract follows the state.

**Two concepts are new.** Gold questions require them, and each is checked against the question text:

- `scope.role` (`production` / `tests`) and `scope.direct_only`, used only by:
  - q16 r1 "Which production code";
  - q16 r2 "which tests exercise that helper directly";
  - q17 r2 "which tests call that splitting function directly".

  A scope limit must quote its `evidence_span` from the question.
- `evidence_source`: `static_code`, `runtime_telemetry` (q22, q24), `deployment_or_business_data` (q23), or `external_service` (the q21 memcached/redis interpretation).

**Left out deliberately** (no demonstrated use in the 24 questions):

- `search_terms`;
- class/module hints;
- `partially_answerable`;
- unsupported-premise lists;
- `search_bounds`.

They belong to P1–P3, when something produces or consumes them.

## Gold-derived fixtures

These are **not evidence of natural-language understanding.** They are what a perfect interpreter would output; they exist to score later stages in isolation.

**What is mechanical:** requirement ids, kinds, mandatory flags and subjects come from the matrix; answerability, interpretations and reasons come from gold.

**What is hand-annotated:** only `ANNOTATIONS` in `gold_fixtures.py`, namely the mention spans, the three scope limits and the three external evidence sources.

**Atomic facts are not copied.** `load()` joins each fixture to its matrix entry by question and requirement id. It raises if a requirement is dropped, added, re-kinded, re-subjected or made optional. All 28 requirements and 83 facts are covered.

## Lexical search (reproduced, not re-tuned)

The constants, tokenizer, stop words, stemming, BM25 parameters (k1 = 1.2, b = 0.75), candidate pool, tie-break and expansion relations are exactly those of the probes:

- **Pool:** non-attribute, non-test symbols, in symbol-table order.
- **Ties:** broken by pool position.
- **Unmatched symbols:** have no rank.
- **Expansion:** follows `CALLS` / `INSTANTIATES` / `OVERRIDES` in both directions, within the pool.

Re-measured on fresh indexes, all 24 questions match `fixtures/lexical_recorded.json` field for field (ranks, top 10, interpretation ranks, expansion sizes, found flags).

**Recorded results, on the 10 questions that name no symbol (q09–q18):**

| | questions | subjects |
|---|---|---|
| gold subject ranked ≤ 10 (either variant) | 4/10 | 4/11 |
| in the 1-hop-expanded set at k = 10 | 6/10 | 6/11 |
| in the 1-hop-expanded set at k = 20 | 6/10 | 6/11 |
| in the 1-hop-expanded set at k = 50 | 9/10 | 9/11 |

- **Expanded-set sizes at k = 50** reach 819 candidates (q18).
- **q15** (`evicts` vs `_cull`) is never found at any k.

**Path sensitivity.** `SymbolInfo.file` is absolute, so the recorded documents contain path tokens of the checkout the probes ran in (`/home/user/SCE/.benchmarks/corpora/<corpus>`, recorded as `lexical_eval.RECORDED_CORPUS_ROOTS`).

- **Reproducing anywhere:** `lexical_eval.recorded_index` re-expresses every file path under that recorded root (`LexicalIndex(path_tokens_root=...)`). The documents are then token-for-token the recorded ones wherever the corpora are checked out. At the recording location this is the identity. The recorded fixture is unchanged.
- **A different variant:** `relative_paths=True` drops the root entirely. It does not reproduce the recorded ranks (50 fields differ).

## Metrics

| id | what | method | P0 status |
|---|---|---|---|
| M1, M2, M5a/b, M6 | interpretation vs gold | deterministic | Implemented. On the oracle fixtures these are **ORACLE SELF-CHECKS** (perfect by construction; `oracle_self_check: true` in `p0_metrics.json`), not measurements of understanding |
| M2-gate | retrievals planned for unanswerable questions | deterministic | Measured on baseline plans: 0/3 violations in each mode |
| M3 | subject / interpretation candidate recall (lexical floor) | deterministic | Measured (table above; interpretations 6/12 in the top 20) |
| M4 | subject precision | deterministic | Not measured: every resolution in the baseline is a gold counterfactual |
| M5c | clarification payload covers each in-repo interpretation | deterministic | Measured with `--index` on today's MCP payload |
| M7 | fact recall of derived facts | deterministic | Not measured: no fact-derivation stage yet |
| M8 | proof states | deterministic | Per mode, and best mode per requirement (20/28). "One planned run" not measured (needs P1) |
| M9 | evidence precision | deterministic + judgment | Not measured: the baseline has no per-item accounting |
| M10 | proof-state correctness | judgment | Not measured |
| M11 | answer support | judgment | Not measured (needs model answers) |
| M12 | budget overflow; interpretation cost | deterministic | Overflow measured (29/64 over all modes); interpretation cost not measured |
| M13 | byte-identical reruns | deterministic | Measured |

## P1: deterministic evidence planning

| file | role |
|---|---|
| `planner.py` | Turns a `QuestionInterpretation` into an `EvidencePlan` using fixed rules (module docstring) |
| `plan_exec.py` | Runs each distinct call through the real `prism_slice` / `prism_blast_radius`, then scores facts with `proof_eval.fact_state` |
| `p1_run.py` | Plans and executes all 24 questions, compares the result with the four fixed modes, and writes to `--out` |

```
TIKTOKEN_CACHE_DIR=... python -m harness.experiments.query_to_evidence.nlq.p1_run --out <scratch dir> [--replay]
python -m pytest tests/test_nlq_p1.py [-m slow]
```

### Planner rules

- **Operation by kind:**

  | kind | operation |
  |---|---|
  | behavior | `prism.slice` (task_type `debug`) |
  | impact, tests | `prism.blast_radius` |
  | concurrency | unsupported (`state_reads_and_definition`) |
  | absence | unsupported (`exhaustive_absence`) |
  | ambiguity | the question is clarified |
  | external | the question is abstained |

- **Budget:** 13,000 tokens per call.
- **Shared calls:** identical calls are shared.
- **No cross-crediting:** each requirement is scored only against its own call's envelope.

**Scope limits** are applied only where a tool argument enforces them. Verified in `server.py` and `candidate_index._upstream_walk`:

- **Enforced:** `role=production` on `blast_radius` becomes `include_tests=False`, so test callers are neither walked nor packed.
- **Not enforceable, recorded per requirement:**
  - `role=tests`: there is no tests-only option, and `include_tests=True` also returns production callers;
  - `direct_only`: `blast_radius` walks 6 hops and has no hop argument;
  - any limit on `slice`.

An unenforced limit changes no fact predicate, and the proof state is left as measured. It still matters: for a scoped claim such as q16 r2 ("which tests exercise that helper **directly**"), a PROVED state means only that the required direct test edges were delivered. The envelope also holds production callers and indirect tests, so the scoped claim itself was not established.

That is why every requirement in a planned `ProofReport` carries `scope_compliance` next to its state:

| `status` | meaning |
|---|---|
| `no_limits` | the question stated no scope limit |
| `enforced` | every stated limit was enforced by a tool argument |
| `not_enforced` | at least one limit could not be enforced; the limits are listed in `not_enforced` |
| `not_applicable` | nothing was retrieved |

`M8-planned` (PROVED or PROVED_WITH_WARNING) is reported next to `M8-planned-scope-compliant`, which excludes `not_enforced` and any requirement whose compliance was not recorded. On the gold set the two are 20/28 and 19/28; the difference is q16 r2, listed in `proved_with_unenforced_scope`. Fixed-mode replays have no scope information, so their reports omit the field (it is optional in the schema).

**Subjects** resolve the same way the MCP seed check does (`qualified_name_matches`). One match resolves the subject; more than one means clarify; none means abstain.

**Replay equivalence.** `plan_exec.replay_matches_baseline` re-executes the P0 fixed-mode plans through the same executor. They must equal `proof_baseline.json`: every requirement state, fact state, `why`, token count and overflow flag.

### Reading P1 results

`p1_result.json` starts with a `characterization` statement: P1 is deterministic planning and retrieval **given oracle interpretations**.

- **Oracle self-checks** (flagged `oracle_self_check: true`, printed `[ORACLE SELF-CHECK]`): `M2-gate-planned`, `M5a-planned`, `M5b-planned`, `M2-planned-abstain`, and the routing comparison (`comparison.routing`).
  - Clarify and abstain follow from the gold `ambiguity` and `external` kinds.
  - Routing follows from the gold requirement kind, which also scoped the fixed modes.
  - So "planned matches the best fixed mode" is expected by construction for routing.
- **Measured:** the proof states behind `M8-planned` and `M8-planned-scope-compliant`. Every P1 metric still records `interpretation_source: "oracle_gold_derived"`.
