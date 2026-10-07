# Dynamic-hop investigation: PRISM blast-radius retrieval

_Analysis only: no code was modified and nothing ran on Kaggle. Graphs were rebuilt locally with `prism.cli.build_pipeline(root, use_cache=False)` on the pinned M4 checkouts (FastAPI 9,007 nodes; Django 79,286 nodes), and weights were read from PRISM's own functions (`build_causal_graph`, `compute_upstream_callers`, `compute_topological_distances`). Commit `d95d858`. The context is `prism_t5_diagnosis.md`: PRISM's T5 shortfall is retrieval, caused by an upstream channel of ≤3 direct callers._

**Headline findings.**

1. **No edge in PRISM's graph carries a numeric weight.** Weights are derived at traversal time from a handful of boolean indicators, so they are discrete and nearly constant: 89–96% of causal edges weigh exactly 1.0, and the per-anchor MAD is 0 in most cases.
2. **Proposal 1's "radius" has no single definition.** Its plausible readings select anywhere from 0 to 4,609 nodes for the same anchor. The readings that use PRISM's weight-to-cost rule (cost = 1/W) never pass 1 hop, which is exactly the limit that causes the T5 failure.
3. **Proposal 2's `median + 0.5×MAD` threshold degenerates on these weights.** It selects all edges or none, depending on `>` vs `≥` and float rounding.
4. **A budget-aware reverse walk (Design C) reaches the gold.** At 3 hops, a production-only reverse walk would cover 87.5% / 91.1% / 40.0% / 91.7% of T5 gold (FastAPI / Django / Express / tRPC). This is partly circular, because T5 gold was derived from this same graph (§6).

---

## 1. Premise check: do weights exist?

**Structural graph (`ConcreteGraphBuilder.graph`): no weights.** Edge attributes are `relation`, `kind`, and call-site features (`args_passed_count`, `argument_flow`, `bound_to`, `call_kind`, `call_site_role`, `guarded_by_null_check`, `inside_loop`, `inside_try_catch`, `is_return_bound`). `weight` is absent on every edge of both graphs. `src/prism/graph/weights.py` holds a reference table (`EDGE_WEIGHTS`) whose own docstring says it is "not the live cost model" and is "not imported".

**Two live, derived weight models**, each computed per traversal:

| model | formula (quoted) | where | possible values |
|---|---|---|---|
| downstream causal W | `W(u, v) = w_base(relation) * (1 + lambda_1 * I_dataflow(u, v) + lambda_2 * I_guard(u, v))`, λ1 = 0.25, λ2 = 0.15; `w_base`: CALLS/INSTANTIATES 1.00, OVERRIDES 0.90, EXTENDS/EMBEDS 0.85, IMPLEMENTS 0.80 | `prism/traversal/causal_weights.py:62-90`; stored on `build_causal_graph` edges as `causal_weight`, with Dijkstra cost `weight = 1/W` | bounded 0.80–1.40 (`MAX_CAUSAL_WEIGHT = 1.40`) |
| upstream W_upstream | `W_upstream(u, s) = w_base(relation) * (1 + mu_1 * I_return_unpack(u, s) + mu_2 * I_nontrivial_args(u, s))`, μ1 = 0.30, μ2 = 0.20 | `prism/packer/blast_radius.py:12-16, 168-173`; `dist_w_upstream = 1/W` | for CALLS: exactly {1.0, 1.2, 1.3, 1.5} |

**Direction dependence: yes.** The same caller→seed pair gets a different weight depending on which model reads it. Viewed upstream it gets `W_upstream` (1.2 or 1.5). Viewed as the caller's own downstream edge in the causal graph it gets causal W (1.0). This difference appears on 4/4, 3/3 and 2/2 pairs for the FastAPI anchors, on 407/709 for `django.urls.base.reverse`, and on 6/6 and 9/9 for the other Django anchors.

