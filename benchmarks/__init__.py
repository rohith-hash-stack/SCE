"""SCE evaluation/benchmarking harness.

Quantifies the Semantic Context Engine's context-packing quality against a
naive whole-file-dump baseline, across three dimensions:

  - token reduction (exact tokenizer counts, SCE vs. raw file dump)
  - structural/semantic coverage (how much of the ground-truth k-hop call
    subgraph and its architectural invariant tags survive into the packed
    context)
  - syntactic validity (every rendered Python code block must still parse)

See `benchmarks/run_benchmark.py` for the CLI entry point.
"""
