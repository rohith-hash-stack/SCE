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


def test_cached_and_uncached_feature_masks_identical_on_real_django_corpus():
    from benchmarks.corpora.resolver import resolve

    repo_path = str(resolve("django"))
    builder, _ = build_pipeline(repo_path)

    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, repo_path)

    assert uncached == cached
