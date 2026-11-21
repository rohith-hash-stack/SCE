# Query-to-evidence: requirement model, proof states and minimum sufficient evidence

**Status:** research and design only. No production change.
**Baseline:** `manual-sanity-check/test-framework-support` @ `2396594`.

Labels used throughout:

- **[M]** measured on this commit
- **[C]** read from code (with `path:line`)
- **[P]** proposal
- **[A]** assumption

| Artifact | Contents | How it was produced |
|---|---|---|
| `baseline/requirements_matrix.json` | Every gold question → its requirements → their atomic facts → the predicate each fact must satisfy | Derived from `dataset/gold.json`, which is read-only here |
| `baseline/proof_baseline.json` | The proposed proof state of every fact, requirement and question, in each of the four production-envelope modes | `proof_eval.py` run against the real `prism_slice` / `prism_blast_radius` envelopes; byte-identical over 2 runs |
| `proof_eval.py` | The evaluation prototype that produced the two files above | Evaluation code only; reuses `envelope.py` and does not touch retrieval |

To regenerate: `TIKTOKEN_CACHE_DIR=… python -m harness.experiments.query_to_evidence.design.proof_eval --out DIR`. Add `--static-only` to build the matrix without indexing.

---

## 1. Verified architecture and data flow [C, M]

```
caller (agent / CLI)
  │ seed_symbol (exact qualified name), budget, task_type, d_max
  ▼
PrismQuery            src/prism/query/schema.py:57      validation only; no natural-language parsing
  ▼
seed check            src/prism/mcp/server.py:370       exact name, or error -32002 + difflib candidates
  │                   locate_symbol_by_name (query/locate.py:59) detects ambiguity but is NOT wired in
  ├── prism.slice (server.py:510) ──► build_context_package (surface/build.py:538)
  │        ├─ pack_symbol_context (packer/submodular_knapsack.py:1483)
  │        │    a knapsack over forward weighted distance; d_max 5.0 (server.py:79)
  │        │    a method's class is promoted only if it fits the budget (:1631)
  │        └─ edges = causal edges whose two ends are both packed (build.py:691)
  │
  └── prism.blast_radius (server.py:623)
           ├─ build_candidate_manifest(direction="both", include_tests=True)  packer/candidate_index.py:295
           ├─ every caller row → retrieve_requested → build_context_package_requested (build.py:776)
           └─ upstream_callers_by_hop: the structured `callers` list; 6 hops (candidate_index.py:67,259)
                     ▼
_enforce_render_budget (build.py:429)
   container-first stubbing plus a bounded cheaper-stub search (:395)
   protected roles {seed, callee, caller} are never stubbed (:315)
                     ▼
render (surface/renderer.py)
   <warnings> <nodes>(<contract>, <body>) <edges>(:426) <causal_path>
                     ▼
answer: written by the consuming agent; Prism has no answer or abstention stage
```

### Graph content

Relation counts from a fresh index **[M]**:

| Relation | FastAPI | Django |
|---|---|---|
| `CALLS` | 4,066 | 85,571 |
| `INSTANTIATES` | 523 | 10,634 |
| `READS_STATE` | 141 | 9,064 |
| `EXTENDS` | 134 | 6,430 |
| `OVERRIDES` | 9 | 2,387 |

Other graph features **[M]**:

- **Tentative edges:** `TENTATIVE_CALL` edges exist (26 in FastAPI, 8,868 in Django).
- **Sentinel nodes:** `<ambiguous:…>` (564 / 12,508) and `<dynamic:…>` (45 / 879).
- **Runtime confirmation:** the corpora carry no runtime-confirmed edges.

How these surface in production **[C]**:

- **`READS_STATE`** (`graph/concrete_builder.py:2980`) points from a method to an indexed attribute symbol. For example, `LocMemCache.add` → `LocMemCache._lock`, an attribute symbol whose definition line is `locmem.py:23`. It is excluded from `TRAVERSABLE_RELATIONS` (`concrete_builder.py:113`), so it never reaches an envelope.
- **Edge records** (`EdgeEntry`, `surface/models.py:153`) carry type, weight, `data_flow`, `guard` and `back_edge`. They carry **no** tentative or confidence marker and **no** call line; the call line exists only as the callee's `<contract call_line>` (`models.py:122`).
- **Warnings:** 4 of the 10 `EnvelopeWarning` codes are emitted. `CoverageGap` only ever reports `no_candidate_in_budget`.
- **Proof-like states:** `SubgraphValidator` (`graph/subgraph_validator.py:81,165`) has `GroundingState` = {BOUND, EXTERNAL, DYNAMIC_UNRESOLVED, UNBOUND}. It is used only by its own test.

