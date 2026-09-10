# Prism: Mathematical Design Formalism

This document is the formal counterpart to the README's prose description -
the exact graph model, scoring functions, compression taxonomy, knapsack
formulation, and dynamic-reconciliation model Prism's engine implements, with
pointers to the concrete module/constant each piece lives in. Every formula
below is implemented, not aspirational - where a constant is cited, it is the
real, currently-shipping value.

## 1. Graph Definition

Prism builds one directed graph per repository, the **Concrete Graph**
`G_C = (V, E)` (`prism.graph.concrete_builder.ConcreteGraphBuilder.graph`).

### 1.1 Node taxonomy

Every node `v in V` is one of four kinds (`SymbolInfo.kind`,
`prism.graph.symbol_table`):

| Kind | Meaning |
|---|---|
| `function` | A free function or non-method callable. |
| `method` | A function bound to an enclosing class. |
| `class` | A class/struct/interface definition. |
| `attribute` | A module/class/instance-level simple-name binding with no `def`/`class` of its own (Issue #16). |

A node may additionally be one of two **sentinel** kinds, which are never
real symbols and never carry an outgoing edge (Tasks 1-3, Issues #6/#7's
counterparts for ambiguity/dynamism):

- `UnresolvedPolymorphicNode` - an ambiguous call site whose winning
  candidate never cleared the Polysemy threshold (Section 2.3).
- `DynamicEdgeSentinel` - a call site whose real target only exists at
  runtime (`getattr`/`setattr`, `eval`/`exec`, subscript/map dispatch).

### 1.2 Edge relations

`E` is a set of typed, directed edges. The relations Prism resolves:

| Relation | Meaning | Traversable (`TRAVERSABLE_RELATIONS`) |
|---|---|---|
| `CALLS` | A resolved call expression. | Yes |
| `INSTANTIATES` | A constructor call (`Foo()`, `new Foo()`). | Yes |
| `EXTENDS` | Class inheritance. | Yes (Issue #9) |
| `IMPLEMENTS` | Interface implementation. | Yes (Issue #9) |
| `OVERRIDES` | A subclass method shadowing an MRO ancestor's same-named method. | Yes (Issue #9) |
| `READS_STATE` | A bare attribute read (`self.x`, not a write or call). | No |

"Traversable" (`prism.graph.concrete_builder.TRAVERSABLE_RELATIONS`) defines
the sub-graph `G_C' subseteq G_C` the Distance Engine and Knapsack Packer
actually walk - the "reachable from seed" neighborhood. `READS_STATE` is
excluded: it pulls in unrelated attribute nodes with no comparable
reachability requirement (unlike `EXTENDS`/`IMPLEMENTS`/`OVERRIDES`, which
must be traversable or an inherited method call like `self.validate()` has
no reachable definition at all - the "phantom method" failure Issue #9
closes).

Each `CALLS` edge additionally carries a `CallSiteContext`
(`prism.graph.call_site`): `call_kind`, `inside_loop`, `inside_try_catch`,
`guarded_by_null_check`, `args_passed_count`, `argument_flow`, `bound_to`,
`call_site_role`, `is_return_bound`. `bound_to is not None` - "this call's
result was captured into a variable" - is the signal Data-Flow Centrality
(Section 3.2) and the Fractional-Relaxation diagnostic (Section 4.3) both
read directly off the edge.

## 2. Scoring Function

### 2.1 D_hybrid

For a seed `s` and a reachable node `u`, `DistanceEngine._d_hybrid`
(`prism.slicer.distance`) computes:

```
raw_gc(s, u)   = hops(s, u) / max_hops
d_hat_gt(s, u) = min(tag_distance(sigma(s), sigma(u)) / MAX_TAG_DISTANCE, 1.0)

min_hop_gap    = lambda_weight / max_hops
tag_bonus_cap  = tag_bonus_safety_margin * min_hop_gap
tag_bonus(s,u) = tag_bonus_cap * d_hat_gt(s, u)

D_hybrid(s, u) = min(lambda_weight * raw_gc(s, u) + tag_bonus(s, u), 1.0)
```

`hops(s, u)` is the shortest weighted path in the undirected view of `G_C'`
(`DistanceEngine._weighted_undirected`); `tag_distance` is the shortest path
in the metamodel tag graph `G_T` between the seed's and `u`'s own tag sets
(`prism.graph.metamodel.SemanticMetamodel.get_tag_distance`).

Defaults (`DistanceConfig`): `lambda_weight = 0.7`, `max_hops = 10.0`,
`tag_bonus_safety_margin = 0.5`.

**Edge weight** (hop cost) in `hops(s, u)`'s own weighted graph
(`RELATION_STRUCTURAL_WEIGHT`, Issue #9):

| Relation | Structural weight | Effective hop cost (`1 / weight`) |
|---|---|---|
| `CALLS` / `INSTANTIATES` | 1.00 | 1.00 |
| `OVERRIDES` | 0.90 | ~1.11 |
| `EXTENDS` | 0.85 | ~1.18 |
| `IMPLEMENTS` | 0.80 | 1.25 |

A `confidence="CONFIRMED_RUNTIME"` edge (Section 5) additionally multiplies
its cost by `runtime_confidence_weight` (0.5 default), and by a further
`high_trust_extra_discount` (0.5 default) when RuntimeTrust >= 0.9.

### 2.2 Topological Monotonicity (Issue #8, Invariant #1)

**Claim:** `hops(s, a) < hops(s, b) => D_hybrid(s, a) < D_hybrid(s, b)`,
for any tag sets.

**Proof.** Take the worst case: `a` at maximum tag penalty
(`d_hat_gt = 1`, `tag_bonus(a) = tag_bonus_cap`), `b` at zero penalty
(`tag_bonus(b) = 0`). Since `hops(s,b) >= hops(s,a) + 1`:

```
D_hybrid(s,b) - D_hybrid(s,a)
  >= lambda_weight * (raw_gc(b) - raw_gc(a)) - tag_bonus_cap
  >= lambda_weight / max_hops - tag_bonus_safety_margin * (lambda_weight / max_hops)
  =  min_hop_gap * (1 - tag_bonus_safety_margin)
  >  0   (since tag_bonus_safety_margin < 1.0)
```

At the shipping default (`tag_bonus_safety_margin = 0.5`), the tag term's
entire possible range is half of one hop-step - a 2x safety margin, not a
knife's-edge bound. (This holds on the raw, unclamped combination; the
final `min(D, 1.0)` saturation for two nodes whose raw scores *both* already
exceed 1.0 is an accepted, inherent limitation of any bounded distance
metric - see `distance.py`'s own module docstring.)

### 2.3 Polysemy Disambiguation

For an unresolved bare call whose simple name matches >= 2 repo-local
candidates `C` (`prism.graph.symbol_table.score_candidate`,
`GlobalSymbolTable.candidates_for_simple_name`):

```
Score(C) = 0.50 * NamespaceMatch(C) + 0.35 * ArityMatch(C) + 0.15 * LocalityDistance(C)
```

- `NamespaceMatch(C) in {0.0, 1.0}` - 1.0 iff the caller's file imports
  `C`'s module (or a parent/child package of it) or shares it outright.
- `ArityMatch(C) in {0.0, 1.0}` - 1.0 iff the call's argument count equals
  `C`'s own declared parameter count (self/this excluded for Python
  methods); 0.0 if the candidate's parameter count can't be determined.
- `LocalityDistance(C) in {1.0, 0.6, 0.2}` - same file, same package, or
  external, respectively.

`argmax_C Score(C) >= POLYSEMY_THRESHOLD (0.85)` binds a normal `CALLS`
edge to the winner. Otherwise an `UnresolvedPolymorphicNode` sentinel is
emitted, carrying `Tags(Unresolved) = union_{C in Candidates} Tags(C)` -
the conservative worst-case tag union across every candidate.

## 3. Compression Taxonomy

`ASTCompressor`/`CompressionProvider` (`prism.slicer.compressor`) render
each node at one of four resolution levels, `J = {L0, L1, L2, L3}`
(Issue #10):

| Level | Name | Content | Python implementation |
|---|---|---|---|
| L0 | Full | Complete source, signatures, docstrings. | `_raw_slice` |
| L1 | Pruned | Control flow, calls **with real arguments**, assignments; docstrings/logging stripped. | `ArgPreservingSkeletonizer` |
| L2 | Skeleton | Control-flow shape only; call arguments collapsed to `...`; trailing tags/raises/calls annotation. | `ControlFlowSkeletonizer` |
| L3 | Interface | One-line signature stub. | `_stub_signature` |

A `BehavioralContract`-bearing node at L2 renders as a compact YAML contract
block instead of the code fence when one is available
(`markdown._CONTRACT_RESOLUTIONS = {2}`) - L1 is deliberately excluded from
that override, since its entire purpose is showing real argument values a
YAML interface summary would hide.

### 3.1 Assignment criteria

Base resolution is distance-driven (`DistanceEngine.resolution_for_distance`):

```
resolution(D_hybrid) = L1  if D_hybrid <= 0.25
                      = L2  if D_hybrid <= 0.55
                      = L3  otherwise
```

### 3.2 Data-Flow Centrality (Issue #10.2)

```
DataFlowCentrality(v) = |{(u, v) in E : bound_to(u, v) is not None}|
```

- proxied by the count of distinct incoming `CALLS` edges whose
  `CallSiteContext.bound_to` is set (the caller captured `v`'s return
  value into a variable, not a bare fire-and-forget call).

```
if DataFlowCentrality(v) >= DATA_FLOW_CENTRALITY_THRESHOLD (2):
    resolution(v) <= L1   (a hard floor, not merely a starting point -
                            if L1 doesn't fit the budget, v is excluded
                            entirely rather than further degraded)
```

## 4. Knapsack Formulation

`ContextKnapsackPacker.pack` (`prism.slicer.knapsack`) packs the seed
(pinned at L0) plus its nearest neighbors, under a real BPE token budget
(Issues #11/#13, `prism.slicer.tokenizer` - tiktoken's `cl100k_base`
encoding, falling back to a deterministic regex approximation only if that
encoding table can't be loaded).

### 4.1 The 0/1 formulation

For candidate `i` with tier set `J_i subseteq {L0, L1, L2, L3}`, weight
`w_{i,j}` = real token cost at tier `j` (heading + fence + content, exactly
tokenized - `_wrapping_overhead_tokens`), and `x_{i,j} in {0, 1}`:

```
maximize   sum_i sum_{j in J_i} u_{i,j} * x_{i,j}
subject to sum_i sum_{j in J_i} w_{i,j} * x_{i,j} <= B_token
           sum_{j in J_i} x_{i,j} <= 1              (at most one tier per item)
           x_{i,j} in {0, 1}
```

`B_token = token_budget * SAFETY_MARGIN (0.92)`, further reduced by
`reserved_overhead_tokens` (content the serializer renders outside the
packed-items loop - the hierarchical intent profile, the idiomatic
blueprint). Utility `u_{i,j}` is realized as `1 / (D_hybrid(s, i) + 1)`,
consistent with the distance-first candidate ordering the greedy loop
already sorts by.

Solved greedily: candidates admitted in `D_hybrid` order, each downgraded
tier-by-tier until it fits or is excluded - `O(n log n)` for the sort, `O(1)`
amortized per candidate for the downgrade loop, well under the real-time
budget a `prism query` invocation needs.

### 4.2 Swap-Refinement Pass (Issue #12)

The greedy pass alone can starve out a still-unselected, more-relevant
candidate that arrives later in sorted order once an earlier one fails to
fit even at L3. A bounded post-hoc pass
(`ContextKnapsackPacker._swap_refine`) checks, for each of the closest
`MAX_SWAP_ATTEMPTS` (20) unselected candidates, whether it can displace the
least-relevant already-packed *ordinary* item (never the seed, a
"requires" architectural-path obligation, or an infallible-leaf/sentinel
item) without exceeding budget - capped at `MAX_SWAPS` (5) actual
substitutions. A cheap distance-only pre-check (no rendering) gates every
attempt before it pays for an L3 render, keeping the pass's own cost
bounded independent of how large the full candidate pool is.

### 4.3 Fractional relaxation (diagnostic only)

`ContextKnapsackPacker._fractional_relaxation_bound` reports the classic
LP relaxation - candidates packed in arbitrarily divisible fractions,
ranked by `value/weight` - as `PackResult.fractional_upper_bound`, purely
for comparison against the real integer result. Value is proxied by
`1 / (1 + D_hybrid)`; weight by a flat per-candidate footprint estimate
(`APPROX_L3_WEIGHT_TOKENS = 15.0`), cheap enough to compute over every
candidate without rendering each one. Never used to gate a real admission
decision.

### 4.4 Strict Budget Compliance (Invariant #2)

By construction, `ActualTokens(RenderedContext) <= B_token` for any
admitted item set: every admission check (`total_tokens + cost >
self._admission_budget`) happens *before* appending to `items`, and the
Swap-Refinement Pass only ever accepts a substitution when the post-swap
total still fits. `B_token`'s own `SAFETY_MARGIN` (0.92) and
`reserved_overhead_tokens` absorb the residual estimation error inherent
to any tokenizer (including a real BPE one, since the packer's own
per-item accounting doesn't render the *complete* final document
incrementally - see the module docstring's "not byte-exact" note on
`_wrapping_overhead_tokens`).

## 5. Dynamic Reconciliation Model

`GraphReconciler.reconcile` (`prism.runtime.reconciler`) merges observed
runtime events (`.prism/traces/run_*.jsonl`, or an ingested OpenTelemetry
export) into `G_C`, purely additively - nothing here ever removes or
downgrades a statically-found edge.

### 5.1 Confidence

A traced `caller -> callee` edge the static resolver *also* found is
promoted: `confidence = "CONFIRMED_RUNTIME"`. A traced edge the static
resolver missed (reflection, runtime-injected dispatch) becomes a new edge,
`provenance = "RUNTIME_DISCOVERED"`, at the same confidence level.

### 5.2 Orphan Event Classification (Issue #15)

Every event that fails to attach to a real static symbol gets exactly one
`OrphanReason` (`prism.runtime.reconciler.classify_orphan`) - Invariant #6,
"zero silent orphans":

```
NO_STATIC_MATCH      - no symbol identity at all (caller unresolved)
ANONYMOUS_CLOSURE     - callee/caller name matches a lambda/comprehension marker
AMBIGUOUS_SIGNATURE   - callee's bare simple name matches >= 1 real, differently-
                        qualified static symbol (decorator/version-drift signal)
NATIVE_OR_C_EXTENSION - an OTel-sourced event naming a symbol with no
                        corresponding indexed Python source
DYNAMIC_DISPATCH      - none of the above; a genuinely novel symbol name
                        (eval/exec/metaclass-injected)
```

### 5.3 RuntimeTrust

```
RuntimeTrust = MatchedEvents / TotalEvents        (1.0 when TotalEvents = 0)
```

- `RuntimeTrust < RUNTIME_TRUST_LOW_THRESHOLD (0.5)`: a diagnostic warning
  is emitted (`ReconciliationResult.trust_warning`) - static analysis
  remains the primary ground truth for that trace.
- `RuntimeTrust >= RUNTIME_TRUST_HIGH_THRESHOLD (0.9)`: every
  confirmed/discovered edge from that reconciliation is marked
  `high_trust_runtime = True`, which `DistanceEngine` gives an additional
  `high_trust_extra_discount` (0.5) multiplier on top of the normal
  `runtime_confidence_weight` hop-cost discount (Section 2.1).

A `DYNAMIC_DISPATCH` orphan whose *caller* resolves statically feeds a
`#dynamic` tag back onto that caller (`RUNTIME_DYNAMIC_TAG`) - a real
execution observed it dispatching somewhere the static indexer couldn't
see, distinct from the static-only `#dynamic_hazard` tag
(`prism.tagger.rules.DYNAMIC_HAZARD_TAG`) a `getattr`/`setattr`/subscript-
dispatch/eval construct earns purely from its AST shape.

## 6. Invariant Summary

The six formal invariants this design guarantees, and where each is proven
or enforced:

1. **Topological Monotonicity** - Section 2.2's proof; regression-tested in
   `tests/test_distance_and_inheritance.py`.
2. **Strict Budget Compliance** - Section 4.4; every admission path checks
   before appending, never after.
3. **Resolution Termination** - `resolve_export`
   (`prism.graph.symbol_table`) bounds recursive barrel-chain chasing to
   `EXPORT_RESOLUTION_MAX_DEPTH` (5) hops with cycle-set tracking
   (`visited`), and never returns an unverified guess - only a symbol
   actually present in `GlobalSymbolTable`, or `None`.
4. **Seed Dominance** - the seed is always pinned at L0 (`hops = 0`), the
   minimum possible `raw_gc`, and is `protected` from the Swap-Refinement
   Pass unconditionally.
5. **Compression Monotonicity** - `resolution_for_distance` is a
   non-decreasing step function of `D_hybrid`, and Data-Flow Centrality
   only ever *lowers* (never raises) a node's assigned tier relative to
   that base mapping.
6. **Zero Silent Orphans** - Section 5.2; every unresolved event passes
   through `classify_orphan`, which always returns a member of
   `OrphanReason` (never `None`).
