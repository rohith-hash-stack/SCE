"""v1.1+ Empirical Benchmarking Harness: the Task Success Rate (TSR)
pipeline - a pluggable LLM client (`client.py`) plus the three binary
scorers (`scorer_chain.py`, `scorer_blast.py`, `scorer_redundancy.py`)
that grade one LLM response against one task's ground truth.

**Stated honestly**: `client.py` is real, working code, but no LLM API
key is configured in this environment and none is called automatically -
running a real evaluation sweep requires the operator's own credentials
and explicit invocation. Every scorer is a pure function over already-
obtained text, fully testable (and tested, `tests/benchmarks/`) without
any live API call.
"""