### Discrepancies with the prior audit

- **None in substance.** Two additions:
  1. `READS_STATE` edges and indexed attribute symbols already exist (the audit had said "class state is unreachable"; it is unreachable only because traversal excludes it).
  2. `prism.slice` defaults to `d_max=5.0`, not 6.0. The measured counts were unaffected.

---

## 2. Query requirement schema [P]

It reuses existing identifiers (qualified names, `file:line`, graph relation names) and stores references, never copies of graph data.

```
QueryRequirements
  query_id, question_type ∈ {direct_relation, multi_hop_impact, test_coverage, class_state,
                             behavioral_multi_path, negative, ambiguous, dynamic, external_evidence, compound}
  subject_candidates: [qualified_name]       # from a resolver; >1 means ambiguous
  ambiguity_constraints: {resolving_information}
  requirements: [Requirement]
  budget: tokens
Requirement
  id, kind ∈ {behavior, impact, tests, concurrency, absence, ambiguity, external}
  mandatory: bool;  subject: qualified_name
  facts: [Fact]
Fact
  type ∈ {call_edge, symbol_body, source_text, state_read, test_edge, negative_claim,
          subject_resolution, external}
  references: from/to | symbol | location (file:line) | text
  predicate: deterministic check (section 5); evidence: what can satisfy it
```

### What the current graph can answer [C, M]

| Question category | Established by | Today |
|---|---|---|
| Direct call / relationship | `CALLS` + `<edge>` + call site in body | Reliable when both ends are selected |
| Multi-hop call chain / impact | Chain of `CALLS` edges; `blast_radius` callers | Reliable via `blast_radius` within 6 hops and the budget |
| Test coverage | Test methods as `CALLS` sources (`include_tests=True`) | `blast_radius` only. `slice` and the benchmark's Arm 5 manifest exclude tests |
| Class state / lifecycle | `READS_STATE` → attribute symbol → definition line | Graph has it; **traversal and rendering do not** |
| Behaviour across several paths | Several bodies + edges | Structurally only; behaviour claims are not encoded as predicates |
| Negative (no caller / no test) | An exhaustive enumeration | **Never provable** by a bounded walk |
| Ambiguous | Symbol-table candidates | No resolver; no clarification path |
| Dynamic dispatch / reflection / external | Sentinels, tentative edges / runtime data | Present in the graph but invisible in the envelope; external data is not indexed |

---

## 3. Gold question → requirement → evidence matrix [M]

The full matrix is in `baseline/requirements_matrix.json`, covering all 24 questions, none dropped:

- 28 requirements;
- 83 atomic facts: 70 call edges, 3 symbol bodies, 3 source-text facts, 3 subject-resolution facts, 3 external-evidence facts, 1 negative claim.

**What each fact type requires:**

| Fact type | Needs | Mandatory? | Missing evidence means / does not mean |
|---|---|---|---|
| `call_edge` | edge in graph ∧ both ends selected ∧ `<edge>` rendered ∧ call-site text in the caller's full body at the gold line | yes | Missing means "not delivered". It does **not** mean the call doesn't exist; the graph has all 70 |
| `symbol_body` | indexed ∧ selected ∧ full body (`L0_full`) | yes | A stub means the detail was withheld, not absent |
| `source_text` | text inside the owning symbol's full body ∧ location within its range | yes (concurrency) | Not selected means outside traversal; it does not mean the code lacks a lock |
| `negative_claim` (q17 r2) | exhaustive search over a complete index | yes | Not finding a test does **not** prove there is none |
| `subject_resolution` (q19–q21) | exactly one candidate | yes | More than one candidate means ask the user, never guess |
| `external` (q22–q24) | an evidence source Prism indexes | yes | Means abstain; it is not a retrieval failure |

`context_symbols` are supporting, not mandatory. Behaviour prose (what a function *does*) is **not** encoded as facts. That is a limitation of the gold set (section 9).

### Per-question outcome under the proposed predicates [M]

