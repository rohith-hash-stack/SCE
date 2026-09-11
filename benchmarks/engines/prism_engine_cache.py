"""v1.1+ Empirical Benchmarking Harness: `PrismEngineCache` - a harness-
level wrapper around the real `PrismEngine` that caches the one thing
`prism_engine.py`'s own docstring admits is uncached and, critically,
seed/budget-*independent*: `compute_feature_masks(builder)`'s full
result - the four-axis feature coordinates over the entire indexed
graph. Never modifies `prism_engine.py`, `prism.surface.build`, or
`prism.packer.submodular_knapsack` - every call into those stays the
real, unmodified engine code; this module only decides, from the
outside, whether that one already-expensive sub-call needs to run again.

**Why feature_masks is exactly the right thing to cache, and the final
package is exactly the wrong thing**: `compute_feature_masks(builder)`
takes only `builder` - not `seed_symbol`, not `budget_tokens` - so its
result is identical for every `retrieve()` call against the same
indexed repo. Caching it lets `retrieve()` still run its real,
per-call work (Continuous Dijkstra distances from *this* seed, the
submodular knapsack pack at *this* budget) - a different `(seed,
budget)` pair genuinely produces a different `ContextPackage`, exactly
as required. Caching the rendered package itself, by contrast, would
silently return the *same* package for every seed/budget - the one
mistake this module is built specifically to avoid.

**Why only feature_masks is pickled to disk, not the full builder**:
`ConcreteGraphBuilder` holds a `threading.Lock` (and, transitively,
tree-sitter parse trees) - genuinely not picklable. `index()`'s other
half, `build_pipeline`/`compute_or_load_contracts`, already has its own
real, tested, cross-process disk caches inside `prism` itself (file-
content-hash- and repo-signature-keyed respectively - see
`prism.cache.sqlite_cache`/`prism.runtime.contract_cache`); this module
adds an in-process cache of the live `(builder, contracts)` pair on top
of that (so a multi-task sweep against the same repo pays that cost
once per process, not once per task), plus a genuinely new cache -
in-process *and* disk-persisted - for `feature_masks`, a plain
`dict[str, int]` with no such picklability problem.
"""
from __future__ import annotations

import hashlib
import pickle
import subprocess
from dataclasses import dataclass
from pathlib import Path

from prism import __version__ as PRISM_VERSION
from prism.packer.submodular_knapsack import DEFAULT_MAX_HOPS
from prism.semantics.extractor import compute_feature_masks
from prism.surface.models import ContextPackage

from benchmarks.engines.base import AbstractRetrievalEngine
from benchmarks.engines.prism_engine import PrismEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "prism_graph_cache"


