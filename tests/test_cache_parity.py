"""Test-practice: cache invariance for `benchmarks.engines.
prism_engine_cache.PrismEngineCache` - the harness-level wrapper that
skips the seed/budget-independent `compute_feature_masks` extraction on
a cache hit (Gap 5). `tests/benchmarks/test_prism_engine_cache.py`
already proves the extraction-count invariant (real work happens at
most once); this file proves the invariant that actually matters for
correctness: a cache hit must never change *what* `retrieve()` returns,
only how fast it returns it. Compares `PrismEngineCache` (cache warm,
cache cold-but-disk-persisted, cache cleared-in-process) against the
real, unmodified `PrismEngine` for the same (seed, budget) - if these
ever diverge, the cache is returning something other than the real
engine's answer."""
from __future__ import annotations

from benchmarks.engines.base import selected_symbols
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.engines.prism_engine_cache import PrismEngineCache

_SOURCE = (
    "def calculate_tax(amount):\n    return amount * 0.2\n\n\n"
    "def invoice_generator(amount):\n    total = calculate_tax(amount)\n    return total\n\n\n"
    "def apply_discount(amount, pct):\n    return amount * (1 - pct)\n\n\n"
    "def checkout(amount):\n    discounted = apply_discount(amount, 0.1)\n    return invoice_generator(discounted)\n\n\n"
    "def unrelated_helper(x):\n    return x + 1\n"
)


def _repo(tmp_path, subdir="repo"):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    return repo


def _package_fingerprint(pkg):
    """The parts of a ContextPackage that a real retrieval difference
    would change - excludes run_id/generated_at, which are per-call
    metadata, not retrieval output."""
    return (
        selected_symbols(pkg),
        tuple(n.id for n in pkg.nodes),
        tuple((e.from_node, e.to_node, e.type) for e in pkg.edges),
        pkg.coverage.model_dump() if hasattr(pkg.coverage, "model_dump") else pkg.coverage,
        pkg.budget.model_dump() if hasattr(pkg.budget, "model_dump") else pkg.budget,
    )


def test_cached_retrieve_matches_uncached_engine_for_same_seed_and_budget(tmp_path):
    repo = _repo(tmp_path)

    plain = PrismEngine()
    plain.index(str(repo))
    plain_pkg = plain.retrieve("svc.checkout", 3000)

    PrismEngineCache._process_graph_cache.clear()
    cached = PrismEngineCache(cache_dir=tmp_path / "cache")
    cached.index(str(repo))
    cached_pkg = cached.retrieve("svc.checkout", 3000)

    assert _package_fingerprint(plain_pkg) == _package_fingerprint(cached_pkg)


def test_cache_warm_second_retrieve_matches_first_for_same_seed_and_budget(tmp_path):
    """Two retrieve() calls, same (seed, budget), same warm instance -
    the underlying engine's own real per-call work (Dijkstra + knapsack)
    should be deterministic regardless of the feature-mask cache."""
    repo = _repo(tmp_path)
    PrismEngineCache._process_graph_cache.clear()
    engine = PrismEngineCache(cache_dir=tmp_path / "cache")
    engine.index(str(repo))

    first = engine.retrieve("svc.checkout", 3000)
    second = engine.retrieve("svc.checkout", 3000)
    assert _package_fingerprint(first) == _package_fingerprint(second)


def test_disk_persisted_cache_in_a_fresh_process_matches_the_original_run(tmp_path):
    """Simulates a second process: in-process cache cleared, feature
    masks reloaded from disk - the retrieved package must still match
    the first process's output exactly."""
    repo = _repo(tmp_path)
    cache_dir = tmp_path / "cache"

    PrismEngineCache._process_graph_cache.clear()
    first_process = PrismEngineCache(cache_dir=cache_dir)
    first_process.index(str(repo))
    first_pkg = first_process.retrieve("svc.checkout", 3000)

    PrismEngineCache._process_graph_cache.clear()  # simulate a new process
    second_process = PrismEngineCache(cache_dir=cache_dir)
    second_process.index(str(repo))
    second_pkg = second_process.retrieve("svc.checkout", 3000)

    assert _package_fingerprint(first_pkg) == _package_fingerprint(second_pkg)


def test_cache_parity_holds_across_multiple_seeds_and_budgets(tmp_path):
    """Cache invariance isn't a one-seed accident: check parity across a
    small seed x budget matrix, since a real bug (e.g. a stale
    feature-mask monkey-patch not restored between calls) could plausibly
    only surface on the second or third retrieve()."""
    repo = _repo(tmp_path)

    plain = PrismEngine()
    plain.index(str(repo))

    PrismEngineCache._process_graph_cache.clear()
    cached = PrismEngineCache(cache_dir=tmp_path / "cache")
    cached.index(str(repo))

    for seed in ("svc.checkout", "svc.invoice_generator", "svc.calculate_tax"):
        for budget in (500, 2000, 4000):
            plain_pkg = plain.retrieve(seed, budget)
            cached_pkg = cached.retrieve(seed, budget)
            assert _package_fingerprint(plain_pkg) == _package_fingerprint(cached_pkg), (seed, budget)


def test_retrieve_restores_the_real_compute_feature_masks_cached_after_each_call(tmp_path):
    """PrismEngineCache.retrieve() monkey-patches
    prism.surface.build.compute_feature_masks_cached and
    prism.packer.submodular_knapsack.compute_feature_masks_cached for
    the duration of one call, restoring the original in `finally`. If
    that restore ever leaked, every *subsequent* PrismEngine (uncached)
    call in the same process would silently start returning
    cache-frozen feature masks instead of computing its own - a real
    cross-contamination bug the try/finally is supposed to prevent."""
    import prism.packer.submodular_knapsack as knapsack_module
    import prism.surface.build as build_module

    repo = _repo(tmp_path)

    original_build_fn = build_module.compute_feature_masks_cached
    original_knapsack_fn = knapsack_module.compute_feature_masks_cached

    PrismEngineCache._process_graph_cache.clear()
    cached = PrismEngineCache(cache_dir=tmp_path / "cache")
    cached.index(str(repo))
    cached.retrieve("svc.checkout", 3000)

    assert build_module.compute_feature_masks_cached is original_build_fn
    assert knapsack_module.compute_feature_masks_cached is original_knapsack_fn

    # And a plain, never-touched-by-the-cache PrismEngine instance still
    # produces the same result afterward - proof the monkey-patch really
    # didn't leak into global state.
    plain = PrismEngine()
    plain.index(str(repo))
    plain_pkg = plain.retrieve("svc.checkout", 3000)
    assert "svc.checkout" in selected_symbols(plain_pkg)
