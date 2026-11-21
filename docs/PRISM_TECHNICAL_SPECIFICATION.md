# Project Prism: Complete Technical Specification

| | |
|---|---|
| **Audience** | New engineers, maintainers, advanced users |
| **Scope** | The `prism` package (`src/prism/`, ~30,400 lines of Python), its CLI, its MCP server, and the evaluation harness (`harness/`) used to measure it |
| **Source of truth** | The code on branch `harness/eight-arm-slm` (commit `58d107e` and later), `docs/design_formalism.md`, and the measured reports under `reports/` |
| **Status** | Living document. Each section says what is **implemented** and what is **proposed**. |

---

## 0. How to read this document

Several topics a reader may expect from a "code-graph engine" are **not implemented** in Prism, for example:

- a memory-mapped CSR graph;
- bit-packed edge structs;
- a delta store with compaction;
- named Tier 1/2/3 blast thresholds.

Prism is a Python system built on `networkx` and SQLite. To stay accurate, every section uses three status labels:

| Label | Meaning |
|---|---|
| **[IMPLEMENTED]** | Exists in the code. Names, constants and equations are copied from the source. |
| **[PARTIAL]** | Some of the behaviour exists. The text says which part. |
| **[PROPOSED]** | Does not exist. The text gives the most likely specification consistent with the existing design, so it can be built later. Nothing labelled [PROPOSED] should be cited as a property of the current system. |

Every assumption is written out as **Assumption:** in place.

Notation:
- $G_C=(V,E)$ is the concrete graph.
- $u\to v$ is a directed edge from caller/source $u$ to callee/target $v$.
- $s$ is the seed symbol.
- $B$ is a token budget.
- Python module paths are relative to `src/`.

---

## 1. Executive summary

### 1.1 What Prism is

Prism is a **context-slicing engine for code**. Given:
- a repository,
- a *seed* symbol (a function, method or class), and
- a token budget,

it returns the smallest, most relevant set of surrounding code needed to understand or safely change the seed. Each symbol is rendered at the cheapest **compression level** that still conveys what the reader needs:
- full source;
- pruned body;
- control-flow skeleton;
- interface stub.

It is designed to be called by LLM coding agents, through a CLI or an MCP server, in place of grep or reading whole files.

### 1.2 What it computes

