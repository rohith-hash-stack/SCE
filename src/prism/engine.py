"""Phase H (Issue #36): the real, production `PrismEngine` facade.

A thin wrapper around the two real pipeline entry points every actual
caller already uses - `prism.cli.build_pipeline` (indexing) and `prism.
surface.build.build_context_package` (retrieval, the same function
`prism.mcp.server`'s own MCP tools call directly) - rather than a
parallel reimplementation of either. Adds a clean, non-invasive
extension point: a caller can register a callback that observes a
query just before traversal starts, or the resulting `ContextPackage`
just after packing finishes, without any way to mutate what this
engine itself actually does with either.

**Distinct from `benchmarks.engines.prism_engine.PrismEngine`**, an
older, benchmark-harness-only class of the same name - that one exists
purely to give the evaluation harness (`benchmarks/`, `tests/test_*_
parity.py`) a uniform `AbstractRetrievalEngine` interface to compare
Prism against baseline engines under, and is never imported by `prism.
cli` or `prism.mcp.server`. This module is the first `PrismEngine`
actually wired into the real production call path; a real production
hook mechanism belongs here, not grafted onto the benchmark-only class.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.surface.build import build_context_package
from prism.surface.models import ContextPackage


@dataclass(frozen=True)
class QueryContext:
    """An immutable snapshot of one `retrieve()` call's own inputs -
    handed to every registered pre-traversal hook as a fresh `copy.
    deepcopy`, never a live reference, so a hook can inspect (log,
    validate, collect metrics on) a query without any way to affect
    what the engine itself goes on to do with it."""

    seed_id: str
    repo_root: str
    budget_tokens: int
    task_type: str | None = None


PreTraversalHook = Callable[[QueryContext], None]
PostPackingHook = Callable[[ContextPackage], None]


class PrismEngine:
    """Indexes a repository once (`from_repo`/`__init__`), then serves
    `retrieve()` calls against it - the same two-step shape `prism.mcp.
    server`'s own internal `RepoContext` already follows, given a real,
    public, importable name other code (a hook registrant, an embedder)
    can depend on instead of reaching into the MCP server's internals.
    """

    def __init__(
        self,
        builder: ConcreteGraphBuilder,
        repo_root: str,
        contracts: dict[str, BehavioralContract] | None = None,
    ) -> None:
        self._builder = builder
        self._repo_root = repo_root
        self._contracts = contracts if contracts is not None else {}
        self._pre_traversal_hooks: list[PreTraversalHook] = []
        self._post_packing_hooks: list[PostPackingHook] = []

    @classmethod
    def from_repo(cls, repo_root: str) -> "PrismEngine":
        """Convenience constructor: runs the real indexing pipeline
        (`build_pipeline` + `compute_or_load_contracts`, the same two
        calls `benchmarks.engines.prism_engine.PrismEngine.index` makes)
        and wraps the result. Equivalent to calling `build_pipeline`/
        `compute_or_load_contracts` yourself and passing them to
        `__init__` directly - offered for the common case where a
        caller has nothing to reuse from a prior build."""
        builder, _tag_matrix = build_pipeline(repo_root)
        contracts = compute_or_load_contracts(builder, repo_root)
        return cls(builder, repo_root, contracts=contracts)

    @property
    def builder(self) -> ConcreteGraphBuilder:
        return self._builder

    @property
    def repo_root(self) -> str:
        return self._repo_root

    def register_pre_traversal_hook(self, callback: PreTraversalHook) -> None:
        """`callback` runs immediately before `retrieve()` calls into
        `build_context_package`, once per `retrieve()` call, in
        registration order. Receives a `QueryContext` - never this
        engine's own live state."""
        self._pre_traversal_hooks.append(callback)

    def register_post_packing_hook(self, callback: PostPackingHook) -> None:
        """`callback` runs immediately after `retrieve()`'s call into
        `build_context_package` returns, once per `retrieve()` call, in
        registration order. Receives the resulting `ContextPackage` -
        a real deep copy, so mutating it (a hook appending to `pkg.
        nodes`, say - `ContextPackage`'s own pydantic `frozen=True`
        only blocks reassigning a field, not mutating a mutable field's
        contents in place) can never affect the object `retrieve()`
        itself returns to its own caller."""
        self._post_packing_hooks.append(callback)

    def retrieve(
        self,
        seed_id: str,
        budget_tokens: int,
        task_type: str | None = None,
        **kwargs: object,
    ) -> ContextPackage:
        query_ctx = QueryContext(
            seed_id=seed_id, repo_root=self._repo_root, budget_tokens=budget_tokens, task_type=task_type,
        )
        for hook in self._pre_traversal_hooks:
            hook(copy.deepcopy(query_ctx))

        pkg = build_context_package(
            self._builder, seed_id, self._repo_root, budget_tokens,
            contracts=self._contracts, task_type=task_type, **kwargs,
        )

        for hook in self._post_packing_hooks:
            hook(copy.deepcopy(pkg))

        return pkg