**Is the seed's "max weighted edge" computable, and is it used?** It is computable: `max` over `cg.out_edges(s)` / `compute_upstream_callers(s)`. It is not used. `build_candidate_manifest` ranks upstream callers by `-weight`, keeps the top `UPSTREAM_FRONTIER_CAP = 3`, and applies a cost cutoff `≤ DEFAULT_UPSTREAM_MAX_HOPS = 1.5`. Every direct caller passes that cutoff (cost ≤ 1.0), so the cap of 3 is the only binding limit. Nothing reads a maximum edge weight.

**Weight distributions** (`n | min | p50 | p75 | p90 | max | MAD | distinct values`):

| corpus | edge set | n | min | p50 | p75 | p90 | max | MAD | distinct values |
|---|---|---|---|---|---|---|---|---|---|
| fastapi | all causal-graph edges (downstream model) | 4774 | 0.850 | 1.000 | 1.000 | 1.000 | 1.250 | 0.000 | 0.85, 0.9, 1.0, 1.15, 1.175, 1.225, 1.25 |
| fastapi | W_upstream, all direct callers of the first 4000 nodes | 302 | 1.000 | 1.200 | 1.500 | 1.500 | 1.500 | 0.200 | 1.0, 1.2, 1.3, 1.5 |
| fastapi | `fastapi.dependencies.utils.get_dependant` downstream (out-edges, causal W) | 8 | 1.000 | 1.000 | 1.000 | 1.067 | 1.225 | 0.000 | 1.0, 1.225 |
| fastapi | `fastapi.dependencies.utils.get_dependant` upstream (W_upstream) | 4 | 1.500 | 1.500 | 1.500 | 1.500 | 1.500 | 0.000 | 1.5 |
| fastapi | `fastapi.dependencies.utils.get_dependant` upstream (same in-edges, causal W) | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.0 |
| fastapi | `fastapi.routing.APIRouter.add_api_websocket_route` downstream (out-edges, causal W) | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.0 |
| fastapi | `fastapi.routing.APIRouter.add_api_websocket_route` upstream (W_upstream) | 3 | 1.200 | 1.200 | 1.200 | 1.200 | 1.200 | 0.000 | 1.2 |
| fastapi | `fastapi.routing.APIRouter.add_api_websocket_route` upstream (same in-edges, causal W) | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.0 |
| fastapi | `fastapi.utils.get_value_or_default` downstream (out-edges, causal W) | 0 | — | — | — | — | — | — | (no out-edges) |
| fastapi | `fastapi.utils.get_value_or_default` upstream (W_upstream) | 2 | 1.500 | 1.500 | 1.500 | 1.500 | 1.500 | 0.000 | 1.5 |
| fastapi | `fastapi.utils.get_value_or_default` upstream (same in-edges, causal W) | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.0 |
| django | all causal-graph edges (downstream model) | 103295 | 0.850 | 1.000 | 1.000 | 1.000 | 1.400 | 0.000 | 0.85, 0.9, 0.9988, 1.0, 1.0625, 1.135, 1.15, 1.175, 1.225, 1.25, 1.31, 1.36, 1.375, 1.385, 1.4 |
| django | W_upstream, all direct callers of the first 4000 nodes | 3467 | 1.000 | 1.200 | 1.500 | 1.500 | 1.500 | 0.100 | 1.0, 1.2, 1.3, 1.5 |
| django | `django.urls.base.reverse` downstream (out-edges, causal W) | 29 | 1.000 | 1.225 | 1.250 | 1.250 | 1.400 | 0.090 | 1.0, 1.135, 1.225, 1.25, 1.385, 1.4 |
| django | `django.urls.base.reverse` upstream (W_upstream) | 709 | 1.000 | 1.200 | 1.300 | 1.500 | 1.500 | 0.200 | 1.0, 1.2, 1.3, 1.5 |
| django | `django.urls.base.reverse` upstream (same in-edges, causal W) | 714 | 1.000 | 1.000 | 1.000 | 1.000 | 1.225 | 0.000 | 1.0, 1.135, 1.15, 1.175, 1.225 |
| django | `django.http.response.HttpResponse.__init__` downstream (out-edges, causal W) | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.0 |
| django | `django.http.response.HttpResponse.__init__` upstream (W_upstream) | 6 | 1.200 | 1.200 | 1.200 | 1.200 | 1.200 | 0.000 | 1.2 |
| django | `django.http.response.HttpResponse.__init__` upstream (same in-edges, causal W) | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.0 |
| django | `django.utils.http.url_has_allowed_host_and_scheme` downstream (out-edges, causal W) | 5 | 1.000 | 1.000 | 1.150 | 1.150 | 1.150 | 0.000 | 1.0, 1.15 |
| django | `django.utils.http.url_has_allowed_host_and_scheme` upstream (W_upstream) | 9 | 1.200 | 1.200 | 1.200 | 1.260 | 1.500 | 0.000 | 1.2, 1.5 |
| django | `django.utils.http.url_has_allowed_host_and_scheme` upstream (same in-edges, causal W) | 11 | 1.000 | 1.000 | 1.000 | 1.150 | 1.150 | 0.000 | 1.0, 1.15 |