def _run_git_head(cwd: str | Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _engine_commit_hash() -> str:
    """Which version of the *Prism engine's own source* produced a
    cached `feature_masks` entry - the SCE repo's own HEAD, not the
    target repo being indexed. A real engine-source change (a tagger/
    extractor fix) must invalidate the cache same as a target-repo file
    change would."""
    return _run_git_head(PROJECT_ROOT) or "unknown"


def _repo_file_signature(repo_path: str) -> str:
    """A real content-derived signature of every file in `repo_path` -
    the checked-out commit SHA if it's a git working tree (cheap: one
    `git rev-parse HEAD`, and cryptographically commits to the exact
    content of every tracked file - the same pinning technique
    `benchmarks.corpora.resolver` already uses for corpus reproducibility),
    else a real sha256 over every `*.py` file's own content (a slower
    but still real fallback for a non-git fixture directory, e.g. the
    smoke test's own temp repo)."""
    git_head = _run_git_head(repo_path) if (Path(repo_path) / ".git").is_dir() else None
    if git_head:
        return f"git:{git_head}"
    hasher = hashlib.sha256()
    for path in sorted(Path(repo_path).rglob("*.py")):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        hasher.update(str(path.relative_to(repo_path)).encode())
        hasher.update(content)
    return f"content:{hasher.hexdigest()}"


@dataclass(frozen=True)
class CacheKey:
    """`(repo_path, engine_commit_hash, prism_version, file_hash_set)` -
    Gap 5's own literal key shape. Any one component changing - a
    different repo, a different Prism engine build, a different pinned
    commit / edited file - produces a different key, so a stale cache
    entry is never silently reused."""

    repo_path: str
    engine_commit_hash: str
    prism_version: str
    file_hash_set: str

    def digest(self) -> str:
        raw = "|".join((self.repo_path, self.engine_commit_hash, self.prism_version, self.file_hash_set))
        return hashlib.sha256(raw.encode()).hexdigest()


def compute_cache_key(repo_path: str) -> CacheKey:
    return CacheKey(
        repo_path=str(Path(repo_path).resolve()),
        engine_commit_hash=_engine_commit_hash(),
        prism_version=PRISM_VERSION,
        file_hash_set=_repo_file_signature(repo_path),
    )


class PrismEngineCache(AbstractRetrievalEngine):
    """Drop-in replacement for `PrismEngine` in the harness's own engine
    list - same `name` (`"prism_v11"`, so reports/diagnostics group its
    records with plain `PrismEngine` runs), same `index()`/`retrieve()`
    contract, real `PrismEngine` underneath every call. Only the
    feature-extraction sub-step is ever skipped, and only when this
    exact `(repo, engine build, Prism version, target-repo content)`
    combination was already extracted, this process or a previous one.
    """

    name = "prism_v11"

    #: In-process cache, shared across every instance in this Python
    #: process - `digest -> (builder, contracts, feature_masks)`. Not
    #: disk-persisted (`ConcreteGraphBuilder` isn't picklable - see this
    #: module's own docstring); a multi-task sweep within one process
    #: still gets full reuse, which is the common case this exists for.
    _process_graph_cache: dict[str, tuple] = {}

    def __init__(self, max_hops: float = DEFAULT_MAX_HOPS, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> None:
        self._inner = PrismEngine(max_hops=max_hops)
        self._cache_dir = Path(cache_dir)
        self._feature_masks: dict[str, int] | None = None
        #: Real, observable count of how many times this instance
        #: actually ran `compute_feature_masks` for real (a cache hit -
        #: in-process or from disk - never increments this). The Gap 5
        #: acceptance test reads this directly, not an internal mock.
        self.extraction_count = 0

    def _disk_path(self, key: CacheKey) -> Path:
        return self._cache_dir / f"{key.digest()}.featuremasks.pkl"

    def _load_feature_masks_from_disk(self, key: CacheKey) -> dict[str, int] | None:
        path = self._disk_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                payload = pickle.load(f)
        except (pickle.PickleError, OSError, EOFError, AttributeError, ValueError):
            return None  # a corrupt/foreign cache file degrades to a real recompute, never a crash
        if payload.get("key") != key:
            return None
        return payload.get("feature_masks")

    def _save_feature_masks_to_disk(self, key: CacheKey, feature_masks: dict[str, int]) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._disk_path(key), "wb") as f:
                pickle.dump({"key": key, "feature_masks": feature_masks}, f)
        except OSError:
            pass  # best-effort - an unwritable cache dir degrades to in-process-only reuse, never a hard failure

    def index(self, repo_path: str) -> None:
        key = compute_cache_key(repo_path)
        digest = key.digest()

        cached = PrismEngineCache._process_graph_cache.get(digest)
        if cached is not None:
            builder, contracts, feature_masks = cached
            self._inner._builder = builder
            self._inner._contracts = contracts
            self._inner._repo_root = repo_path
            self._feature_masks = feature_masks
            return

        self._inner.index(repo_path)  # the real build_pipeline + compute_or_load_contracts - never skipped

        feature_masks = self._load_feature_masks_from_disk(key)
        if feature_masks is None:
            feature_masks = compute_feature_masks(self._inner._builder)  # the one real, expensive extraction
            self.extraction_count += 1
            self._save_feature_masks_to_disk(key, feature_masks)

        self._feature_masks = feature_masks
        PrismEngineCache._process_graph_cache[digest] = (self._inner._builder, self._inner._contracts, feature_masks)

    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        if self._feature_masks is None:
            raise RuntimeError("PrismEngineCache.retrieve called before index()")

        # Both `build_context_package` (prism/surface/build.py:243,
        # Blocker 1 Decision 1) and `pack_symbol_context` (prism/packer/
        # submodular_knapsack.py, Blocker 1 Step 1) now call
        # `compute_feature_masks_cached(builder, repo_root)` - neither
        # takes a pre-computed value as a parameter, so the only
        # harness-level way to skip that redundant recomputation without
        # editing either module is to substitute the *module-level* name
        # each already bound at its own import time, for the duration of
        # this one call, restored in `finally` no matter what happens
        # inside `retrieve()` - the same monkey-patch-and-restore
        # technique `benchmarks.runner`'s own ablation sweep already
        # uses for `causal_weights.LAMBDA_DATA_FLOW`/`LAMBDA_GUARD`.
        import prism.packer.submodular_knapsack as knapsack_module
        import prism.surface.build as build_module

        cached_masks = self._feature_masks
        original_build_fn = build_module.compute_feature_masks_cached
        original_knapsack_fn = knapsack_module.compute_feature_masks_cached
        build_module.compute_feature_masks_cached = lambda builder, repo_root: cached_masks
        knapsack_module.compute_feature_masks_cached = lambda builder, repo_root: cached_masks
        try:
            return self._inner.retrieve(seed_symbol, budget_tokens)
        finally:
            build_module.compute_feature_masks_cached = original_build_fn
            knapsack_module.compute_feature_masks_cached = original_knapsack_fn
