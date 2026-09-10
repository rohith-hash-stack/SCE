"""Causal Pipeline Integrity (CPI) - how completely a retrieval engine's
packed set `S_M` covers the ground-truth causal pipeline `G*_pipeline`
(a Type 1 "chain" task's ordered `pipeline_symbols`).
"""
from __future__ import annotations


def cpi_strict(selected: set[str], pipeline: list[str] | set[str]) -> float:
    """`1.0` if the *entire* pipeline is a subset of `selected`, else
    `0.0` - a pipeline missing even one stage is not "mostly there", it's
    broken. `1.0` (vacuously true, nothing required) if the pipeline
    itself is empty."""
    pipeline_set = set(pipeline)
    if not pipeline_set:
        return 1.0
    return 1.0 if pipeline_set <= selected else 0.0


def cpi_fractional(selected: set[str], pipeline: list[str] | set[str]) -> float:
    """`|S_M ∩ pipeline| / |pipeline|` - the softer, partial-credit
    companion to `cpi_strict`. `1.0` for an empty pipeline (same
    vacuous-truth convention)."""
    pipeline_set = set(pipeline)
    if not pipeline_set:
        return 1.0
    return len(selected & pipeline_set) / len(pipeline_set)