Value counts over every causal-graph edge:
- **FastAPI:** 1.0 × 4,578 (95.9%), 0.85 × 134, 1.225 × 32, 1.175 × 15, 0.9 × 9, 1.25 × 4, 1.15 × 2.
- **Django:** 1.0 × 91,640 (88.7%), 0.85 × 6,428, 0.9 × 2,390, 1.225 × 1,437, 1.25 × 817, …, 1.4 × 3.

**Verdict: the premise is refuted.** Weights exist only as derived, near-constant tiers. They have no continuous distribution: the global MAD is 0 in both corpora, and the ratio of max to min weight is at most 1.76. They carry almost no anchor-specific information beyond "does the caller bind the return value / pass real arguments" (upstream) and "is there data flow / a guard" (downstream).

Reachability by Dijkstra cost from the anchor (`compute_topological_distances`; reverse = upstream, forward = downstream). Cells give node counts within cost ≤ 0.7 / 1.0 / 1.5 / 2.0 / 3.0:

| anchor | in-degree | out-degree | same pair, different weight by direction | reverse within 0.7 / 1 / 1.5 / 2 / 3 | forward within 0.7 / 1 / 1.5 / 2 / 3 | reverse-reachable (d ≤ 6) | gold reverse distances |
|---|---|---|---|---|---|---|---|
| `fastapi.dependencies.utils.get_dependant` | 4 | 7 | 4/4 | 0 / 4 / 4 / 8 / 8 | 0 / 8 / 8 / 38 / 51 | 8 | [1.0, 2.0] |
| `fastapi.routing.APIRouter.add_api_websocket_route` | 3 | 4 | 3/3 | 0 / 3 / 3 / 9 / 20 | 0 / 1 / 1 / 1 / 1 | 22 | [1.0, 2.0] |
| `fastapi.utils.get_value_or_default` | 2 | 0 | 2/2 | 0 / 2 / 2 / 10 / 21 | 0 / 0 / 0 / 0 / 0 | 23 | [1.0, 2.0] |
| `django.urls.base.reverse` | 709 | 11 | 407/709 | 0 / 714 / 714 / 951 / 1114 | 0 / 29 / 29 / 145 / 359 | 4609 | [1.0, 2.0] |
| `django.http.response.HttpResponse.__init__` | 6 | 1 | 6/6 | 0 / 6 / 6 / 7 / 7 | 0 / 1 / 1 / 3 / 3 | 7 | [1.0, 2.0] |
| `django.utils.http.url_has_allowed_host_and_scheme` | 9 | 3 | 9/9 | 0 / 11 / 11 / 30 / 49 | 0 / 5 / 5 / 23 / 63 | 863 | [1.0, 2.0, 3.0] |

On every T5 gold symbol of these anchors, weighted reverse distance equals the structural hop count exactly (1.0 per hop). The weights do not reorder anything along gold paths.

## 2. Proposal 1 as stated: anchor as radius center

