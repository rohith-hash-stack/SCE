# Prism: Mathematical Design Formalism

> **Core Epistemological Premise** (Item 23, second post-implementation
> audit): Prism relies on static AST/CST structural causality. In
> codebases heavily reliant on dynamic metaprogramming (`eval`,
> `setattr`, dynamic class construction, runtime reflection), static
> analysis will index declarations but cannot resolve dynamic call
> chains. Dynamic trace reconciliation (Section 5) augments static gaps
> when traces are provided, but Prism does not perform symbolic runtime
> execution.

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
| `EMBEDS` | Go anonymous struct embedding - the real mechanism Go composition uses in place of EXTENDS syntax it doesn't have. | Yes (Item 5) |
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
| `EXTENDS` / `EMBEDS` | 0.85 | ~1.18 |
| `IMPLEMENTS` | 0.80 | 1.25 |

A `confidence="CONFIRMED_RUNTIME"` edge (Section 5) additionally multiplies
its cost by `runtime_confidence_weight` (0.5 default), and by a further
`high_trust_extra_discount` (0.5 default) when RuntimeTrust >= 0.9.

**Item 3 (second post-implementation audit)** adds a second,
independent multiplier: a Go `CALLS` edge produced only by the
codebase-unique-receiver fallback (`kind="TENTATIVE_CALL"` -
`ConcreteGraphBuilder._go_unique_receiver_for_method`, Item 3 Stage 2)
has its structural weight further multiplied by
`RELATION_TENTATIVE_CALL_WEIGHT` (0.60, effective hop cost `~1.67`) -
strictly more expensive than a normal CALLS hop, but still reachable and
usable, since it's a best-effort guess (exactly one struct in the whole
repo defines the called method) rather than a confidently-linked call.

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

`prism.slicer.tokenizer` tries, in order: (1) a vendored, offline copy of
`cl100k_base`'s merge-rank table at `src/prism/slicer/assets/
cl100k_base.tiktoken`, if present (`get_offline_bpe_encoding`, Item 1 -
zero network calls, this repository does not currently ship the ~1.6MB
asset itself, see that directory's own README); (2) `tiktoken.get_encoding`'s
normal network-fetching path; (3) the regex fallback. That fallback is
deliberately fail-*closed* (Issue A1, refined by Item 2): a `\w+`
identifier run is split into a conservative subword-piece estimate
(snake_case/camelCase boundaries, `(len(digits)+2)//3` for multi-digit
numbers) rather than counted as one flat token; a recognized multi-char
compound operator (`->`, `==`, `::`, `:=`, `...`, ...) counts as one
token, matching how a real BPE vocabulary trained on code typically
merges these, rather than one token per character; a quoted string
literal over `STRING_LITERAL_DENSITY_THRESHOLD` (32) characters is
counted by byte density (`len(content)//3`) instead of run through
identifier-splitting logic calibrated for code, not prose/blob content;
a fixed 1.08x ceiling multiplier is applied to the summed total as an
explicit buffer. This fallback's `>= real_bpe_count` guarantee is proven
directly for plain identifiers (`tests/test_tokenizer.py`'s flat-word-
count regression) but has **not** been empirically validated against a
real BPE encoding across a broad corpus in this project's own
development environment - see `tests/test_bpe_vs_fallback.py`'s own
honest-skip behavior and that file's docstring for why (no network path
to a real encoding exists in this sandbox, and no offline asset is
vendored here for the same reason `assets/README.md` explains).

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
*admitted candidate set*: every admission check (`total_tokens + cost >
self._admission_budget`) happens *before* appending to `items`, and the
Swap-Refinement Pass only ever accepts a substitution when the post-swap
total still fits. `B_token`'s own `SAFETY_MARGIN` (0.92) and
`reserved_overhead_tokens` absorb the residual estimation error inherent
to any tokenizer (including a real BPE one, since the packer's own
per-item accounting doesn't render the *complete* final document
incrementally - see the module docstring's "not byte-exact" note on
`_wrapping_overhead_tokens`).