| Class (best mode per requirement) | Questions |
|---|---|
| Provable today: seeded controls, via existing tools | q01–q08 (q06, q07 only with a warning) |
| Conditionally provable: provable once the subject is resolved | q09–q14, q16, q18 |
| Partially provable | q15 (r2: lock created in `__init__`, BOUNDED_MISSING), q17 (r2: negative claim, BOUNDED_MISSING) |
| Not provable | q19–q21 (STRUCTURAL_AMBIGUOUS), q22–q24 (INDEX_MISSING: external data) |

"Best mode" lets each requirement use whichever tool proves it. One query can therefore need `slice` for behaviour and `blast_radius` for impact. A single tool call proves fewer (see the mode table in section 7).

---

## 4. Minimum sufficient evidence selector [P]

**Input:** `QueryRequirements`, resolved candidates, budget.

1. **Resolve the subject.**
   - Run `locate_symbol_by_name` (already written, not wired in), plus file:line resolution.
   - With more than one candidate and no constraint that picks one: stop with STRUCTURAL_AMBIGUOUS and return the candidates. Never pick one.
   - Unknown name: INDEX_MISSING.
2. **Expand the facts into evidence items.** Each fact maps to the smallest set of renderable items that can satisfy it:
   - `call_edge` → the `<edge>` record, the caller's full body (its call site), and the callee as a stub or signature unless its body is itself required;
   - `source_text` / `state_read` → the owning symbol's full body, reached through `READS_STATE` → attribute → its definition line (needs `READS_STATE` traversal; today it is excluded at `concrete_builder.py:113`);
   - `test_edge` → enumerate test callers (`upstream_callers_by_hop(include_tests=True)`, `candidate_index.py:259`);
   - multi-hop → a shortest call path of `CALLS` edges (BFS bounded at 6 hops), all of whose bodies show the call.
3. **Cost.** Each item costs its rendered tokens (`node.cost`, the stub cost, plus a per-edge overhead). Items shared by several facts are counted once.
4. **Greedy coverage.**
   - Repeatedly add the item with the highest (unmet mandatory facts it completes) ÷ (marginal cost); break ties by distance, then id. This replaces proximity-only knapsack admission (`pack_symbol_context`, `submodular_knapsack.py:1483`) for query-driven retrieval.
   - Items for mandatory facts are *protected*: they are never stubbed by `_enforce_render_budget` (it already has protected roles, `build.py:315`). If protected items don't fit, report BUDGET_EXCEEDED for the affected facts rather than silently stubbing.
5. **Fill the remaining budget** with supporting context using today's ranking, unchanged.
6. **Return** the envelope plus, per fact, its state (section 5) and a `search_bounds` record:
   - hops walked;
   - whether `include_tests` was on;
   - whether the caller list was truncated (`callers_truncated`);
   - whether any dynamic or ambiguous sentinels were met on the frontier.

   Unresolved mandatory facts are listed explicitly. Bounded failure is reported as BOUNDED_MISSING, never as absence.

**Compared with today [C]:**

- `slice` ranks by forward proximity and misses multi-hop callers. Measured: 25–28 of 70 gold edges are lost because the caller is not selected.
- `blast_radius` takes every caller row, with no fact targeting, so tokens go to unrequired callers. It also cannot reach class state.

---

## 5. Proof states [P]: deterministic predicates

The states apply at three levels:
- **Fact level:** the predicates below.
- **Requirement level:** the worst state among its mandatory facts.
- **Answer level:** the worst state among its mandatory requirements.

Severity order, most blocking first: `INDEX_MISSING` > `STRUCTURAL_AMBIGUOUS` > `DYNAMIC_UNRESOLVED` > `BOUNDED_MISSING` > `BUDGET_EXCEEDED` > `PROVED_WITH_WARNING` > `PROVED`.