**Proposal 1's radius cannot be given a single precise definition.** Each plausible reading, applied to the same anchors, gives a different node set:

| reading of "within radius = max edge weight" | `get_dependant` (gold: 4 at hop 1, 3 at hop 2) | `django.urls.base.reverse` (709 direct callers) | reaches 2-hop gold? |
|---|---|---|---|
| (a) Dijkstra cost ≤ 1/W_max (the closest a node can be) | upstream via causal graph: **0** (every reverse hop costs 1.0 > 0.667); via W_upstream: the 4 callers at W = 1.5 | 0 (causal) / 132 (callers with W_upstream = 1.5) | no |
| (b) Dijkstra cost ≤ W_max (treating the weight as a distance) | reverse within 1.5 = **4**; forward = 8 | 714 reverse; 29 forward | no: within 1.5 equals within 1.0 for every sample anchor |
| (c) hop count ≤ ⌊W_max⌋ | 1 hop: 4 up + 8 down | 709 + 11 | no |
| (d) bottleneck: every edge on the path ≥ some fraction of W_max | with ~90% of edges at 1.0 and W_max = 1.0–1.5, this admits nearly everything reachable | ≥ 4,609 reverse-reachable | yes, plus thousands of non-gold nodes |
| (e) product or sum of weights ≤ W_max | products are ≥ 1 on CALLS paths and sums grow by about 1 per hop, so this collapses to (b) or (c) | as (b) / (c) | no |

- **Outlier max edge.** Weights are bounded (downstream 0.80–1.40, upstream 1.0–1.5), so a single "very strong" edge can be at most 1.4–1.5× the typical one. There is no real outlier regime. The radius is therefore almost the same constant for every anchor and does not adapt. Where an anchor does have one strong edge (`get_dependant` downstream: one edge at 1.225, seven at 1.0), reading (a) keeps only that single edge.
- **Dense anchor (200+ edges).** On `reverse()`, readings (b) and (c) admit 709–714 direct callers. 681 of those 709 are test or non-production code: the production-only set is 28 direct, 32 at hop 2, 20 at hop 3 and 14 at hop 4. Step 4 ("prune lower-weight edges") must then choose among ties: 709 callers share four weight values (1.0 / 1.2 / 1.3 / 1.5; 132 at 1.5). Pruning by weight is mostly arbitrary tie-breaking.
- **Direction asymmetry: not addressed.** Proposal 1 is direction-agnostic, and none of its weight-based readings (a)–(c) gets past 1 hop. The T5 failure is a missing transitive upstream walk (`compute_upstream_callers` is "strictly 1 hop" by its own docstring), not a weighting problem. Proposal 1 does not lean downstream for weight reasons: `W_upstream` (up to 1.5) is actually larger than downstream causal W (typically 1.0). It leans downstream for structural reasons, because only the downstream side has a multi-hop walk (`CANDIDATE_INDEX_MAX_HOPS = 3.0`).

## 3. Proposal 2: direction split + distribution threshold + reach/rank/prune

- **Direction split: correct.** It names the right fix, an independent upstream reach.
- **`median + 0.5 × MAD` does not suit PRISM's weights.**
  - The per-anchor upstream MAD is 0 for all five non-hub anchors, and the downstream MAD is 0 for 3 of the 4 anchors that have downstream edges. With MAD = 0, the threshold equals the median. A strict `>` then admits **none** of `get_dependant`'s 4 direct callers, all of which are gold. A `≥` admits all of them, which makes the step a no-op.
  - On the hub `reverse()`, the upstream threshold computes to `1.2999999999999998`. Whether the 1.3 tier passes depends on float rounding: it admits 193 callers, versus 132 if the threshold were exactly 1.3.
  - The weights are ordinal tiers (four upstream levels from two booleans), so a robust-statistics cutoff has no distribution to work on.
  - **Use the tiers as a sort key and let the budget cut.** If a hard filter is wanted, filter on the indicators themselves (`unpacks_return`, `supplies_nontrivial_args`) or on production vs test code. The test filter is the one that matters on hubs: 681 of 709 callers of `reverse()` are non-production.
  - The threshold is also defined only on the anchor's own edges (hop 1). Proposal 2 does not say what threshold applies at hops 2+.
