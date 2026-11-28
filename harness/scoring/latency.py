"""Latency instrumentation. Monotonic clock only (`perf_counter_ns`); wall
clock (`time.time`) drifts with NTP.

Four layers, never one "latency" number:
    L_index     Phase 1, one-time per repo
    L_retrieve  Phase 2, per query (per turn for Arm 4)
    L_generate  Phase 3, per query
    L_e2e       per-query total
plus per-arm sub-components (e.g. `L_bm25`, `L_rerank`, `L_turn1_manifest`).

The first sample of each key is the cold one; warm percentiles exclude it
(when there is more than one sample) and cold numbers go to the appendix.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

import numpy as np

LAYERS = ("L_index", "L_retrieve", "L_generate", "L_e2e")
ARM_SUBCOMPONENTS = {
    "arm1": ("L_embed_query", "L_bm25", "L_dense", "L_rrf", "L_rerank"),
    "arm2": ("L_ast_parse", "L_priority_sort", "L_binary_search_tokenize"),
    "arm3": ("L_lsp_hover", "L_lsp_definition", "L_lsp_documentSymbol", "L_didOpen"),
    "arm4": ("L_generate_turn", "L_tool_exec", "L_compact"),
    "arm5": ("L_turn1_manifest", "L_turn2_hydrate", "L_turn2b_external"),
}


@contextmanager
def timed(metrics: dict, key: str) -> Iterator[None]:
    """Append the block's duration in milliseconds to `metrics[key]`, even if
    the block raises."""
    t0 = time.perf_counter_ns()
    try:
        yield
    finally:
        dt_ms = (time.perf_counter_ns() - t0) / 1e6
        metrics.setdefault(key, []).append(dt_ms)


def percentiles(samples_ms: list[float], warm: bool = True) -> dict[str, float]:
    """p50/p95/p99 (+n, mean). `warm=True` drops the first (cold) sample
    when more than one exists."""
    xs = list(samples_ms[1:] if warm and len(samples_ms) > 1 else samples_ms)
    if not xs:
        nan = float("nan")
        return {"n": 0, "mean": nan, "p50": nan, "p95": nan, "p99": nan}
    arr = np.asarray(xs, dtype=float)
    return {"n": len(xs), "mean": float(arr.mean()), "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)), "p99": float(np.percentile(arr, 99))}


def latency_profile(metrics: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """Warm percentiles per key, plus each key's cold (first) sample."""
    out = {}
    for key, samples in metrics.items():
        prof = percentiles(samples, warm=True)
        prof["cold_first_ms"] = float(samples[0]) if samples else float("nan")
        out[key] = prof
    return out


def merge(*metric_dicts: dict[str, list[float]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for d in metric_dicts:
        for k, v in d.items():
            out.setdefault(k, []).extend(v)
    return out
