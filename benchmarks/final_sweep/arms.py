"""Per-corpus state and the retrieval side of each of the 8 arms.

`CorpusState` indexes a corpus once and holds everything every arm needs
(builder, contracts, 4-axis masks, BFS engine, BM25 anchor index,
scaffold rule). The functions below build each arm's context *without*
any LLM call; `benchmarks.final_sweep.runner` owns the LLM turns and
stitches these together.

Retrieval is not thread-safe (shared builder caches, the external-symbol
cache), so every call into it goes through `CorpusState.lock`; the
runner only parallelizes the LLM calls.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from prism.engine import PrismEngine
from prism.external.index import external_symbol_to_node_entry
from prism.language_tiers import precision_tier_for
from prism.semantics.extractor import compute_feature_masks_cached
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import _coverage_summary, _relative_path
from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    EdgeEntry,
    EngineRef,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeEntry,
    SeedRef,
)
from prism.traversal.continuous_dijkstra import compute_topological_distances

from benchmarks.engines.baseline_bfs import BaselineBFSEngine
from benchmarks.final_sweep.context_ops import (
    LexicalAnchorIndex,
    ScaffoldEntry,
    ScaffoldIndex,
    node_for_symbol,
    required_scaffold,
    scaffold_body,
)
from benchmarks.ground_truth.schema import EvaluationTask
from benchmarks.runner import _ground_truth_universe

_ORACLE_EDGE_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"})


@dataclass
class TaskStatic:
    """Everything about a task that doesn't depend on arm or seed -
    computed once per task and shared by every cell."""

    gt_universe: set[str]
    scaffold: list[ScaffoldEntry]
    required_scaffold: list[str]
    #: `(qualified_name, bm25_score)` - the lexical arm's anchor.
    lexical_anchor: tuple[str, float] | None
    #: taxonomy-anchor two-pass candidate universe (used to keep
    #: distractors out of anything the real manifest could surface).
    taxonomy_universe: set[str] = field(default_factory=set)


class CorpusState:
    def __init__(self, repo: str, repo_path: str) -> None:
        self.repo = repo
        self.repo_path = repo_path
        self.lock = threading.RLock()
        self.prism = PrismEngine.from_repo(repo_path)
        self.builder = self.prism.builder
        self.contracts = self.prism._contracts
        self.feature_masks = compute_feature_masks_cached(self.builder, repo_path)
        self.bfs = BaselineBFSEngine(mode="bidirectional")
        # Reuse the already-built graph rather than `bfs.index()`, which
        # would re-run the whole indexing pipeline for an identical builder.
        self.bfs._builder = self.builder
        self.bfs._repo_root = repo_path
        self.lexical = LexicalAnchorIndex(self.builder, self.contracts)
        self.scaffold_index = ScaffoldIndex(self.builder, self.feature_masks)
        self._distances: dict[str, dict[str, float]] = {}
        self._static: dict[str, TaskStatic] = {}

    def distances(self, seed: str) -> dict[str, float]:
        with self.lock:
            if seed not in self._distances:
                self._distances[seed] = compute_topological_distances(self.builder, seed)
            return self._distances[seed]

    def task_static(self, task: EvaluationTask) -> TaskStatic:
        with self.lock:
            cached = self._static.get(task.task_id)
            if cached is not None:
                return cached
            scaffold = self.scaffold_index.scaffold(list(task.adjudicated.pipeline_symbols))
            top = self.lexical.top(task.prompt, k=1)
            _manifest, universe = self.prism.build_candidate_manifest(task.seed_symbol)
            static = TaskStatic(
                gt_universe=_ground_truth_universe(task),
                scaffold=scaffold,
                required_scaffold=required_scaffold(task, scaffold, self.builder),
                lexical_anchor=top[0] if top else None,
                taxonomy_universe=set(universe),
            )
            self._static[task.task_id] = static
            return static

    def envelope(self, engine_id: str, seed: str, budget: int, nodes: list[NodeEntry], considered: int) -> ContextPackage:
        """The `ContextPackage` wrapper for a harness-assembled node list
        (the oracle arms), shaped like `PragmaticOracle`'s own."""
        builder = self.builder
        packed_ids = {n.id for n in nodes}
        edges = []
        for u, v, data in builder.graph.edges(data=True):
            if u in packed_ids and v in packed_ids:
                relation = data.get("relation", "CALLS")
                if relation not in _ORACLE_EDGE_RELATIONS:
                    relation = "CALLS"
                edges.append(EdgeEntry(from_node=u, to_node=v, type=relation, weight=1.0, data_flow=False, guard=False))
        seed_info = builder.symbol_table.get(seed)
        primary_language = seed_info.language_id if seed_info else "python"
        tier = precision_tier_for(primary_language)
        files = {n.file for n in nodes}
        return ContextPackage(
            engine=EngineRef(name=engine_id, version="1.0", commit="final-sweep"),
            seed=SeedRef(
                symbol=seed,
                file=_relative_path(self.repo_path, seed_info.file) if seed_info else "",
                line=seed_info.line_range[0] if seed_info else 0,
            ),
            budget=BudgetRef(tokens=budget, tokenizer="cl100k_base", exact=True),
            language=LanguageRef(tier=tier.value[-1] if tier is not None else "3", primary=primary_language, files=len(files)),
            options={"engine": engine_id},
            manifest=Manifest(
                packed_nodes=len(nodes),
                considered_nodes=considered,
                reachable_nodes=considered,
                compression=[ManifestCompression(level="L0_full", count=len(nodes))] if nodes else [],
                distance_metric=ManifestDistanceMetric(name="oracle_dist_w", lambda_data_flow=0.0, lambda_guard=0.0, dist_max=0.0),
            ),
            coverage=_coverage_summary(self.feature_masks, packed_ids, packed_ids),
            nodes=nodes,
            edges=edges,
        )


