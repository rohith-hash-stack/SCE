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
    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        """Retrieves and populates a canonical ContextPackage object."""
        raise NotImplementedError


def selected_symbols(pkg: ContextPackage) -> set[str]:
    """`S_M` in the spec's own notation - the set of symbol IDs a
    retrieval engine actually packed, read directly off its rendered
    `ContextPackage`. Every diagnostic metric (`benchmarks.metrics.*`)
    and scorer computes `S_M` this same way, so it's defined once here
    rather than as `{n.id for n in pkg.nodes}` re-typed in six places.
    """
    return {node.id for node in pkg.nodes}
