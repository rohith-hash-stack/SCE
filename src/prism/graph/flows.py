"""Layer 4 of the Semantic Knowledge Engine: per-entry-root Execution Flow
Pipeline Contracts - trace the reachable subgraph from each
`FlowEngineResult.entry_roots` member through `calls_graph`
(`prism.graph.concrete_builder`) out to its terminating sinks, and
classify the resulting pipeline's topology, depth, fallibility, and
sync/concurrency mode.

Bounded, not exhaustive: a single traversal per entry root, capped at
`MAX_NODES`/`MAX_DEPTH`, mirroring `FlowEngine.MAX_HOPS`'s own "k-hop
horizon, not the whole graph" philosophy - a pipeline whose real depth
exceeds the cap is reported at the cap (a legitimate, if approximate,
signal that the pipeline is at least that deep) rather than paying for an
unbounded walk on a pathological repository.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from prism.analysis.flow_engine import FlowEngineResult
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract

LINEAR_PIPELINE = "LINEAR_PIPELINE"
FAN_OUT_DISPATCHER = "FAN_OUT_DISPATCHER"
CYCLIC_LOOP = "CYCLIC_LOOP"

PURE_SYNC = "PURE_SYNC"
FULL_ASYNC_CHAIN = "FULL_ASYNC_CHAIN"
MIXED_FIRE_AND_FORGET = "MIXED_FIRE_AND_FORGET"

#: Reachable-subgraph traversal caps (see module docstring).
MAX_NODES = 500
MAX_DEPTH = 20

#: `call_site.py`'s own `call_kind` vocabulary uses "fire_and_forget" for
#: any statement-level call whose *return value* is discarded (a plain
#: `validate(x)` in normal synchronous code counts, with nothing to do
#: with concurrency) - not "an async call nobody awaited". `_sync_mode`
#: below deliberately does NOT key off `call_kind` alone for that reason;
#: it uses each callee's own `BehavioralContract.is_async` to decide
#: whether a call is concurrency-relevant at all, and only then looks at
#: whether that specific call site awaited it.


@dataclass
class FlowContract:
    entry_root: str
    flow_topology: str = LINEAR_PIPELINE
    critical_path_depth: int = 0
    fallibility_index: float = 0.0
    sync_concurrency_mode: str = PURE_SYNC
    reached_sinks: tuple[str, ...] = field(default_factory=tuple)
    #: Every internal symbol this pipeline's bounded traversal reached -
    #: not serialized via `to_dict` (that's a per-entry-root summary, not a
    #: full node dump), but used by `prism.graph.hierarchy` to pick which
    #: pipeline "covers" a given target symbol for the serializer's Layer 4
    #: section.
    reached_nodes: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict:
        return {
            "entry_root": self.entry_root,
            "flow_topology": self.flow_topology,
            "critical_path_depth": self.critical_path_depth,
            "fallibility_index": self.fallibility_index,
            "sync_concurrency_mode": self.sync_concurrency_mode,
            "reached_sinks": list(self.reached_sinks),
        }


def _bounded_reachable_subgraph(g: nx.DiGraph, entry: str) -> nx.DiGraph:
    visited: set[str] = {entry}
    frontier = [(entry, 0)]
    edges: list[tuple[str, str]] = []
    while frontier and len(visited) < MAX_NODES:
        node, depth = frontier.pop()
        if depth >= MAX_DEPTH:
            continue
        for succ in g.successors(node):
            edges.append((node, succ))
            if succ not in visited:
                visited.add(succ)
                if len(visited) >= MAX_NODES:
                    break
                frontier.append((succ, depth + 1))
    sub = nx.DiGraph()
    sub.add_nodes_from(visited)
    sub.add_edges_from((u, v) for u, v in edges if u in visited and v in visited)
    return sub


def _classify_topology(sub: nx.DiGraph, entry: str) -> str:
    if not nx.is_directed_acyclic_graph(sub):
        return CYCLIC_LOOP
    max_out = max((sub.out_degree(n) for n in sub.nodes), default=0)
    if max_out > 2:
        return FAN_OUT_DISPATCHER
    return LINEAR_PIPELINE


def _critical_path_depth(sub: nx.DiGraph) -> int:
    if sub.number_of_nodes() <= 1:
        return 0
    if nx.is_directed_acyclic_graph(sub):
        return nx.dag_longest_path_length(sub)
    condensation = nx.condensation(sub)
    return nx.dag_longest_path_length(condensation) if condensation.number_of_nodes() > 1 else 0


def _fallibility_index(sub: nx.DiGraph, g: nx.DiGraph, contracts: dict[str, BehavioralContract]) -> float:
    total = 0
    risky = 0
    for u, v in sub.edges:
        data = g.get_edge_data(u, v) or {}
        total += 1
        contract = contracts.get(v)
        throws = bool(contract and contract.thrown_exceptions)
        guarded = bool(data.get("guarded_by_null_check") or data.get("inside_try_catch"))
        if throws and not guarded:
            risky += 1
    return round(risky / total, 4) if total else 0.0


def _sync_mode(sub: nx.DiGraph, g: nx.DiGraph, contracts: dict[str, BehavioralContract]) -> str:
    saw_async_call = False
    saw_unawaited_async_call = False
    for u, v in sub.edges:
        contract = contracts.get(v)
        if contract is None or not contract.is_async:
            continue
        saw_async_call = True
        data = g.get_edge_data(u, v) or {}
        if data.get("call_kind") != "awaited":
            saw_unawaited_async_call = True
    if not saw_async_call:
        return PURE_SYNC
    if saw_unawaited_async_call:
        return MIXED_FIRE_AND_FORGET
    return FULL_ASYNC_CHAIN


def compute_flows(
    builder: ConcreteGraphBuilder,
    result: FlowEngineResult,
    contracts: dict[str, BehavioralContract],
) -> dict[str, FlowContract]:
    """One `FlowContract` per entry root in `result.entry_roots` - the
    "Active Execution Pipeline" the spec's Layer 4 asks for, traced
    directly off `calls_graph` rather than re-deriving reachability from
    scratch (the same graph `FlowEngine` itself already traverses)."""
    g = builder.calls_graph
    flows: dict[str, FlowContract] = {}
    for entry in result.entry_roots:
        if entry not in g:
            continue
        sub = _bounded_reachable_subgraph(g, entry)
        topology = _classify_topology(sub, entry)
        depth = _critical_path_depth(sub)
        fallibility = _fallibility_index(sub, g, contracts)
        sync_mode = _sync_mode(sub, g, contracts)
        reached_sinks = tuple(sorted(n for n in sub.nodes if n in result.sink_nodes))
        flows[entry] = FlowContract(
            entry_root=entry,
            flow_topology=topology,
            critical_path_depth=depth,
            fallibility_index=fallibility,
            sync_concurrency_mode=sync_mode,
            reached_sinks=reached_sinks,
            reached_nodes=frozenset(sub.nodes),
        )
    return flows
