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
    #: Phase H (Issue #35): the full, PEP-257-normalized docstring (see
    #: `prism.graph.contracts.BehavioralContract.docstring`) - `None`
    #: when the symbol has no contract or no real docstring, exactly
    #: like every other optional field here.
    docstring: Optional[str] = None


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
    #: Phase C (schema_version 3): "external" is a real dependency node
    #: resolved outside the target repo (`prism.external.index`) - its
    #: `file`/`line`/`end_line` point at the located stub/source file on
    #: disk, not a location inside the repo every other role assumes, and
    #: its `contract` below is always None - no `BehavioralContract` can
    #: be computed for code this system never AST-indexes as a repo file.
    role: Literal["seed", "callee", "caller", "transitive", "external"]
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


class CausalPathStage(_Frozen):
    order: int
    symbol: str
    distance: float
    role: Literal["entry", "transform", "sink", "return"]


class CausalPath(_Frozen):
    seed: str
    direction: Literal["forward"] = "forward"
    stages: list[CausalPathStage]
    truncated: bool = False


class ContextPackage(_Frozen):
    #: Phase C: bumped 2 -> 3 for the `role="external"` NodeEntry member
    #: above, mirroring Phase F's own unconditional 1 -> 2 bump for
    #: `causal_path` - the version tracks this module's own current
    #: capability, not whether a given package actually uses the new one.
    schema_version: int = 3
    #: Phase F (Blocker B3 / Issue #26): the task_type build_context_package
    #: was called with, if any - None for every pre-Phase-F caller and any
    #: caller without a benchmark-task context (identical default/meaning
    #: to that function's own task_type parameter). Round-tripped through
    #: prism.surface.renderer/parser as a schema_version>=2 root attribute
    #: only - never rendered under schema_version=1, matching that
    #: version's own unchanged, legacy root shape.
    task_type: Optional[str] = None
    engine: EngineRef
    seed: SeedRef
    budget: BudgetRef
    language: LanguageRef
    options: dict[str, str] = Field(default_factory=dict)
    causal_path: Optional[CausalPath] = None
    manifest: Manifest
    coverage: CoverageSummary
    warnings: list[EnvelopeWarning] = Field(default_factory=list)
    nodes: list[NodeEntry]
    edges: list[EdgeEntry]
    run_id: Optional[str] = None
    generated_at: Optional[str] = None