| State | Entry predicate | Required metadata | Resolvable by more retrieval? | Answer may |
|---|---|---|---|---|
| `PROVED` | The fact's predicate holds on the delivered envelope, the edge is static (not tentative), and no blocking warning | `<edge>` record, full caller body containing the call site, `file:line` inside the caller's range | — | state the claim |
| `PROVED_WITH_WARNING` | The predicate holds, but: the edge is `TENTATIVE_*`, the call site is not visible (an edge record only), or the envelope has `BUDGET_OVERFLOW`, `TOKENIZER_FALLBACK` or `LANGUAGE_TIER_2/3` | the warning code(s) | sometimes | state it with the qualification |
| `DYNAMIC_UNRESOLVED` | The required relation passes through a `<dynamic:…>` sentinel or `TENTATIVE_DYNAMIC_CALL`, and no static edge exists | the sentinel id and call-site location | no, static analysis can't; runtime trace data could | abstain on the claim; name the hazard |
| `STRUCTURAL_AMBIGUOUS` | More than one subject candidate, or the required call resolves to an `<ambiguous:…>` sentinel | the candidate list | no; needs clarification | ask for clarification |
| `BOUNDED_MISSING` | The evidence was not found within the recorded search bounds, or the fact is a negative claim | `search_bounds` (hops, include_tests, truncation, sentinels met) | possibly, with wider bounds | abstain; say it was not found within the bounds |
| `INDEX_MISSING` | A required symbol or relation is not in the index, or the evidence source isn't indexed (external) | what is missing | no; needs reindexing or a different source | abstain |
| `BUDGET_EXCEEDED` | The evidence was located (walked or selected) but not delivered at the needed fidelity (not packed, or stubbed) | the item, its cost, the budget | yes, with more budget | abstain on the claim; report the cost |

**Negative claims:**

- A negative claim is at best `BOUNDED_MISSING`. It may reach `PROVED_WITH_WARNING` ("no static callers") only if the walk is exhaustive over a fully indexed relation: no hop cap was reached, no truncation, no dynamic or ambiguous sentinel on the frontier, and tests were included.
- It is never `PROVED`, because dynamic dispatch can't be ruled out statically.

**The model's role:** an LLM may map a question to requirements and propose candidates. It never decides a state; the states come only from the predicates above.

---

## 6. Failure taxonomy → responsible stage [M, C]

| Failure | Stage | Gold evidence |
|---|---|---|
| No subject from the question | Query interpretation (no resolver) | q09–q24 under production inputs: 45/80 symbols |
| Ambiguous subject | Resolution | q19–q21 |
| Evidence source not indexed | Index | q22–q24 |
| Caller outside forward traversal | Selection (`slice`) | 25–28/70 edges in slice modes |
| Tests excluded | Manifest (`include_tests=False`, `candidate_index.py:302`) | q16 r2: 7 edges in slice and the Arm 5 benchmark |
| Class state not traversed | Traversal (`READS_STATE` excluded) | q15 r2 (`__init__` / `locmem.py:23`) in every mode |
| Caller walked but not packed | Budget enforcement or packing | blast_radius@4k q01: 2 edges BUDGET_EXCEEDED |
| Call site hidden by a stub | Render-budget stubbing | blast_radius@4k q15 r1: edge record only |
| Edge records dropped | Benchmark serialization (Arm 5 only) | 0/70 in the benchmark; the production envelopes do carry them |
| Tentative edge not distinguishable | Serialization (no `kind` on `EdgeEntry`) | Not exercised: all 70 gold edges are static |
| Negative claim unprovable | Inherent to bounded search | q17 r2 |

---

## 7. Evaluation methodology and baseline [M]

The method:
- runs `proof_eval.py` per mode, against the real tool envelopes;
- reports the state of every fact and requirement, plus tokens and `BUDGET_OVERFLOW`;
- keeps the Arm 5 benchmark separate and unchanged (`run.py`).

Production inputs: only q01–q08 resolve a subject today.

