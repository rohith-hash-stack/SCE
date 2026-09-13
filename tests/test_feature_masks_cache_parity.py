"""Bookmark 2 (G27 remediation): `compute_feature_masks` and
`compute_feature_masks_cached` must produce byte-for-byte identical
results - the cached path is meant to be a pure performance
optimization over the same four-axis computation, never a second,
independently-drifting reimplementation of any part of it.

Regression coverage for the specific divergence the audit found: the
two paths' own one-hop transitive-wrapper-propagation threshold had
silently drifted apart (`substance.py`'s `_TRANSITIVE_WRAPPER_MAX_
STATEMENTS` was raised from 2 to 3 for v1.1+; `extractor.py`'s cached
path kept a separately hardcoded `<= 2`, missing that update). Fixed by
having the cached path import and reuse the same constant instead of
reimplementing it.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline
from prism.semantics.extractor import compute_feature_masks, compute_feature_masks_cached

_SOURCE = (
    "import requests\n"
    "\n"
    "\n"
    "def fetch_data(url):\n"
    "    return requests.get(url)\n"
    "\n"
    "\n"
    "def send(payload):\n"
    "    # A 3-statement wrapper - qualifies under the corrected\n"
    "    # threshold (<=3) but would NOT have under the stale\n"
    "    # hardcoded <=2 the cached path used before this fix.\n"
    "    prepared = payload\n"
    "    result = fetch_data(prepared)\n"
    "    return result\n"
    "\n"
    "\n"
    "def process(data):\n"
    "    return data\n"
)


def test_cached_and_uncached_feature_masks_are_identical(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, str(repo))

    assert uncached == cached


def test_three_statement_wrapper_transitively_propagates_in_both_paths(tmp_path):
    """The exact shape the divergence hid: a 3-statement wrapper whose
    own body is a thin pass-through to a sink-touching callee. Must
    propagate the same way through both entry points."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    from prism.semantics.bitmask import FeatureBit

    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, str(repo))

    for masks, label in ((uncached, "uncached"), (cached, "cached")):
        assert masks["svc.send"] & int(FeatureBit.SINK_NETWORK_IO), (
            f"{label}: expected the 3-statement wrapper svc.send to inherit "
            "svc.fetch_data's SINK_NETWORK_IO bit transitively"
        )


@pytest.mark.xfail(
    reason=(
        "Blocked by a separate, deeper bug (tracked as G38, not this "
        "module): prism.runtime.index_cache's warm-path reconstruction "
        "loses builder.def_node() linkage for at least some real symbols "
        "that resolve correctly on a cold build - confirmed directly "
        "(cold build: def_node resolves; the next, warm-cache build "
        "against the same unchanged repo: def_node is None for the same "
        "symbol). compute_feature_masks (uncached) calls def_node() "
        "fresh and is exposed to this directly; compute_feature_masks_"
        "cached is incidentally shielded by its own separate per-file "
        "disk cache (sqlite_cache.py), which is why the two paths still "
        "diverge on a warm real corpus even after the G27 threshold/"
        "purity-fallback fixes (verified correct on synthetic fixtures "
        "above, which never hit index_cache). Remove this xfail once "
        "G38 is fixed - it should then pass unmodified."
    ),
    # Not strict: whether this builder ends up cold or warm depends on
    # ambient .prism/cache/ state left by whatever ran against this
    # corpus earlier in the process/CI run - the same non-determinism
    # that is G38 itself. A cold-built pass here is a real, if lucky,
    # outcome, not evidence G38 is fixed; strict=True would make CI
    # flaky-red on exactly the runs that happen to start warm.
    strict=False,
)
def test_cached_and_uncached_feature_masks_identical_on_real_django_corpus():
    from benchmarks.corpora.resolver import resolve

    repo_path = str(resolve("django"))
    builder, _ = build_pipeline(repo_path)

    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, repo_path)

    assert uncached == cached
