"""v1.1+ Empirical Benchmarking Harness: the common interface every
retrieval engine under comparison implements.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from prism.surface.models import ContextPackage


class AbstractRetrievalEngine(ABC):
    #: A short, stable name for this engine - used throughout `benchmarks.
    #: reporting`/`benchmarks.metrics` for labeling results; overridden
    #: per concrete engine.
    name: str = "abstract"

    @abstractmethod
    def index(self, repo_path: str) -> None:
        """Indexes the repository."""
        raise NotImplementedError

    @abstractmethod
    def retrieve(self, seed_symbol: str, budget_tokens: int, task_type: str | None = None) -> ContextPackage:
        """Retrieves and populates a canonical ContextPackage object.

        `task_type` (Phase D, Invariant 1): the calling task's own
        `EvaluationTask.task_type` ("chain"/"blast"/"redundancy"/
        "architecture"/"debug"), or `None` if unavailable/inapplicable.
        Only `PrismEngine`/`PrismEngineCache` (the two engines that call
        `prism.surface.build.build_context_package`) act on it - other
        engines accept it purely so `benchmarks.runner`'s own engine
        loop can pass it uniformly to every engine without a per-engine
        special case.
        """
        raise NotImplementedError


def selected_symbols(pkg: ContextPackage) -> set[str]:
    """`S_M` in the spec's own notation - the set of symbol IDs a
    retrieval engine actually packed, read directly off its rendered
    `ContextPackage`. Every diagnostic metric (`benchmarks.metrics.*`)
    and scorer computes `S_M` this same way, so it's defined once here
    rather than as `{n.id for n in pkg.nodes}` re-typed in six places.
    """
    return {node.id for node in pkg.nodes}
