# The Four-Axis Semantic Model and the T5 traversal problem

_Analysis only: no code was modified and nothing ran on Kaggle. Source citations are against `harness/eight-arm-slm` at `94ddc43`. Empirical checks rebuilt the pinned M4 checkouts locally (`prism.cli.build_pipeline(root, use_cache=False)`, plus `prism.semantics.extractor.compute_feature_masks`, the uncached variant, so nothing was written to the corpora). They then looked at every symbol within 3 upstream hops of each T5 seed. Follows `prism_t5_diagnosis.md` and `dynamic_hop_investigation.md`._

**Answer in one line.** The mechanism exists: PRISM v1.1's **Four-Axis Semantic Model**. It is a per-symbol feature bitmask over Substance, Form, Output and Role. It has no direction, distance, edge-weight or relation-type axis, and PRISM's two-pass path, which is what Arm 5 runs, never uses it for selection. It is orthogonal to the T5 problem and cannot replace Design C. A *different* existing index-time field, `SymbolRole` (IMPLEMENTATION / VERIFICATION / INTERFACE, not one of the four axes), is directly useful to Design C as its production filter.

---

## 1. Search report: what is the 4-axis mechanism?

**Search.** I grepped `4-axis`, `four-axis`, `four axes`, `axis`, `axes`, and `four`/`4-dimensional` across the repo, excluding `.benchmarks/` and the M4 report outputs. The hits that name the mechanism:

- `src/prism/semantics/__init__.py:1`: "v1.1 Four-Axis Semantic Model: Substance, Form, Output, Role"
- `src/prism/semantics/bitmask.py:1`: "v1.1: The Four-Axis Coordinate Space, encoded as a single unsigned 64-bit bitmask per symbol."
- `docs/design_formalism.md:733`: "## 8. Causal Coupling & Four-Axis Semantic Model (Prism v1.1)", and §8.1 "The Four-Axis Coordinate Space"
- `src/prism/semantics/extractor.py:1`: "the one entry point most callers need - composes all four axes"
- Uses: `src/prism/packer/submodular_knapsack.py` (lines 104, 427-441, 617-690, 849-876, 1559-1599, 1795), `src/prism/query/tag_filter.py:2` ("over a symbol's real four-axis feature mask"), `src/prism/runtime/index_cache.py:339`, `src/prism/cache/sqlite_cache.py:33`, `src/prism/external/index.py:39`

`docs/architecture_boundaries.md` has no axis content. The other "axis" hits (`benchmarks/final_sweep/*`, `benchmarks/metrics/fcc.py`, `harness/*`) are plot axes or unrelated uses of the word.

**The four axes** (quoted, `docs/design_formalism.md` §8.1):

> Every symbol `v` gets a coordinate `Phi(v) = (S(v), F(v), O(v), R(v))` - Substance, Form, Output, Role - packed into one unsigned 64-bit integer (`prism.semantics.bitmask.FeatureBit`, an `IntFlag`) so the knapsack's marginal-coverage comparisons reduce to `&`/`|`/`int.bit_count()`

