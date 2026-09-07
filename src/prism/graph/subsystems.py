"""Layer 3 of the Semantic Knowledge Engine: per-module/directory
architectural profiles, computed bottom-up over `calls_graph`
(`prism.graph.concrete_builder`) and each symbol's `BehavioralContract` -
the mitigation strategy the spec calls out by name ("compute intent
bottom-up (per module/directory partition first)") so a monorepo/polyglot
repository's global archetype (Layer 5) is a federation of these per-module
numbers rather than one flat metric blind to subsystem boundaries.

A "module" here is a source file's containing directory, relative to the
repo root - the same coarse-grained partition `os.path.dirname` gives for
free, with no language-specific package/namespace parsing required (every
supported language already puts related files in the same directory by
convention, and directory boundaries are exactly what "cross-module call"
should mean for a polyglot repo where a real package system differs
per-language anyway).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract

STATELESS = "STATELESS"
ENCAPSULATED_MUTATION = "ENCAPSULATED_MUTATION"
LEAKY_GLOBAL_MUTATION = "LEAKY_GLOBAL_MUTATION"


def module_of(file_path: str, repo_root: str) -> str:
    try:
        rel = os.path.relpath(file_path, repo_root)
    except ValueError:
        rel = file_path
    directory = os.path.dirname(rel)
    return directory or "."


@dataclass
class SubsystemProfile:
    module: str
    symbol_count: int = 0
    cohesion_score: float = 0.0
    coupling_instability: float = 0.0
    purity_ratio: float = 0.0
    state_locality: str = STATELESS
    dominant_sinks: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "symbol_count": self.symbol_count,
            "cohesion_score": self.cohesion_score,
            "coupling_instability": self.coupling_instability,
            "purity_ratio": self.purity_ratio,
            "state_locality": self.state_locality,
            "dominant_sinks": list(self.dominant_sinks),
        }


def _state_locality(mutations_seen: list[str]) -> str:
    if not mutations_seen:
        return STATELESS
    # A `self.<attr>`/`this.<attr>` mutation string always has exactly two
    # dot-joined segments (see `ContractExtractor._state_mutations`); a
    # bare, dot-free entry is one of `_MUTATING_GLOBAL_ROOTS`
    # (`localStorage`, `globalThis`, ...) or a module-level global - either
    # way, state escaping the owning instance.
    if any("." not in m for m in mutations_seen):
        return LEAKY_GLOBAL_MUTATION
    return ENCAPSULATED_MUTATION


def compute_subsystems(
    builder: ConcreteGraphBuilder,
    contracts: dict[str, BehavioralContract],
    repo_root: str,
    downstream_egress: dict | None = None,
) -> dict[str, SubsystemProfile]:
    """Partitions every internal (function/method) symbol into its module,
    then computes each module's cohesion/instability/purity/state-locality
    from `calls_graph` edges and `contracts` - one linear pass over the
    graph's edges plus one over its nodes, no new traversal."""
    g = builder.calls_graph
    module_symbols: dict[str, list[str]] = {}
    symbol_module: dict[str, str] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        if symbol.qualified_name not in g:
            continue
        mod = module_of(symbol.file, repo_root)
        symbol_module[symbol.qualified_name] = mod
        module_symbols.setdefault(mod, []).append(symbol.qualified_name)

    internal_edges: dict[str, int] = {m: 0 for m in module_symbols}
    cross_out: dict[str, int] = {m: 0 for m in module_symbols}  # efferent (Ce)
    cross_in: dict[str, int] = {m: 0 for m in module_symbols}  # afferent (Ca)
    sink_hits: dict[str, dict[str, int]] = {m: {} for m in module_symbols}

    for u, v, data in g.edges(data=True):
        if data.get("relation") not in ("CALLS", "INSTANTIATES"):
            continue
        u_mod = symbol_module.get(u)
        if u_mod is None:
            continue
        v_mod = symbol_module.get(v)
        if v_mod is None:
            # v is an external sink (or an internal symbol this pass
            # doesn't otherwise see) - counts as this module's own
            # boundary crossing either way.
            cross_out[u_mod] += 1
            sink_hits[u_mod][v] = sink_hits[u_mod].get(v, 0) + 1
            continue
        if v_mod == u_mod:
            internal_edges[u_mod] += 1
        else:
            cross_out[u_mod] += 1
            cross_in[v_mod] = cross_in.get(v_mod, 0) + 1

    profiles: dict[str, SubsystemProfile] = {}
    for mod, symbols in module_symbols.items():
        e_internal = internal_edges.get(mod, 0)
        e_cross_out = cross_out.get(mod, 0)
        cohesion = e_internal / (e_internal + e_cross_out) if (e_internal + e_cross_out) > 0 else 1.0

        ce = cross_out.get(mod, 0)
        ca = cross_in.get(mod, 0)
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0

        pure_count = sum(1 for s in symbols if contracts.get(s) is not None and contracts[s].purity == "pure")
        purity_ratio = pure_count / len(symbols) if symbols else 0.0

        mutations_seen: list[str] = []
        for s in symbols:
            c = contracts.get(s)
            if c is not None:
                mutations_seen.extend(c.state_mutations)
        locality = _state_locality(mutations_seen)

        top_sinks = sorted(sink_hits.get(mod, {}).items(), key=lambda kv: -kv[1])[:3]
        dominant = tuple(name for name, _count in top_sinks)

        profiles[mod] = SubsystemProfile(
            module=mod,
            symbol_count=len(symbols),
            cohesion_score=round(cohesion, 4),
            coupling_instability=round(instability, 4),
            purity_ratio=round(purity_ratio, 4),
            state_locality=locality,
            dominant_sinks=dominant,
        )
    return profiles
