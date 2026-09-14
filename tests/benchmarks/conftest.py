"""G42: an autouse fixture clearing every module-level session cache
before and after each test in this directory.

These caches (`prism.semantics.extractor._FEATURE_MASKS_CACHE`,
`prism.traversal.continuous_dijkstra._GRAPH_CACHE`/`_DISTANCE_CACHE`,
`prism.traversal.causal_weights._DATA_FLOW_EDGES_CACHE`/
`_GUARD_INDICATOR_EDGES_CACHE`/`_CAUSAL_EDGES_CACHE`) are in-process
session caches with no per-test teardown of their own - real, deliberate
performance layers for a long-lived server process, but a real
correctness hazard for a test suite, where one test's cache write can
leak into an unrelated later test's own assertions if both happen to
touch the same repo at the same engine commit. Confirmed directly: this
is exactly what caused G42 (`tests/test_feature_masks_cache_parity.py`'s
real-corpus parity test intermittently failing in a full-suite run while
always passing in isolation).

Scoped to `tests/benchmarks/` only, so it doesn't change test semantics
- or test run time, most of these benchmark tests exist specifically to
measure warm-cache behavior - anywhere else in the suite. Does not by
itself cover `tests/test_feature_masks_cache_parity.py` (a different
directory) - that test's own real-corpus case clears
`_FEATURE_MASKS_CACHE` directly at its own call site instead; this
fixture is the broader, forward-looking guard for future benchmark
tests added under this directory.
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_module_caches():
    from prism.semantics.extractor import _FEATURE_MASKS_CACHE
    from prism.traversal.continuous_dijkstra import _DISTANCE_CACHE, _GRAPH_CACHE
    from prism.traversal.causal_weights import (
        _CAUSAL_EDGES_CACHE,
        _DATA_FLOW_EDGES_CACHE,
        _GUARD_INDICATOR_EDGES_CACHE,
    )

    caches = (
        _FEATURE_MASKS_CACHE,
        _GRAPH_CACHE,
        _DISTANCE_CACHE,
        _DATA_FLOW_EDGES_CACHE,
        _GUARD_INDICATOR_EDGES_CACHE,
        _CAUSAL_EDGES_CACHE,
    )
    for cache in caches:
        cache.clear()

    yield

    for cache in caches:
        cache.clear()
