"""v1.1+ Empirical Benchmarking Harness: Baseline B - vanilla topological
AST BFS.

Real AST call-graph edges (`ConcreteGraphBuilder.calls_graph` - Prism's
own Pass 2 `CALLS`/`INSTANTIATES` linking, unavoidably "real" since that
*is* what an AST call graph means) traversed by plain unweighted BFS -
deliberately **without** the causal engine's own additions: no synthetic
data-flow/guard edges (`prism.traversal.causal_weights`), no canonical
sink detection, no four-axis `FeatureBit` masks, no continuous-Dijkstra
weighting. Two modes:

  - `"forward"`: only `calls_graph.successors` (callees), decayed
    `1.0 / (1 + depth)**2`.
  - `"bidirectional"`: forward callees at the same decay, plus
    `calls_graph.predecessors` (callers) decayed `0.7 / (1 + depth)**2` -
    a real, if cruder, blast-radius notion than Prism's own weighted
    `prism.packer.blast_radius` (no return-unpack/argument-triviality
    indicators, just raw hop distance).

Candidates are sorted strictly by that one topological score and admitted
greedily until `budget_tokens` is filled (skipping, not stopping at, a
candidate that would overflow - the same greedy-fill convention
`baseline_rag.py` uses). `NodeSignature`/`NodeFeatures` stay empty/"NONE"
for the same reason `baseline_rag.py`'s do: this baseline has no
signature-contract or four-axis-semantic capability of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.language_tiers import precision_tier_for
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import _relative_path
from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    SeedRef,
)

from benchmarks.engines.base import AbstractRetrievalEngine

FORWARD_DECAY_BASE = 1.0
BACKWARD_DECAY_BASE = 0.7

_TRAVERSABLE_RELATIONS = frozenset({"CALLS", "INSTANTIATES"})


def _decay(base: float, depth: int) -> float:
    return base / ((1 + depth) ** 2)


@dataclass
class _Candidate:
    symbol: str
    depth: int
    direction: Literal["forward", "backward"]
    score: float


def _bfs_depths(graph, seed: str) -> dict[str, int]:
    """Plain unweighted BFS hop distances from `seed` via `graph.
    successors` - no edge weighting, no Dijkstra, the "vanilla AST BFS"
    the spec asks for. `seed` itself is never included (matches `prism.
    traversal.continuous_dijkstra.compute_topological_distances`'s own
    convention)."""
    if seed not in graph:
        return {}
    depths: dict[str, int] = {seed: 0}
    frontier = [seed]
    while frontier:
        next_frontier = []
        for node in frontier:
            for succ in graph.successors(node):
                if succ not in depths:
                    depths[succ] = depths[node] + 1
                    next_frontier.append(succ)
        frontier = next_frontier
    depths.pop(seed, None)
    return depths


class BaselineBFSEngine(AbstractRetrievalEngine):
    def __init__(self, mode: Literal["forward", "bidirectional"] = "forward") -> None:
        if mode not in ("forward", "bidirectional"):
            raise ValueError(f"mode must be 'forward' or 'bidirectional', got {mode!r}")
        self.mode = mode
        self.name = f"baseline_bfs_{mode}"
        self._repo_root: str | None = None
        self._builder: ConcreteGraphBuilder | None = None

    def index(self, repo_path: str) -> None:
        self._repo_root = repo_path
        self._builder, _tag_matrix = build_pipeline(repo_path)

    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        if self._builder is None or self._repo_root is None:
            raise RuntimeError("BaselineBFSEngine.retrieve called before index()")
        builder = self._builder
        g = builder.calls_graph

        candidates: dict[str, _Candidate] = {}
        for sym, depth in _bfs_depths(g, seed_symbol).items():
            candidates[sym] = _Candidate(symbol=sym, depth=depth, direction="forward", score=_decay(FORWARD_DECAY_BASE, depth))

        if self.mode == "bidirectional":
            reverse = g.reverse(copy=False)
            for sym, depth in _bfs_depths(reverse, seed_symbol).items():
                if sym in candidates:
                    continue  # already scored via a forward path - the stronger signal wins, no double-counting
                candidates[sym] = _Candidate(symbol=sym, depth=depth, direction="backward", score=_decay(BACKWARD_DECAY_BASE, depth))

        ranked = sorted(candidates.values(), key=lambda c: c.score, reverse=True)

        seed_info = builder.symbol_table.get(seed_symbol)
        selected: dict[str, _Candidate] = {}
        if seed_info is not None:
            selected[seed_symbol] = _Candidate(seed_symbol, 0, "forward", 1.0)

        total_tokens = 0
        for candidate in ranked:
            info = builder.symbol_table.get(candidate.symbol)
            if info is None or candidate.symbol in selected:
                continue
            parsed = builder.parsed_file(info.file)
            body = ""
            if parsed is not None:
                src = parsed.source.decode("utf-8", errors="replace").splitlines()
                start, end = info.line_range
                body = "\n".join(src[max(start - 1, 0):end])
            cost = count_tokens(body)
            if total_tokens + cost > budget_tokens and selected:
                continue
            selected[candidate.symbol] = candidate
            total_tokens += cost

        nodes: list[NodeEntry] = []
        files_seen: set[str] = set()
        for symbol, candidate in selected.items():
            info = builder.symbol_table.get(symbol)
            if info is None:
                continue
            files_seen.add(info.file)
            parsed = builder.parsed_file(info.file)
            body = ""
            if parsed is not None:
                src = parsed.source.decode("utf-8", errors="replace").splitlines()
                start, end = info.line_range
                body = "\n".join(src[max(start - 1, 0):end])
            role = "seed" if symbol == seed_symbol else ("caller" if candidate.direction == "backward" else "callee")
            nodes.append(
                NodeEntry(
                    id=symbol,
                    role=role,
                    distance=float(candidate.depth),
                    compression="L0_full",
                    cost=count_tokens(body),
                    symbol_name=symbol.rsplit(".", 1)[-1],
                    symbol_kind=info.kind,
                    language=info.language_id,
                    file=_relative_path(self._repo_root, info.file),
                    line=info.line_range[0],
                    end_line=info.line_range[1],
                    signature=NodeSignature(),
                    features=NodeFeatures(substance="NONE", form="NONE", output="NONE", role="NONE"),
                    body=body,
                )
            )

        packed_ids = set(selected)
        edges: list[EdgeEntry] = []
        for u, v, data in builder.graph.edges(data=True):
            if u not in packed_ids or v not in packed_ids:
                continue
            relation = data.get("relation", "CALLS")
            if relation not in _TRAVERSABLE_RELATIONS:
                continue
            u_dist = selected[u].depth
            v_dist = selected[v].depth
            edges.append(EdgeEntry(from_node=u, to_node=v, type=relation, weight=1.0, data_flow=False, guard=False, back_edge=v_dist < u_dist))

        primary_language = seed_info.language_id if seed_info else "python"
        tier = precision_tier_for(primary_language)
        tier_digit = tier.value[-1] if tier is not None else "3"

        return ContextPackage(
            engine=EngineRef(name=self.name, version="1.0", commit="ast-bfs"),
            seed=SeedRef(
                symbol=seed_symbol,
                file=_relative_path(self._repo_root, seed_info.file) if seed_info else "",
                line=seed_info.line_range[0] if seed_info else 0,
            ),
            budget=BudgetRef(tokens=budget_tokens, tokenizer="cl100k_base", exact=True),
            language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
            options={"engine": self.name, "mode": self.mode},
            manifest=Manifest(
                packed_nodes=len(nodes),
                considered_nodes=len(candidates) + 1,
                reachable_nodes=len(candidates) + 1,
                compression=[ManifestCompression(level="L0_full", count=len(nodes))] if nodes else [],
                distance_metric=ManifestDistanceMetric(name="bfs_decay", lambda_data_flow=0.0, lambda_guard=0.0, dist_max=0.0),
            ),
            coverage=CoverageSummary(total_features=0, covered_features=0, omitted_features=0, features=[]),
            nodes=nodes,
            edges=edges,
        )
