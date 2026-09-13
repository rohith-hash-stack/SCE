"""Blocker 1 performance work, Decision 1 (scoped exception to the
prism/surface/build.py guardrail): `build_context_package` now calls
`prism.semantics.extractor.compute_feature_masks_cached(builder,
repo_root)` instead of the uncached `compute_feature_masks` - the same
one-line swap pattern Step 1 used in `pack_symbol_context`, at the one
remaining call site that was out of scope until this explicit approval.
Same verification requirement as Step 1: hit/miss counter, file-change
invalidation, output equivalence.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.surface.build import build_context_package

_SOURCE = (
    "def parse_order(raw):\n    return raw\n\n\n"
    "def store_order(data):\n    return data\n\n\n"
    "def process(raw):\n    data = parse_order(raw)\n    return store_order(data)\n"
)


def _patched_cache_counters(monkeypatch):
    """Same external-counter technique as tests/
    test_feature_mask_cache_wiring.py: wraps the real
    load_file_cache_entry/save_file_cache_entry names as bound inside
    prism.semantics.extractor's own namespace."""
    import prism.semantics.extractor as extractor_mod

    counts = {"hits": 0, "misses": 0, "saves": 0}
    real_load = extractor_mod.load_file_cache_entry
    real_save = extractor_mod.save_file_cache_entry

    def counting_load(*args, **kwargs):
        result = real_load(*args, **kwargs)
        if result is None:
            counts["misses"] += 1
        else:
            counts["hits"] += 1
        return result

    def counting_save(*args, **kwargs):
        counts["saves"] += 1
        return real_save(*args, **kwargs)

    monkeypatch.setattr(extractor_mod, "load_file_cache_entry", counting_load)
    monkeypatch.setattr(extractor_mod, "save_file_cache_entry", counting_save)
    return counts


def test_build_context_package_hits_feature_mask_cache_on_second_call(tmp_path, monkeypatch):
    """Step 1c added an in-memory session cache in front of the disk
    cache these counters observe (see `_FEATURE_MASKS_CACHE` in
    extractor.py). `build_context_package` still calls `compute_
    feature_masks_cached` twice per invocation (once indirectly via
    `pack_symbol_context`, once directly at build.py:243), but now
    only the very first of all these calls (across both top-level
    calls below) ever reaches the disk layer at all - every call after
    that, including the second internal call within the very first
    `build_context_package` invocation, is answered by the in-memory
    cache before `load_file_cache_entry`/`save_file_cache_entry` are
    reached."""
    from prism.semantics.extractor import _FEATURE_MASKS_CACHE

    _FEATURE_MASKS_CACHE.clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    contracts = compute_or_load_contracts(builder, str(repo))

    counts = _patched_cache_counters(monkeypatch)

    pkg_1 = build_context_package(builder, "x.parse_order", str(repo), 2000, contracts=contracts)
    assert pkg_1.nodes
    assert counts["misses"] == 1, "first run: no prior cache entry for x.py, must be a real miss (once)"
    assert counts["hits"] == 0, "the direct call now hits the in-memory layer before the disk layer is ever reached"
    assert counts["saves"] == 1
    assert len(_FEATURE_MASKS_CACHE) == 1, "the in-memory layer must be populated after the first call"

    counts["misses"] = counts["saves"] = counts["hits"] = 0
    pkg_2 = build_context_package(builder, "x.parse_order", str(repo), 2000, contracts=contracts)
    assert [n.id for n in pkg_2.nodes] == [n.id for n in pkg_1.nodes]
    assert counts["hits"] == 0, "second run: both internal calls are answered by the in-memory cache, disk layer untouched"
    assert counts["misses"] == 0
    assert counts["saves"] == 0


def test_build_context_package_feature_mask_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    """Step 1c: the in-memory layer intercepts before the disk layer on
    every same-process call once populated (see the sibling hits-test
    above), so 'warm' below sees 0/0/0 at the disk layer, not 2 hits.
    After the file is touched, the in-memory cache key (which folds in
    file_hash_set) differs too, so the first internal call in run 3
    correctly falls through to a real disk miss; the second internal
    call within that same run 3 is then answered by the in-memory
    cache (just populated by the first), not the disk layer."""
    from prism.semantics.extractor import _FEATURE_MASKS_CACHE

    _FEATURE_MASKS_CACHE.clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    contracts = compute_or_load_contracts(builder, str(repo))

    counts = _patched_cache_counters(monkeypatch)
    build_context_package(builder, "x.parse_order", str(repo), 2000, contracts=contracts)  # cold
    counts["misses"] = counts["saves"] = counts["hits"] = 0
    build_context_package(builder, "x.parse_order", str(repo), 2000, contracts=contracts)  # warm
    assert counts["hits"] == 0 and counts["misses"] == 0 and counts["saves"] == 0

    (repo / "x.py").write_text(_SOURCE + "\n\ndef unrelated_new_function():\n    return 1\n")
    builder_after_touch, _ = build_pipeline(str(repo))
    contracts_after_touch = compute_or_load_contracts(builder_after_touch, str(repo))

    counts["misses"] = counts["saves"] = counts["hits"] = 0
    pkg_3 = build_context_package(builder_after_touch, "x.parse_order", str(repo), 2000, contracts=contracts_after_touch)
    assert pkg_3.nodes
    assert counts["misses"] == 1, "file content changed, the first internal call must be a real miss, not a stale hit"
    assert counts["saves"] == 1
    assert counts["hits"] == 0, "the second internal call now hits the in-memory layer, populated by the first call in this same run"


def test_build_context_package_feature_masks_identical_cached_vs_uncached(tmp_path):
    from prism.semantics.extractor import compute_feature_masks, compute_feature_masks_cached

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, str(repo))
    assert cached == uncached


def test_build_context_package_output_equivalent_across_five_seeds(tmp_path):
    """5 seeds: build_context_package's own result, with the file-
    content-hash cache warm (shared) vs. force-cleared before every
    single call, must be identical."""
    import prism.cache.sqlite_cache as sqlite_cache_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(
        "def parse_order(raw):\n    return validate_order(raw)\n\n\n"
        "def validate_order(data):\n    return price_order(data)\n\n\n"
        "def price_order(data):\n    return store_order(data)\n\n\n"
        "def store_order(data):\n    notify_order(data)\n    return data\n\n\n"
        "def notify_order(data):\n    return data\n"
    )
    builder, _ = build_pipeline(str(repo))
    contracts = compute_or_load_contracts(builder, str(repo))
    seeds = ["x.parse_order", "x.validate_order", "x.price_order", "x.store_order", "x.notify_order"]

    warm_results = {}
    for seed in seeds:
        warm_results[seed] = build_context_package(builder, seed, str(repo), 2000, contracts=contracts)

    cold_results = {}
    disk_path = sqlite_cache_mod.sqlite_cache_path(str(repo))
    for seed in seeds:
        if disk_path.exists():
            disk_path.unlink()
        cold_results[seed] = build_context_package(builder, seed, str(repo), 2000, contracts=contracts)

    for seed in seeds:
        warm_view = [(n.id, n.role, n.cost, n.distance, n.symbol_kind) for n in warm_results[seed].nodes]
        cold_view = [(n.id, n.role, n.cost, n.distance, n.symbol_kind) for n in cold_results[seed].nodes]
        assert warm_view == cold_view, seed


def test_build_module_no_longer_imports_uncached_feature_masks():
    import prism.surface.build as build_module

    assert not hasattr(build_module, "compute_feature_masks")
    assert hasattr(build_module, "compute_feature_masks_cached")
