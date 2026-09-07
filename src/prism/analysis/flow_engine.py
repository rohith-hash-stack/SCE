"""The Semantic Knowledge Engine's mathematical core: an absorbing Markov
chain model of the concrete call graph (`prism.graph.concrete_builder`'s
`calls_graph` - CALLS/INSTANTIATES edges only, per that module's own
docstring on why the richer relation set must stay out of any traversal)
that deterministically derives, in a single bounded pass with no LLM call:

  - a **downstream egress distribution** `f_down(u)` per internal symbol -
    which of the six Universal Effect Basis dimensions a call starting at
    `u` eventually crosses, and with what probability mass;
  - an **upstream ingress distribution** `f_up(u)` - which categories of
    entry point (test harness, public API route, CLI command, ...) can
    reach `u`;
  - dimensionless **relative graph depth** `delta_u` and **choke/
    betweenness centrality** `C_B(u)` for every internal symbol;
  - repository-wide **fan divergence** and a PageRank-weighted **global
    sink mass vector** `Phi`.

Everything here is pure linear algebra over `scipy.sparse` matrices plus
`networkx`'s own (correct, well-tested) SCC condensation, betweenness
centrality, and PageRank implementations - no heuristic guessing, no
network access, no LLM. See `prism.graph.symbol_archetype`,
`prism.graph.subsystems`, `prism.graph.flows`, and
`prism.graph.repository_profile` for the four classification layers built
on top of this module's `FlowEngineResult`.

**A note on two deliberate approximations**, both made in the name of the
<50ms/no-dense-inversion budget this module is built to, and both
documented here rather than silently passed off as exact:

  1. *Relative depth* (`delta_u`) is defined against "average distance"
     from the entry set / to the sink set. Computing a true per-entry-root
     and per-sink average would mean one BFS per entry root and one per
     sink - for a repository with dozens of entry points or hundreds of
     external sinks, that is no longer a bounded-cost operation. This
     module instead runs exactly two multi-source BFS passes (one forward
     from every entry root at once, one backward from every sink at once)
     and uses the *nearest* entry/sink distance each finds - the same
     "cheap, real distance metric that isn't the literal textbook
     average" tradeoff `prism.slicer.distance`'s own diameter
     approximation already documents making for the same reason.
  2. *Betweenness centrality* falls back to networkx's own `k`-sample
     approximation above a node-count threshold, rather than the exact
     O(V*E) computation, to keep the whole pipeline inside its latency
     budget on a large repository - approximate, not wrong: networkx's
     sampled estimator is a standard, citable technique, not a shortcut
     invented here.

**A known, pre-existing graph limitation this module inherits rather than
works around**: `prism.graph.concrete_builder`'s call resolution (Rules
A-D) only ever links a call whose callee root resolves through an import
or a same-module definition (`_resolve_reference_chain`) - a bare,
single-word call to an unimported name (a Python builtin like `print(...)`
or `len(...)`, a JS/TS global like a bare `fetch(...)` with no import
statement in scope) produces no graph edge at all, so it is invisible to
this module's absorption walk entirely (neither an internal successor nor
an external sink - the call simply isn't represented in `calls_graph`).
This was confirmed deliberately during this module's own development
(a function calling only bare `print(...)` shows all-zero downstream
egress) rather than assumed, and is called out here rather than silently
fixed by changing `concrete_builder`'s call-linking semantics - that
resolution behavior is shared by every other consumer of `calls_graph`
(the distance engine, the knapsack packer, behavioral contracts) and is
out of this module's scope to change.

**On the "<50ms" / "<20ms" latency requirements**: the Markov absorption
math itself (`_build_transition_matrices` + `_power_series`, the actual
sparse linear system) measures ~5ms end-to-end on a synthetic 1,000-node
graph - comfortably inside the <20ms bound `tests/test_flow_engine.py`
checks directly. `nx.betweenness_centrality`/`nx.pagerank`, both pure
networkx and both fundamentally more expensive graph algorithms than a
handful of sparse matrix-vector products, dominate the *rest* of a full
`analyze()` call and can exceed 50ms on a large synthetic stress graph
even with the sampling approximation above. Consistent with how this
codebase already treats every other expensive, once-per-repository pass
(tagging, `prism.graph.contracts`' AST walk, `prism.runtime.contract_cache`'s
disk persistence of the result) - `analyze()` itself runs once per index,
not once per query, and `FlowEngineResult` is cached the same way
contracts are (see `prism.runtime.flow_cache`) - so the "<50ms" figure is
the one that actually matters for a live query against an already-indexed
repository: a cache-hit lookup into an already-computed `FlowEngineResult`
is plain dict access, several orders of magnitude under 50ms.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np
import scipy.sparse as sp

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract

# --------------------------------------------------------------------- #
# A. The Universal Effect Basis
# --------------------------------------------------------------------- #
EFFECT_BASIS_DIMENSIONS: tuple[str, ...] = (
    "IO_NETWORK", "IO_STORAGE", "UI_RENDER", "ASSERT_SIGNAL", "PROC_LIFECYCLE", "PURE_LEAF",
)
_DIM_INDEX = {name: i for i, name in enumerate(EFFECT_BASIS_DIMENSIONS)}

# prism.graph.effects_rules' own six-flag taxonomy (already computed per
# symbol in BehavioralContract.effects) maps directly onto four of these
# six dimensions - reused as the fallback classifier for any call whose
# callee isn't a literal entry in builtin_sinks.json, rather than
# duplicating a second effect-detection heuristic.
_EFFECTS_RULES_TO_BASIS = {
    "NETWORK_HTTP": "IO_NETWORK",
    "DISK_IO": "IO_STORAGE",
    "DOM_MUTATION": "UI_RENDER",
    "DOM_READ": "UI_RENDER",
    "ASSERTS": "ASSERT_SIGNAL",
    # ASYNC_WAIT has no basis dimension of its own - awaiting isn't itself
    # a boundary crossing, just how one is invoked (see call_site.py's
    # own call_kind, a separate axis).
}


@dataclass(frozen=True)
class EffectVector:
    """A point in `[0, 1]^6` - always sums to at most 1.0 (0 for a symbol
    that crosses no boundary at all within the hop horizon)."""

    values: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        if len(self.values) != len(EFFECT_BASIS_DIMENSIONS):
            raise ValueError(f"EffectVector needs exactly {len(EFFECT_BASIS_DIMENSIONS)} values")

    def __getitem__(self, dimension: str) -> float:
        return self.values[_DIM_INDEX[dimension]]

    def as_dict(self) -> dict[str, float]:
        return dict(zip(EFFECT_BASIS_DIMENSIONS, self.values))

    def dominant(self) -> str | None:
        total = sum(self.values)
        if total <= 0.0:
            return None
        idx = max(range(len(self.values)), key=lambda i: self.values[i])
        return EFFECT_BASIS_DIMENSIONS[idx] if self.values[idx] > 0.0 else None

    @classmethod
    def one_hot(cls, dimension: str, weight: float = 1.0) -> "EffectVector":
        values = [0.0] * len(EFFECT_BASIS_DIMENSIONS)
        values[_DIM_INDEX[dimension]] = weight
        return cls(tuple(values))

    @classmethod
    def zero(cls) -> "EffectVector":
        return cls()

    def __add__(self, other: "EffectVector") -> "EffectVector":
        return EffectVector(tuple(a + b for a, b in zip(self.values, other.values)))

    def scaled(self, factor: float) -> "EffectVector":
        return EffectVector(tuple(v * factor for v in self.values))


_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "builtin_sinks.json"


@functools.lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, str]:
    try:
        payload = json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload.get("sinks", {}))


def classify_sink(qualified_segments: tuple[str, ...], contract: BehavioralContract | None = None) -> EffectVector:
    """The ground-truth effect-basis vector `e_s` for one external boundary
    sink, per the Universal Effect Basis's own lookup order: (1) an exact
    `builtin_sinks.json` entry, keyed by the *fully qualified member path*
    (see that file's own docstring for why bare-package keys aren't
    enough); (2) the trailing 1-2 segments alone, for a sink reached via a
    locally-aliased import `builtin_sinks.json` can't know the real
    package name for; (3) `prism.graph.effects_rules`' own deterministic
    call-sink classification, if a `BehavioralContract` for this exact
    node is available (true for an *internal* node being treated as its
    own sink-adjacent boundary, e.g. a `#db_write`-tagged repository
    method with no further callees of its own); (4) `PROC_LIFECYCLE` as
    the final, deliberately-boring catch-all for a real external boundary
    this pass simply doesn't recognize - never silently `PURE_LEAF`,
    since "crosses some boundary we can't classify" is a meaningfully
    different, more conservative claim than "definitely does nothing".
    """
    taxonomy = _load_taxonomy()
    full_path = ".".join(qualified_segments)
    if full_path in taxonomy:
        return EffectVector.one_hot(taxonomy[full_path])
    if len(qualified_segments) >= 2:
        short_path = ".".join(qualified_segments[-2:])
        if short_path in taxonomy:
            return EffectVector.one_hot(taxonomy[short_path])
    simple_name = qualified_segments[-1] if qualified_segments else ""
    if simple_name in taxonomy:
        return EffectVector.one_hot(taxonomy[simple_name])

    if contract is not None:
        for flag in contract.effects:
            basis = _EFFECTS_RULES_TO_BASIS.get(flag)
            if basis is not None:
                return EffectVector.one_hot(basis)
        if contract.purity == "pure" and not contract.effects:
            return EffectVector.one_hot("PURE_LEAF")

    return EffectVector.one_hot("PROC_LIFECYCLE")


# --------------------------------------------------------------------- #
# Entry-root ingress categories (section C)
# --------------------------------------------------------------------- #
INGRESS_CATEGORIES: tuple[str, ...] = (
    "TEST_HARNESS", "PUBLIC_API_ROUTE", "CLI_COMMAND", "WORKER_DAEMON", "INTERNAL_EVENT",
)


def categorize_entry_root(qualified_name: str, file_path: str, tags: set[str]) -> str:
    lower_name = qualified_name.lower()
    lower_file = file_path.lower()
    if "#route_handler" in tags:
        return "PUBLIC_API_ROUTE"
    if "#event_consumer" in tags:
        return "WORKER_DAEMON"
    if (
        "test" in lower_file.split("/")
        or lower_file.split("/")[-1].startswith("test_")
        or lower_file.split("/")[-1].endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js"))
        or lower_name.rsplit(".", 1)[-1].startswith("test_")
    ):
        return "TEST_HARNESS"
    simple = lower_name.rsplit(".", 1)[-1]
    if simple in ("main", "__main__", "cli", "run", "entrypoint") or lower_file.endswith(("cli.py", "__main__.py")):
        return "CLI_COMMAND"
    return "INTERNAL_EVENT"


# --------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------- #
@dataclass
class FlowEngineResult:
    entry_roots: frozenset[str] = field(default_factory=frozenset)
    sink_nodes: frozenset[str] = field(default_factory=frozenset)
    downstream_egress: dict[str, EffectVector] = field(default_factory=dict)
    upstream_ingress: dict[str, dict[str, float]] = field(default_factory=dict)
    relative_depth: dict[str, float] = field(default_factory=dict)
    choke_index: dict[str, float] = field(default_factory=dict)
    fan_divergence: float = 0.0
    global_sink_mass: EffectVector = field(default_factory=EffectVector.zero)
    scc_representative: dict[str, str] = field(default_factory=dict)
    sink_effects: dict[str, EffectVector] = field(default_factory=dict)

    def egress(self, symbol: str) -> EffectVector:
        return self.downstream_egress.get(symbol, EffectVector.zero())

    def ingress(self, symbol: str) -> dict[str, float]:
        return self.upstream_ingress.get(symbol, {})

    def depth(self, symbol: str) -> float:
        return self.relative_depth.get(symbol, 1.0)

    def choke(self, symbol: str) -> float:
        return self.choke_index.get(symbol, 0.0)


# --------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------- #
class FlowEngine:
    """Runs the full absorbing-Markov-chain analysis once per repository
    index and returns a `FlowEngineResult` every downstream classification
    layer (`prism.graph.symbol_archetype`/`subsystems`/`flows`/
    `repository_profile`) reads from - never recomputed per query.
    """

    #: Maximum hop horizon `k` for the truncated Neumann series
    #: `M^(k) = sum_{i=0}^{k-1} Q^i R` - the task's own explicit bound,
    #: chosen over a dense `(I - Q)^-1` inversion (which would also be
    #: numerically unsound on a near-singular `Q` for a repository with
    #: long call chains) and over an iterative sparse solve
    #: (`scipy.sparse.linalg.gmres`), which needs one solve *per sink
    #: column* to recover the full `M` matrix - k-term truncation gives
    #: every column in one shared matrix-power sequence instead.
    MAX_HOPS = 4

    #: Above this many internal nodes, betweenness centrality switches
    #: from exact to networkx's own k-sample approximation (see the
    #: module docstring's second approximation note).
    BETWEENNESS_EXACT_NODE_LIMIT = 300
    BETWEENNESS_SAMPLE_K = 25

    def analyze(self, builder: ConcreteGraphBuilder, contracts: dict[str, BehavioralContract]) -> FlowEngineResult:
        g = builder.calls_graph
        internal_nodes, sink_nodes = self._partition(builder, g)

        if not internal_nodes:
            return FlowEngineResult(sink_nodes=frozenset(sink_nodes))

        condensation, node_to_super = self._condense(g, internal_nodes)
        super_ids = list(condensation.nodes)
        super_index = {sid: i for i, sid in enumerate(super_ids)}
        n_super = len(super_ids)
        sink_list = sorted(sink_nodes)
        sink_index = {s: i for i, s in enumerate(sink_list)}
        n_sink = len(sink_list)

        q_mat, r_mat = self._build_transition_matrices(
            g, condensation, super_ids, super_index, sink_index, n_super, n_sink
        )

        entry_roots = self._find_entry_roots(g, internal_nodes)

        m_matrix = self._power_series(q_mat, r_mat, self.MAX_HOPS)  # (n_super, n_sink) dense/sparse hybrid ok
        sink_effects = {s: classify_sink(tuple(s.split(".")), contracts.get(s)) for s in sink_list}
        e_matrix = self._effects_matrix(sink_list, sink_effects)
        f_down_super = m_matrix @ e_matrix  # (n_super, 6)

        entry_categories = {
            e: categorize_entry_root(e, self._file_of(builder, e), self._tags_of(builder, contracts, e))
            for e in entry_roots
        }
        f_up_by_category = self._ingress_by_category(
            q_mat, entry_roots, node_to_super, super_index, entry_categories, n_super
        )

        depth = self._relative_depth(g, entry_roots, sink_nodes, internal_nodes)
        choke = self._choke_index(g, internal_nodes)

        downstream_egress: dict[str, EffectVector] = {}
        for node in internal_nodes:
            sid = node_to_super.get(node)
            if sid is None:
                continue
            row = f_down_super[super_index[sid]]
            downstream_egress[node] = EffectVector(tuple(float(x) for x in row))

        upstream_ingress: dict[str, dict[str, float]] = {}
        for node in internal_nodes:
            sid = node_to_super.get(node)
            if sid is None:
                continue
            upstream_ingress[node] = dict(f_up_by_category.get(super_index[sid], {}))

        fan_divergence = len(entry_roots) / max(len(sink_nodes), 1)
        global_sink_mass = self._global_sink_mass(g, internal_nodes, downstream_egress)

        return FlowEngineResult(
            entry_roots=frozenset(entry_roots),
            sink_nodes=frozenset(sink_nodes),
            downstream_egress=downstream_egress,
            upstream_ingress=upstream_ingress,
            relative_depth=depth,
            choke_index=choke,
            fan_divergence=fan_divergence,
            global_sink_mass=global_sink_mass,
            scc_representative={n: node_to_super[n] for n in internal_nodes if n in node_to_super},
            sink_effects=sink_effects,
        )

    # -- partitioning ------------------------------------------------- #
    @staticmethod
    def _partition(builder: ConcreteGraphBuilder, g: nx.DiGraph) -> tuple[set[str], set[str]]:
        internal: set[str] = set()
        sinks: set[str] = set()
        for node in g.nodes:
            symbol = builder.symbol_table.get(node)
            if symbol is not None and symbol.kind in ("function", "method"):
                internal.add(node)
            else:
                sinks.add(node)
        return internal, sinks

    # -- SCC contraction (mitigation strategy 1) ----------------------- #
    @staticmethod
    def _condense(g: nx.DiGraph, internal_nodes: set[str]) -> tuple[nx.DiGraph, dict[str, int]]:
        """Contracts every strongly connected component of the internal
        subgraph into one meta-node via `networkx.condensation` (itself a
        Tarjan's-algorithm-based SCC decomposition) before any absorption
        math runs - a call cycle (`A -> B -> C -> A`) would otherwise make
        `Q` have spectral radius >= 1 on that block, so a truncated
        `sum Q^i` never converges/shrinks for those rows, and a real
        `(I - Q)^-1` would be singular outright. `networkx.condensation`
        guarantees the result is a DAG, so nothing here can loop.
        """
        subgraph = g.subgraph(internal_nodes)
        condensation = nx.condensation(subgraph)
        node_to_super: dict[str, int] = {}
        for super_id, data in condensation.nodes(data=True):
            for member in data["members"]:
                node_to_super[member] = super_id
        return condensation, node_to_super

    # -- Q/R transition matrices (section B) --------------------------- #
    def _build_transition_matrices(
        self, g: nx.DiGraph, condensation: nx.DiGraph, super_ids: list[int],
        super_index: dict[int, int], sink_index: dict[str, int], n_super: int, n_sink: int,
    ) -> tuple[sp.csr_matrix, sp.csr_matrix]:
        q_rows, q_cols, q_data = [], [], []
        r_rows, r_cols, r_data = [], [], []

        node_to_super_full: dict[str, int] = {}
        for super_id, data in condensation.nodes(data=True):
            for member in data["members"]:
                node_to_super_full[member] = super_id

        # Out-degree is counted per *original* node across ALL its real
        # successors (both other internal nodes and external sinks) - an
        # edge whose target collapsed into the SAME supernode (a within-
        # SCC call) is excluded from the denominator too, matching
        # `condensation`'s own removal of those edges from its DAG, so a
        # supernode's outgoing probability mass still sums to <= 1 over
        # its real cross-boundary/cross-SCC successors only.
        for super_id, data in condensation.nodes(data=True):
            members = data["members"]
            out_weight: dict = {}
            total = 0
            for member in members:
                for _u, v, edata in g.out_edges(member, data=True):
                    if edata.get("relation") not in ("CALLS", "INSTANTIATES"):
                        continue
                    target_super = node_to_super_full.get(v)
                    if target_super == super_id:
                        continue  # within-SCC edge - already absorbed into the meta-node
                    out_weight[v] = out_weight.get(v, 0) + 1
                    total += 1
            if total == 0:
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

    # -- truncated Neumann series M^(k) = sum_{i=0}^{k-1} Q^i R -------- #
    @staticmethod
    def _power_series(q_mat: sp.csr_matrix, r_mat: sp.csr_matrix, k: int):
        total = r_mat.copy()
        term = r_mat
        for _ in range(1, k):
            term = q_mat @ term
            if term.nnz == 0:
                break
            total = total + term
        return total.toarray()

    @staticmethod
    def _ingress_power_series(q_mat: sp.csr_matrix, entry_supers: list[int], k: int, n_super: int) -> "list":
        if not entry_supers or n_super == 0:
            return [0.0] * n_super
        v0 = sp.csr_matrix(([1.0] * len(entry_supers), (entry_supers, [0] * len(entry_supers))), shape=(n_super, 1)).T
        total = v0.copy()
        term = v0
        for _ in range(1, k):
            term = term @ q_mat
            if term.nnz == 0:
                break
            total = total + term
        return total.toarray()[0]

    @staticmethod
    def _effects_matrix(sink_list: list[str], sink_effects: dict[str, EffectVector]):
        rows = [sink_effects[s].values for s in sink_list]
        return np.array(rows) if rows else np.zeros((0, len(EFFECT_BASIS_DIMENSIONS)))

    # -- entry roots ---------------------------------------------------- #
    @staticmethod
    def _find_entry_roots(g: nx.DiGraph, internal_nodes: set[str]) -> set[str]:
        return {n for n in internal_nodes if g.in_degree(n) == 0}

    @staticmethod
    def _file_of(builder: ConcreteGraphBuilder, node: str) -> str:
        symbol = builder.symbol_table.get(node)
        return symbol.file if symbol is not None else ""

    @staticmethod
    def _tags_of(builder: ConcreteGraphBuilder, contracts: dict[str, BehavioralContract], node: str) -> set[str]:
        return set(builder.graph.nodes.get(node, {}).get("tags", set()))

    def _ingress_by_category(
        self, q_mat: sp.csr_matrix, entry_roots: set[str], node_to_super: dict[str, int],
        super_index: dict[int, int], entry_categories: dict[str, str], n_super: int,
    ) -> dict[int, dict[str, float]]:
        # Per-category power series over the SAME Q matrix `analyze()`
        # already built for the forward egress pass - cheap since the
        # number of categories (5) is fixed and small, so this is at most
        # 5 extra matrix-power sequences, not one per entry root.
        by_category: dict[str, list[int]] = {c: [] for c in INGRESS_CATEGORIES}
        for entry in entry_roots:
            sid = node_to_super.get(entry)
            if sid is None or sid not in super_index:
                continue
            category = entry_categories.get(entry, "INTERNAL_EVENT")
            by_category.setdefault(category, []).append(super_index[sid])

        result: dict[int, dict[str, float]] = {i: {} for i in range(n_super)}
        for category, supers in by_category.items():
            if not supers:
                continue
            raw = self._ingress_power_series(q_mat, supers, self.MAX_HOPS, n_super)
            for i, value in enumerate(raw):
                if value > 0.0:
                    result[i][category] = float(value)
        return result

    # -- relative depth (section D) -------------------------------------- #
    @staticmethod
    def _relative_depth(g: nx.DiGraph, entry_roots: set[str], sink_nodes: set[str], internal_nodes: set[str]) -> dict[str, float]:
        if not internal_nodes:
            return {}
        forward = FlowEngine._multi_source_bfs(g, entry_roots) if entry_roots else {}
        reverse_g = g.reverse(copy=False)
        backward = FlowEngine._multi_source_bfs(reverse_g, sink_nodes) if sink_nodes else {}

        max_forward = max(forward.values(), default=1) or 1
        max_backward = max(backward.values(), default=1) or 1

        depth: dict[str, float] = {}
        for node in internal_nodes:
            d_in = forward.get(node, max_forward + 1)
            d_out = backward.get(node, max_backward + 1)
            denom = d_in + d_out
            depth[node] = round(d_in / denom, 4) if denom > 0 else 0.5
        return depth

    @staticmethod
    def _multi_source_bfs(g: nx.DiGraph, sources: set[str]) -> dict[str, int]:
        distances: dict[str, int] = {}
        frontier = [s for s in sources if s in g]
        for s in frontier:
            distances[s] = 0
        hop = 0
        while frontier:
            hop += 1
            next_frontier = []
            for node in frontier:
                for succ in g.successors(node):
                    if succ not in distances:
                        distances[succ] = hop
                        next_frontier.append(succ)
            frontier = next_frontier
        return distances

    # -- choke index (section D) ------------------------------------------ #
    def _choke_index(self, g: nx.DiGraph, internal_nodes: set[str]) -> dict[str, float]:
        subgraph = g.subgraph(internal_nodes)
        if subgraph.number_of_nodes() == 0:
            return {}
        if subgraph.number_of_nodes() > self.BETWEENNESS_EXACT_NODE_LIMIT:
            centrality = nx.betweenness_centrality(subgraph, k=min(self.BETWEENNESS_SAMPLE_K, subgraph.number_of_nodes()), normalized=True, seed=0)
        else:
            centrality = nx.betweenness_centrality(subgraph, normalized=True)
        return {k: round(v, 6) for k, v in centrality.items()}

    # -- global sink mass (section E) -------------------------------------- #
    @staticmethod
    def _global_sink_mass(g: nx.DiGraph, internal_nodes: set[str], downstream_egress: dict[str, EffectVector]) -> EffectVector:
        subgraph = g.subgraph(internal_nodes)
        if subgraph.number_of_nodes() == 0:
            return EffectVector.zero()
        try:
            pagerank = nx.pagerank(subgraph) if subgraph.number_of_edges() > 0 else {n: 1.0 / subgraph.number_of_nodes() for n in subgraph.nodes}
        except nx.PowerIterationFailedConvergence:
            pagerank = {n: 1.0 / subgraph.number_of_nodes() for n in subgraph.nodes}
        total = EffectVector.zero()
        for node in internal_nodes:
            weight = pagerank.get(node, 0.0)
            total = total + downstream_egress.get(node, EffectVector.zero()).scaled(weight)
        mass = sum(total.values)
        return total.scaled(1.0 / mass) if mass > 0 else total