- **Three steps vs a single walk.** Reach-K → rank by weight → prune is not equivalent to a budget-aware walk ordered by distance. Ranking by weight inside the reachable set can put a 3-hop W = 1.5 caller above a 1-hop W = 1.0 caller. Gold is nested by hop, and direct callers are the highest-precision tier: production precision at K = 1 is 0.882 / 0.250 / 0.500 / 0.861. If the rank key is (hop, then weight), the three steps reduce to the single walk. The extra stages add code and two parameters without changing the result.
- **Per-direction K can be derived.** Set K = the largest k whose production node set fits that direction's token sub-budget. The remaining hand-set parameters are the MAD multiplier (0.5) and the up/down budget split. Measured sizes show K is rarely the binding limit (§4 table): at K = 3 the production upstream set averages 3.9k (FastAPI), 13.0k (Django), 1.0k (Express) and 2.8k (tRPC) tokens. Only Django hubs exceed a 13k budget (max 55k), and there pruning order decides the result.

## 4. Feasibility

What a reverse walk can reach on the real T5 tasks: production-only (tests/docs/examples/benchmarks/scripts excluded by path), CALLS/INSTANTIATES in-edges, K hops. Token figures are an estimate (source characters / 4, plus 8 per symbol).

| corpus | K | gold recall (production set) | precision | production nodes, mean / max | all nodes incl. tests, mean / max | production tokens, mean / max |
|---|---|---|---|---|---|---|
| fastapi | 1 | 15/32 = 0.469 | 0.882 | 2.1 / 4 | 2.1 / 4 | 2,383 / 3,781 |
| fastapi | 2 | 27/32 = 0.844 | 0.818 | 4.1 / 8 | 5.9 / 10 | 3,828 / 6,325 |
| fastapi | 3 | 28/32 = 0.875 | 0.778 | 4.5 / 8 | 10.1 / 20 | 3,897 / 6,325 |
| fastapi | 5 | 28/32 = 0.875 | 0.757 | 4.6 / 8 | 10.2 / 20 | 3,911 / 6,325 |
| django | 1 | 29/45 = 0.644 | 0.250 | 14.5 / 54 | 126.6 / 709 | 5,820 / 25,133 |
| django | 2 | 38/45 = 0.844 | 0.171 | 27.8 / 109 | 171.0 / 789 | 11,283 / 46,009 |
| django | 3 | 41/45 = 0.911 | 0.144 | 35.5 / 139 | 188.8 / 823 | 13,048 / 55,003 |
| django | 5 | 41/45 = 0.911 | 0.128 | 40.1 / 157 | 206.8 / 850 | 14,948 / 64,426 |
| express | 1 | 2/5 = 0.400 | 0.500 | 2.0 / 4 | 2.0 / 4 | 898 / 1,796 |
| express | 2 | 2/5 = 0.400 | 0.400 | 2.5 / 5 | 2.5 / 5 | 940 / 1,881 |
| express | 3 | 2/5 = 0.400 | 0.250 | 4.0 / 8 | 4.0 / 8 | 1,020 / 2,041 |
| express | 5 | 2/5 = 0.400 | 0.250 | 4.0 / 8 | 4.0 / 8 | 1,020 / 2,041 |
| trpc | 1 | 31/96 = 0.323 | 0.861 | 2.6 / 7 | 2.6 / 7 | 1,200 / 3,753 |
| trpc | 2 | 65/96 = 0.677 | 0.844 | 5.5 / 14 | 5.5 / 14 | 2,389 / 7,274 |
| trpc | 3 | 88/96 = 0.917 | 0.863 | 7.3 / 18 | 7.3 / 18 | 2,837 / 8,139 |
| trpc | 5 | 95/96 = 0.990 | 0.819 | 8.3 / 19 | 8.3 / 19 | 2,941 / 8,266 |