| axis | bits | values (from `FeatureBit`) | computed in | what it describes |
|---|---|---|---|---|
| Substance S(v) | 0–9 | `SINK_NETWORK_IO`, `SINK_DATABASE_IO`, `SINK_FILESYSTEM_IO`, `SINK_PROCESS_IO`, `SINK_TIME_IO`, `SINK_RANDOMNESS`, `SINK_PURE_COMPUTE` | `prism/semantics/substance.py` | which I/O sinks the symbol's own body touches (folded up one hop through thin wrappers) |
| Form F(v) | 10–24 | `FORM_LINEAR`, `_RETRY_LOOP`, `_BRANCH_DISPATCH`, `_PIPELINE`, `_GUARD_EARLY_EXIT`, `_VALIDATOR`, `_BATCH_LOOP`, `_WRAPPED_TRY`, `_RECURSIVE`, `_ASYNC_CONCURRENT` | `prism/semantics/form.py` | the syntactic motif of the body |
| Output O(v) | 25–34 | `OUTPUT_PREDICATE`, `_COMMAND`, `_QUERY`, `_FACTORY`, `_TRANSFORMER`, `_AGGREGATOR`, `_FLUENT`, `_ASYNC_DEFERRED`, `_GUARD` | `prism/semantics/output.py` | the shape of the return contract |
| Role R(v) | 35–44 | `ROLE_ENTRYPOINT` (in = 0, out > 0), `ROLE_ORCHESTRATOR` (fan_out ≥ 4, fan_in ≤ 2), `ROLE_ADAPTER`, `ROLE_LEAF_UTILITY` (fan_in ≥ 5, fan_out ≤ 1, pure), `ROLE_BRIDGE` (fan_in ≥ 5, fan_out ≥ 5), `ROLE_PUBLIC_API`, `ROLE_LEAF_SERVICE` | `prism/semantics/role.py` (fan-in/fan-out over `builder.calls_graph`) | the node's topological neighbourhood shape |

**When computed.** All four are computed at index time: `compute_feature_masks(builder)` / `compute_feature_masks_cached(builder, repo_root)` in `prism/semantics/extractor.py:58, 92`. The file-local parts are cached per file in `prism.cache.sqlite_cache`. The result is one `int` per function or method.

**How the axes are combined.** There is no weighted per-axis sum. The mask is used as a **novelty bonus** on top of distance decay, in the one-pass greedy knapsack (`submodular_knapsack.py:427-441`):

> `V(v) = TopologicalDecay(dist_w) * (1 + beta * delta_feat)` - `TopologicalDecay(d) = 1/(1+d)**2`, `delta_feat = min(popcount(candidate_mask & ~covered_mask), delta_max)`

The defaults are `DEFAULT_BETA = 0.10` and `DEFAULT_DELTA_MAX = 10`. The bonus is deliberately capped so it can never reorder hops: `beta * delta_max` must stay below `DOMINANCE_SAFETY_BOUND = 1.25`, which is asserted on every call (`submodular_knapsack.py:328, 608`). That is the module's \"1-hop-vs-2-hop dominance\" guarantee. The mask has two other uses:
- **Phase E scope gate.** A candidate beyond `SCOPE_GATE_MIN_DIST` that is outside the seed's module prefix stays in scope only if it shares a Substance sink bit with the seed (`_is_in_scope`, `submodular_knapsack.py:658-668`).
- **Tag queries.** `prism/query/tag_filter.py` runs queries such as `\"#network AND NOT #test\"` against the masks.

**What it is used for today.** The one-pass path, `pack_symbol_context` → `select_submodular_context` (`prism causal-query`, the debug/localization packer). **It is not used for selection on the two-pass path Arm 5 runs:**
- `build_candidate_manifest` (`candidate_index.py:162-246`) never reads a feature mask. Downstream candidates come from distance plus a module-prefix scope rule; upstream candidates are the top 3 direct callers by `W_upstream`.
- `pack_symbol_context_requested` (`submodular_knapsack.py:1744`) is, in its own docstring's words, \"no knapsack, no density competition\". It computes `feature_masks` only to stamp `feature_mask` onto each item and OR them into `covered_mask` for reporting.

So the Four-Axis Model influenced neither T2 nor T5 selection in any M4 Arm 5 cell.

## 2. Relevance to T5

