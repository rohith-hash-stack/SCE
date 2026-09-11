"""Gap 5: `PrismEngineCache` - proves the one real invariant this harness-
level wrapper exists for: `compute_feature_masks(builder)` (seed/budget-
independent, real, expensive) runs at most once per (repo, engine
build, Prism version, target-repo content) combination, while different
seeds/budgets still produce genuinely different `ContextPackage`s (never
the same cached final result). No network, no LLM API calls.
"""
from __future__ import annotations

from benchmarks.engines.base import selected_symbols
from benchmarks.engines.prism_engine_cache import PrismEngineCache, compute_cache_key


def _rag_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def calculate_tax(amount):\n    return amount * 0.2\n\n\n"
        "def invoice_generator(amount):\n    total = calculate_tax(amount)\n    return total\n\n\n"
        "def unrelated_helper(x):\n    return x + 1\n"
    )
    return repo


def test_prism_engine_cache_shares_extraction_across_different_seeds(tmp_path):
    PrismEngineCache._process_graph_cache.clear()
    repo = _rag_repo(tmp_path)
    engine = PrismEngineCache(cache_dir=tmp_path / "cache")
    engine.index(str(repo))
    assert engine.extraction_count == 1  # the one real index()-time extraction

    pkg_a = engine.retrieve("svc.calculate_tax", 2000)
    pkg_b = engine.retrieve("svc.invoice_generator", 2000)
    assert engine.extraction_count == 1  # neither retrieve() call triggered a second real extraction

    # Not caching the final package: different seeds really do produce
    # different packages (a different seed, at minimum).
    assert pkg_a.seed.symbol != pkg_b.seed.symbol


def test_prism_engine_cache_shares_extraction_across_different_budgets(tmp_path):
    PrismEngineCache._process_graph_cache.clear()
    repo = _rag_repo(tmp_path)
    engine = PrismEngineCache(cache_dir=tmp_path / "cache")
    engine.index(str(repo))

    engine.retrieve("svc.calculate_tax", 500)
    engine.retrieve("svc.calculate_tax", 4000)
    assert engine.extraction_count == 1


def test_prism_engine_cache_key_changes_when_file_content_changes(tmp_path):
    repo = _rag_repo(tmp_path)
    key_before = compute_cache_key(str(repo))
    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\ndef added(x):\n    return x\n")
    key_after = compute_cache_key(str(repo))
    assert key_before.file_hash_set != key_after.file_hash_set
    assert key_before.digest() != key_after.digest()


def test_prism_engine_cache_reloads_from_disk_in_a_fresh_instance(tmp_path):
    """Simulates a second process: a fresh `PrismEngineCache` instance,
    with the in-process class-level cache cleared, still avoids a real
    re-extraction because the first instance's run already persisted
    `feature_masks` to disk."""
    PrismEngineCache._process_graph_cache.clear()
    repo = _rag_repo(tmp_path)
    cache_dir = tmp_path / "cache"

    first = PrismEngineCache(cache_dir=cache_dir)
    first.index(str(repo))
    assert first.extraction_count == 1

    PrismEngineCache._process_graph_cache.clear()  # simulate a new process: no in-memory cache

    second = PrismEngineCache(cache_dir=cache_dir)
    second.index(str(repo))
    assert second.extraction_count == 0  # loaded from disk, no real recompute
    pkg = second.retrieve("svc.calculate_tax", 2000)
    assert "svc.calculate_tax" in selected_symbols(pkg)


def test_prism_engine_cache_extraction_counter_is_not_self_reported(tmp_path, monkeypatch):
    """An external counter wrapping the real `compute_feature_masks` -
    not `PrismEngineCache`'s own bookkeeping - confirms the same thing:
    exactly one real call across index() + two retrieve()s with
    different seeds."""
    import benchmarks.engines.prism_engine_cache as cache_module

    PrismEngineCache._process_graph_cache.clear()

    real_compute_feature_masks = cache_module.compute_feature_masks
    calls = {"n": 0}

    def counting_wrapper(builder):
        calls["n"] += 1
        return real_compute_feature_masks(builder)

    monkeypatch.setattr(cache_module, "compute_feature_masks", counting_wrapper)

    repo = _rag_repo(tmp_path)
    engine = PrismEngineCache(cache_dir=tmp_path / "cache")
    engine.index(str(repo))
    engine.retrieve("svc.calculate_tax", 2000)
    engine.retrieve("svc.invoice_generator", 2000)

    assert calls["n"] == 1
