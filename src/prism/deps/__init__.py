"""Offline export summarization of third-party dependencies (Operation
Groundwire): each installed pure-Python package is summarized once per
version - which atomic primitives its entry points can reach, with tier,
depth and a witness path - cached in SQLite, and consumed by host builds
through signature-only headers and boundary nodes.

- `prism.deps.discover`: installed distribution, cache key, imports.
- `prism.deps.headers`: signature-only header packages.
- `prism.deps.summarizer`: one package, in isolation, against its
  dependencies' headers.
- `prism.deps.store`: the SQLite summary cache.
- `prism.deps.index`: summarize-on-demand over the declared closure.
- `prism.deps.host`: host-side attach/detach of dependency headers.
"""
from prism.deps.index import DependencyIndex
from prism.deps.store import SummaryStore

__all__ = ["DependencyIndex", "SummaryStore"]
