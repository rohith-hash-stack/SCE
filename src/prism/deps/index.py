"""Summarize-on-demand access to installed dependencies (offline export
summarization).

`DependencyIndex.ensure("requests")` returns the cached summary for the
installed `requests`, building it first if needed - and, before it, the
summaries of its declared runtime requirements (`Requires-Dist`, extras
excluded), deepest first, so each package is summarized against its
dependencies' headers and bits only. Optional imports a package merely
tries (`brotli`, `h2`, ...) are not followed.
"""
from __future__ import annotations

from prism.deps.discover import distribution_packages, find_distribution
from prism.deps.store import SummaryStore
from prism.deps.summarizer import PackageSummary, summarize_package


class DependencyIndex:
    def __init__(self, store: SummaryStore | None = None, paths: list[str] | None = None) -> None:
        self.store = store if store is not None else SummaryStore()
        self.paths = paths
        self._memo: dict[str, PackageSummary | None] = {}
        #: Import names summarized (not served from the cache) by this
        #: instance, in build order.
        self.built: list[str] = []

    def ensure(self, import_name: str, _active: frozenset[str] = frozenset()) -> PackageSummary | None:
        """The summary for installed package `import_name`, or `None` when
        it is not an installed third-party package."""
        if import_name in self._memo:
            return self._memo[import_name]
        ref = find_distribution(import_name, self.paths)
        if ref is None:
            self._memo[import_name] = None
            return None
        summary = self.store.get(ref)
        if summary is None:
            active = _active | {import_name}
            dependencies = []
            for dist in ref.requires:
                for package in distribution_packages(dist, self.paths):
                    if package not in active:  # a dependency cycle is cut, not followed
                        dep = self.ensure(package, active)
                        if dep is not None and dep.status == "summarized":
                            dependencies.append(dep)
            summary = summarize_package(ref, dependencies)
            self.store.put(summary)
            self.built.append(import_name)
        self._memo[import_name] = summary
        return summary
