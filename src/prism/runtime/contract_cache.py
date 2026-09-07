"""Disk persistence for computed `BehavioralContract`s, so a long-lived
`prism mcp` server (or a repeated `prism query`/`prism index` invocation)
doesn't pay the contract-extraction cost again for a repository whose
source hasn't changed since the last index - the caching behavior asked
for in the "cache persistence" requirement this module backs.

Mirrors `prism.runtime.reconciler`'s own `.prism/runtime_state.json`
persistence (same directory, same load/save-a-plain-JSON-dict shape), so
`.prism/` stays the one place Prism's own generated/cached state lives per
repository - not a second, differently-named cache convention.

**Scope note**: this persists computed *contracts* (the output of
`prism.graph.contracts.ContractExtractor`), not the parsed AST or the
concrete graph itself - re-indexing a repository still re-parses every
source file and re-links the call graph from scratch (that step is already
fast relative to a real LLM round-trip - see `benchmarks/README.md`'s
indexing-time figures - and caching *that* safely would mean invalidating
on a per-file basis with a much larger blast radius than this module's
single content-signature check is meant to cover). What this cache saves
is the second, separate AST walk `ContractExtractor` performs per symbol
on top of an already-built graph.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract, compute_contracts


def contract_cache_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "contracts_cache.json"


def compute_signature(builder: ConcreteGraphBuilder) -> str:
    """A cheap (mtime, size) fingerprint of every source file the builder
    actually indexed - changes whenever a file is added, removed, or
    modified, without needing to hash file contents."""
    files: set[str] = set()
    for symbol in builder.symbol_table:
        files.add(symbol.file)
    parts: list[str] = []
    for path in sorted(files):
        try:
            stat = os.stat(path)
        except OSError:
            parts.append(f"{path}:missing")
            continue
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


def load_contracts(repo_root: str, signature: str) -> dict[str, BehavioralContract] | None:
    """Returns the cached contracts if a cache file exists AND its stored
    signature matches `signature` exactly - `None` on any mismatch, miss,
    or unreadable/corrupt cache file (never raises; a cache is an
    optimization, not a source of truth a caller should have to guard)."""
    path = contract_cache_path(repo_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("signature") != signature:
        return None
    try:
        return {qname: BehavioralContract.from_dict(d) for qname, d in payload.get("contracts", {}).items()}
    except (KeyError, TypeError):
        return None


def save_contracts(repo_root: str, signature: str, contracts: dict[str, BehavioralContract]) -> None:
    path = contract_cache_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"signature": signature, "contracts": {qname: c.to_dict() for qname, c in contracts.items()}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compute_or_load_contracts(builder: ConcreteGraphBuilder, repo_root: str) -> dict[str, BehavioralContract]:
    """The one entry point most callers need: load from
    `.prism/contracts_cache.json` if the repo's file signature hasn't
    changed since it was written, else recompute and persist a fresh
    cache. Also hydrates `builder.graph`'s own `contract` node attribute
    either way, matching `ContractExtractor.extract_all`'s own behavior,
    so a cache hit is indistinguishable from a fresh computation to every
    other consumer of `builder.graph`.
    """
    signature = compute_signature(builder)
    cached = load_contracts(repo_root, signature)
    if cached is not None:
        for qname, contract in cached.items():
            if qname in builder.graph:
                builder.graph.nodes[qname]["contract"] = contract
        return cached

    contracts = compute_contracts(builder)
    save_contracts(repo_root, signature, contracts)
    return contracts
