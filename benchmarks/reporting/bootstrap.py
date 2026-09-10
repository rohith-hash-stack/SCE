"""v1.1+ Empirical Benchmarking Harness: percentile bootstrap confidence
intervals - the spec's own 10,000-resample, 95%-CI protocol for every
`TSR(M, B, tau)` estimate. Distribution-free (no normality assumption),
which matters here since a set of binary 0/1 per-run TSR scores is about
as far from normally distributed as a sample gets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

DEFAULT_N_RESAMPLES = 10_000
DEFAULT_CONFIDENCE = 0.95


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class BootstrapCI:
    point_estimate: float
    lower: float
    upper: float
    n_resamples: int
    confidence: float


def bootstrap_ci(
    samples: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = _mean,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    random_seed: int | None = None,
) -> BootstrapCI:
    """Resamples `samples` with replacement `n_resamples` times, computes
    `statistic` on each resample, and takes the
    `[(1-confidence)/2, 1-(1-confidence)/2]` percentiles of that
    distribution as the CI bounds. `statistic` defaults to the plain
    mean (fast-pathed via vectorized numpy for that common case); any
    other callable falls back to one Python-level call per resample.

    `random_seed` is `None` by default (a fresh, non-reproducible
    resample draw each call, the honest default for a real evaluation
    run) - pass an explicit seed for a deterministic/testable CI.
    """
    if not samples:
        return BootstrapCI(point_estimate=0.0, lower=0.0, upper=0.0, n_resamples=n_resamples, confidence=confidence)

    rng = np.random.default_rng(random_seed)
    arr = np.asarray(samples, dtype=float)
    n = len(arr)
    point_estimate = float(statistic(arr.tolist()))

    resample_indices = rng.integers(0, n, size=(n_resamples, n))
    resampled = arr[resample_indices]  # shape (n_resamples, n)

    if statistic is _mean:
        resample_stats = resampled.mean(axis=1)
    else:
        resample_stats = np.fromiter((statistic(row.tolist()) for row in resampled), dtype=float, count=n_resamples)

    alpha = 1.0 - confidence
    lower = float(np.percentile(resample_stats, 100 * (alpha / 2)))
    upper = float(np.percentile(resample_stats, 100 * (1 - alpha / 2)))
    return BootstrapCI(point_estimate=point_estimate, lower=lower, upper=upper, n_resamples=n_resamples, confidence=confidence)
