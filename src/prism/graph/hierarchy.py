"""Ties Layers 2-5 of the Semantic Knowledge Engine together into one
`HierarchicalIntentProfile` per repository - the single object
`prism.serializers.markdown.render_markdown` and every CLI/MCP call site
needs, instead of each caller separately importing and sequencing
`symbol_archetype`/`subsystems`/`flows`/`repository_profile` itself.

Mirrors the existing `contracts`/`graph` wiring pattern exactly: computed
once per index (via `prism.runtime.flow_cache.compute_or_load_flow_result`
for the expensive Markov pass, plain function calls for the cheap
Layer 2-5 lookups on top of it), stored on `RepoContext`, passed through
as one more optional keyword argument everywhere `render_markdown` is
called.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from prism.analysis.flow_engine import FlowEngineResult
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.graph.flows import FlowContract, compute_flows
from prism.graph.repository_profile import RepositoryProfile, classify_repository
from prism.graph.subsystems import SubsystemProfile, compute_subsystems, module_of
from prism.graph.symbol_archetype import classify_all
from prism.runtime.flow_cache import compute_or_load_flow_result


@dataclass
class HierarchicalIntentProfile:
    flow_result: FlowEngineResult
    archetypes: dict[str, str] = field(default_factory=dict)
    subsystems: dict[str, SubsystemProfile] = field(default_factory=dict)
    flows: dict[str, FlowContract] = field(default_factory=dict)
    repository: RepositoryProfile = field(default_factory=RepositoryProfile)

    def archetype_of(self, symbol: str) -> str:
        return self.archetypes.get(symbol, "GENERAL_LOGIC_UNIT")

    def subsystem_of(self, symbol: str, file_path: str, repo_root: str) -> SubsystemProfile | None:
        return self.subsystems.get(module_of(file_path, repo_root))

    def subsystem_for_relative_path(self, relative_path: str) -> SubsystemProfile | None:
        """Same lookup as `subsystem_of`, but for a caller (like the
        serializer) that already has a repo-root-relative path - e.g.
        `PackResult` item's own `relative_path` - and would otherwise have
        to re-derive an absolute path just to hand it back to `module_of`.
        """
        return self.subsystems.get(os.path.dirname(relative_path) or ".")

    def flow_covering(self, symbol: str) -> FlowContract | None:
        """The active execution pipeline whose reachable set includes
        `symbol` - preferring the shallowest-depth match (the most
        directly relevant entry root) when more than one pipeline reaches
        it."""
        candidates = [f for f in self.flows.values() if symbol in f.reached_nodes or symbol == f.entry_root]
        if not candidates:
            return None
        return min(candidates, key=lambda f: f.critical_path_depth)


def compute_hierarchical_profile(
    builder: ConcreteGraphBuilder,
    contracts: dict[str, BehavioralContract],
    repo_root: str,
) -> HierarchicalIntentProfile:
    flow_result = compute_or_load_flow_result(builder, repo_root, contracts)
    archetypes = classify_all(builder, flow_result, contracts)
    subsystems = compute_subsystems(builder, contracts, repo_root)
    flows = compute_flows(builder, flow_result, contracts)
    repository = classify_repository(flow_result, subsystems, flows)
    return HierarchicalIntentProfile(
        flow_result=flow_result,
        archetypes=archetypes,
        subsystems=subsystems,
        flows=flows,
        repository=repository,
    )
