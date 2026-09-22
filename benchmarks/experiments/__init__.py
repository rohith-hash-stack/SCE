"""Isolated experimental spikes - never imported by `benchmarks.runner`
or any production code path. Each spike reuses real scoring machinery
(`benchmarks.metrics`, `benchmarks.tsr.client`, `benchmarks.runner`'s
own scoring helpers) so its numbers stay comparable to real resweeps,
but writes its own results under `benchmarks/experiments/results/`
only - never `reports/pilot/` or `reports/pilot-resweep-*/`.
"""
