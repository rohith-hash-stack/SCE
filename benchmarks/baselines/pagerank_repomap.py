"""PageRank repo-map baseline, in the style of Aider's repository map.

Aider ranks definitions by PageRank over a reference graph, personalized
toward identifiers the user mentioned, and shows the model only the
top-ranked *signatures* (no bodies) that fit a token budget. This module
does the same over PRISM's own AST index, so the only thing that differs
from the other arms is the retrieval policy:

1. **Graph.** Directed symbol graph `G` from `ConcreteGraphBuilder.graph`:
   an edge `u -> v` for every AST relation (CALLS, INSTANTIATES, EXTENDS,
   IMPLEMENTS, READS_STATE, ...) between two symbols in the symbol table.
   Unresolved targets (`<dynamic:...>`, externals) are dropped.
2. **Rank.** `networkx.pagerank(G, alpha=0.85)`. If any symbol's bare name
   appears as an identifier in the query, the teleport vector is
   personalized: query-hit symbols get `QUERY_HIT_WEIGHT`, every other
   node 1. Otherwise uniform PageRank.
3. **Pack.** Symbols in descending score (ties by name) as signature stubs
   (declaration header only), greedily until `budget_tokens`. A stub that
   doesn't fit is skipped, not a stopping point, the same greedy-fill
   convention as the BFS baseline.

No seed symbol is given to the retriever: like Aider, it knows only the
query text. The task seed matters only through the query, where most
prompts name it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.submodular_knapsack import _signature_stub
from prism.slicer.tokenizer import count_tokens

DEFAULT_BUDGET_TOKENS = 4000
DAMPING = 0.85
#: Teleport weight of a query-hit symbol relative to any other node.
#: Aider uses a similar large multiplier for mentioned identifiers.
QUERY_HIT_WEIGHT = 100.0
#: Identifiers shorter than this don't count as a mention ("A", "id").
MIN_IDENT_LEN = 3
#: Bare names too generic to count as a query mention on their own.
_GENERIC_NAMES = frozenset({"__init__", "__call__", "get", "set", "run", "call", "init", "main", "create", "render"})


@dataclass(frozen=True)
class RepoMapEntry:
    symbol: str
    score: float
    rank: int
    signature: str
    tokens: int


@dataclass(frozen=True)
class RepoMap:
    entries: list[RepoMapEntry]
    query_hits: set[str]
    personalized: bool
    graph_nodes: int
    graph_edges: int
    tokens: int


def build_symbol_graph(builder: ConcreteGraphBuilder) -> nx.DiGraph:
    """Every symbol-table symbol as a node, and one edge per AST relation
    between two known symbols (parallel relations collapse to one edge
    with a `weight` count, as Aider collapses repeated references)."""
    known = set(builder.symbol_table._symbols)
    g = nx.DiGraph()
    g.add_nodes_from(known)
    for u, v in builder.graph.edges():
        if u in known and v in known and u != v:
            if g.has_edge(u, v):
                g[u][v]["weight"] += 1.0
            else:
                g.add_edge(u, v, weight=1.0)
    return g


def query_hits(builder: ConcreteGraphBuilder, query_tokens: set[str]) -> set[str]:
    """Symbols whose bare name (lower-cased) appears among the query's
    identifier tokens - Aider's "mentioned identifiers"."""
    hits = set()
    for qname in builder.symbol_table._symbols:
        bare = qname.rsplit(".", 1)[-1].lower()
        if bare in query_tokens and len(bare) >= MIN_IDENT_LEN and bare not in _GENERIC_NAMES:
            hits.add(qname)
    return hits


def rank_symbols(g: nx.DiGraph, hits: set[str]) -> dict[str, float]:
    personalization = None
    if hits:
        personalization = {n: (QUERY_HIT_WEIGHT if n in hits else 1.0) for n in g.nodes}
    return nx.pagerank(g, alpha=DAMPING, personalization=personalization, weight="weight")


def build_repo_map(
    builder: ConcreteGraphBuilder,
    query_tokens: set[str],
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    graph: nx.DiGraph | None = None,
    cost_fn: Callable[[str, str], int] | None = None,
) -> RepoMap:
    """`cost_fn(qname, stub)` prices one entry in whatever unit the caller's
    budget is in (e.g. its rendered size); default is the stub's own tokens."""
    price = cost_fn or (lambda _q, stub: count_tokens(stub))
    g = graph if graph is not None else build_symbol_graph(builder)
    hits = query_hits(builder, query_tokens)
    scores = rank_symbols(g, hits)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    entries: list[RepoMapEntry] = []
    total = 0
    for rank, (qname, score) in enumerate(ranked):
        stub = _signature_stub(builder, qname)
        if not stub:
            continue
        cost = price(qname, stub)
        if total + cost > budget_tokens:
            if budget_tokens - total < 20:
                break  # nothing meaningful fits any more
            continue
        entries.append(RepoMapEntry(qname, score, rank, stub, cost))
        total += cost
    return RepoMap(entries, hits, bool(hits), g.number_of_nodes(), g.number_of_edges(), total)
