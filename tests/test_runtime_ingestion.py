"""Tests for the Hybrid Dynamic Runtime Trace Ingestion Engine:
`prism.runtime.trace_validator` (Section 2.1.2 staleness guard),
`prism.runtime.trace_ingester` (Section 2.1.3 environment aggregation),
`prism.graph.concrete_builder.apply_runtime_overlay` (Section 2.1.1/2.1.5
non-destructive overlay), and `prism.analysis.hybrid_engine` (Section
2.1.4 logarithmic frequency dampening).
"""
from __future__ import annotations

import hashlib
import json
import warnings

import pytest

from prism.analysis.hybrid_engine import DEFAULT_RUNTIME_BIAS, hybrid_edge_weight, weighted_execution_count
from prism.cli import build_pipeline
from prism.runtime.trace_ingester import AggregatedTrace, ingest_trace_file, merge_env_traces
from prism.runtime.trace_validator import PrismWarning, StaleTraceError, TraceManifest, validate_trace


def _write_json(path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------- #
# Trace validation & invalidation
# --------------------------------------------------------------------- #
def test_matching_fingerprints_and_commit_are_accepted(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "sample.py"
    source.write_text("def f():\n    return 1\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    manifest = TraceManifest(git_commit_sha=None, environment="staging", timestamp=None, file_fingerprints={"sample.py": digest})
    result = validate_trace(manifest, str(repo), current_commit_sha=None)
    assert result.accepted
    assert result.reason is None


def test_mismatched_file_fingerprint_is_rejected_with_prism_warning(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "sample.py"
    source.write_text("def f():\n    return 1\n")

    manifest = TraceManifest(
        git_commit_sha=None, environment="staging", timestamp=None,
        file_fingerprints={"sample.py": "0" * 64},  # deliberately wrong
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = validate_trace(manifest, str(repo), current_commit_sha=None)

    assert not result.accepted
    assert "fingerprint mismatch" in result.reason
    assert len(caught) == 1
    assert issubclass(caught[0].category, PrismWarning)
    assert "PrismWarning: StaleTraceIgnored" in str(caught[0].message)


def test_mismatched_git_commit_is_rejected(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = TraceManifest(git_commit_sha="deadbeef", environment="prod", timestamp=None, file_fingerprints={})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = validate_trace(manifest, str(repo), current_commit_sha="cafef00d")
    assert not result.accepted
    assert "git commit mismatch" in result.reason
    assert any(issubclass(w.category, PrismWarning) for w in caught)


def test_strict_mode_raises_instead_of_warning(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = TraceManifest(git_commit_sha="deadbeef", environment="prod", timestamp=None, file_fingerprints={})
    with pytest.raises(StaleTraceError, match="StaleTraceIgnored"):
        validate_trace(manifest, str(repo), strict=True, current_commit_sha="cafef00d")


def test_stale_trace_falls_back_to_static_weights_with_no_errors(tmp_path) -> None:
    """The end-to-end ingestion path: a stale trace is rejected cleanly
    (no exception, no crash - "0 errors") and simply contributes nothing
    to the aggregated overlay, which is exactly what "fall back to pure
    static weights" means in practice - an empty overlay behaves
    identically to no `--trace-file` having been given at all."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def f():\n    return 1\n")
    trace_path = repo / "trace.json"
    _write_json(trace_path, {
        "manifest": {
            "git_commit_sha": None, "environment": "prod", "timestamp": None,
            "file_fingerprints": {"sample.py": "0" * 64},
        },
        "edges": [{"caller": "sample.f", "callee": "sample.g", "count": 10, "errors": 0}],
    })

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        trace, validation = ingest_trace_file(trace_path, str(repo))
    assert trace is None
    assert not validation.accepted

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        aggregated = merge_env_traces([trace_path], str(repo))
    assert aggregated.per_env_edge_counts == {}
    assert aggregated.per_env_run_counts == {}
    assert len(aggregated.rejected) == 1
    assert aggregated.total_hits("sample.f", "sample.g") == 0


# --------------------------------------------------------------------- #
# Environment aggregation (Section 2.1.3)
# --------------------------------------------------------------------- #
def test_merges_multiple_environments_additively(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    e2e_path = repo / "e2e.json"
    prod_path = repo / "prod.json"
    _write_json(e2e_path, {
        "manifest": {"git_commit_sha": None, "environment": "e2e", "timestamp": None, "file_fingerprints": {}},
        "edges": [{"caller": "a", "callee": "b", "count": 34, "errors": 0}],
    })
    _write_json(prod_path, {
        "manifest": {"git_commit_sha": None, "environment": "prod", "timestamp": None, "file_fingerprints": {}},
        "edges": [{"caller": "a", "callee": "b", "count": 1200, "errors": 3}],
    })

    aggregated = merge_env_traces([e2e_path, prod_path], str(repo))
    assert aggregated.total_hits("a", "b") == 34 + 1200
    assert aggregated.total_errors("a", "b") == 3
    assert aggregated.breakdown("a", "b") == {"e2e": 34, "prod": 1200}
    assert aggregated.per_env_run_counts == {"e2e": 1, "prod": 1}
    assert aggregated.provenance_summary() == "merged (e2e=1 runs, prod=1 runs)"


# --------------------------------------------------------------------- #
# Coverage Trap & Dead Code Verification (Section 2.1.1 / 2.1.5)
# --------------------------------------------------------------------- #
def test_uncalled_error_handler_is_never_pruned_from_calls_graph(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def handle_panic(err):\n"
        "    raise SystemExit(1)\n"
        "\n"
        "def run(err):\n"
        "    if err:\n"
        "        handle_panic(err)\n"
        "    return None\n"
    )
    builder, _tags = build_pipeline(str(repo))
    before_nodes = set(builder.calls_graph.nodes)
    before_edges = set(builder.calls_graph.edges)
    assert "sample.handle_panic" in before_nodes
    assert ("sample.run", "sample.handle_panic") in before_edges

    # A runtime trace with ZERO hits for handle_panic - only `run` itself
    # was observed running, never actually reaching the panic branch.
    builder.apply_runtime_overlay(edge_counts={("sample.entry", "sample.run"): 500})

    after_nodes = set(builder.calls_graph.nodes)
    after_edges = set(builder.calls_graph.edges)
    assert after_nodes == before_nodes, "runtime overlay must never remove a statically-real node"
    assert after_edges == before_edges, "runtime overlay must never remove a statically-real edge"

    handler_data = builder.calls_graph.nodes["sample.handle_panic"]
    assert handler_data.get("observed") is False
    assert handler_data.get("statically_reachable") is True


# --------------------------------------------------------------------- #
# Priority Collision & Log-Dampening (Section 2.1.4)
# --------------------------------------------------------------------- #
def test_log_dampening_prevents_priority_collision() -> None:
    """Node A (core logic): static distance 1, 5 runtime hits.
    Node B (loop logger): static distance 3, 50,000 runtime hits.
    Under the default --runtime-bias (0.25), A must still outrank B -
    linear execution counts must never overwhelm static topological
    distance."""
    weight_a = hybrid_edge_weight(static_dist_to_target=1, exec_count=5, alpha=DEFAULT_RUNTIME_BIAS, beta=1.0)
    weight_b = hybrid_edge_weight(static_dist_to_target=3, exec_count=50_000, alpha=DEFAULT_RUNTIME_BIAS, beta=1.0)
    assert weight_a > weight_b


def test_alpha_zero_is_purely_static() -> None:
    """`alpha=0` must behave 100% statically - an exact algebraic
    collapse to `1 / dist_static`, not merely a small runtime influence."""
    assert hybrid_edge_weight(1, 5, alpha=0.0) == pytest.approx(1.0)
    assert hybrid_edge_weight(4, 999_999, alpha=0.0) == pytest.approx(0.25)


def test_higher_alpha_increases_runtime_influence_monotonically() -> None:
    low = hybrid_edge_weight(2, 1000, alpha=0.1)
    high = hybrid_edge_weight(2, 1000, alpha=0.9)
    assert high > low


def test_weighted_execution_count_applies_environment_weights() -> None:
    aggregated = AggregatedTrace()
    aggregated.per_env_edge_counts["unit"] = {("a", "b"): 100}
    aggregated.per_env_edge_counts["prod"] = {("a", "b"): 100}
    from prism.analysis.hybrid_engine import DEFAULT_ENV_WEIGHTS

    count = weighted_execution_count("a", "b", aggregated)
    expected = DEFAULT_ENV_WEIGHTS["unit"] * 100 + DEFAULT_ENV_WEIGHTS["prod"] * 100
    assert count == pytest.approx(expected)
    # prod weight is strictly higher than unit's, so prod-heavy traffic
    # must count for more than the same volume of unit-test traffic.
    assert DEFAULT_ENV_WEIGHTS["prod"] > DEFAULT_ENV_WEIGHTS["unit"]
