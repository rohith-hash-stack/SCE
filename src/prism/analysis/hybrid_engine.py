"""Section 2.2 - Hybrid Dynamic Runtime Trace Ingestion Engine's
mathematical core: the Unified Hybrid Edge Weighting Equation, and a
dynamic-absorption-calibration variant of `prism.analysis.flow_engine`'s
absorbing Markov chain that uses those hybrid weights (instead of a flat
per-edge count) to build `Q`.

**How the 5 operational invariants (spec Section 2.1) are honored here**:

  1. *Static AST as Hard Ground Truth*: `HybridFlowEngine` reuses
     `FlowEngine._partition`/`_condense` unchanged - the node/edge set
     `V`/`E_static` is decided purely by the static graph, exactly like
     the parent class. Runtime weights only ever change how much
     probability mass an *existing* edge gets; nothing here can add or
     remove a node, and an edge with zero runtime hits still gets its
     full static base weight (see `hybrid_edge_weight`'s `alpha=0`
     collapse) rather than being zeroed out.
  2. *Deterministic Invalidation & Provenance*: entirely upstream of this
     module, in `prism.runtime.trace_validator`/`trace_ingester` - by the
     time an `AggregatedTrace` reaches here, every trace it contains has
     already passed fingerprint/commit validation.
  3. *Environment Tagging & Aggregation*: `weighted_execution_count` sums
     `w_env * count_env(u, v)` across every environment in the
     `AggregatedTrace` separately, per the equation in Section 2.2.A -
     never a single flattened count.
  4. *Logarithmic Frequency Dampening*: `hybrid_edge_weight` applies
     `log1p` to the raw execution count before it can influence anything -
     see that function's own docstring and
     `tests/test_runtime_ingestion.py::test_log_dampening_prevents_priority_collision`
     for the exact worked example (5 hits at distance 1 beats 50,000 hits
     at distance 3, under the default `--runtime-bias 0.25`).
  5. *Separation of "Unobserved" vs "Dead"*: `HybridFlowEngine` never
     drops a node or an edge for having zero runtime hits - a zero-hit
     edge simply gets `exec_count=0`, and `math.log1p(0) == 0`, so its
     weight is exactly its static base weight, no different from an edge
     no trace ever covered at all. `node_observation` below exposes the
     `observed: bool` / `statically_reachable: bool` distinction Section
     2.1.5 and the serializer both need, computed from the *static* node
     set crossed with the runtime overlay - never the other way around.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx
import scipy.sparse as sp

from prism.analysis.flow_engine import EFFECT_BASIS_DIMENSIONS, FlowEngine, FlowEngineResult, classify_sink
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.runtime.trace_ingester import AggregatedTrace

#: `--runtime-bias` (`prism.cli`)'s default - a purely-static system
#: (alpha=0) is the other end of the dial; 0.25 keeps runtime evidence a
#: meaningful but never-dominant tiebreaker against static structure.
DEFAULT_RUNTIME_BIAS = 0.25

#: Per-environment weights for `N_exec`'s env-weighted sum - production
#: traffic is the strongest real-world signal a repository can have about
#: which paths matter; unit tests run the most often but are the least
#: representative of real usage, so they carry the lowest weight.
DEFAULT_ENV_WEIGHTS: dict[str, float] = {
    "unit": 0.25,
    "integration": 0.5,
    "e2e": 0.75,
    "staging": 0.85,
    "prod": 1.0,
    "production": 1.0,
}
_DEFAULT_ENV_WEIGHT = 0.5  # an unrecognized environment name - a middling, non-extreme default.

#: A callee whose simple name reads as a logging leaf - the canonical
#: "noisy utility that must never starve real business logic" case
#: Section 2.1.4 exists to protect against (see the module docstring's
#: worked "Loop Logger" example).
_LOGGING_LEAF_NAMES = frozenset({"debug", "info", "warn", "warning", "error", "log", "trace", "critical"})


def weighted_execution_count(
    caller: str, callee: str, aggregated: AggregatedTrace, env_weights: dict[str, float] | None = None,
) -> float:
    """N_exec(u, v) = sum_env w_env * count_env(u, v) (Section 2.2.A)."""
    weights = env_weights or DEFAULT_ENV_WEIGHTS
    total = 0.0
    for env, counts in aggregated.per_env_edge_counts.items():
        count = counts.get((caller, callee), 0)
        if count:
            total += weights.get(env, _DEFAULT_ENV_WEIGHT) * count
    return total


def synonym_priority_multiplier(edge_data: dict, callee_simple_name: str | None = None) -> float:
    """beta_synonym(u, v): the call-site semantic priority multiplier
    Phase 1's native call-site synonyms feed into Phase 2's weighting -
    a call whose result demonstrably flows into the caller's own `return`
    (`is_return_bound`) is boosted (it reads as load-bearing business
    logic, not incidental); an assertion subject is boosted more mildly
    (verification code matters, but less than a direct data dependency);
    a callee whose own simple name reads as a logging leaf is dampened -
    every other call site is neutral (`1.0`)."""
    if edge_data.get("is_return_bound"):
        return 1.5
    if edge_data.get("call_site_role") == "assertion_subject":
        return 1.2
    if callee_simple_name and callee_simple_name.lower() in _LOGGING_LEAF_NAMES:
        return 0.5
    return 1.0


def hybrid_edge_weight(
    static_dist_to_target: int, exec_count: float, alpha: float = DEFAULT_RUNTIME_BIAS, beta: float = 1.0,
) -> float:
    """The Unified Hybrid Edge Weighting Equation (Section 2.2.A):

        W(u, v) = (1 / dist_static(seed, v)) * (1 + alpha * beta_synonym(u, v) * log(1 + N_exec(u, v)))

    At `alpha = 0` this is exactly `1 / dist_static` - pure static
    ranking, with no runtime term evaluated at all in effect (the
    multiplier collapses to `1 + 0 = 1`) - satisfying "when alpha = 0, the
    system behaves 100% statically" as a literal algebraic identity, not
    an approximation. `math.log1p` (`log(1 + x)`) is what keeps a runtime
    execution count - which can differ from a sibling edge's by 4-5 orders
    of magnitude in a real repository (a hot loop vs. a rare error path) -
    from ever linearly overwhelming static topological distance, per
    Section 2.1.4.
    """
    if static_dist_to_target < 1:
        raise ValueError(f"static_dist_to_target must be >= 1, got {static_dist_to_target}")
    if exec_count < 0:
        raise ValueError(f"exec_count must be >= 0, got {exec_count}")
    base = 1.0 / static_dist_to_target
    runtime_boost = alpha * beta * math.log1p(exec_count)
    return base * (1.0 + runtime_boost)


@dataclass(frozen=True)
class NodeObservation:
    observed: bool
    statically_reachable: bool  # always True here - see this dataclass's own docstring
    total_hits: int

    def to_dict(self) -> dict:
        return {"observed": self.observed, "statically_reachable": self.statically_reachable, "total_hits": self.total_hits}


def node_observation(node: str, g: nx.DiGraph, aggregated: AggregatedTrace) -> NodeObservation:
    """Section 2.1.5 - Separation of "Unobserved" vs "Dead": every node
    passed in here is, by construction, a member of the *static* call
    graph (`statically_reachable` is always `True` - a node this function
    is never even asked about because it doesn't exist in `calls_graph` at
    all is a different, categorically unrelated case, not "dead code").
    `observed` is purely descriptive of whether any ingested trace ever
    hit this node's own incoming edges - it must never gate eligibility
    for knapsack packing or any other static-graph traversal; callers are
    responsible for keeping it that way (this function only reports the
    fact, it doesn't filter anything).
    """
    total_hits = sum(aggregated.total_hits(u, node) for u in g.predecessors(node)) if node in g else 0
    return NodeObservation(observed=total_hits > 0, statically_reachable=True, total_hits=total_hits)


class HybridFlowEngine(FlowEngine):
    """`FlowEngine`, but `Q_uv` is calibrated by the Unified Hybrid Edge
    Weighting Equation instead of a uniform per-edge count, relative to
    one query's own seed symbol (`dist_static(seed, v)` is, per Section
    2.2.A, a distance *from the seed*, not a repo-global quantity the way
    `FlowEngine`'s own relative-depth/choke-index numbers are) - so unlike
    the parent class's single whole-repository `analyze()`, this is a
    per-query recalibration, called once per `prism query` invocation
    exactly like `prism.slicer.distance.DistanceEngine` already is.

    Every structural guarantee `FlowEngine` provides (SCC contraction so a
    call cycle can't diverge the truncated series, the `k<=4` hop horizon,
    no dense `(I - Q)^-1` inversion) is inherited unchanged - only
    `_build_transition_matrices`'s per-edge weight is overridden.
    """

    def __init__(self, aggregated: AggregatedTrace, alpha: float = DEFAULT_RUNTIME_BIAS, env_weights: dict[str, float] | None = None) -> None:
        super().__init__()
        self._aggregated = aggregated
        self._alpha = alpha
        self._env_weights = env_weights
        self._seed_distances: dict[str, int] = {}

    def analyze_from_seed(
        self, builder: ConcreteGraphBuilder, contracts: dict[str, BehavioralContract], seed: str,
    ) -> FlowEngineResult:
        g = builder.calls_graph
        self._seed_distances = self._multi_source_bfs(g, {seed}) if seed in g else {}
        return self.analyze(builder, contracts)

    def _edge_weight(self, u: str, v: str, edge_data: dict) -> float:
        dist = self._seed_distances.get(v)
        if dist is None or dist < 1:
            # `v` isn't reachable from the seed at all within this hybrid
            # pass's own traversal (or the seed itself never resolved) -
            # a large-but-finite sentinel distance keeps the formula total
            # (never a division by zero / undefined distance) while still
            # ranking every unreached target far below anything the seed
            # can actually structurally get to.
            dist = max(self._seed_distances.values(), default=0) + 1 or 1
        exec_count = weighted_execution_count(u, v, self._aggregated, self._env_weights)
        beta = synonym_priority_multiplier(edge_data, callee_simple_name=v.rsplit(".", 1)[-1])
        return hybrid_edge_weight(dist, exec_count, alpha=self._alpha, beta=beta)

    def _build_transition_matrices(
        self, g, condensation, super_ids, super_index, sink_index, n_super, n_sink,
    ):
        q_rows, q_cols, q_data = [], [], []
        r_rows, r_cols, r_data = [], [], []

        node_to_super_full: dict[str, int] = {}
        for super_id, data in condensation.nodes(data=True):
            for member in data["members"]:
                node_to_super_full[member] = super_id

        for super_id, data in condensation.nodes(data=True):
            members = data["members"]
            out_weight: dict[str, float] = {}
            total = 0.0
            for member in members:
                for _u, v, edata in g.out_edges(member, data=True):
                    if edata.get("relation") not in ("CALLS", "INSTANTIATES"):
                        continue
                    target_super = node_to_super_full.get(v)
                    if target_super == super_id:
                        continue
                    weight = self._edge_weight(member, v, edata)
                    out_weight[v] = out_weight.get(v, 0.0) + weight
                    total += weight
            if total <= 0:
                continue
            si = super_index[super_id]
            for target, weight in out_weight.items():
                prob = weight / total
                if target in sink_index:
                    r_rows.append(si)
                    r_cols.append(sink_index[target])
                    r_data.append(prob)
                elif target in node_to_super_full:
                    tj = super_index.get(node_to_super_full[target])
                    if tj is not None:
                        q_rows.append(si)
                        q_cols.append(tj)
                        q_data.append(prob)

        q_mat = sp.csr_matrix((q_data, (q_rows, q_cols)), shape=(n_super, n_super))
        r_mat = sp.csr_matrix((r_data, (r_rows, r_cols)), shape=(n_super, n_sink))
        return q_mat, r_mat
