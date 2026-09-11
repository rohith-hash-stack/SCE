"""v1.1+ Empirical Benchmarking Harness: two Oracle engines sharing the
same `ContextPackage`-rendering machinery and `ENGINE_NAME = "oracle"`
identity (a pilot run picks exactly one of the two - never both at once,
since `benchmarks.runner`'s own `fpr_oracle` wiring keys off
`engine.name == "oracle"` to mean "the Oracle's package for this cell"):

- `OracleEngine` - loads a hand-curated "optimal package" symbol list
  for one task and resolves each symbol's real body/signature/features
  from the actually-indexed repository.

  **Stated honestly**: this loader is real and fully functional, but
  this repository ships no genuine hand-curated oracle packages for
  django/gin/trpc/express - producing those requires a domain expert
  manually selecting the truly-optimal symbol set per task, which this
  session cannot fabricate convincingly (a self-authored "hand-curated"
  answer would just be this engine grading itself). `oracle_packages_
  path` points at whatever curated file an operator supplies; `tests/
  benchmarks/` uses a small synthetic one to exercise this module for
  real.

  File format: a YAML or JSON mapping `{task_id: [ordered qualified
  symbol names]}` - the seed itself doesn't need to be listed (it's
  always included).

- `PragmaticOracle` (Gap 2 Blocker 2, Option B) - a real, deterministic,
  zero-annotation-cost substitute: the union of an `EvaluationTask`'s
  own `adjudicated.pipeline_symbols`/`required_context`/`boundary_
  symbols` (Gap 8's three real annotated sets - never fabricated),
  rendered at L0 and truncated to budget by ascending real topological
  distance from the seed (see `PragmaticOracle`'s own docstring for
  exactly what "ascending distance" means and why it deliberately
  doesn't reuse Prism's full weighted Continuous Dijkstra). Chosen over
  Option A (hand-curated, 18-36 annotator-hours for 72 packages) and
  Option C (deferring FPR entirely) - see `reports/pilot/methodology.md`.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.language_tiers import precision_tier_for
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.semantics.bitmask import FORM_BITS, OUTPUT_BITS, ROLE_BITS, SUBSTANCE_BITS
from prism.semantics.extractor import compute_feature_masks
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import _axis_labels, _coverage_summary, _node_body, _node_signature, _relative_path
from prism.traversal.continuous_dijkstra import compute_topological_distances
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
    NodeFeatures,
    SeedRef,
)

from benchmarks.engines.base import AbstractRetrievalEngine
from benchmarks.ground_truth.schema import EvaluationTask

ENGINE_NAME = "oracle"

_TRAVERSABLE_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"})


class OracleLoadError(Exception):
    """Raised for a missing oracle-packages file, or one with no entry
    for the requested `task_id` - never a bare `KeyError`/`OSError`."""


def _load_symbol_map(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        raise OracleLoadError(f"oracle packages file not found: {path}")
    text = path.read_text()
    data = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    if not isinstance(data, dict):
        raise OracleLoadError(f"{path}: expected a {{task_id: [symbols]}} mapping, got {type(data).__name__}")
    return data


class OracleEngine(AbstractRetrievalEngine):
    name = ENGINE_NAME

    def __init__(self, oracle_packages_path: str | Path, task_id: str) -> None:
        self._path = Path(oracle_packages_path)
        self._task_id = task_id
        self._builder: ConcreteGraphBuilder | None = None
        self._repo_root: str | None = None
        self._contracts: dict[str, BehavioralContract] = {}

    def index(self, repo_path: str) -> None:
        self._builder, _tag_matrix = build_pipeline(repo_path)
        self._repo_root = repo_path
        self._contracts = compute_or_load_contracts(self._builder, repo_path)

    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        if self._builder is None or self._repo_root is None:
            raise RuntimeError("OracleEngine.retrieve called before index()")
        builder = self._builder

        symbol_map = _load_symbol_map(self._path)
        if self._task_id not in symbol_map:
            raise OracleLoadError(f"no oracle package defined for task_id {self._task_id!r} in {self._path}")
        symbols = list(symbol_map[self._task_id])
        if seed_symbol not in symbols:
            symbols = [seed_symbol, *symbols]

        feature_masks = compute_feature_masks(builder)
        nodes: list[NodeEntry] = []
        files_seen: set[str] = set()
        for qname in symbols:
            info = builder.symbol_table.get(qname)
            if info is None:
                continue
            files_seen.add(info.file)
            mask = feature_masks.get(qname, 0)
            role = "seed" if qname == seed_symbol else "transitive"
            nodes.append(
                NodeEntry(
                    id=qname,
                    role=role,
                    distance=0.0 if role == "seed" else 1.0,
                    compression="L0_full",
                    cost=count_tokens(_node_body(builder, qname)),
                    symbol_name=qname.rsplit(".", 1)[-1],
                    symbol_kind=info.kind,
                    language=info.language_id,
                    file=_relative_path(self._repo_root, info.file),
                    line=info.line_range[0],
                    end_line=info.line_range[1],
                    signature=_node_signature(qname, self._contracts, mask),
                    features=NodeFeatures(
                        substance=_axis_labels(mask, SUBSTANCE_BITS),
                        form=_axis_labels(mask, FORM_BITS),
                        output=_axis_labels(mask, OUTPUT_BITS),
                        role=_axis_labels(mask, ROLE_BITS),
                    ),
                    body=_node_body(builder, qname),
                )
            )

        packed_ids = {n.id for n in nodes}
        edges: list[EdgeEntry] = []
        for u, v, data in builder.graph.edges(data=True):
            if u not in packed_ids or v not in packed_ids:
                continue
            relation = data.get("relation", "CALLS")
            if relation not in _TRAVERSABLE_RELATIONS:
                relation = "CALLS"
            edges.append(EdgeEntry(from_node=u, to_node=v, type=relation, weight=1.0, data_flow=False, guard=False))

        seed_info = builder.symbol_table.get(seed_symbol)
        primary_language = seed_info.language_id if seed_info else "python"
        tier = precision_tier_for(primary_language)
        tier_digit = tier.value[-1] if tier is not None else "3"

        return ContextPackage(
            engine=EngineRef(name=ENGINE_NAME, version="1.0", commit="hand-curated"),
            seed=SeedRef(
                symbol=seed_symbol,
                file=_relative_path(self._repo_root, seed_info.file) if seed_info else "",
                line=seed_info.line_range[0] if seed_info else 0,
            ),
            budget=BudgetRef(tokens=budget_tokens, tokenizer="cl100k_base", exact=True),
            language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
            options={"engine": ENGINE_NAME, "task_id": self._task_id},
            manifest=Manifest(
                packed_nodes=len(nodes),
                considered_nodes=len(nodes),
                reachable_nodes=len(nodes),
                compression=[ManifestCompression(level="L0_full", count=len(nodes))] if nodes else [],
                distance_metric=ManifestDistanceMetric(name="oracle_curated", lambda_data_flow=0.0, lambda_guard=0.0, dist_max=0.0),
            ),
            coverage=_coverage_summary(feature_masks, packed_ids, packed_ids),
            nodes=nodes,
            edges=edges,
        )


class PragmaticOracle(AbstractRetrievalEngine):
    """Gap 2 Blocker 2, Option B: a real, deterministic Oracle substitute
    with zero annotation cost. The candidate set is the union of one
    `EvaluationTask`'s own adjudicated `pipeline_symbols`/
    `required_context`/`boundary_symbols` (Gap 8's three real annotated
    sets - never fabricated), rendered at L0 and truncated to budget by
    ascending real `dist_W(seed, ·)` - literally `prism.traversal.
    continuous_dijkstra.compute_topological_distances`, the same
    weighted Continuous Dijkstra distance Prism's own knapsack packer
    computes, reused here rather than approximated by some cheaper
    proxy. `distance=0.0` sentinel (an unreachable candidate - a
    `required_context` *class*, say, which the causal graph doesn't
    traverse to at all) sorts last, not first: real annotated context
    that happens to be structurally distant from the seed is still kept
    if the budget allows, only dropped first if it doesn't.

    This is a **real stand-in, not literal optimality** - a human expert
    curating a package (Option A) might reasonably choose differently.
    It exists to make `fpr_oracle` (Gap 2) measurable in the pilot
    without 18-36 hours of hand-curation (see `reports/pilot/
    methodology.md`).

    Same `name = ENGINE_NAME` ("oracle") as `OracleEngine` - `benchmarks.
    runner._build_engines` passes one or the other, never both, and
    Gap 2's `fpr_oracle` wiring only checks `engine.name == "oracle"`,
    not which class produced it.
    """

    name = ENGINE_NAME

    #: `(repo_cache_digest, seed_symbol) -> {node: dist_w}` - computed
    #: once per (repo, seed) - i.e. once per task, not once per budget -
    #: and shared across every budget of that task the same way
    #: `PrismEngineCache` shares `feature_masks`. Real Continuous
    #: Dijkstra over the whole causal graph is non-trivial work; this
    #: cache is what keeps `PragmaticOracle` from paying it redundantly
    #: 3x per task (once per budget) for nothing.
    _distance_cache: dict[tuple[str, str], dict[str, float]] = {}

    def __init__(self, task: EvaluationTask) -> None:
        self._task = task
        self._builder: ConcreteGraphBuilder | None = None
        self._repo_root: str | None = None
        self._contracts: dict[str, BehavioralContract] = {}
        self._feature_masks: dict[str, int] | None = None
        self._cache_digest: str | None = None

    def index(self, repo_path: str) -> None:
        # Reuses PrismEngineCache's own shared, disk-persisted
        # (repo_path, engine_commit_hash, prism_version, file_hash_set)
        # cache for `(builder, contracts, feature_masks)` - the exact
        # same expensive-to-recompute triple, keyed identically, so a
        # pilot run indexing both Prism and PragmaticOracle against the
        # same repo pays that cost once, not twice. See
        # `prism_engine_cache.py`'s own docstring for why only
        # `feature_masks` (not `builder`) is ever disk-pickled.
        from benchmarks.engines.prism_engine_cache import PrismEngineCache, compute_cache_key

        key = compute_cache_key(repo_path)
        self._cache_digest = key.digest()
        cached = PrismEngineCache._process_graph_cache.get(self._cache_digest)
        if cached is not None:
            self._builder, self._contracts, self._feature_masks = cached
        else:
            self._builder, _tag_matrix = build_pipeline(repo_path)
            self._contracts = compute_or_load_contracts(self._builder, repo_path)
            self._feature_masks = compute_feature_masks(self._builder)
            PrismEngineCache._process_graph_cache[self._cache_digest] = (self._builder, self._contracts, self._feature_masks)
        self._repo_root = repo_path

    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        if self._builder is None or self._repo_root is None or self._feature_masks is None:
            raise RuntimeError("PragmaticOracle.retrieve called before index()")
        builder = self._builder
        adjudicated = self._task.adjudicated

        # Seed first, then pipeline order (causally meaningful), then
        # the two annotated sets in a stable (sorted) order - real
        # symbols only, deduplicated, never arbitrary.
        candidates = list(
            dict.fromkeys(
                [seed_symbol, *adjudicated.pipeline_symbols, *sorted(adjudicated.boundary_symbols), *sorted(adjudicated.required_context)]
            )
        )

        distance_key = (self._cache_digest, seed_symbol)
        distances = PragmaticOracle._distance_cache.get(distance_key)
        if distances is None:
            distances = compute_topological_distances(builder, seed_symbol)
            PragmaticOracle._distance_cache[distance_key] = distances

        def sort_key(qname: str) -> float:
            if qname == seed_symbol:
                return -1.0  # always first, real distance to self is 0 but never present in `distances`
            return distances.get(qname, float("inf"))  # unreachable via the causal graph sorts last, not first

        candidates.sort(key=sort_key)

        nodes: list[NodeEntry] = []
        files_seen: set[str] = set()
        total_cost = 0
        for qname in candidates:
            info = builder.symbol_table.get(qname)
            if info is None:
                continue  # a real symbol that isn't in *this* indexed repo build - skip, never fabricate a node for it
            cost = count_tokens(_node_body(builder, qname))
            if qname != seed_symbol and total_cost + cost > budget_tokens:
                continue  # over budget from here - drop, but keep checking (a cheaper later candidate may still fit)
            total_cost += cost
            files_seen.add(info.file)
            mask = self._feature_masks.get(qname, 0)
            role = "seed" if qname == seed_symbol else "transitive"
            nodes.append(
                NodeEntry(
                    id=qname,
                    role=role,
                    distance=0.0 if role == "seed" else distances.get(qname, 0.0),
                    compression="L0_full",
                    cost=cost,
                    symbol_name=qname.rsplit(".", 1)[-1],
                    symbol_kind=info.kind,
                    language=info.language_id,
                    file=_relative_path(self._repo_root, info.file),
                    line=info.line_range[0],
                    end_line=info.line_range[1],
                    signature=_node_signature(qname, self._contracts, mask),
                    features=NodeFeatures(
                        substance=_axis_labels(mask, SUBSTANCE_BITS),
                        form=_axis_labels(mask, FORM_BITS),
                        output=_axis_labels(mask, OUTPUT_BITS),
                        role=_axis_labels(mask, ROLE_BITS),
                    ),
                    body=_node_body(builder, qname),
                )
            )

        packed_ids = {n.id for n in nodes}
        edges: list[EdgeEntry] = []
        for u, v, data in builder.graph.edges(data=True):
            if u not in packed_ids or v not in packed_ids:
                continue
            relation = data.get("relation", "CALLS")
            if relation not in _TRAVERSABLE_RELATIONS:
                relation = "CALLS"
            edges.append(EdgeEntry(from_node=u, to_node=v, type=relation, weight=1.0, data_flow=False, guard=False))

        seed_info = builder.symbol_table.get(seed_symbol)
        primary_language = seed_info.language_id if seed_info else "python"
        tier = precision_tier_for(primary_language)
        tier_digit = tier.value[-1] if tier is not None else "3"

        return ContextPackage(
            engine=EngineRef(name=ENGINE_NAME, version="1.0", commit="pragmatic-oracle"),
            seed=SeedRef(
                symbol=seed_symbol,
                file=_relative_path(self._repo_root, seed_info.file) if seed_info else "",
                line=seed_info.line_range[0] if seed_info else 0,
            ),
            budget=BudgetRef(tokens=budget_tokens, tokenizer="cl100k_base", exact=True),
            language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
            options={"engine": ENGINE_NAME, "task_id": self._task.task_id, "variant": "pragmatic"},
            manifest=Manifest(
                packed_nodes=len(nodes),
                considered_nodes=len(candidates),
                reachable_nodes=len(candidates),
                compression=[ManifestCompression(level="L0_full", count=len(nodes))] if nodes else [],
                distance_metric=ManifestDistanceMetric(
                    name="pragmatic_oracle_dist_w", lambda_data_flow=0.0, lambda_guard=0.0,
                    dist_max=max(distances.values()) if distances else 0.0,
                ),
            ),
            coverage=_coverage_summary(self._feature_masks, packed_ids, packed_ids),
            nodes=nodes,
            edges=edges,
        )