**One deliberate, unconditional exception:** the seed itself is always
packed - nothing ever excludes it outright (Section 4/Seed Dominance,
Invariant #4 - a query response must always show its own target) - but,
as of Item 18 below, it is no longer always packed *at full L0 cost*
regardless of budget.

Formally, the invariant this section proves is the disjunction:

$$\text{Tokens}(\text{Rendered}) \le \text{Budget} \quad \lor \quad \text{Tokens}(\text{Seed}_{L3}) > \text{Budget}$$

The left disjunct is the ordinary case, proved above by construction over
every admission check the candidate-selection loop and the
Swap-Refinement Pass perform. The right disjunct is the seed-dominance
exception: whenever it holds, the left disjunct is permitted to fail, and
does so for exactly one reason (even the seed's own minimal L3 stub - the
smallest representation this class ever renders - doesn't fit), never
silently for any other.

**Issue A3 (post-implementation audit):** nothing in `PackResult` let a
caller (the CLI's `--json` output, the MCP server, any other downstream
consumer) *observe* that the right disjunct - not the left - was the
reason a response came back over budget, short of independently
comparing `allocated_tokens` against `budget` themselves. `PackResult`
now exposes this directly via `seed_cost`/`budget_exceeded`/
`truncation_occurred` (see below), rendered in `prism.serializers.
render_markdown` as an explicit `[!] Budget exceeded: ...` line and
included in `prism.cli`'s `--json` output.

**Item 18 (third post-implementation audit), Progressive Seed
Degradation:** Issue A3 fixed the *visibility* gap but not the
underlying one - a seed that didn't fit at L0 was still packed at full
L0 cost regardless, guaranteeing a response that would fail hard at a
downstream LLM's own context-window boundary rather than degrading like
every other candidate in the pack already does. `ContextKnapsackPacker.
_degrade_seed_to_fit` now tries L0, then L1 (pruned - real call
arguments retained, docstrings/logging stripped), then L2 (skeleton -
arguments collapsed to `...`), then L3 (minimal stub), in that fixed
order, stopping at the first tier whose real token cost fits `budget`
(the raw nominal budget, not the reduced `_admission_budget` ordinary
candidates are checked against - the seed's own admission has never gone
through that safety-margin reduction, before or after Item 18). Every
tier past L0 prepends an explicit `/* Warning: Seed compressed to ... */`
comment directly into the rendered content, visible to a downstream
reader/LLM without needing to inspect `PackResult`'s own metadata. L3 is
always accepted regardless of whether it actually fits - nothing smaller
exists to try, and the seed must always be shown even when doing so
unavoidably exceeds the budget; that residual case is exactly what
`budget_exceeded`/`fatal_seed_overflow` now report, and it is
substantially rarer than before (only a seed whose bare one-line
signature stub alone exceeds the budget, not any seed whose full source
does).

`PackResult` exposes:

- `seed_cost: float` - the seed's real token cost *at whatever tier it
  was actually packed at* (not always L0 anymore).
- `seed_compression_level: int` - which of L0/L1/L2/L3 the seed ended up
  at (0 is the common case).
- `budget_exceeded` / `fatal_seed_overflow: bool` - two names for the
  same condition (`seed_cost > budget` at the final, L3-or-fitting,
  tier) - `fatal_seed_overflow` is kept as an explicit separate field
  (not a property) so it round-trips through plain-dataclass
  serialization (`asdict`, JSON, ...) alongside `budget_exceeded`.
- `truncation_occurred: bool` - `allocated_tokens > budget` at the end of
  `pack()`, computed independently from `budget_exceeded` rather than
  aliased to it (in the current architecture every non-seed candidate is
  strictly admission-gated, so the seed is the only possible overflow
  source and the two flags are always equal today - kept as two separate
  computations because the questions they answer, "did the mandatory
  seed alone not fit" vs. "did the final document not fit," are
  conceptually distinct and a future non-seed-only overflow source
  should not need to retrofit either flag's meaning).

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

### 5.4 Bayesian Confidence Degradation for Static False Positives (Item 9)

The confidence promotions above are all *positive* evidence (a trace
confirmed or discovered an edge); this is the one *negative* signal a
trace can carry against a static edge, and it is deliberately weak -
degrading, never deleting.

For a static `CALLS` edge `e = (u, v)`, with `symbol_execution_count[u]`
the number of traced events this reconciliation run recorded with `u` as
the caller:

```
if symbol_execution_count[u] >= NON_OBSERVATION_EXECUTION_THRESHOLD (10)
   and e was not among this run's confirmed edges:
       metadata["unobserved_in_traces"] = True      # edge kept, never removed
```

`u` executing at least ten times in the recorded trace is the load-bearing
condition: it is what separates "the static resolver over-linked" from
merely "this particular trace happened not to cover `u` much" - the same
asymmetry `RuntimeTrust` (Section 5.3) already encodes, just applied
edge-by-edge instead of trace-wide. A caller invoked only once or twice
tells you almost nothing about its *other* edges; a caller invoked ten-plus
times that never once took a specific statically-inferred branch is real
evidence.

The flag is purely additive metadata on an existing `G_C` edge - it is
never deleted, precisely so a cold error path (`except`/`if err != nil`)
a test suite doesn't happen to exercise stays fully visible to `prism
query`, just re-priced. `DistanceEngine._weighted_undirected`
(`prism.slicer.distance`) is where the flag actually changes behavior:
`structural_weight` for such an edge is multiplied by
`GAMMA_UNOBSERVED = 0.50`, composing independently with the
`kind="TENTATIVE_CALL"` discount (Item 3) and the `CONFIRMED_RUNTIME`
discount above (mutually exclusive with this one in practice, since
"confirmed" and "unobserved" cannot both describe the same edge from the
same run) exactly the way those two already compose with each other. A
`RUNTIME_DISCOVERED` edge is never eligible - it is itself trace evidence,
so it cannot simultaneously be "never observed".

The flag persists across `prism trace` runs the same way
`confirmed_edges`/`discovered_edges` do
(`ReconciliationResult.non_observed_edges` -> `merge_result_into_state`'s
`state["unobserved_edges"]`), with one difference from those two: a pair
that a *later* run does confirm is removed from the accumulated list
(`unobserved -= confirmed`, mirroring the pre-existing `discovered -=
confirmed` line just above it) rather than staying flagged forever, since
a later confirmation is direct proof the earlier non-observation was
incomplete coverage, not a genuine static false positive.

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

## 7. Language Capability Matrix (Issue B2)

Every formula and invariant above is stated for `G_C` in the abstract,
but how precisely each node/edge in `G_C` was actually *derived* differs
per source language - not along one linear "better/worse" axis, but per
feature, independently. An earlier "Precision Tier 1/2/3" framing
implied the former; this section (and `README.md`'s own copy, kept in
sync) states the latter explicitly, sourced from
`src/prism/graph/concrete_builder.py` and `src/prism/slicer/
compressor.py` directly rather than summarized from memory:

| Feature | Python | TypeScript / JavaScript | Go |
|---|---|---|---|
| Parser frontend | Tree-sitter CST + native `ast` (compression transforms) | Tree-sitter CST only | Tree-sitter CST only |
| Symbol registration | Functions, classes, methods | Functions, classes, methods, interfaces (`kind="interface"`, Item 17) | Functions, structs, receiver methods (receiver-qualified as of Issue B1) |
| Re-export resolution | Recursive `ExportRegistry` (depth <=5, `__all__` whitelist) | Recursive `ExportRegistry` (`export {x} from`/`export * from`) | Package-level import resolution only - no re-export syntax exists in Go |
| Call resolution | Rules A-D: constructor-based instance binding + lexical resolution | Rules B/C/D: lexical resolution only, no instance binding | Receiver/parameter/short-var-decl type-signature binding (Issue B1, Item 3) + codebase-unique-receiver fallback (Item 3 Stage 2, tentative) - not constructor-*function*-call tracking; measured 49.75% receiver-call resolution on gin-gonic/gin |
| Inheritance traversal | EXTENDS walk approximating C3 MRO + OVERRIDES | Same EXTENDS/IMPLEMENTS walk (single-parent in practice) | `EMBEDS` walk (Item 5) with BFS depth-based shadowing (Item 7) - Go's real mechanism (struct embedding), not EXTENDS/IMPLEMENTS, which Go has no syntax for at all |
| Compression fidelity (Section 3) | 4-tier L0-L3, L1 preserves real call arguments, Data-Flow Centrality floor | `UniversalSlicer` CST byte-range L0-L3 - L1 strips comments and noisy/logging calls but retains real call arguments (Item 11, matching Python's own L1 parity) | Same `UniversalSlicer` pipeline as JS/TS |

Two consequences worth stating explicitly, since they run against the
old linear framing's intuition:

- Go's Compression Fidelity row is identical to JS/TS's, not lower -
  the byte-range `UniversalSlicer` path is shared code, not a
  Go-specific degradation.
- Go's Call Resolution row is a real, if narrower, mechanism - not the
  complete absence a "Tier 3, lexical only" label might suggest - added
  by Issue B1 specifically so a receiver-heavy Go codebase (the
  dominant idiom in most real Go APIs) produces real `CALLS` edges to
  its own receiver methods instead of the definition existing in the
  graph with zero incoming edges.

`src/prism/language_tiers.py`'s `PrecisionTier` enum still derives a
coarse three-value label from this table (`--language-tier tier1-only`
needs a simple predicate, not the full matrix), but the table above is
the actual source of truth it summarizes.
