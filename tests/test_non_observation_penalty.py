"""Tests for Item 9 (second post-implementation audit): Bayesian
Confidence Degradation for Static False Positives -
`GraphReconciler._apply_non_observation_penalty` flags a static `CALLS`
edge `metadata["unobserved_in_traces"] = True` (never deleting it) when
its caller executed at least `NON_OBSERVATION_EXECUTION_THRESHOLD` times
in a trace but that specific edge was never among the confirmed edges.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.runtime.reconciler import (
    NON_OBSERVATION_EXECUTION_THRESHOLD,
    GraphReconciler,
    merge_result_into_state,
)
from prism.runtime.tracer import TraceRecord


def _dispatch_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text(
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "\n"
        "    def dispatch(self, amount):\n"
        "        if amount > 0:\n"
        "            return self.validate(amount)\n"
        "        return self.reject(amount)\n"
        "\n"
        "    def validate(self, amount):\n"
        "        return amount\n"
        "\n"
        "    def reject(self, amount):\n"
        "        raise ValueError('bad amount')\n"
    )
    return repo


def _heavy_trace(caller: str, callee: str, count: int = NON_OBSERVATION_EXECUTION_THRESHOLD):
    return [TraceRecord(caller=caller, callee=callee, source="pytest_tracer") for _ in range(count)]


def test_frequently_executing_caller_flags_its_never_observed_static_edge(tmp_path):
    repo = _dispatch_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("orders.OrderService.dispatch", "orders.OrderService.reject")

    # `dispatch` executes NON_OBSERVATION_EXECUTION_THRESHOLD times, every
    # time calling `validate` - `reject`'s statically-known edge is never
    # traced at all.
    events = _heavy_trace("orders.OrderService.dispatch", "orders.OrderService.validate")
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    reject_edge = ("orders.OrderService.dispatch", "orders.OrderService.reject")
    assert reject_edge in result.non_observed_edges
    assert builder.graph.edges[reject_edge]["unobserved_in_traces"] is True
    # Never deleted.
    assert builder.graph.has_edge(*reject_edge)
    assert builder.graph.edges[reject_edge]["relation"] == "CALLS"


def test_observed_edge_from_the_same_frequent_caller_is_not_flagged(tmp_path):
    repo = _dispatch_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    events = _heavy_trace("orders.OrderService.dispatch", "orders.OrderService.validate")
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    validate_edge = ("orders.OrderService.dispatch", "orders.OrderService.validate")
    assert validate_edge not in result.non_observed_edges
    assert "unobserved_in_traces" not in builder.graph.edges[validate_edge]


def test_caller_below_execution_threshold_is_not_penalized(tmp_path):
    repo = _dispatch_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    # Only executed a handful of times - below NON_OBSERVATION_EXECUTION_THRESHOLD.
    events = _heavy_trace(
        "orders.OrderService.dispatch", "orders.OrderService.validate", count=NON_OBSERVATION_EXECUTION_THRESHOLD - 1
    )
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    reject_edge = ("orders.OrderService.dispatch", "orders.OrderService.reject")
    assert reject_edge not in result.non_observed_edges
    assert "unobserved_in_traces" not in builder.graph.edges[reject_edge]


def test_no_penalty_at_all_when_no_trace_events_exist(tmp_path):
    repo = _dispatch_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    result = GraphReconciler(builder, tag_matrix).reconcile([])
    assert result.non_observed_edges == []


def test_runtime_discovered_edges_are_never_flagged_unobserved(tmp_path):
    """A `RUNTIME_DISCOVERED` edge is itself runtime evidence - it can't
    simultaneously be 'never observed'."""
    repo = _dispatch_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    events = _heavy_trace("orders.OrderService.dispatch", "orders.OrderService.validate") + [
        TraceRecord(caller="orders.OrderService.dispatch", callee="orders.OrderService.__init__", source="pytest_tracer")
    ]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    discovered_edge = ("orders.OrderService.dispatch", "orders.OrderService.__init__")
    assert discovered_edge in result.discovered_edges
    assert discovered_edge not in result.non_observed_edges


def test_merge_result_into_state_accumulates_unobserved_edges_and_drops_later_confirmed(tmp_path):
    repo = _dispatch_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    reject_edge = ("orders.OrderService.dispatch", "orders.OrderService.reject")

    run1_events = _heavy_trace("orders.OrderService.dispatch", "orders.OrderService.validate")
    result1 = GraphReconciler(builder, tag_matrix).reconcile(run1_events)
    assert reject_edge in result1.non_observed_edges

    state = merge_result_into_state({}, result1, "run1.jsonl")
    assert list(reject_edge) in state["unobserved_edges"]

    # A later run *does* trace the previously-unobserved edge.
    run2_events = _heavy_trace("orders.OrderService.dispatch", "orders.OrderService.reject")
    result2 = GraphReconciler(builder, tag_matrix).reconcile(run2_events)
    state = merge_result_into_state(state, result2, "run2.jsonl")

    assert list(reject_edge) not in state["unobserved_edges"]
    assert list(reject_edge) in state["confirmed_edges"]