1. **A concrete multi-relational code graph** $G_C$.
   - Built from tree-sitter CSTs (plus Python's native `ast`) for Python, TypeScript/TSX, JavaScript, Go, Java and C#.
   - Edges are `CALLS`, `INSTANTIATES`, `EXTENDS`, `IMPLEMENTS`, `OVERRIDES`, `EMBEDS` and `READS_STATE`, each with rich call-site metadata.
2. **Semantic annotations.**
   - Rule-based tags (`#db_write`, `#route_handler`, `#auth_guard`, …) with a metamodel of required orderings (`REQUIRES_BEFORE`).
   - A four-axis 64-bit feature mask (Substance / Form / Output / Role).
3. **Distances.** There are two independent metrics.
   - $D_{hybrid}$, a bounded blend of weighted hop distance and tag dissimilarity (the classic `prism query` path).
   - A **causal Dijkstra** distance whose edge costs are $1/W$, where $W$ is boosted by data-flow and guard evidence (the v1.1 "agent surface" path).
4. **Budgeted selection.** There are two packers.
   - A 0/1 multi-tier knapsack with swap refinement and a strict budget invariant.
   - A submodular, precedence-constrained greedy knapsack with an **upstream blast-radius frontier**.
5. **Runtime feedback.**
   - Traces from `sys.settrace` or OpenTelemetry are reconciled against $G_C$.
   - Confirmed edges get cheaper, unobserved edges more expensive, and discovered edges are added.
   - Every unmatched event is classified ("zero silent orphans").
6. **A two-pass retrieval protocol for LLMs** (`PrismEngine`).
   - Turn 1 presents a candidate manifest (one row per symbol).
   - Turn 2 hydrates the requested symbols under a budget.

### 1.3 Headline measured results

All results are from repository reports. See §10.

| Result | Value | Source |
|---|---|---|
| Call-edge recall vs runtime ground truth, Django | 0.590 → **0.707** after the universal call-resolution fixes | `reports/harness_m4/analysis/call_resolution_and_design_c.md` |
| Call-edge recall, FastAPI | 0.991 (unchanged) | same |
| T5 (blast-radius) gold reachable in the manifest with Design C | 0.30 → **0.89** | same |
| T5 task success rate (TSR), pipeline as shipped (R1) vs M4 baseline | e.g. tRPC 0.151 → 0.372, Django 0.190 → 0.407 | `reports/harness_r0r1/R0_vs_R1_T5.md` |
| T5 TSR, deterministic caller rule (R0) vs LLM Turn-1 selection (R1) | R0 ahead in all 4 corpora; significant only on tRPC (+0.294, $p_{holm}<0.001$) | same |
| Warm re-index, `gin-gonic/gin` (1,474 symbols) | 3.8 s cold → **0.14 s** warm | `docs/design_formalism.md` §5.6 |
| Fuzzy "did you mean" seed suggestion, ~50k symbols | < 50 ms (hard requirement) | `docs/design_formalism.md` §9 |

The most important recent finding: **for blast-radius queries, the deterministic caller slice is at least as good as an LLM choosing from it.** The value is in the graph and the manifest, not in the Turn-1 model call.

### 1.4 What Prism is not

- It is **not** a whole-program type checker. Python gets instance binding through constructor and return-type inference. Other languages get lexical, structural and receiver resolution. Some calls are linked only as `TENTATIVE_CALL`.
- It is **not** a compiled, memory-mapped graph database. The in-memory model is `networkx.DiGraph`, and persistence is SQLite holding JSON (§6).
- It does **not** re-link incrementally per file. Any source change triggers a full rebuild, by design, for correctness (§7).

---

## 2. System architecture

### 2.1 Package map [IMPLEMENTED]

| Package | Key modules | Responsibility |
|---|---|---|
| `prism.scanner`, `prism.parser` | `tree_sitter_loader`, `lang_config`, `queries`, `cache` | File discovery, language detection, CST parsing, per-language node-type tables |
| `prism.graph` | `concrete_builder` (3.9k LOC), `symbol_table`, `call_site`, `hierarchy`, `contracts`, `effects_rules`, `flows`, `guard`, `metamodel`, `weights`, `subsystems`, `repository_profile`, `subgraph_validator`, `symbol_archetype` | Two-pass graph construction, symbol and export registries, inheritance, behavioural contracts, metamodel |
| `prism.tagger` | `engine`, `rules` | Semantic tags per symbol (tag matrix $M$) |
| `prism.semantics` | `bitmask`, `substance`, `form`, `output`, `role`, `canonical_sinks`, `extractor` | Four-axis 64-bit feature masks |
| `prism.slicer` | `distance`, `knapsack` (970), `compressor`, `universal_slicer`, `tokenizer`, `semantic_topology_score`, `blueprint` | $D_{hybrid}$, the 0/1 knapsack, L0–L3 compression, token counting |
| `prism.traversal` | `causal_weights`, `continuous_dijkstra`, `data_flow_{py,go,ts}`, `_data_flow_common`, `_cache_keys` | Causal edge weights, synthetic edges, weighted Dijkstra |
| `prism.packer` | `submodular_knapsack` (1.9k), `blast_radius`, `candidate_index` | v1.1 packer, upstream frontier, Turn-1 manifest |
| `prism.engine` | `PrismEngine` (792) | Two-pass engine facade used by the harness and agents |
| `prism.surface` | `models`, `build`, `renderer`, `parser`, `causal_path` | The `<prism_context>` envelope (pydantic models, deterministic XML/JSON) |
| `prism.runtime` | `tracer`, `trace_ingester`, `trace_validator`, `reconciler` (928), `index_cache` (581), `flow_cache`, `contract_cache` | Runtime traces, reconciliation, whole-repo index cache |
| `prism.cache` | `sqlite_cache` | Per-file feature cache `file_cache_v2` |
| `prism.mcp` | `server` (609), `cache`, `auth`, `security` | MCP server, per-repo graph cache, API keys, rate limits, input validation |
| `prism.query` | `schema`, `locate`, `tag_filter`, `errors` | `PrismQuery` request model, symbol location |
| `prism.external` | `index`, `locator_ts` | Locating third-party dependency stubs/sources ("external" role) |
| `prism.analysis` | `flow_engine`, `hybrid_engine` | Flow analyses used by contracts and causal paths |
| `prism.cli` | `prism index / query / causal-query / trace / mcp` | Command line |

### 2.2 Data flow

```
                ┌──────────────┐
 repo files ──▶ │ scanner      │  discover_files(), language tier filter
                └──────┬───────┘
                       ▼
                ┌──────────────┐  tree-sitter CST (+ Python ast)
                │ parser       │
                └──────┬───────┘
                       ▼
   ┌───────────────────────────────────────────┐
   │ ConcreteGraphBuilder                      │
   │  Pass 1: collect definitions → SymbolTable│
   │  Pass 2: resolve calls/heritage → G_C     │
   └──────┬────────────────────────────┬───────┘
          ▼                            ▼
   TaggingEngine (tag matrix M)   ContractExtractor (contracts)
          │                            │
          ├───────────▶ index_cache (.prism/cache/index.db) ◀── SHA-256 per file
          ▼
   ┌───────────────── read path (stateless) ─────────────────┐
   │ Classic:  DistanceEngine(D_hybrid) → ContextKnapsackPacker│
   │           → ASTCompressor/UniversalSlicer → Markdown      │
   │ v1.1:     causal_weights → continuous_dijkstra →          │
   │           select_submodular_context (+ blast_radius) →    │
   │           ContextPackage → <prism_context> XML / JSON     │
   │ Two-pass: build_candidate_manifest → (LLM / rule) →       │
   │           retrieve_requested                              │
   └─────────────────────────────────────────────────────────┘
          ▲
   prism trace → .prism/traces/run_*.jsonl → GraphReconciler
   (CONFIRMED_RUNTIME / RUNTIME_DISCOVERED / unobserved / fuzzy)
```

### 2.3 Entry points

| Entry point | Path | Packer | Output |
|---|---|---|---|
| `prism index <repo>` | build only | — | summary / debug JSON |
| `prism query` | classic | `ContextKnapsackPacker` ($D_{hybrid}$) | Markdown |
| `prism causal-query` | v1.1 | `pack_symbol_context` | XML envelope / JSON |
| `prism trace [--ingest-otel F] -- <cmd>` | runtime | — | `.prism/traces/run_*.jsonl`, reconciled state |
| `prism mcp [--repo R]` | server | both | MCP tools (§8) |
| `PrismEngine.from_repo(...)` | library | two-pass | manifest text, hydrated context |

### 2.4 Concurrency model [IMPLEMENTED]

**Stateless slicing.** After Pass 1, Pass 2 and tagging, nothing on the read path mutates `builder.graph`, `symbol_table`, `tag_matrix` or `contracts`. `DistanceEngine` builds its own `to_undirected()` copy for each call.

The only shared mutable state is three lazy memo caches on `ConcreteGraphBuilder`:
- `calls_graph`;
- `_go_method_registry`;
- `_go_class_registry`.

They are guarded by one `_lazy_cache_lock` with double-checked locking, so the warm path never takes the lock. The MCP server keeps one `RepoContext` per canonical repository path (`GraphCache`) and serves overlapping tool calls against it.

---

## 3. Ingestion and static analysis

### 3.1 Languages and precision tiers [IMPLEMENTED]

| Language | `PrecisionTier` | Meaning |
|---|---|---|
| Python | `tier1` Semantic & Instance Precision | Full instance binding, Rules A–D, relative imports and barrel resolution |
| TypeScript / TSX / JavaScript | `tier2` Structural & Lexical | CST linking, import/export, class relations; no instance binding |
| Java, C# | `tier2` | as above |
| Go | `tier3` Lexical & Package-level | Package imports, receiver/parameter type binding, `EMBEDS` |
| Robot Framework (`.robot`, `.resource`) | — (no grammar; line-based reader) | Test cases and user keywords as symbols; keyword calls to user keywords and Python library keywords (`prism.graph.robot_framework`) |

JS/TS test-runner callbacks (Playwright, Jest, Vitest, Mocha) are registered as test symbols, and Playwright fixtures are typed through `base.extend<T>()` and `use(new C())` (`prism.graph.js_test_blocks`). See `docs/manual_sanity_check.md`.

The tier label is a coarse summary. The authoritative per-feature capability matrix is in `docs/design_formalism.md` §7.

`--language-tier tier1-only` restricts indexing to `TIER_1_ONLY_LANGUAGES` (Python).

### 3.2 Pipeline

1. **Discovery.** `prism.cli.discover_files(repo_root, language_tier)` walks the repository, maps extensions to `LanguageID`, and skips vendored and excluded paths.
2. **Parse.** Each file is parsed once with tree-sitter (`prism.parser.tree_sitter_loader`). The source bytes read for parsing are the same bytes hashed for the cache (§7.1).
3. **Pass 1, `pass1_collect_definitions`.** Registers every definition in the `GlobalSymbolTable`.
   - Registered: functions, methods, classes, Go structs and receiver methods (`pkg.Type.Method`), and TS interfaces (`kind="interface"`).
   - Also registered: attributes (feeding the attribute index), import maps and the `ExportRegistry`.
4. **Pass 2, `pass2_resolve_calls`.** Resolves every call site and heritage clause to edges in $G_C$ (§3.4).
5. **Tagging.** `TaggingEngine.tag_graph` produces $M: V\to 2^{\text{Tags}}$.
6. **Contracts.** `ContractExtractor` records state mutations, effects, return shapes and guard clauses.
7. **Semantics (v1.1).** `compute_feature_masks(builder)` produces $\Phi(v)$ (§3.7).

### 3.3 Node model [IMPLEMENTED]

A node is keyed by its **fully qualified name** (qname), for example `django.db.models.query.QuerySet.get` or `pkg.Type.Method`. The `SymbolInfo` / node attributes are:

| Field | Type | Notes |
|---|---|---|
| `kind` | `function \| method \| class \| interface \| attribute` | |
| `file` | path | absolute at index time, relative in envelopes |
| `line_range` | `(start, end)` | 1-based, inclusive |
| `language_id` | `LanguageID` | |
| `tags` | `set[str]` | mirrors $M$ |
| `signature`, `params` | | used for arity and manifests |

There are also **sentinel nodes**:
- `UnresolvedPolymorphicNode`: a call site that matches ≥2 plausible targets and that the resolver refuses to guess.
- `DynamicEdgeSentinel`: `getattr`, `eval`, subscript dispatch and similar.

Sentinels are never packed as code. They exist so an ambiguity is visible rather than silently dropped.

### 3.4 Edge types [IMPLEMENTED]

| Relation | Traversable | Produced by | Structural weight $w_{rel}$ (D_hybrid) | Causal base $w_{base}$ |
|---|---|---|---|---|
| `CALLS` | yes | Pass 2 call resolution | 1.00 | 1.00 |
| `INSTANTIATES` | yes | constructor call `C(...)` / `new C()` / composite literal | 1.00 | 1.00 |
| `OVERRIDES` | yes | method redefinition along the MRO | 0.90 | 0.90 |
| `EXTENDS` | yes | class inheritance (C3-MRO approximation) | 0.85 | 0.85 |
| `EMBEDS` | yes | Go struct embedding (BFS depth shadowing) | 0.85 | 0.85 |
| `IMPLEMENTS` | yes | TS/Java/C# interface implementation | 0.80 | 0.80 |
| `READS_STATE` | **no** | attribute/state reads | — | — |

Edge `kind` qualifiers multiply the weight:

| `kind` | Meaning | Multiplier |
|---|---|---|
| (absent) | confidently resolved | 1.0 |
| `TENTATIVE_CALL` | best-effort static link: unique-name, family root, receiver-name, attribute index, inferred return type, virtual dispatch, Go Stage 2 | `RELATION_TENTATIVE_CALL_WEIGHT` = 0.60 |
| `TENTATIVE_DYNAMIC_CALL` | runtime fuzzy-anchored link (§5.5) | 0.65 |

Other edge attributes:

| Attribute | Values / meaning |
|---|---|
| `confidence` | `STATIC` (default), `CONFIRMED_RUNTIME`, `TENTATIVE_RUNTIME` |
| `provenance` | `STATIC_ANALYSIS`, `RUNTIME_DISCOVERED`, `RUNTIME_FUZZY_MATCHED` |
| `runtime_invocation_count` | int |
| `high_trust_runtime` | bool (RuntimeTrust ≥ 0.9) |
| `unobserved_in_traces` | bool (Bayesian degradation, §5.4) |
| `dispatch_of` | for a virtual-dispatch expansion edge: the statically named target it was expanded from |
| `call_sites` | list of `CallSiteContext` (§3.5) |

**Weight versus cost.** In both distance systems a *weight* means higher is closer, and the hop *cost* is its inverse ($1/w$ for $D_{hybrid}$, $1/W$ for the causal path). A multiplier below 1 on a weight therefore makes an edge **farther**.

### 3.5 Call-site context [IMPLEMENTED]

Every resolved call carries a `CallSiteContext` (`prism.graph.call_site`):

| Field | Meaning |
|---|---|
| `call_kind` | direct / method / constructor / dynamic |
| `inside_loop`, `inside_try_catch`, `guarded_by_null_check` | control context |
| `args_passed_count` | arity at the call site |
| `argument_flow` | which caller values flow into which parameters |
| `bound_to` | name the result is assigned to, if any |
| `is_return_bound` | the callee's result is used (assigned, returned, unpacked) |
| `call_site_role` | e.g. guard, transform, sink |

These fields feed the upstream blast-radius weights (§4.3) and the manifest annotations (`binds_return`, `nontrivial_args`).

### 3.6 Call resolution rules [IMPLEMENTED]

Pass 2 tries the following rules in order. The first rule that yields exactly one target wins.

1. **Lexical / import resolution.** Local scope, then module scope, then the `LocalImportMap`, then `resolve_export`.
   - `resolve_export` follows recursive barrel chains, bounded by `EXPORT_RESOLUTION_MAX_DEPTH = 5` with a `visited` cycle set.
   - It returns only a real symbol or `None`. It never guesses (Invariant 3).
2. **Instance binding (Python, Rules A–D).** `x = C(...)` binds `x: C` in the `InstanceTypeMap`, so `x.m()` resolves along `C`'s MRO.
   - **Return-type inference**: `x = f()`, where `f` returns `C(...)` or is annotated `-> C`, binds `x: C` with `inferred=True`. Calls through an inferred binding are marked `TENTATIVE_CALL`.
3. **Receiver-name typing.** `self.repo.save()` resolves through an attribute whose type is known, or through the attribute index (one class owns `repo`). Such calls are tentative.
4. **Go receivers.** Receiver, parameter and short-var-decl type signatures bind confidently (Issue B1). A codebase-unique receiver method is a tentative fallback (Stage 2). Measured receiver-call resolution on gin is 49.75%.
5. **Single-family root resolution.** If every candidate with a given method name belongs to one inheritance family, link the family root (tentative).
6. **CHA virtual dispatch.** A call resolved to `Base.m` is expanded to every override `Sub.m`, each a tentative edge carrying `dispatch_of="Base.m"`. Data-flow extraction skips `dispatch_of` edges, so the call site stays unambiguous for return-binding analysis.
7. **Builtin guard.** For a builtin-named method (`get`, `append`, `items`, …) with **≥2** candidates, the resolver does not guess. It emits an `UnresolvedPolymorphicNode` sentinel. With exactly one candidate, the normal rules apply. `*args`/`**kwargs` forwarding is treated as builtin.
8. **Otherwise**: the call becomes a sentinel or is dropped as external. External dependencies are resolved separately by `prism.external` (role `external`, budget fraction 0.125 with a floor of 256 and a ceiling of 1,024 tokens).

### 3.7 Semantic layers [IMPLEMENTED]

**Tags (`prism.tagger.rules`).** The rule-based labels are:
- `#auth_guard`, `#db_read`, `#db_write`, `#dynamic_attribute`, `#dynamic_hazard`, `#entrypoint`, `#error_handler`, `#event_consumer`, `#event_producer`, `#external_io`, `#io_sink`, `#payment_charge`, `#property`, `#pure_transform`, `#route_handler`, `#state_mutation`;
- plus the runtime-only `#dynamic` (§5.3).

The **metamodel** (`prism.graph.metamodel`) is a tag graph with relations such as `REQUIRES_BEFORE`, for example `#db_write` requires `#auth_guard` somewhere in its ancestry. It is exposed through `get_architectural_invariants`.

**Four-axis mask** (`prism.semantics.bitmask.FeatureBit`, a 64-bit `IntFlag`):

| Bits | Axis | Values |
|---|---|---|
| 0–9 | Substance $S(v)$ | `SINK_NETWORK_IO`, `SINK_DATABASE_IO`, `SINK_FILESYSTEM_IO`, `SINK_PROCESS_IO`, `SINK_TIME_IO`, `SINK_RANDOMNESS`, `SINK_PURE_COMPUTE` (bits 0–6; 7–9 reserved) |
| 10–24 | Form $F(v)$ | `RETRY_LOOP`, `BRANCH_DISPATCH`, `GUARD_EARLY_EXIT`, `PIPELINE`, `VALIDATOR`, `BATCH_LOOP`, `WRAPPED_TRY`, `RECURSIVE`, `ASYNC_CONCURRENT`, `LINEAR` |
| 25–34 | Output $O(v)$ | `PREDICATE`, `COMMAND`, `QUERY`, `FACTORY`, `TRANSFORMER`, `AGGREGATOR`, `FLUENT`, `ASYNC_DEFERRED`, `GUARD` |
| 35–44 | Role $R(v)$ | `ENTRYPOINT`, `ORCHESTRATOR`, `ADAPTER`, `LEAF_UTILITY`, `BRIDGE`, `PUBLIC_API`, `LEAF_SERVICE` |
| 45–63 | reserved | |

**Known limitation.** On real corpora the masks are close to degenerate: `SINK_PURE_COMPUTE` is set on 87–97% of functions. Coverage novelty $\delta_{feat}$ (§4.4) is therefore a weak signal in practice.

---

## 4. The blast-radius engine

"Blast radius" means the set of symbols that may need attention when the seed changes. That is mainly its **transitive callers** (upstream), plus the immediate downstream contracts the seed depends on. Prism implements it in three cooperating places.

### 4.1 Distances

#### 4.1.1 $D_{hybrid}$ (classic path) [IMPLEMENTED]

For each $v$, the engine computes the weighted shortest hop count `hops(s, v)` on the undirected view of the traversable relations (`DistanceEngine._weighted_undirected`). The per-edge cost is computed in two steps.

First, a structural weight, where higher means closer:

$$
w(e)=\frac{w_{rel}(e)\cdot k_{tent}(e)\cdot \gamma(e)}{\sqrt{h(\deg^-_u)\,h(\deg^-_v)}},\qquad h(n)=1+\ln\!\big(1+\max(n-1,0)\big)
$$

Then the hop cost, where lower means closer:

$$
\ell(e)=\frac{1}{w(e)}\cdot r(e)
$$

where:
- $w_{rel}$ comes from the edge-type table in §3.4;
- $k_{tent}\in\{1, 0.60, 0.65\}$;
- $\gamma(e)=$ `GAMMA_UNOBSERVED` $=0.50$ if `unobserved_in_traces`, otherwise 1;
- $h$ is the **hub fan-in penalty** on in-degree. It is split as a square root per edge, so a path that *passes through* a hub pays the full penalty once, while a direct call into a hub pays only its square root;
- $r(e)=0.5$ if `CONFIRMED_RUNTIME`, $0.25$ if also `high_trust_runtime`, otherwise 1.

So a tentative edge (×0.60), an unobserved edge (×0.50) or an edge touching a hub becomes **longer**, and a runtime-confirmed edge becomes **shorter**. Unobserved edges are re-priced, never deleted.

Then:

$$
\text{raw}_{gc}=\frac{\text{hops}}{\text{max\_hops}},\qquad
\text{tag\_bonus}=\text{margin}\cdot\frac{\lambda}{\text{max\_hops}}\cdot\hat d_{gt},\qquad
D_{hybrid}=\min\!\big(\lambda\,\text{raw}_{gc}+\text{tag\_bonus},\;1\big)
$$

where:
- $\lambda=0.7$, $\text{max\_hops}=10$, $\text{margin}=0.5$;
- $\hat d_{gt}=\min(\text{tag\_distance}/\texttt{MAX\_TAG\_DISTANCE},1)$, where `tag_distance` is the metamodel's distance between the seed's and the node's tag sets (`metamodel.get_tag_distance`).

The final value is clamped to $[0,1]$ for the packer and the resolution tiers. The monotonicity argument is made on the unclamped sum.

Because the tag bonus is bounded by half of one hop, it can reorder candidates at equal hop distance but can **never** move a farther node ahead of a closer one. This is **Topological Monotonicity**, Invariant 1, proven in formalism §2.2.

Polysemy scoring (choosing among same-name candidates) is:

$$
\text{Score}=0.50\,N+0.35\,A+0.15\,L,\qquad L\in\{1.0,0.6,0.2\},\qquad \text{threshold}=0.85
$$

where N is the namespace match, A the arity match and L the locality.

#### 4.1.2 Causal distance (v1.1 path) [IMPLEMENTED]

$$
W(u,v)=w_{base}(rel)\cdot k_{tent}(u,v)\cdot\big(1+\lambda_1 I_{df}(u,v)+\lambda_2 I_{guard}(u,v)\big),\qquad c(u,v)=\frac{1}{W(u,v)}
$$

- $\lambda_1=0.25$, $\lambda_2=0.15$, and $W_{max}=1.0\times1.40$.
- $I_{df}$ is 1.0 for a nested call argument (`store(clean(raw))`), 0.9 for a variable hand-off (`x=clean(raw); store(x)`), and 0.7 for an attribute hand-off.
- $I_{guard}$ is 1.0 for a predicate gate, 0.9 for an exception gate, and 0.9 for an early-return gate.

**Synthetic edges.** A (producer, consumer) pair with data-flow or guard evidence and no structural edge gets a synthetic `CALLS`-priced edge. These pairs are usually siblings under one orchestrator.

**Gate (recent):**

$$
\text{create}(u,v)\iff \text{Pred}(u)\cap\text{Pred}(v)=\varnothing\;\lor\;\exists f\in\text{Pred}(u)\cap\text{Pred}(v):\;k_{tent}(f,u)=k_{tent}(f,v)=1
$$

In words: an inference is never stacked on two best-effort guesses.

`continuous_dijkstra.compute_topological_distances(builder, s)` runs Dijkstra once over a directed `nx.DiGraph` with costs $c$. The horizon is `d_max` (default 5.0 for MCP; the packer's own `DEFAULT_MAX_HOPS` is 6.0). The distance field is frozen before selection begins.

### 4.2 The upstream frontier (`prism.packer.blast_radius`) [IMPLEMENTED]

Callers are not reachable along `successors()`, so the packer adds an explicit **upstream frontier** of callers of already-admitted nodes:

$$
W_{up}(f\to v)=w_{base}\cdot k_{tent}(f,v)\cdot\big(1+\mu_1\,\mathbb 1[\text{return unpacked}]+\mu_2\,\mathbb 1[\text{non-trivial args}]\big)
$$

- $\mu_1$ = `MU_RETURN_UNPACK` = 0.30;
- $\mu_2$ = `MU_NONTRIVIAL_ARGS` = 0.20;
- `CONTRACT_PRESERVATION_MULTIPLIER` = 1.30.

A caller that *consumes* the seed's return value, or passes real expressions into it, is the one most likely to break, so it is pulled closer. Non-trivial arguments exclude literals and bare names; see `_TRIVIAL_ARG_NODE_TYPES`.

Limits:
- `DEFAULT_UPSTREAM_MAX_HOPS` = 1.5;
- `UPSTREAM_FRONTIER_CAP` = 3 callers admitted per expansion;
- only *confident* (non-tentative) upstream callers can be force-admitted.

### 4.3 Design C: the bidirectional manifest (`prism.packer.candidate_index`) [IMPLEMENTED]

The two-pass engine's Turn-1 manifest is the agent-facing blast-radius view. With `direction="both"` (enabled for blast-radius tasks through `PRISM_BLAST_MODE`):

1. **Upstream.** BFS over the reverse `CALLS`/`INSTANTIATES` edges from $s$ to `UPSTREAM_WALK_MAX_HOPS` = 6. Symbols with `SymbolRole == VERIFICATION` (tests, fixtures) are excluded.
2. **Downstream.** The causal-distance neighbourhood within `CANDIDATE_INDEX_MAX_HOPS` = 3.0, plus every node on the seed's **real call chain**. The call chain is reachable by hop count, so tentative discounts cannot push real callees out ("hop-count admission").
3. **Interleave.** Caller and downstream rows are interleaved under the manifest budget (13,000 tokens in the harness). Downstream rows are ordered by `dist_w`.

**Row grammar** (one line per symbol):

```
qname|role|kind|signature|calls=[c1,c2,…](|binds_return)(|nontrivial_args)|lines=N
```

| Field | Rule |
|---|---|
| `role` | `seed`, `callee`, `caller`, `transitive` |
| `signature` | Full declaration (`_full_declaration`), capped at `MANIFEST_SIGNATURE_MAX_TOKENS` = 128, then suffixed `" …[signature truncated]"`. Long string literals are collapsed, and `\|` is replaced by `¦`. |
| `calls=[…]` | up to `_MAX_CALLS_PER_SYMBOL` = 12 resolved callees |
| `binds_return`, `nontrivial_args` | from §3.5, on caller rows |
| `lines=N` | body length, so the selector can judge cost |

Turn 2 (`retrieve_requested`) hydrates the requested names. It always unions in the seed's direct callees, and prices and compresses everything under the arm budget.

### 4.4 Submodular selection [IMPLEMENTED]

$$
V(v)=\frac{1}{(1+d_w(s,v))^2}\Big(1+\beta\,\delta_{feat}(v)\Big),\qquad
\delta_{feat}(v)=\min\big(\text{popcount}(\Phi(v)\,\&\,\lnot\,\text{covered}),\,\delta_{max}\big)
$$

- $\beta=0.10$, $\delta_{max}=10$.
- Admission is greedy by density $V/\text{cost}$ from a precedence-constrained frontier, with `covered |= Φ(v)` after each admission.
- The dominance bound requires $\beta\delta_{max}<1.25$. Violating it raises `ValueError`.
- Dominance of a closer node holds only for $d\in\{0,1\}$ (`MAX_DOMINANT_SEED_DISTANCE`=1). At $d=2$, $1/9<2/16$, a documented counterexample.

Gates:
- **Phase E scope gate.** Nodes at $d\ge$ `SCOPE_GATE_MIN_DIST` = 2.0 without a substance sink are filtered. Nodes at $d\le$ `PHASE_E_IMMUNE_DIST` = 1.0 are immune.
- **Novelty streak.** Selection stops after `MAX_ZERO_NOVELTY_K` = 10 consecutive zero-novelty admissions.
- **Stub-packing.** Distance-1 successors that do not fit at full resolution are packed as signature stubs (≤10 header lines; constants ≤64 tokens).
- **Protected roles.** `seed`, `callee` and `caller` are never downgraded.

Envelope overhead reserves: 280 tokens per node tag and 450 tokens for the package envelope.

### 4.5 Blast tiers and thresholds

**[PARTIAL]** There are **no** named "Tier 1 / Tier 2 / Tier 3 blast" thresholds in the code. What exists instead:

| Existing mechanism | Value |
|---|---|
| Compression tier by distance | $D\le0.25\to$ L1; $\le0.55\to$ L2; else L3 (seed at L0) |
| Data-flow centrality floor | centrality ≥ 2 forces ≥ L1 |
| Upstream horizon | 1.5 (packer); 6 BFS hops (manifest) |
| Causal horizon | `d_max` 5.0 |
| Language precision tier | `tier1/2/3` (§3.1), a different concept from blast tiers |

**[PROPOSED] Blast tiers.** This is the most likely specification if such tiers are introduced. It derives them from quantities Prism already computes and maps them onto the existing compression levels.

| Tier | Definition | Suggested rendering |
|---|---|---|
| **T1 Direct** | callers at reverse hop 1 with `binds_return` or `nontrivial_args`, plus overriding/implementing symbols | L0/L1, always admitted (protected) |
| **T2 Indirect** | reverse hop 2–3, or hop 1 without a contract signal | L2 skeleton |
| **T3 Peripheral** | reverse hop 4–6, or reached only through `TENTATIVE_*` edges | L3 stub / manifest row only |

Equivalently, on the upstream weight: $W_{up}\ge1.2$ → T1; $0.6\le W_{up}<1.2$ → T2; otherwise T3. **Assumption:** the thresholds are chosen so that a confident direct caller with any contract signal ($\ge1.2$) is T1, and any tentative-only path ($\le 0.60\cdot1.5=0.9$, discounted further over additional hops) cannot reach T1.

---

## 5. Dynamic empirical weight feedback

### 5.1 Trace capture [IMPLEMENTED]

- `prism trace -- <cmd>` runs the command under `prism.runtime.tracer.Tracer` (`sys.settrace`-based).
  - Each `TraceRecord(caller, callee, callee_file, callee_line, …)` is written to `.prism/traces/run_*.jsonl`.
  - Excluded paths (site-packages, stdlib) and synthetic code names (`<module>`, `<lambda>`, comprehensions) are skipped.
- `--ingest-otel F` converts an OpenTelemetry JSON export instead. It reads span kinds and SQL verbs for DB-write detection.
- `trace_validator` checks the file shape before reconciliation.

### 5.2 Reconciliation [IMPLEMENTED]

For each event $(u,v)$ that resolves to static symbols:

| Case | Effect |
|---|---|
| $(u,v)\in E$ | `confidence = CONFIRMED_RUNTIME`, `runtime_invocation_count += n` |
| $(u,v)\notin E$ | new edge with `provenance = RUNTIME_DISCOVERED`, confirmed |
| $u$ resolved, $v$ not | fuzzy anchor match (§5.5), otherwise an orphan |
| unresolved | `classify_orphan` → `NO_STATIC_MATCH`, `ANONYMOUS_CLOSURE`, `AMBIGUOUS_SIGNATURE`, `NATIVE_OR_C_EXTENSION`, `DYNAMIC_DISPATCH` (never `None`: Invariant 6) |

### 5.3 RuntimeTrust

$$
\text{RuntimeTrust}=\frac{\text{MatchedEvents}}{\text{TotalEvents}}\quad(1.0\text{ if TotalEvents}=0)
$$

- If RuntimeTrust < 0.5, a `trust_warning` is emitted and static analysis stays primary.
- If RuntimeTrust ≥ 0.9, every confirmed or discovered edge from the run gets `high_trust_runtime=True`.
- A `DYNAMIC_DISPATCH` orphan with a resolvable caller tags the caller `#dynamic`.

### 5.4 Update equations

The effective $D_{hybrid}$ hop cost of an edge after any number of runs, ignoring the hub penalty, is:

$$
\ell(e)=\frac{1}{w_{rel}(e)\,k_{tent}(e)}\cdot
\begin{cases}
0.5 & \text{confirmed}\\
0.25 & \text{confirmed, high trust}\\
1/0.50=2 & \text{unobserved}\\
1 & \text{otherwise}
\end{cases}
$$

The confirmed and unobserved cases are mutually exclusive in practice. A confirmed `CALLS` edge costs 0.5, an untouched one 1.0, and an unobserved one 2.0.

**Bayesian non-observation (negative evidence).** Let $n_u$ be the execution count of $u$ in a run:

$$
n_u\ge \texttt{NON\_OBSERVATION\_EXECUTION\_THRESHOLD}=10\;\land\;(u,v)\notin\text{confirmed}\;\Rightarrow\;\texttt{unobserved\_in\_traces}(u,v)=\text{True}
$$

- The edge is never deleted, so cold error paths stay visible.
- Runtime-discovered edges are not eligible.

**Persistence across runs** (`merge_result_into_state`):

$$
\begin{aligned}
C_{t+1}&=C_t\cup C_{run}\\
D_{t+1}&=(D_t\cup D_{run})\setminus C_{t+1}\\
U_{t+1}&=(U_t\cup U_{run})\setminus C_{t+1}\\
F_{t+1}&=(F_t\cup F_{run})\setminus\text{exactly-resolved}_{run}
\end{aligned}
$$

where $C$ is confirmed, $D$ discovered, $U$ unobserved and $F$ fuzzy-matched. A later confirmation therefore always clears an earlier "unobserved" verdict.

**Why "Bayesian".** The rule is a thresholded likelihood-ratio test. If the edge were real and taken with probability $p$ per execution of $u$, observing zero uses in $n$ executions has likelihood $(1-p)^n$. At $n=10$ this falls below $e^{-1}$ for any $p\ge0.1$. **Assumption:** the implementation uses the fixed threshold and fixed multiplier rather than a continuous posterior. A continuous form, if wanted, is [PROPOSED]:

$$
\hat p_{e}=\frac{\alpha+k_e}{\alpha+\beta+n_u},\qquad \gamma(e)=\max\!\big(0.5,\;\hat p_e/\hat p_{prior}\big)
$$

with a Beta($\alpha,\beta$) prior, $k_e$ observed traversals, and $\gamma$ clamped so that it never prices an edge below the current 0.5.

### 5.5 Fuzzy anchor matching

Decorated or metaclass-synthesised callees have a `co_qualname` that has drifted from their definition. Within the same file, the matcher picks a function/method whose `line_range` is within `FUZZY_ANCHOR_LINE_WINDOW` = 25 lines of `callee_line`:
- distance 0 (containment) wins, and the innermost span wins among containers;
- otherwise the nearest one wins;
- exact ties are left unresolved.

The resulting edge is `TENTATIVE_DYNAMIC_CALL` (×0.65), with provenance `RUNTIME_FUZZY_MATCHED`. `orphan_resolution_ratio` reports how many orphans were rescued.

### 5.6 Interaction with the v1.1 causal path

The causal path uses `w_base · k_tent` and the data-flow/guard indicators. **It does not currently consume the runtime multipliers**. Those multipliers only affect $D_{hybrid}$ and edge views such as `get_architectural_invariants`.

**[PROPOSED]** Fold the runtime evidence into $W$ as $W'=W\cdot\gamma(e)/r(e)$. A confirmed edge ($r=0.5$) doubles its weight, and an unobserved edge ($\gamma=0.5$) halves it, mirroring $D_{hybrid}$. This preserves Property 3, since positive evidence never lengthens a path.

---

## 6. Binary encoding and storage format

### 6.1 What exists [IMPLEMENTED]

| Concern | Actual implementation |
|---|---|
| In-memory graph | `networkx.DiGraph`. Node keys are qname strings, with attribute dicts on nodes and edges. |
| String interning | none explicit; it relies on CPython string sharing for dict keys |
| Edge encoding | Python dict per edge (relation, kind, confidence, provenance, call_sites, …) |
| Adjacency layout | networkx dict-of-dicts |
| Persistence | SQLite at `.prism/cache/index.db`; the graph is stored as `node_link_data` JSON |
| Feature masks | Python `int` (`IntFlag`), 64 bits used |
| Per-file cache | SQLite table `file_cache_v2` with JSON columns |
| Traces | JSON Lines `.prism/traces/run_*.jsonl` |
| Envelope | deterministic XML (`<prism_context>`) or JSON from pydantic models |

Index cache schema (`prism.runtime.index_cache`, `_SCHEMA_VERSION = 3`):

```sql
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL            -- SHA-256 of file bytes
);
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY CHECK (id = 1), -- single row
  schema_version INTEGER NOT NULL,
  language_tier TEXT NOT NULL,
  engine_version TEXT NOT NULL,          -- engine_and_grammar_version()
  graph_json TEXT NOT NULL,              -- nx.node_link_data(G_C)
  symbols_json TEXT NOT NULL,
  tag_matrix_json TEXT NOT NULL,
  index_errors_json TEXT NOT NULL,
  go_receiver_total INTEGER NOT NULL,
  go_receiver_resolved INTEGER NOT NULL
);
```

A legacy `snapshot` table without `engine_version` is detected with `PRAGMA table_info` and dropped (self-healing).

**Versioning rule.** A cache hit requires all of the following to be equal:
- `schema_version`;
- `language_tier`;
- `engine_version` (Prism version plus tree-sitter grammar versions).

Otherwise the cache rebuilds.

### 6.2 [PROPOSED] Compact binary `.prism` store

No such format exists today. If profiling ever shows that JSON load time or networkx memory dominates (the current measurement is a 0.14 s warm load on 1.5k symbols), the most likely design consistent with Prism's semantics is below. It is offered as a buildable specification, not a description of the current system.

**File layout** (little-endian, 8-byte aligned sections, read-only, `mmap`-able):

```
offset  size  field
0       8     magic "PRISMG\0\1"
8       4     format_version (u32) = 1
12      4     flags (u32): bit0 = has_runtime, bit1 = has_masks
16      32    engine_version SHA-256 (matches index_cache.engine_version)
48      8     n_nodes (u64)
56      8     n_edges (u64)
64      8×8   section table: [strings, nodes, out_offsets, out_edges,
              in_offsets, in_edges, edge_meta, masks]  (u64 offsets)
...           sections
```

1. **String interning.** One `strings` section holds UTF-8 qnames sorted lexicographically, as `u32 offsets[n+1]` plus a blob. Node id = rank in sorted order, so lookup by qname is a binary search, $O(\log n)$.
2. **Node record** (32 B): `u32 name_id, u32 file_id, u32 line_start, u32 line_end, u8 kind, u8 language, u16 tier_flags, u64 tag_bits, u32 reserved`.
3. **CSR adjacency.** `out_offsets: u64[n+1]` and `out_edges: u32[m]` (target ids), plus the mirrored `in_offsets`/`in_edges` for upstream walks. Edges of a node are sorted by target id.
4. **Edge bit layout** (`u32` per edge in `edge_meta`, parallel to `out_edges`):

| Bits | Field | Encoding |
|---|---|---|
| 0–2 | relation | 0 CALLS, 1 INSTANTIATES, 2 EXTENDS, 3 IMPLEMENTS, 4 OVERRIDES, 5 EMBEDS, 6 READS_STATE |
| 3–4 | kind | 0 confident, 1 TENTATIVE_CALL, 2 TENTATIVE_DYNAMIC_CALL |
| 5–6 | confidence | 0 STATIC, 1 CONFIRMED_RUNTIME, 2 TENTATIVE_RUNTIME |
| 7–8 | provenance | 0 static, 1 RUNTIME_DISCOVERED, 2 RUNTIME_FUZZY_MATCHED |
| 9 | high_trust_runtime | |
| 10 | unobserved_in_traces | |
| 11 | binds_return (any call site) | |
| 12 | nontrivial_args (any call site) | |
| 13 | is_dispatch_expansion (`dispatch_of` set) | |
| 14–17 | $I_{df}$ quantised ×10 | |
| 18–21 | $I_{guard}$ quantised ×10 | |
| 22–31 | reserved | |

   Full `CallSiteContext` lists stay in a side section, keyed by edge index.
5. **Masks.** `u64[n]`, which is the existing `FeatureBit` layout unchanged.
6. **Versioning.** `format_version` is bumped on any layout change, and the reader rejects unknown versions. The file is regenerated from source rather than migrated, the same policy as the SQLite cache.
7. **SIMD.** Not applicable to the current Python code. In a native reader, the popcount loop over masks is the natural vectorisation target.

---

## 7. Incremental sync and cache invalidation

### 7.1 Whole-repository index cache [IMPLEMENTED]

```
on build_pipeline(repo):
    files  = discover_files(repo, tier)
    hashes = {p: sha256(bytes(p)) for p in files}
    snap   = SELECT … FROM snapshot
    if snap and set(files)==set(cached files) and every hash equal
           and schema/tier/engine versions equal:
        G_C, symbols, M = deserialize(snap)          # warm path
        ParsedFile objects rebuilt from the same bytes  # one read per file
    else:
        full Pass 1 + Pass 2 + tagging                 # cold path
        overwrite files + snapshot
```

**Invalidation granularity is the whole repository, on purpose.** Pass 2 is cross-file: imports, instance binding and Go's type registries. Changing one file can change what an unrelated file's call sites resolve to, and Prism does not track that dependency graph. A partial re-link would risk silently stale edges.

Measured on gin: 3.8 s cold, 0.14 s warm.

### 7.2 Per-file feature cache `file_cache_v2` [IMPLEMENTED]

```sql
CREATE TABLE IF NOT EXISTS file_cache_v2 (
  relative_path TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,      -- versioned: hash ⊕ engine/grammar version
  mtime REAL NOT NULL,
  serialized_symbols TEXT NOT NULL,
  feature_bitmasks TEXT NOT NULL,  -- S_direct, F, O (file-local)
  local_data_flow TEXT NOT NULL,   -- trusted only on a whole-repo hit
  schema_version INTEGER NOT NULL  -- SCHEMA_VERSION = 3
);
```

| Data | Safe to reuse when |
|---|---|
| `feature_bitmasks` | the file's `content_hash` is unchanged |
| `local_data_flow` | the file is unchanged **and** the whole-repo index cache hit |
| $S_{transitive}$, Role bits | never cached; recomputed from the graph each run |

This table plays the role of the "shadow metadata" store: it sits beside the graph snapshot and records per-file facts with their own validity conditions.

### 7.3 Server-side cache [IMPLEMENTED]

`prism.mcp.cache.GraphCache` holds one `RepoContext` per canonical path:
- `get_or_index` builds on a miss;
- `reindex` and `invalidate` are the **only** ways to force a rebuild (tool `reindex_repo`).

The server does not watch the file system. A client that edits files must call `reindex_repo`. The rebuild itself goes through §7.1, so an unchanged repository costs a warm load.

Other caches:
- Traversal caches (`_LRUCache(maxsize=10)` for data-flow, guard and causal edges) are keyed by `prism.traversal._cache_keys`, which includes the graph identity and edge count.
- `flow_cache` and `contract_cache` store derived analyses under `.prism/`.

### 7.4 [PROPOSED] Dirty propagation, delta store, compaction

None of these exist. If file-level incrementality becomes necessary, this is the design that keeps Prism's "never serve an unverified state" rule.

1. **Dependency index.** At Pass 2, record for each file $f$:
   - $\text{Defs}(f)$, the symbols it defines;
   - $\text{Uses}(f)$, the external names its call sites and imports resolved through (including names that failed to resolve).
2. **Dirty set.** For changed files $\Delta$:

$$
\text{Dirty}_0=\Delta,\qquad
\text{Dirty}_{k+1}=\text{Dirty}_k\cup\{g: \text{Uses}(g)\cap\big(\text{Defs}_{old}(\text{Dirty}_k)\,\triangle\,\text{Defs}_{new}(\text{Dirty}_k)\big)\ne\varnothing\}
$$

   Iterate to a fixpoint. Barrel re-exports propagate through $\text{Uses}$. Repository-wide registries (Go method/class registries, unique-name and attribute indexes) force a full rebuild if their *key sets* change.
3. **Delta store.** Write re-linked edges for the dirty files to a `delta` table, with an `(edge, op ∈ {add, remove})` log and a monotonically increasing `generation`. Readers overlay delta on the snapshot.
4. **Compaction.** When `delta_rows > 0.2 · |E|`, or on idle, fold the delta into a new snapshot generation atomically (`BEGIN IMMEDIATE`; write; swap `snapshot.id`).
5. **Verification.** In tests, a full rebuild must produce identical $(V,E)$. Any mismatch disables incremental mode for that repository.

---

## 8. MCP server

### 8.1 Transport, security, auth [IMPLEMENTED]

- `prism mcp [--repo R] [--transport stdio|…]` uses FastMCP. `--repo` sets `PRISM_MCP_DEFAULT_REPO` and also acts as a sandbox: paths that escape it are rejected.
- Input validation (`prism.mcp.security`):
  - symbol names match `^[\w.\-]+$`, at most 512 characters;
  - tags match `^#[a-z][a-z0-9_]*$`;
  - token budgets are ≤ `MAX_TOKEN_BUDGET` = 128,000;
  - `..` path segments are rejected.
- Auth (`prism.mcp.auth`): if `PRISM_MCP_API_KEYS` is set, a bearer key is required, from the Authorization header or the `api_key` argument. The rate limit is `RATE_LIMIT_PER_MINUTE` = 60 per key (60 s sliding window).

### 8.2 Tools

| Tool | Purpose |
|---|---|
| `get_symbol_context` | Classic Markdown context pack ($D_{hybrid}$ knapsack) |
| `get_architectural_invariants` | Tags, in/out edges with runtime confidence, unmet `REQUIRES_BEFORE` |
| `find_symbols_by_tag` | All symbols carrying a tag, with location and runtime count |
| `get_graph_status` | Symbol/edge counts, runtime-confirmed/discovered counts, per-tag counts |
| `reindex_repo` | Force a rebuild of the repository's cached graph |
| `prism.slice` | v1.1 `<prism_context>` envelope (callers + callees, causal knapsack) |
| `prism.explain` | Same envelope at reduced detail |
| `prism.blast_radius` | Every transitive caller of a seed (tests included by default) with hops, plus a budgeted envelope of their code. This is the measured R0 path: Design C manifest, every caller row, `retrieve_requested`. |

The tool names sometimes expected (`get_blast_radius`, `trace_call_chain`, `verify_contract`) **do not exist**. Their equivalents:
- blast radius → `prism.blast_radius`;
- call chain → `prism.slice` with `task_type="chain"` (includes `<causal_path>`);
- contracts → `get_architectural_invariants`, plus the `contract` element on envelope nodes.

#### Schemas

```jsonc
// get_symbol_context → string (Markdown)
{ "target_symbol": "string (required)", "repo_path": "string|null", "token_budget": "int = 2000" }

// get_architectural_invariants → object
{ "target_symbol": "string (required)", "repo_path": "string|null" }
// returns
{ "symbol": "…", "active_tags": ["#db_write"],
  "incoming_edges": [{"caller":"…","callee":"…","confidence":"STATIC|CONFIRMED_RUNTIME",
                      "provenance":"STATIC_ANALYSIS|RUNTIME_DISCOVERED|…","runtime_invocation_count":0}],
  "outgoing_edges": [ … ],
  "unfulfilled_invariants": [{"tag":"#db_write","requires_before":"#auth_guard"}] }

// find_symbols_by_tag → object
{ "tag": "#route_handler (required)", "repo_path": "string|null" }
// returns {"tag","count","symbols":[{"symbol","file","line_range":[a,b],"language","runtime_invocation_count"}]}

// get_graph_status → {symbols, call edges, confirmed_runtime_edges, runtime_discovered_edges, tag_counts}
// reindex_repo     → status object for the rebuilt context

// prism.blast_radius → {seed, callers:[{symbol, hop, is_test, language, file, line}], callers_total,
//                        callers_truncated, callers_in_context, envelope, token_count, truncated}
{ "repo_path": "string (required)", "seed_symbol": "string (required)",
  "budget_tokens": "int 500..128000 = 13000", "include_tests": "bool = true",
  "format": "xml|json", "api_key": "string|null" }

// prism.slice / prism.explain → {"envelope": "string", "token_count": int, "truncated": bool}
{ "repo_path": "string (required)",
  "seed_symbol": "string|null",            // exactly one of seed_symbol / seeds
  "seeds": ["string"] ,                    // length must be 1
  "budget_tokens": "int 500..128000 = 4000",
  "language_tier": "auto|1|2|3",
  "format": "xml|json",
  "task_type": "chain|blast|redundancy|architecture|debug|null",
  "d_max": "float|null = 5.0",              // null → packer DEFAULT_MAX_HOPS 6.0
  "include_warnings": "bool = true",
  "detail_level": "full|manifest|summary",  // prism.explain only
  "api_key": "string|null" }
```

#### Error codes

| Code | Condition |
|---|---|
| `-32001` | invalid or out-of-sandbox `repo_path` |
| `-32002` | unknown seed. The response includes fuzzy suggestions (threshold 0.6, top 5). |
| `-32003` | the seed body alone exceeds `budget_tokens` |
| `-32602` | both or neither of `seed_symbol`/`seeds`, or `len(seeds) > 1` |
| `ToolError` | classic tools: unknown symbol or tag, validation failure |

### 8.3 The `<prism_context>` envelope (`prism.surface.models`) [IMPLEMENTED]

| Element | Fields |
|---|---|
| `engine` | name, version, commit |
| `seed` | symbol, file, line |
| `budget` | tokens, tokenizer, exact |
| `language` | tier, primary, files |
| `manifest` | packed / considered / reachable counts; per-level compression counts (`L0_full`, `L1_pruned`, `L2_skeleton`, `L3_alias`); distance metric ($\lambda_{df}$, $\lambda_{guard}$, `dist_max`) |
| `coverage` | feature coverage and gaps (`no_candidate_in_budget`, `no_candidate_reachable`, `filtered_by_policy`, `covered_transitively`) |
| `nodes[]` | id, role (`seed/callee/caller/transitive/external`), distance, compression, cost, kind, language, file, line, end_line, signature (params, returns.kind), features (S/F/O/R), contract (call_line, unpacks, passes_args), body |
| `edges[]` | from, to, type, weight, data_flow, guard, back_edge |
| `causal_path` | ordered stages (`entry/transform/sink/return`), forward. Included only for applicable `task_type`. |
| `warnings[]` | `BUDGET_OVERFLOW`, `TOKENIZER_FALLBACK`, `ORPHANED_RUNTIME_EVENTS`, `GRAPH_INCOMPLETE`, `LANGUAGE_TIER_2`, `LANGUAGE_TIER_3`, `RE_EXPORT_UNRESOLVED`, `DYNAMIC_ATTRIBUTES_DETECTED`, `CACHE_STALE`, `SCHEMA_VERSION_MISMATCH` |

Serialisation is deterministic: the same input produces byte-identical XML.

---

## 9. Performance

### 9.1 Measured

| Metric | Value | Conditions |
|---|---|---|
| Cold index, gin (1,474 symbols) | ~3.8 s | `docs/design_formalism.md` §5.6 |
| Warm index (cache hit), gin | ~0.14 s | target < 500 ms |
| Fuzzy seed suggestion | < 50 ms on ~50k symbols (an earlier exact version took 0.75–1.4 s on Django) | §9 of the formalism |
| Turn-1 manifest budget | 13,000 tokens | harness Arm 5 |
| R0/R1 T5 run | 192 cells, 0 failures | Kaggle, Arm 5 |

### 9.2 Complexity

| Stage | Cost |
|---|---|
| Parse + Pass 1 | $O(\text{bytes})$ |
| Pass 2 | $O(\text{call sites}\cdot c)$, where $c$ is the candidate-list length (bounded in practice by the builtin guard) |
| $D_{hybrid}$ | one Dijkstra on the undirected copy, $O((V+E)\log V)$ per query |
| Causal Dijkstra | $O((V+E)\log V)$, bounded by `d_max` |
| 0/1 knapsack (greedy + ≤20 swap attempts / ≤5 swaps) | $O(k\log k)$ for $k$ candidates |
| Submodular greedy | $O(k^2)$ worst case, with popcounts on Python ints; bounded by the novelty-streak and frontier caps |
| Token counting | `cl100k_base` (offline asset → tiktoken → regex fallback × 1.08 ceiling) |

### 9.3 Performance properties [IMPLEMENTED]

- **Strict budget.** Every admission checks the budget *before* appending (Invariant 2). If the seed alone does not fit, it is progressively degraded from L0 to L3. `fatal_seed_overflow` is set only when even L3 does not fit.
- **Determinism.** Sorted iteration in synthetic-edge creation, deterministic renderers and seeded bootstraps in the harness.

### 9.4 [PROPOSED] Targets for a native store

If §6.2 is built:
- load at memory-map speed ($\ll$ 50 ms for 100k nodes);
- upstream BFS at about $10^7$ edges/s;
- about 40 bytes per node and 8 bytes per edge, plus side tables.

These are engineering targets, not measurements.

---

## 10. Empirical evaluation (harness)

The harness (`harness/`, `benchmarks/`) compares retrieval "arms" on four pinned corpora (FastAPI, Django, Express, tRPC) with a fixed local model (`qwen2.5-coder:14b-instruct-q8_0`), seeds 42–44.

- Task types: T2 localization and T5 blast radius.
- The primary metric is **task success rate (TSR)**.
- Statistics: per-task 3-seed mean, 10,000-resample cluster bootstrap CI, Holm correction.
- Corpora are never pooled.
- Arms never see gold; gold is read only at analysis time.

### 10.1 T5 matrix (mean TSR [95% CI])

| Config | FastAPI (n=8) | Django (n=8) | Express (n=2) | tRPC (n=14) |
|---|---|---|---|---|
| B0M4 (stored M4) | 0.281 [0.179, 0.375] | 0.190 [0.088, 0.307] | 0.250 [0.000, 0.500] | 0.151 [0.080, 0.240] |
| R1 (LLM Turn 1) | 0.375 [0.240, 0.514] | 0.407 [0.172, 0.656] | 0.333 [0.000, 0.667] | 0.372 [0.224, 0.535] |
| R0 (rule: every caller row) | 0.518 [0.392, 0.633] | 0.558 [0.280, 0.809] | 0.500 [0.000, 1.000] | 0.666 [0.524, 0.802] |

| Config | Gold coverage | Selection precision | Selection recall |
|---|---|---|---|
| B0M4 | 0.197 | 0.312 | 0.196 |
| R1 | 0.461 | 0.507 | 0.460 |
| R0 | 0.878 | 0.681 | 0.874 |

R0 − R1 after Holm:
- FastAPI +0.143 (ns);
- Django +0.151 (ns);
- Express +0.167 (ns, anecdotal);
- **tRPC +0.294 (p < 0.001)**.

On independent change-based gold (4 Django seeds), answer recall is B0M4 0.042, R1 0.335, R0 0.355.

Caveat: T5 gold is PRISM-derived, which favours R0.

### 10.2 Interpretation

The large gain is from pipeline improvements (call resolution, Design C, manifest fixes), not from LLM selection.

**Adopted:** for T5 (impact) queries, Arm 5 now hands the deterministic caller slice straight to Turn 2 by default, with no Turn-1 model call. `HARNESS_PRISM_T5_RULE_SELECTOR=0` restores the LLM selection (R1).

---

## 11. Competitive analysis

**Caveat:** competitor facts come from public listings and READMEs found by web search on 2026-10-08. They were not reproduced in this project, and several sources disagree. Treat them as indicative.

| Dimension | **Prism** | **GitNexus** | **DeusData codebase-memory-mcp** |
|---|---|---|---|
| Implementation | Python, networkx + SQLite | Node/TypeScript CLI (`npx gitnexus analyze`), plus a web UI | Single static C binary, SQLite graph |
| Parsing | tree-sitter + Python `ast`; 7 languages | tree-sitter; about 8 languages (sources differ) | tree-sitter, "158 languages", optional LSP type resolution |
| Call resolution depth | Instance binding, return inference, CHA dispatch, explicit tentative/sentinel marking | Precomputed call chains / "processes" (depth not documented in the sources) | Structural + LSP hybrid for major languages |
| Blast radius | Upstream walk with contract-weighted callers ($\mu_1,\mu_2$), Design C manifest, measured T5 TSR | `impact` tool; `detect_changes` maps diffs to affected processes | Call-graph queries (impact tooling not verified) |
| Budgeted context | **Yes**: exact token budget, L0–L3 compression, strict budget invariant | Returns "complete context" in one call; no budget-aware compression documented | Compact query outputs; claimed 99.2% token reduction vs file-by-file grep |
| Search | Exact qname + fuzzy suggestion; tag queries | Hybrid BM25 + vector + RRF | Structural queries |
| Runtime feedback | **Yes**: traces/OTel reconcile edges (confirm / discover / unobserved / fuzzy) | Not documented | Not documented |
| Semantics | Tags + metamodel invariants + four-axis masks | Not documented at this level | Not documented |
| Scale claims | 0.14 s warm re-index on 1.5k symbols | — | Linux kernel (~28M LOC) in ~3 min (vendor figure) |
| Licence | Repository licence | PolyForm Noncommercial 1.0.0 | Open source (licence not verified) |
| MCP tools | 7 | about 7 code-intelligence tools | 14 |

**Positioning.** Prism's distinctive features are:
- **budgeted, multi-resolution context**;
- **runtime-reconciled edge weights**;
- explicit uncertainty marking (`TENTATIVE_*`, sentinels);
- a measured evaluation harness.

Its weaknesses relative to both competitors are:
- language breadth;
- raw indexing speed (Python and networkx);
- no semantic/vector search.

The [PROPOSED] binary store (§6.2) and incremental sync (§7.4) are the main levers on speed.

Sources: [GitNexus README](https://cdn.jsdelivr.net/gh/abhigyanpatwari/GitNexus@main/README.md), [GitNexus MCP docs](https://www.mintlify.com/abhigyanpatwari/GitNexus/mcp/overview), [Pebblous review](https://blog.pebblous.ai/blog/gitnexus-code-knowledge-graph-2026/en/), [codebase-memory-mcp landscape entry](https://landscape.jimmysong.io/projects/codebase-memory-mcp/), [letsdatascience coverage](https://letsdatascience.com/news/codebase-memory-mcp-speeds-ai-coding-agent-queries-a6a04a1b), [dev.co listing](https://dev.co/ai/mcp/codebase-memory-mcp).

---

## 12. Recent enhancements (this development cycle)

| Area | Change | Effect |
|---|---|---|
| Call resolution | CHA virtual dispatch with `dispatch_of`; single-family root; builtin guard (≥2 candidates → sentinel); `*args/**kwargs` as builtin; receiver-name typing; return-type inference marked tentative; attribute index | Django edge recall 0.590 → 0.707 |
| Edge pricing | `tentative_factor` applied consistently in causal weights, Dijkstra and upstream; synthetic edges gated on a confident common caller; force-admit only confident upstream | Fixed T2 selection regressions |
| Design C | Bidirectional manifest: upstream BFS to depth 6 excluding VERIFICATION, interleaved with downstream | T5 gold reach 0.30 → 0.89 |
| Manifest | Hop-count admission of the real call chain; full declarations (128-token cap, truncation marker, literal collapsing, `\|` → `¦`); `lines=N` | Fixed an Express T2 regression |
| Experiments | `harness/experiments/production_routing`: flags `HARNESS_PRISM_T5_RULE_SELECTOR` (R0, **on by default** for T5 since the ablation) and `HARNESS_PRISM_PRODUCTION_ROUTING` (R2, off), mutually exclusive | R0/R1 ablation (§10). R0 is the T5 default; R2 routing is parked. |
| Tests | +33 tests (call resolution 17, manifest 6, routing 10) | |

---

## Appendix A. Glossary

| Term | Definition |
|---|---|
| **Seed** | The symbol a query is about; always packed at distance 0 |
| **$G_C$** | Concrete multi-relational code graph |
| **qname** | Fully qualified symbol name; the node key |
| **Tentative edge** | Best-effort static or runtime-fuzzy link, discounted ×0.60 / ×0.65 |
| **Sentinel** | Placeholder node for an ambiguous or dynamic call the resolver refused to guess |
| **$D_{hybrid}$** | Bounded hop + tag-dissimilarity distance (classic path) |
| **Causal distance** | Dijkstra over $c=1/W$ with data-flow/guard boosts (v1.1 path) |
| **L0–L3** | Full / pruned / skeleton / interface-stub compression |
| **Manifest** | Turn-1 candidate list, one row per symbol |
| **Design C** | Bidirectional (callers + callees) manifest for blast-radius tasks |
| **RuntimeTrust** | Fraction of trace events matched to static symbols |
| **Orphan** | A trace event that matches no static symbol; always classified |
| **TSR** | Task success rate (harness metric) |
| **R0 / R1 / R2 / B0M4** | Rule selector / shipped LLM selector / parked routing experiment / stored M4 baseline |

## Appendix B. Constants

| Constant | Value | Module |
|---|---|---|
| `DEFAULT_LAMBDA` | 0.7 | `slicer.distance` |
| `DEFAULT_MAX_HOPS` (D_hybrid) | 10.0 | `slicer.distance` |
| `DEFAULT_TAG_BONUS_SAFETY_MARGIN` | 0.5 | `slicer.distance` |
| `DEFAULT_RUNTIME_CONFIDENCE_WEIGHT` | 0.5 | `slicer.distance` |
| `DEFAULT_HIGH_TRUST_EXTRA_DISCOUNT` | 0.5 | `slicer.distance` |
| `RELATION_TENTATIVE_CALL_WEIGHT` | 0.60 | `slicer.distance` |
| `RELATION_TENTATIVE_DYNAMIC_CALL_WEIGHT` | 0.65 | `slicer.distance` |
| `GAMMA_UNOBSERVED` | 0.50 | `slicer.distance` |
| `RESOLUTION_WEIGHT` | {L0: 1.0, L1: 0.7, L2: 0.4, L3: 0.15} | `slicer.knapsack` |
| Budget safety factor | 0.92 | `slicer.knapsack` |
| `MAX_SWAP_ATTEMPTS` / `MAX_SWAPS` | 20 / 5 | `slicer.knapsack` |
| `APPROX_L3_WEIGHT_TOKENS` | 15 | `slicer.knapsack` |
| `STRING_LITERAL_DENSITY_THRESHOLD` | 32 | `slicer.tokenizer` |
| Regex tokenizer ceiling | ×1.08 | `slicer.tokenizer` |
| `MAX_DOMINANT_HOP_DISTANCE` | 9 | `slicer.semantic_topology_score` |
| `LAMBDA_DATA_FLOW` / `LAMBDA_GUARD` | 0.25 / 0.15 | `traversal.causal_weights` |
| Gate confidences (predicate / exception / early-return) | 1.0 / 0.9 / 0.9 | `traversal.causal_weights` |
| `DEFAULT_MAX_HOPS` (packer) | 6.0 | `packer.submodular_knapsack` |
| `DEFAULT_BETA` / `DEFAULT_DELTA_MAX` | 0.10 / 10 | `packer.submodular_knapsack` |
| `DOMINANCE_SAFETY_BOUND` | 1.25 | `packer.submodular_knapsack` |
| `MAX_DOMINANT_SEED_DISTANCE` | 1 | `packer.submodular_knapsack` |
| `DEFAULT_UPSTREAM_MAX_HOPS` | 1.5 | `packer.submodular_knapsack` |
| `UPSTREAM_FRONTIER_CAP` | 3 | `packer.submodular_knapsack` |
| `PHASE_E_IMMUNE_DIST` / `SCOPE_GATE_MIN_DIST` | 1.0 / 2.0 | `packer.submodular_knapsack` |
| `MAX_ZERO_NOVELTY_K` | 10 | `packer.submodular_knapsack` |
| External budget fraction / floor / ceiling | 0.125 / 256 / 1024 | `packer.submodular_knapsack` |
| Node tag / envelope overhead | 280 / 450 tokens | `packer.submodular_knapsack` |
| Fuzzy seed threshold / top-k | 0.6 / 5 | `packer.submodular_knapsack` |
| `MU_RETURN_UNPACK` / `MU_NONTRIVIAL_ARGS` | 0.30 / 0.20 | `packer.blast_radius` |
| `CANDIDATE_INDEX_MAX_HOPS` | 3.0 | `packer.candidate_index` |
| `UPSTREAM_WALK_MAX_HOPS` | 6 | `packer.candidate_index` |
| `_MAX_CALLS_PER_SYMBOL` | 12 | `packer.candidate_index` |
| `MANIFEST_SIGNATURE_MAX_TOKENS` | 128 | `packer.candidate_index` |
| `RUNTIME_TRUST_LOW/HIGH_THRESHOLD` | 0.5 / 0.9 | `runtime.reconciler` |
| `NON_OBSERVATION_EXECUTION_THRESHOLD` | 10 | `runtime.reconciler` |
| `FUZZY_ANCHOR_LINE_WINDOW` | 25 | `runtime.reconciler` |
| `EXPORT_RESOLUTION_MAX_DEPTH` | 5 | `graph.symbol_table` |
| Index cache `_SCHEMA_VERSION` | 3 | `runtime.index_cache` |
| `file_cache_v2` `SCHEMA_VERSION` | 3 | `cache.sqlite_cache` |
| `MAX_TOKEN_BUDGET` | 128,000 | `mcp.security` |
| `RATE_LIMIT_PER_MINUTE` | 60 | `mcp.auth` |
| MCP `DEFAULT_TOKEN_BUDGET` / `DEFAULT_D_MAX` | 2000 / 5.0 | `mcp.server` |
| Compression thresholds | $D\le0.25$ L1; $\le0.55$ L2; else L3 | `slicer.knapsack` |
| Polysemy weights / threshold | 0.50 / 0.35 / 0.15; 0.85 | `slicer.distance` |

## Appendix C. File formats

| Path | Format | Contents |
|---|---|---|
| `.prism/cache/index.db` | SQLite | `files`, `snapshot` (§6.1) |
| `.prism/cache/*.db` (feature cache) | SQLite | `file_cache_v2` (§7.2) |
| `.prism/traces/run_*.jsonl` | JSON Lines | one `TraceRecord` per line: `caller`, `callee`, `callee_file`, `callee_line`, counts |
| reconciled runtime state | JSON | `confirmed_edges`, `discovered_edges`, `unobserved_edges`, `fuzzy_matched_edges` |
| `<prism_context>` | XML | §8.3 |
| Manifest | text | §4.3 row grammar |
| `.prism` binary store | **[PROPOSED]** | §6.2 |

**Assumption:** the feature-cache database lives under `.prism/cache/` beside `index.db`, following the index cache's own convention. The exact filename is defined in `prism.cache.sqlite_cache`.

## Appendix D. Pseudocode

### D.1 Build

```python
def build_pipeline(repo, tier):
    files = discover_files(repo, tier)
    hashed = {f: sha256(read(f)) for f in files}
    if (snap := index_cache.load(repo, hashed, tier, engine_version())):
        return snap.rehydrate(parsed=parse_from_same_bytes(hashed))
    b = ConcreteGraphBuilder()
    for f in files: b.pass1_collect_definitions(parse(f))
    for f in files: b.pass2_resolve_calls(f)
    M = TaggingEngine().tag_graph(b)
    index_cache.store(repo, hashed, b, M)
    return b, M
```

### D.2 Call resolution (per call site)

```python
def resolve(call, scope):
    for rule in (lexical, import_export, instance_binding, return_inference,
                 receiver_name, go_receiver, family_root):
        targets = rule(call, scope)
        if len(targets) == 1:
            return edge(targets[0], tentative=rule.is_best_effort or binding.inferred)
        if len(targets) >= 2 and is_builtin_name(call.name):
            return sentinel(UnresolvedPolymorphicNode, call)
    return sentinel_or_external(call)

def expand_virtual_dispatch(base_edge):
    for override in cha_overrides(base_edge.target):
        add_edge(base_edge.source, override, kind="TENTATIVE_CALL", dispatch_of=base_edge.target)
```

### D.3 Design C manifest

```python
def build_candidate_manifest(engine, seed, budget, direction="both"):
    down = {v for v, d in causal_dist(seed).items() if d <= 3.0}
    down |= real_call_chain_reachable(seed)                 # hop-count admission
    up = bfs_reverse(seed, relations=("CALLS", "INSTANTIATES"), depth=6,
                     exclude=lambda v: role(v) == VERIFICATION)
    rows = interleave(sorted(up, key=hop), sorted(down, key=lambda q: dist_w.get(q, inf)))
    out = [row(seed, "seed")]
    for q in rows:
        r = row(q)                                          # qname|role|kind|sig|calls=[..]|…|lines=N
        if tokens(out + [r]) > budget: break
        out.append(r)
    return "\n".join(out), {parse_qname(r) for r in out}
```

### D.4 Submodular knapsack with upstream frontier

```python
def select(seed, budget, beta=0.10, dmax=10):
    assert beta * dmax < 1.25
    dist = dijkstra(causal_graph, seed, horizon=d_max)
    chosen, covered, frontier = [seed], mask(seed), set(succ(seed)) | upstream(seed)
    zero_streak = 0
    while frontier and zero_streak < 10:
        v = max(frontier, key=lambda v: value(v, dist, covered) / cost(v))
        frontier.discard(v)
        if not scope_gate(v, dist): continue
        if fits(chosen, v, budget):                         # check before append
            chosen.append(v)
            zero_streak = zero_streak + 1 if novelty(v, covered) == 0 else 0
            covered |= mask(v)
            frontier |= set(succ(v)) | upstream(v, cap=3, max_hops=1.5)
        elif dist[v] <= 1: stub_pack(v)
    return chosen

def upstream_weight(f, v):
    return w_base(f, v) * tentative_factor(f, v) * (1 + 0.30 * binds_return(f, v) + 0.20 * nontrivial_args(f, v))
```

### D.5 Reconciliation

```python
def reconcile(events, G):
    matched = 0; exec_count = Counter(e.caller for e in events)
    confirmed, discovered, fuzzy, orphans = set(), set(), set(), []
    for e in events:
        u, v = resolve_static(e.caller), resolve_static(e.callee)
        if u and v:
            matched += 1
            (confirmed if G.has_edge(u, v) else discovered).add((u, v))
        elif u and (t := fuzzy_anchor(e.callee_file, e.callee_line, window=25)):
            matched += 1; fuzzy.add((u, t))
        else:
            orphans.append(classify_orphan(e))              # never None
    trust = matched / len(events) if events else 1.0
    unobserved = {(u, v) for (u, v) in static_calls(G)
                  if exec_count[u] >= 10 and (u, v) not in confirmed}
    apply(G, confirmed, discovered, fuzzy, unobserved, high_trust=trust >= 0.9)
    return trust, orphans
```

### D.6 Effective hop length (D_hybrid)

```python
def hop_cost(u, v, d, g):
    w = RELATION_STRUCTURAL_WEIGHT.get(d.get("relation", "CALLS"), 1.0)
    w *= {"TENTATIVE_CALL": 0.60, "TENTATIVE_DYNAMIC_CALL": 0.65}.get(d.get("kind"), 1.0)
    if d.get("unobserved_in_traces"):
        w *= GAMMA_UNOBSERVED                                   # 0.50 → longer
    w /= sqrt(hub(g.in_degree(u)) * hub(g.in_degree(v)))        # hub(n) = 1 + log1p(max(n-1, 0))
    cost = 1.0 / w
    if d.get("confidence") == "CONFIRMED_RUNTIME":
        cost *= 0.5 * (0.5 if d.get("high_trust_runtime") else 1.0)   # shorter
    return cost
```

---

*End of specification.*
