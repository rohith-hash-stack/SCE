"""Disk persistence for a repository's computed `FlowEngineResult`
(`prism.analysis.flow_engine`), mirroring `prism.runtime.contract_cache`
exactly (same `.prism/` directory, same (mtime, size)-signature
invalidation, same "a cache is an optimization, never a source of truth -
never raises" contract) so the absorbing-Markov-chain analysis - the most
expensive deterministic pass Prism runs (see `flow_engine`'s own docstring
on where its real cost lives) - is paid once per unchanged repository, not
once per query.
"""
from __future__ import annotations

import json
from pathlib import Path

from prism.analysis.flow_engine import EFFECT_BASIS_DIMENSIONS, EffectVector, FlowEngine, FlowEngineResult
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.runtime.contract_cache import compute_signature


def flow_cache_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "flow_cache.json"


def _effect_vector_to_list(vector: EffectVector) -> list[float]:
    return list(vector.values)


def _effect_vector_from_list(values: list[float]) -> EffectVector:
    padded = tuple(values) + (0.0,) * (len(EFFECT_BASIS_DIMENSIONS) - len(values))
    return EffectVector(padded[: len(EFFECT_BASIS_DIMENSIONS)])


def _result_to_dict(result: FlowEngineResult) -> dict:
    return {
        "entry_roots": sorted(result.entry_roots),
        "sink_nodes": sorted(result.sink_nodes),
        "downstream_egress": {k: _effect_vector_to_list(v) for k, v in result.downstream_egress.items()},
        "upstream_ingress": result.upstream_ingress,
        "relative_depth": result.relative_depth,
        "choke_index": result.choke_index,
        "fan_divergence": result.fan_divergence,
        "global_sink_mass": _effect_vector_to_list(result.global_sink_mass),
        "scc_representative": {k: str(v) for k, v in result.scc_representative.items()},
        "sink_effects": {k: _effect_vector_to_list(v) for k, v in result.sink_effects.items()},
    }


def _result_from_dict(d: dict) -> FlowEngineResult:
    return FlowEngineResult(
        entry_roots=frozenset(d.get("entry_roots", [])),
        sink_nodes=frozenset(d.get("sink_nodes", [])),
        downstream_egress={k: _effect_vector_from_list(v) for k, v in d.get("downstream_egress", {}).items()},
        upstream_ingress={k: dict(v) for k, v in d.get("upstream_ingress", {}).items()},
        relative_depth=dict(d.get("relative_depth", {})),
        choke_index=dict(d.get("choke_index", {})),
        fan_divergence=d.get("fan_divergence", 0.0),
        global_sink_mass=_effect_vector_from_list(d.get("global_sink_mass", [])),
        scc_representative=dict(d.get("scc_representative", {})),
        sink_effects={k: _effect_vector_from_list(v) for k, v in d.get("sink_effects", {}).items()},
    )


def load_flow_result(repo_root: str, signature: str) -> FlowEngineResult | None:
    path = flow_cache_path(repo_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("signature") != signature:
        return None
    try:
        return _result_from_dict(payload.get("result", {}))
    except (KeyError, TypeError, ValueError):
        return None


def save_flow_result(repo_root: str, signature: str, result: FlowEngineResult) -> None:
    path = flow_cache_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"signature": signature, "result": _result_to_dict(result)}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compute_or_load_flow_result(
    builder: ConcreteGraphBuilder, repo_root: str, contracts: dict[str, BehavioralContract]
) -> FlowEngineResult:
    """The one entry point most callers need - reuses the same file-set
    signature `prism.runtime.contract_cache.compute_signature` already
    computes (the two caches always invalidate together, since a flow
    analysis is only ever run against a graph the same source change
    would also affect)."""
    signature = compute_signature(builder)
    cached = load_flow_result(repo_root, signature)
    if cached is not None:
        return cached
    result = FlowEngine().analyze(builder, contracts)
    save_flow_result(repo_root, signature, result)
    return result
