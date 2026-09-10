"""v1.1+ Empirical Benchmarking Harness: the Oracle engine - loads a
hand-curated "optimal package" symbol list for one task and resolves
each symbol's real body/signature/features from the actually-indexed
repository, packaged through the exact same `ContextPackage` shape every
other engine emits (so TSR differences measure retrieval quality, not
formatting).

**Stated honestly**: this loader is real and fully functional, but this
repository ships no genuine hand-curated oracle packages for
django/gin/trpc - producing those requires a domain expert manually
selecting the truly-optimal symbol set per task, which this session
cannot fabricate convincingly (a self-authored "hand-curated" answer
would just be this engine grading itself). `oracle_packages_path` points
at whatever curated file an operator supplies; `tests/benchmarks/` uses a
small synthetic one to exercise this module for real.

File format: a YAML or JSON mapping `{task_id: [ordered qualified
symbol names]}` - the seed itself doesn't need to be listed (it's always
included).
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
