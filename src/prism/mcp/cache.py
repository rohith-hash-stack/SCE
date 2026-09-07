"""In-memory graph cache for the MCP server.

Indexing a real repository (parse + link + tag every source file) is not
free - `prism mcp` is a long-lived process an agent calls many tools against
over a single session, almost always against the same one or two
repositories, so re-running the full static pipeline on every tool
invocation would make every call pay a cost only the *first* one needs to.
`GraphCache` keys one `RepoContext` (the concrete graph, tag matrix,
metamodel, and distance engine `prism.slicer` needs) per canonical
repository path, built lazily on first access.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.graph.metamodel import SemanticMetamodel
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.runtime.reconciler import apply_runtime_state, load_runtime_state
from prism.slicer.distance import DistanceConfig, DistanceEngine


class RepoNotFoundError(Exception):
    """`repo_path` doesn't exist, or isn't a directory - raised instead of
    silently indexing zero files (which `prism.cli.discover_files` would
    otherwise do without complaint: `os.walk` on a missing path just
    yields nothing) so a typo'd path surfaces as a clear tool error
    instead of an empty, confusing graph.
    """


@dataclass
class RepoContext:
    """Everything one MCP tool call needs for one repository - built once
    by `GraphCache._index`, reused by every subsequent call against the
    same canonical path.
    """

    repo_root: str
    builder: ConcreteGraphBuilder
    tag_matrix: dict[str, set[str]]
    metamodel: SemanticMetamodel
    distance_engine: DistanceEngine
    runtime_state: dict
    contracts: dict[str, BehavioralContract] = field(default_factory=dict)
    indexed_at: float = field(default_factory=time.time)

    @property
    def symbol_table(self):
        return self.builder.symbol_table

    @property
    def graph(self):
        return self.builder.graph


class GraphCache:
    """One `RepoContext` per canonical (`os.path.abspath`-resolved) repo
    path. `get_or_index` is the normal lazy-initialization entry point;
    `reindex`/`invalidate` are the only ways to force a rebuild (source
    files or `.prism/runtime_state.json` changing on disk are not watched -
    a caller must explicitly ask for a refresh, matching the `reindex_repo`
    MCP tool this class backs).
    """

    def __init__(self) -> None:
        self._entries: dict[str, RepoContext] = {}

    @staticmethod
    def canonical_path(repo_path: str | None) -> str:
        # `PRISM_MCP_DEFAULT_REPO` is `prism mcp --repo PATH`'s own mechanism
        # (see prism.mcp.server.run_server) for setting a server-wide default
        # without mutating this process's actual working directory - the
        # same env-var-based-default pattern `prism.runtime.tracer`'s pytest
        # plugin already uses for its own repo root.
        return os.path.abspath(repo_path or os.environ.get("PRISM_MCP_DEFAULT_REPO") or os.getcwd())

    def get_or_index(self, repo_path: str | None) -> RepoContext:
        key = self.canonical_path(repo_path)
        entry = self._entries.get(key)
        if entry is None:
            entry = self._index(key)
            self._entries[key] = entry
        return entry

    def reindex(self, repo_path: str | None) -> RepoContext:
        key = self.canonical_path(repo_path)
        entry = self._index(key)
        self._entries[key] = entry
        return entry

    def invalidate(self, repo_path: str | None) -> None:
        self._entries.pop(self.canonical_path(repo_path), None)

    def clear(self) -> None:
        self._entries.clear()

    def _index(self, repo_root: str) -> RepoContext:
        if not os.path.isdir(repo_root):
            raise RepoNotFoundError(f"repository path does not exist or is not a directory: {repo_root}")

        builder, tag_matrix = build_pipeline(repo_root)

        # Hydrate CONFIRMED_RUNTIME edge confidence / RUNTIME_DISCOVERED
        # edges / sink tags from any prior `prism trace` run - a fresh
        # build_pipeline() call is purely static and knows nothing about
        # them otherwise (see prism.runtime.reconciler.apply_runtime_state).
        runtime_state = load_runtime_state(repo_root)
        if runtime_state.get("trace_files"):
            apply_runtime_state(builder, tag_matrix, runtime_state)

        metamodel = SemanticMetamodel()
        distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
        contracts = compute_or_load_contracts(builder, repo_root)
        return RepoContext(
            repo_root=repo_root,
            builder=builder,
            tag_matrix=tag_matrix,
            metamodel=metamodel,
            distance_engine=distance_engine,
            runtime_state=runtime_state,
            contracts=contracts,
        )
