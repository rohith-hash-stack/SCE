"""Cold index and warm query latency - real wall-clock timers around an
engine's own `index`/`retrieve` calls, nothing simulated.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from benchmarks.engines.base import AbstractRetrievalEngine


@dataclass
class LatencyResult:
    engine_name: str
    cold_index_seconds: float
    warm_query_seconds: list[float] = field(default_factory=list)

    @property
    def warm_query_mean_seconds(self) -> float:
        if not self.warm_query_seconds:
            return 0.0
        return sum(self.warm_query_seconds) / len(self.warm_query_seconds)


def measure_engine_latency(
    engine: AbstractRetrievalEngine, repo_path: str, seed_symbol: str, budget_tokens: int, warm_runs: int = 3
) -> LatencyResult:
    """Times exactly one `index()` call (the "cold" cost - parsing,
    graph-building, whatever indexing the engine itself does) and
    `warm_runs` subsequent `retrieve()` calls against the now-warm index
    (the recurring per-query cost an agent session actually pays).
    """
    start = time.perf_counter()
    engine.index(repo_path)
    cold_seconds = time.perf_counter() - start

    warm_seconds = []
    for _ in range(warm_runs):
        t0 = time.perf_counter()
        engine.retrieve(seed_symbol, budget_tokens)
        warm_seconds.append(time.perf_counter() - t0)

    return LatencyResult(engine_name=engine.name, cold_index_seconds=cold_seconds, warm_query_seconds=warm_seconds)