Gold the walk can never reach:
- **FastAPI:** `APIRouter.decorator` / `FastAPI.decorator`, a naming mismatch with the graph's `decorator#3`.
- **Django:** `prefetch_one_level`.
- **tRPC:** `initTRPCInner`.
- **Express:** all 3 callers of `lib.router.layer.Layer`. `new Layer(...)` resolves to no in-edge (the seed's in-degree is 0), so no traversal design fixes Express `t5_002`. It needs graph construction work.

Current PRISM T5 coverage (for comparison): 0.310 / 0.195 / 0.250 / 0.127.

| design | where the change lives | LOC estimate (non-test) | fits the current `candidate_index` architecture? | T2 regression risk | noise risk |
|---|---|---|---|---|---|
| **A** hard quota, UPSTREAM_CAP = 10, DOWNSTREAM_CAP = 10 | core: `candidate_index.py:205-218` | ~10 if direct-only (use a new manifest-local constant; `UPSTREAM_FRONTIER_CAP` is shared with the packer at `submodular_knapsack.py:822`). Direct-only reaches at most the K = 1 recall (0.469 / 0.644 / 0.400 / 0.323). Going past 1 hop needs the reverse walk, which turns A into C. | yes for the cap; a downstream cap of 10 would cut today's manifests (mean 8–18.6, up to 57) | **high** for the downstream cap: `CANDIDATE_INDEX_MAX_HOPS = 3.0` exists because hop 2 dropped `django_t02_009`'s `QuerySet._clone` | medium: 10 of 709 hub callers chosen among 4 weight ties |
| **B** per-direction MAD threshold + dynamic K (Proposal 2) | core: `candidate_index.py`, a new upstream walk, a budget estimator, redistribution | ~150–250 | needs refactoring: the manifest is budget-independent by design ("Budget-independent by construction", `candidate_index.py` docstring), and B adds a token budget to Turn 1 | medium, unless gated by task type | high: the threshold degenerates (all or none) on MAD = 0 tiers |
| **C** budget-aware single walk, alternating up/down in distance order until a shared budget is spent | core: `candidate_index.py` (upstream admission), `_classify_role` (a role for ≥2-hop callers), a task-type pass-through; harness: ~3 lines to pass the T5 task type | ~60–100 | mostly yes: `compute_topological_distances(builder, seed, d_max, direction="reverse")` already exists (`continuous_dijkstra.py:317-344`) and returns weighted upstream distances. Today `_classify_role` would mislabel a ≥2-hop caller as `transitive`, a downstream-sounding role in the Turn-1 manifest. | low if gated to T5 (`build_candidate_manifest` has no `task_type` today, so it is shared by T2 and T5); medium if not | medium on hubs (Django K = 3 production max 139 nodes / 55k tokens, which needs (hop, weight-tier) ordering and a production filter); low elsewhere (precision 0.78–0.86 on FastAPI and tRPC) |

All three designs leave the Turn-1 prompt unchanged. That prompt asks for "the complete causal execution path from the seed to termination" (H2 in the diagnosis), so wider candidates still depend on Turn 1 requesting them. In M4, Turn 1 did request every caller item that was later delivered (125/125), so callers it can see do get picked.

## 5. Field comparison

Claims are as reported by each source; I verified none of them experimentally.

| system | edge weighting | direction treatment | caps (hops / slots / tokens) | designed for | verification |
|---|---|---|---|---|---|
| Chronos AGR ([arXiv 2507.12482](https://arxiv.org/pdf/2507.12482); [docs](https://app.kodezi.com/docs/chronos-1/core-architecture/adaptive-retrieval-engine)) | not specified in the sources found | not specified as asymmetric | dynamic k-hop expansion "based on query complexity", confidence-based termination "once enough relevant context is assembled" | repository-scale **debugging** (fix generation) | the paper exists; implementation details beyond these phrases are unverified; vendor-reported numbers |
| Codex-Atlas | — | — | — | — | **not found**: no source matching "Codex-Atlas" with query classification and adaptive depth. The nearest hits were an unrelated local code-graph tool ("Atlas", [atlas.aziro.com](https://atlas.aziro.com/)) and a passing mention of adaptive depth. Not characterized. |
| CodeRAG "one-hop expand + RRF" | none (unweighted 1-hop) | symmetric: "callees, callers, and type dependencies" | 1 hop after top-k hybrid (BM25 + dense, RRF) retrieval | code **comprehension** / cited Q&A | the description matches [arXiv 2512.12117](https://arxiv.org/pdf/2512.12117) (cross-file evidence 58% → 82%), which is **not** named CodeRAG; I could not tie it to a system of that name |
| 1Cademy bidirectional diffusion ([method](https://1cademy.com/node/bidirectional-prerequisite-diffusion-with-role-aware-quotas/A_HHqiOSv7k_YCRXZ4Hi); [quota](https://1cademy.com/node/retrieval-stage-role-quota-for-childbridgeglobal/-Eq9hMXlVtSxLcxjrEsA)) | deterministic diffusion over unweighted prerequisite edges | **asymmetric by role**: child (downstream) / bridge (direct prerequisites) / global (hierarchy anchors) | role quota child = 1, bridge = 4, global = 1 (6 slots) | **concept-prerequisite** retrieval (LectureBank), not code | the quota is confirmed. The gain is reported as "roughly 18 points" R@10 over static parent expansion ([source](https://1cademy.com/node/lecturebank-full-diffusion-gain-over-static-parent-expansion-r-points/EDvZYUgdS7JBjrsE3NNd)), not the 55.5 → 86.0 (30.5 points) in the brief. I could not confirm the 55.5 / 86.0 figures. |

**Relevance.** None of the verified systems uses a weight-derived radius. The closest analogue to PRISM's T5 need is 1Cademy's explicit **per-role slot quotas** on a bidirectional walk. Its quota favours the "bridge" role (direct neighbours on the other side of the seed) 4 to 1, which matches the finding here that direct callers are the highest-precision tier.

## 6. Recommendation

Design C fits PRISM's architecture best. The upstream weighted distance it needs already exists (`compute_topological_distances(..., direction="reverse")`). The change is local to the upstream admission in `candidate_index.build_candidate_manifest` (about 15 lines today) plus a role label, at roughly 60–100 core LOC plus ~3 harness lines, with tests on top. On the measured tasks, a 3-hop production-only reverse walk would lift the upstream gold ceiling from today's ≤3 direct callers (actual coverage 0.310 / 0.195 / 0.250 / 0.127) to 0.875 / 0.911 / 0.400 / 0.917, inside the 13k budget everywhere except Django hub seeds.

Proposal 1 is not recommended: its radius has no single definition, and none of its readings that use PRISM's own cost rule gets past 1 hop. Proposal 2's direction split is right, but its MAD threshold degenerates on PRISM's tiered weights, and its separate rank step can put far callers above near ones. With rank = (hop, weight tier), it reduces to C at extra cost.

Risks for C:
1. **T2 regression** unless the walk is gated to T5. The manifest is shared today, so this needs a `task_type` parameter.
2. **Hub noise** on Django. This needs a production filter; 681 of `reverse()`'s 709 callers are non-production.
3. **Turn-1 bias.** The prompt still frames an execution chain.
4. **Measurement circularity.** T5 gold *is* PRISM's own transitive-caller closure (`derive_t5_from_t2.py`: "the seed's transitive callers via PRISM's call graph", production only). A reverse walk on the same graph will score well partly by construction. That is cross-checked against Pyright for Python, but not for TypeScript, so a C result should be reported with the same disclosure as the TypeScript Arm 3 bias.

**Mechanism:** walk upstream and downstream from the seed in order of PRISM's own weighted distance, alternating directions, and admit production-code candidates into the Turn-1 manifest, ordered by hop and then weight tier, until a shared token budget is spent.