| question | answer | evidence |
|---|---|---|
| Does an axis measure closeness to the seed **upstream**? | **No** | All four axes are properties of a symbol alone (its body, its return, its own fan-in and fan-out), never of its relationship to the seed. |
| Does an axis measure **distance** from the seed? | **No** | Distance is a separate factor, `TopologicalDecay(dist_w)`, computed per query by Dijkstra (`compute_topological_distances`), outside the mask. |
| Does an axis measure **edge weight**? | **No** | The mask is per node. Edge weights are the separate, derived `causal_weights` / `blast_radius` models (found near-constant in `dynamic_hop_investigation.md`). |
| Does an axis measure **relation type** (caller / callee / import)? | **No** | The `caller` / `callee` / `transitive` labels come from `_classify_role` (`submodular_knapsack.py:983`), which is unrelated to the Role **axis** despite the shared word. |
| Does an axis measure **centrality / hub-ness**? | **Partially, coarsely** | The Role axis encodes fan-in/fan-out thresholds as bits (`ROLE_BRIDGE` fan_in ≥ 5 and fan_out ≥ 5, `ROLE_LEAF_UTILITY` fan_in ≥ 5, `ROLE_ORCHESTRATOR` fan_out ≥ 4). These are binary at a threshold of 5, so `django.urls.base.reverse` (709 direct callers) and a function with 5 callers get the same bit. A flood check needs the raw in-degree, which is a single `G.in_degree(seed)` call and needs no axis. |

**Availability.** All four axes are computed during indexing and cached, so they would cost nothing extra for a T5 fix. They simply do not carry the signals the fix needs.

**Do the axes separate gold from non-gold upstream callers?** I measured every candidate within 3 upstream hops of every T5 seed (CALLS/INSTANTIATES in-edges), after removing VERIFICATION-role symbols. The second number in each pair is the gold rate (precision) with and without the property:

| corpus | candidates (≤3 hops) | after removing VERIFICATION | gold | shares a Substance bit with seed (yes / no) | shares Form (yes / no) | shares Output (yes / no) | novel mask bits (gold / non-gold) | by hop (1 / 2 / 3) |
|---|---|---|---|---|---|---|---|---|
| FastAPI | 81 | 39 | 28 | 0.700 / 0.737 | 0.900 / 0.655 | 0.800 / 0.632 | 3.04 / 4.64 | 0.882 / 0.667 / 0.250 |
| Django | 1,510 | 299 | 41 | 0.125 / 0.214 | 0.167 / 0.122 | 0.110 / 0.167 | 2.88 / 2.67 | 0.238 / 0.081 / 0.045 |
| Express (n=2) | 8 | 8 | 2 | — / 0.250 | 0.200 / 0.333 | 0.667 / 0.000 | 3.0 / 3.0 | 0.500 / 0.000 / 0.000 |
| tRPC | 102 | 102 | 88 | 0.863 / — (every candidate shares one) | 0.854 / 0.900 | 0.933 / 0.807 | 2.99 / 3.00 | 0.861 / 0.829 / 0.920 |

- **No axis separates gold consistently.** Substance points the wrong way on Django (0.125 vs 0.214) and FastAPI, and is uninformative on tRPC. Form and Output help on FastAPI but invert on Django.
- **Hop distance is the only consistent discriminator** on FastAPI and Django, the two corpora where ranking matters.
- **`SymbolRole` is the large win.** On Django it removes 1,208 of the 1,510 candidates, with all 3 gold losses inside `django_t13_003` (see below). On FastAPI it removes 42 of 81, with no gold loss. It agrees with a path-based test filter on 1,195 of the 1,211 Django VERIFICATION symbols, and on all 42 FastAPI ones. It is **not** one of the four axes: it is `SymbolInfo.role`, set in Pass 1 (`prism/graph/symbol_table.py:20`; classifiers at `concrete_builder.py:3248-3270`).
- **Exception:** `django_t13_003_blast_field_clean`'s curated gold *is* 3 test call sites (its YAML header says \"the 3 curated test call sites\"), so a VERIFICATION filter costs that task its gold.

## 3. Could the 4-axis mechanism replace Design C?

**No.** Each of the three suggested reconfigurations needs an axis that does not exist:

