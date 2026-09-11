"""Step 1 of the Blocker 1 performance work: `pack_symbol_context`
(`prism.packer.submodular_knapsack`) now calls `prism.semantics.
extractor.compute_feature_masks_cached(builder, builder.repo_root)`
instead of the uncached `compute_feature_masks(builder)` - these tests
verify the swap actually engages the existing file-content-hash-keyed
disk cache (`prism.cache.sqlite_cache`), via an external counting
wrapper around the real `load_file_cache_entry`/`save_file_cache_entry`
calls, not `compute_feature_masks_cached`'s own self-reporting.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer import submodular_knapsack
from prism.packer.submodular_knapsack import pack_symbol_context

_SOURCE = (
    "def parse_order(raw):\n    return raw\n\n\n"
    "def store_order(data):\n    return data\n\n\n"
    "def process(raw):\n    data = parse_order(raw)\n    return store_order(data)\n"
)


def _patched_cache_counters(monkeypatch):
    """Wraps the real `load_file_cache_entry`/`save_file_cache_entry`
    names as bound inside `prism.semantics.extractor`'s own namespace
    (it imported them at module load time, so patching `prism.cache.
    sqlite_cache`'s own attributes wouldn't be seen by `extractor.py`'s
    already-bound references) - the same external-counter technique
    `tests/benchmarks/test_prism_engine_cache.py` already established
    for `PrismEngineCache.extraction_count`."""
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


def test_pack_symbol_context_hits_feature_mask_cache_on_second_call(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    counts = _patched_cache_counters(monkeypatch)

    result_1 = pack_symbol_context(builder, "x.parse_order", 2000)
    assert result_1.selected
    assert counts["misses"] == 1, "first run: no prior cache entry for x.py, must be a real miss"
    assert counts["saves"] == 1, "first run: a real miss must write a cache entry"
    assert counts["hits"] == 0

    counts["misses"] = counts["saves"] = counts["hits"] = 0
    result_2 = pack_symbol_context(builder, "x.parse_order", 2000)
    assert result_2.selected == result_1.selected
    assert counts["hits"] == 1, "second run: same repo, same file content, must be a real cache hit"
    assert counts["misses"] == 0
    assert counts["saves"] == 0, "a cache hit must not re-save"


def test_pack_symbol_context_feature_mask_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    counts = _patched_cache_counters(monkeypatch)
    pack_symbol_context(builder, "x.parse_order", 2000)  # run 1: cold, writes cache
    counts["misses"] = counts["saves"] = counts["hits"] = 0
    pack_symbol_context(builder, "x.parse_order", 2000)  # run 2: warm, hits cache
    assert counts["hits"] == 1 and counts["misses"] == 0

    # Touch the source file (real content change) and re-index - a
    # cache is only as good as its invalidation; this proves it isn't
    # a "no expiry" cache masquerading as a correct one.
    (repo / "x.py").write_text(_SOURCE + "\n\ndef unrelated_new_function():\n    return 1\n")
    builder_after_touch, _ = build_pipeline(str(repo))

    counts["misses"] = counts["saves"] = counts["hits"] = 0
    result_3 = pack_symbol_context(builder_after_touch, "x.parse_order", 2000)
    assert result_3.selected
    assert counts["misses"] == 1, "run 3: file content changed, must be a real miss, not a stale hit"
    assert counts["saves"] == 1, "run 3: the miss must write a fresh cache entry for the new content"
    assert counts["hits"] == 0


def test_feature_masks_are_identical_cached_vs_uncached(tmp_path):
    """Output-equivalence sanity check for the Step 1 swap itself:
    compute_feature_masks_cached must return the same masks
    compute_feature_masks (the function it replaced at this call site)
    would have, for a real small repo."""
    from prism.semantics.extractor import compute_feature_masks, compute_feature_masks_cached

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, str(repo))
    assert cached == uncached


def test_pack_symbol_context_module_no_longer_imports_uncached_feature_masks():
    """Guards the Step 1 intent directly: the uncached `compute_feature_
    masks` must not be reachable from this module's own namespace
    anymore (a future edit re-adding it by mistake would silently
    reintroduce the redundant, uncached recomputation Step 1 removed)."""
    assert not hasattr(submodular_knapsack, "compute_feature_masks")
    assert hasattr(submodular_knapsack, "compute_feature_masks_cached")