Requirement states per mode, over the 28 requirements (requirements outside a tool's scope are omitted, so `blast_radius` covers 16):

| Mode | PROVED | PROVED_W_WARN | BUDGET_EXC | BOUNDED_MISS | STRUCT_AMBIG | INDEX_MISS | Retrieved reqs with overflow | Median / max tokens |
|---|---|---|---|---|---|---|---|---|
| slice@4k | 3 | 7 | 0 | 12 | 3 | 3 | 14/22 | 4,454 / 10,764 |
| slice@13k | 8 | 5 | 0 | 9 | 3 | 3 | 6/22 | 6,389 / 16,577 |
| blast_radius@4k | 1 | 6 | 1 | 2 | 3 | 3 | 9/10 | 6,891 / 7,626 |
| blast_radius@13k | 9 | 0 | 0 | 1 | 3 | 3 | 0/10 | 9,546 / 12,390 |

**Gold-edge delivery** (from `envelope.py`, unchanged): edge records 39 / 45 / 37 / 43; the Arm 5 benchmark has 0/70.

**Not measured:**
- answer correctness or abstention, since no model was run;
- behaviour-claim proof, since there are no behaviour predicates in the gold set.

---

## 8. Proposed acceptance criteria for the implementation phase [P]

These are targets, not results.

1. **States computed deterministically:** the `QueryRequirements` model and per-fact states are computed deterministically and are identical over 2 runs. Nothing is marked PROVED while a mandatory fact is unresolved.
2. **Subject resolution:** wiring `locate_symbol_by_name` returns STRUCTURAL_AMBIGUOUS with candidates for q19–q21 (or their symbol-level equivalents). Exact names keep today's behaviour.
3. **Selector with subjects supplied:** every requirement in q01–q18 reaches PROVED or PROVED_WITH_WARNING at 13k in **one** call. Today this requires choosing between `slice` and `blast_radius` per requirement.
4. **Class state:** q15 r2 reaches PROVED via `READS_STATE` → `_lock` → `locmem.py:23`.
5. **Negative claims:** q17 r2 stays BOUNDED_MISSING, with `search_bounds` reported, and is never PROVED.
6. **No regression:** no regression in the existing suites; production envelopes for existing tool calls are byte-identical unless the requirement-driven path is explicitly requested.
7. **Budget:** BUDGET_EXCEEDED is reported whenever a protected item is dropped, with zero silent stub-outs of mandatory evidence.

---

## 9. Open questions and limitations

**Limitations of the gold set:**
- **Behaviour claims:** gold encodes structure (symbols, edges, a few source lines), not behaviour. Proving "what X does" needs fact predicates over body text or semantics; that is open.
- **Missing edge categories:** none of the gold edges is tentative, dynamic or ambiguous, so `PROVED_WITH_WARNING` (tentative) and `DYNAMIC_UNRESOLVED` are untested on real data. Small fixtures are needed (below).

**Limitations of this prototype:**
- It treats a caller missing from the slice selection as BOUNDED_MISSING, because slice exposes no walk trace. A real implementation should record the frontier.
- Its location check uses the symbol table, not the rendered `line`/`end_line`.

**Design questions:**
- **External evidence (q22–q24):** should it be a distinct state? It is mapped to INDEX_MISSING here.
- **Class-state traversal:** whether `READS_STATE` should be added for state facts only. It was excluded because adding it broadly regressed retrieval (`concrete_builder.py:98–107`).

**Adversarial tests to add later.** Gold reuse where possible; otherwise the smallest new fixtures, as tmp-repo tests in the style of `tests/test_knapsack_metering.py`:

| Case | Gold reuse | New fixture needed |
|---|---|---|
| Ambiguous subject name | q19–q21 | — |
| Unknown or missing index entry | a misspelled seed | — |
| Missing relationship vs. missing traversal path | an edge removed in a copy; q15 r2 | — |
| Truncated caller/test enumeration | — | a seed with >500 callers, or `max_hops=1` |
| Dynamic dispatch / unresolved attribute | — | 1 file: `getattr(obj, name)()` |
| Class state not connected by call edges | q15 r2 | — |
| Evidence dropped by budget enforcement | blast_radius@4k q01 | — |
| Explicit edge missing from serialization | the Arm 5 benchmark | — |
| Contradictory or incomplete evidence | — | an edge whose call-site text doesn't match |
| Negative claim about callers or tests | q17 r2 | — |
| Multi-requirement query, partially proven | q15, q17 | — |
| Tentative edge | — | 1 file: an override dispatch producing `TENTATIVE_CALL` |
| Ambiguous call sentinel | — | 1 file: two classes defining the same method name |

---

## 10. Staged implementation plan

| Stage | Change | Depends on |
|---|---|---|
| 1 | Move the proof-state predicates from `proof_eval.py` into a harness-level evaluator with tests: the three missing fixtures plus the gold cases | — |
| 2 | Add `kind` / `confidence` (and an optional `call_line`) to `EdgeEntry` and the renderer; emit `DYNAMIC_ATTRIBUTES_DETECTED` / `GRAPH_INCOMPLETE` when a packed node has sentinel successors. Schema version bump | 1 (to measure) |
| 3 | Wire `locate_symbol_by_name` into the MCP seed check to report ambiguity; this changes only the error payload | — |
| 4 | Fact-targeted selection behind a new request flag (section 4), including `READS_STATE` for state facts and test enumeration; report `search_bounds` | 1, 2 |
| 5 | Return per-fact states in the envelope (e.g. a `<requirements>` block) | 4 |
| 6 | Natural-language → `QueryRequirements` mapping. A model may *propose* the mapping, but the states come only from stage 1's predicates | 3, 5 |