- **\"Promote the distance-upstream axis.\"** There is no such axis. The only distance in the scoring function is `dist_w`, and in the one-pass packer that is the **forward** Dijkstra map. `select_submodular_context` grows its frontier only through \"the admitted node's own successors\" (its docstring). Its upstream frontier is `graph.predecessors(seed_id)` within `upstream_max_hops = 1.5`, capped at `UPSTREAM_FRONTIER_CAP = 3`: the same direct-callers-only limit as the manifest.
- **\"Raise the direction axis's weight for T5.\"** No axis encodes direction.
- **\"Prioritise caller edges.\"** No axis encodes relation type. The caller label comes from `_classify_role`, after candidates have already been chosen.

**Even promoting any axis would break a proved invariant.** The novelty bonus is bounded so that it can never reorder hops (`beta * delta_max < DOMINANCE_SAFETY_BOUND`, asserted). Making an axis \"dominate the ranking\" means violating that check, and it would still rank only the candidates the traversal supplies.

**It is also not on Arm 5's code path.** The two-pass manifest and Turn-2 hydration do not read the masks. Reconfiguring the mechanism would not change any M4 Arm 5 cell unless the two-pass path were also rewired to rank through it, which is a larger change than Design C.

## 4. Interaction with Design C

- **No competition.** Design C produces the T5 candidates (the Turn-1 manifest). The Four-Axis mask is a scoring attribute of symbols, not a candidate generator. On the two-pass path, the selector is the Turn-1 LLM, not a ranker.
- **Feeding C's candidates into the 4-axis ranker is possible but not useful.** `compute_candidate_value` could order C's upstream candidates, but its distance term would need the reverse map, and its mask term shows no consistent gold signal (§2). Hop order alone does better.
- **Manifest generator for T5: Design C,** in `candidate_index.build_candidate_manifest`. The masks can stay as they are: stamped on items, not used to select.
- **What C should reuse** is `SymbolRole`, the existing VERIFICATION exclusion. The one-pass packer already applies it to upstream callers (`submodular_knapsack.py:932-942`: \"a VERIFICATION-role caller (a test method calling straight into the seed) is the single most common real-world shape of 'strongest upstream caller' that is never actually part of a causal pipeline\"). The two-pass manifest's upstream admission does **not** apply it. That gap is why 10 of PRISM's 50 Django T5 caller slots went to test code (`prism_t5_diagnosis.md` §2).

## 5. Recommendation

**The Four-Axis Semantic Model exists and is orthogonal to the T5 problem. It neither replaces nor meaningfully complements Design C.** It describes what a symbol *is* (I/O sinks, body motif, return shape, coarse fan-in/fan-out), not where it sits *relative to the seed*, and the two-pass path Arm 5 runs never ranks by it. The closest relevant existing piece is `SymbolRole`, a separate index-time field that is not one of the axes. It should become Design C's production filter in place of the path regex used in `dynamic_hop_investigation.md`.

**Modified Design C.** In `candidate_index.build_candidate_manifest`, when the task is blast-radius:
1. Run PRISM's existing reverse Dijkstra (`compute_topological_distances(..., direction=\"reverse\")`) alongside the existing forward map.
2. Drop upstream candidates whose `SymbolRole` is VERIFICATION, the same rule `select_submodular_context` already applies to upstream callers.
3. Order the remaining upstream candidates by (hop, then `W_upstream` tier). Interleave them with the downstream candidates in distance order until a shared manifest budget is spent.
4. Label callers at 2+ hops with an upstream role rather than `transitive`, so Turn 1 sees them as callers.

Leave the four-axis masks unchanged, stamped on hydrated items and not used to select. The `SymbolRole` filter reuses an index-time field and an existing rule, so it removes the new path-regex code from the earlier estimate and leaves Design C at roughly 60–90 core LOC.

Two caveats carry over:
- **One task's gold is test code.** `django_t13_003`'s curated gold is test call sites, so the filter costs that one task, and this should be disclosed.
- **The measurement is circular.** T5 gold is PRISM's own transitive-caller closure, so a reverse walk on the same graph scores well partly by construction (`dynamic_hop_investigation.md` §6).
