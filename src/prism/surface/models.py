"""v1.1+ Agent Surface: the domain model for a complete, self-describing
packed-context package - `ContextPackage` is what `prism.surface.
renderer.render` serializes and `prism.surface.parser.parse_context`
reconstructs losslessly. Every model here is frozen (`model_config =
ConfigDict(frozen=True)`) - a `ContextPackage` is a completed snapshot of
one packing run, never mutated in place once built.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class EngineRef(_Frozen):
    name: str
    version: str
    commit: str


class SeedRef(_Frozen):
    symbol: str
    file: str
    line: int


class BudgetRef(_Frozen):
    tokens: int
    tokenizer: str
    exact: bool


class LanguageRef(_Frozen):
    tier: Literal["1", "2", "3"]
    primary: str
    files: int


class ManifestCompression(_Frozen):
    level: Literal["L0_full", "L1_pruned", "L2_skeleton", "L3_alias"]
    count: int


class ManifestDistanceMetric(_Frozen):
    name: str
    lambda_data_flow: float
    lambda_guard: float
    dist_max: float


class Manifest(_Frozen):
    packed_nodes: int
    considered_nodes: int
    reachable_nodes: int
    compression: list[ManifestCompression]
    distance_metric: ManifestDistanceMetric


class FeatureCoverage(_Frozen):
    id: str
    present: bool
    count: int


class CoverageGap(_Frozen):
    feature: str
    reason: Literal["no_candidate_in_budget", "no_candidate_reachable", "filtered_by_policy", "covered_transitively"]


class CoverageSummary(_Frozen):
    total_features: int
    covered_features: int
    omitted_features: int
    features: list[FeatureCoverage]
    gaps: list[CoverageGap] = Field(default_factory=list)


class EnvelopeWarning(_Frozen):
    code: Literal[
        "BUDGET_OVERFLOW", "TOKENIZER_FALLBACK", "ORPHANED_RUNTIME_EVENTS",
        "GRAPH_INCOMPLETE", "LANGUAGE_TIER_2", "LANGUAGE_TIER_3",
        "RE_EXPORT_UNRESOLVED", "DYNAMIC_ATTRIBUTES_DETECTED",
        "CACHE_STALE", "SCHEMA_VERSION_MISMATCH",
    ]
    severity: Literal["low", "medium", "high"]
    message: str
    details: dict[str, str | int | float] = Field(default_factory=dict)


class NodeSignatureParam(_Frozen):
    name: str
    type: Optional[str] = None
    optional: bool = False


class NodeSignatureReturn(_Frozen):
    type: Optional[str] = None
    kind: str  # Predicate, Command, Query, Factory, Transformer, etc.


class NodeSignature(_Frozen):
    params: list[NodeSignatureParam] = Field(default_factory=list)
    returns: Optional[NodeSignatureReturn] = None


class NodeFeatures(_Frozen):
    substance: str
    form: str
    output: str
    role: str


class NodeContract(_Frozen):
    target_id: str
    call_line: int
    unpacks: Optional[str] = None
    passes_args: Optional[str] = None


class NodeEntry(_Frozen):
    id: str
    role: Literal["seed", "callee", "caller", "transitive"]
    distance: float
    compression: Literal["L0_full", "L1_pruned", "L2_skeleton", "L3_alias"]
    cost: int
    symbol_name: str
    symbol_kind: str
    language: str
    file: str
    line: int
    end_line: int
    signature: NodeSignature
    features: NodeFeatures
    contract: Optional[NodeContract] = None
    body: str


class EdgeEntry(_Frozen):
    from_node: str
    to_node: str
    type: Literal["CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"]
    weight: float
    data_flow: bool
    guard: bool
    back_edge: bool = False


class ContextPackage(_Frozen):
    schema_version: int = 1
    engine: EngineRef
    seed: SeedRef
    budget: BudgetRef
    language: LanguageRef
    options: dict[str, str] = Field(default_factory=dict)
    manifest: Manifest
    coverage: CoverageSummary
    warnings: list[EnvelopeWarning] = Field(default_factory=list)
    nodes: list[NodeEntry]
    edges: list[EdgeEntry]
    run_id: Optional[str] = None
    generated_at: Optional[str] = None
