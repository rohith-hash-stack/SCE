"""v1.1+ Empirical Benchmarking Harness: double-blind ground-truth
annotation schema (`schema.py`) and YAML task loading with Cohen's kappa
inter-annotator-agreement enforcement (`loader.py`).

**Stated honestly**: the schema and kappa machinery here are real and
fully functional, but this repository ships no genuine double-blind
human-annotated tasks - producing those requires two independent human
annotators per task, which this session cannot fabricate without the
result being dishonest data pretending to be real inter-rater agreement.
`tests/benchmarks/` and any example tasks under this package are clearly
synthetic fixtures for exercising the pipeline, not real ground truth.
"""
