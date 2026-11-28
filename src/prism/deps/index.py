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

import dataclasses

from prism.deps.discover import distribution_packages, find_distribution, find_stubs
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

    def ensured(self) -> dict[str, PackageSummary]:
        """Every summary this index has served so far - the imported
        packages and their summarized requirements, transitively."""
        return {name: s for name, s in self._memo.items() if s is not None}

    def ensure(self, import_name: str, _active: frozenset[str] = frozenset()) -> PackageSummary | None:
        """The summary for installed package `import_name`, or `None` when
        it is not an installed third-party package."""
        if import_name in self._memo:
            return self._memo[import_name]
        ref = find_distribution(import_name, self.paths)
        if ref is None:
            self._memo[import_name] = None
            return None
        stubs = find_stubs(import_name, self.paths)
        if stubs is not None:
            # Headers depend on the stub package too: part of the cache key.
            ref = dataclasses.replace(ref, record_hash=f"{ref.record_hash}+stubs:{stubs[1]}")
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
            summary = summarize_package(ref, dependencies, stubs[0] if stubs is not None else None)
            self.store.put(summary)
            self.built.append(import_name)
        self._memo[import_name] = summary
        return summary