@dataclass
class BuiltContext:
    pkg: ContextPackage
    #: How many symbols the arm wanted in context but the retrieval
    #: budget kept out - the budget half of `is_truncated`.
    n_budget_dropped: int = 0
    #: Their names, where the arm enumerates them (the BFS floor's
    #: "wanted" set is its whole reachable component, so it only counts).
    budget_dropped: list[str] = field(default_factory=list)
    #: Nodes intentionally rendered below L0 by the arm's own design (type
    #: scaffolds cut to their header) - not counted as budget downgrades.
    designed_partial: set[str] = field(default_factory=set)


def build_bfs(corpus: CorpusState, task: EvaluationTask, budget: int) -> BuiltContext:
    with corpus.lock:
        pkg = corpus.bfs.retrieve(task.seed_symbol, budget, task_type=task.task_type)
        # BFS's own "wanted" set is the whole reachable component; its
        # manifest already counts it (`considered_nodes` = candidates + seed).
        reachable = pkg.manifest.considered_nodes
    return BuiltContext(pkg=pkg, n_budget_dropped=max(reachable - len(pkg.nodes), 0))


def build_oracle(corpus: CorpusState, task: EvaluationTask, budget: int, scaffolded: bool, engine_id: str) -> BuiltContext:
    """`pragmatic_oracle` (scaffolded=False): the seed plus exactly the
    adjudicated `pipeline_symbols`. `scaffolded_oracle`: those, then the
    annotated `required_context`, then the AST scaffold. Both admit
    greedily in that priority order (pipeline by ascending `dist_W` from
    the seed, as `PragmaticOracle` does), skipping - not stopping at - a
    symbol that would overflow `budget`; the seed is always admitted."""
    static = corpus.task_static(task)
    seed = task.seed_symbol
    with corpus.lock:
        distances = corpus.distances(seed)
        external_cache = {}
        if task.root_imports:
            corpus.prism.build_external_candidate_manifest(
                sorted({seed, *task.adjudicated.pipeline_symbols}), root_imports=task.root_imports,
            )
            external_cache = dict(corpus.prism.external_symbol_cache)

    def by_distance(names):
        return sorted(names, key=lambda q: (-1.0 if q == seed else distances.get(q, float("inf")), q))

    tiers: list[tuple[list[str], str]] = [(by_distance(dict.fromkeys([seed, *task.adjudicated.pipeline_symbols])), "pipeline")]
    if scaffolded:
        pipeline_set = set(task.adjudicated.pipeline_symbols) | {seed}
        annotated = [s for s in by_distance(task.adjudicated.required_context) if s not in pipeline_set]
        tiers.append((annotated, "required_context"))
        ast = [e.symbol for e in static.scaffold if e.symbol not in pipeline_set and e.symbol not in set(annotated)]
        tiers.append((ast, "scaffold"))

    nodes: list[NodeEntry] = []
    wanted: list[str] = []
    designed_partial: set[str] = set()
    total = 0
    with corpus.lock:
        for names, tier in tiers:
            for qname in names:
                if qname in {n.id for n in nodes}:
                    continue
                if corpus.builder.symbol_table.get(qname) is None:
                    ext = external_cache.get(qname)
                    if ext is None:
                        continue
                    wanted.append(qname)
                    cost = count_tokens(ext.signature_text)
                    if qname != seed and total + cost > budget:
                        continue
                    total += cost
                    nodes.append(external_symbol_to_node_entry(ext, distance=distances.get(qname, 1.0)))
                    continue
                wanted.append(qname)
                body, pruned = scaffold_body(corpus.builder, qname) if tier != "pipeline" else (None, False)
                role = "seed" if qname == seed else "transitive"
                node = node_for_symbol(
                    corpus.builder, corpus.repo_path, corpus.contracts, corpus.feature_masks, qname, role,
                    0.0 if role == "seed" else distances.get(qname, 0.0), body=body,
                    compression="L1_pruned" if pruned else "L0_full",
                )
                if node is None:
                    continue
                if qname != seed and total + node.cost > budget:
                    continue
                total += node.cost
                nodes.append(node)
                if pruned:
                    designed_partial.add(qname)
        pkg = corpus.envelope(engine_id, seed, budget, nodes, considered=len(wanted))
    packed = {n.id for n in nodes}
    dropped = [q for q in wanted if q not in packed]
    return BuiltContext(pkg=pkg, n_budget_dropped=len(dropped), budget_dropped=dropped, designed_partial=designed_partial)


def distractor_nodes(corpus: CorpusState, names: list[str]) -> list[NodeEntry]:
    with corpus.lock:
        out = []
        for qname in names:
            node = node_for_symbol(
                corpus.builder, corpus.repo_path, corpus.contracts, corpus.feature_masks, qname, "transitive", 1.0,
            )
            if node is not None:
                out.append(node)
        return out
