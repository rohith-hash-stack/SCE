"""Hermetic tests for `sce.runtime.reconciler` - JSONL trace I/O, OTel
export normalization, edge promotion/discovery, sink-tag attachment, and
persisted-state accumulation. No network, no subprocess.
"""
from __future__ import annotations

from sce.cli import build_pipeline
from sce.runtime.reconciler import (
    GraphReconciler,
    _infer_sink,
    _normalize_kind,
    load_runtime_state,
    load_trace_file,
    merge_result_into_state,
    parse_otel_export,
    runtime_state_path,
    save_runtime_state,
    write_trace_file,
)
from sce.runtime.tracer import TraceRecord


def _order_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text(
        "class OrderValidator:\n"
        "    def validate(self, amount):\n"
        "        return amount > 0\n"
        "\n"
        "\n"
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        self.validator = OrderValidator()\n"
        "\n"
        "    def create_order(self, amount):\n"
        "        if not self.validator.validate(amount):\n"
        "            raise ValueError('bad amount')\n"
        "        return amount\n"
    )
    return repo


# --------------------------------------------------------------------- #
# JSONL trace file I/O
# --------------------------------------------------------------------- #
def test_write_and_load_trace_file_roundtrip(tmp_path):
    records = [
        TraceRecord(caller="a.b", callee="a.c", source="pytest_tracer", timestamp=1.0),
        TraceRecord(caller=None, callee="a.b", source="pytest_tracer", timestamp=2.0, sink_tag="#external_io", detail="GET /x"),
    ]
    path = tmp_path / "trace.jsonl"
    write_trace_file(path, records)
    loaded = load_trace_file(path)
    assert loaded == records


def test_load_trace_file_skips_blank_lines(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"caller": null, "callee": "a.b", "source": "pytest_tracer"}\n\n\n')
    loaded = load_trace_file(path)
    assert len(loaded) == 1
    assert loaded[0].callee == "a.b"


# --------------------------------------------------------------------- #
# OTel export normalization
# --------------------------------------------------------------------- #
def _otel_span(span_id, parent_id, attrs, kind=1):
    return {
        "spanId": span_id,
        "parentSpanId": parent_id,
        "kind": kind,
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()],
    }


def test_parse_otel_export_emits_call_edge_for_code_function_attributes():
    export = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            _otel_span("root", None, {"code.namespace": "orders.OrderService", "code.function": "create_order"}),
                            _otel_span("child", "root", {"code.namespace": "orders.OrderValidator", "code.function": "validate"}),
                        ]
                    }
                ]
            }
        ]
    }
    records = parse_otel_export(export)
    assert TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderValidator.validate", source="otel") in records


def test_parse_otel_export_tags_db_write_on_insert_statement():
    export = {
        "resourceSpans": [{"scopeSpans": [{"spans": [
            _otel_span("root", None, {"code.namespace": "orders.OrderService", "code.function": "create_order"}),
            _otel_span("child", "root", {"db.system": "postgresql", "db.statement": "INSERT INTO orders (id) VALUES ($1)"}, kind=3),
        ]}]}]
    }
    records = parse_otel_export(export)
    sinks = [r for r in records if r.sink_tag]
    assert len(sinks) == 1
    assert sinks[0].sink_tag == "#db_write"
    assert sinks[0].caller == "orders.OrderService.create_order"
    assert "INSERT" in sinks[0].detail


def test_parse_otel_export_defaults_unrecognized_db_verb_to_read():
    export = {
        "resourceSpans": [{"scopeSpans": [{"spans": [
            _otel_span("root", None, {"code.namespace": "orders.OrderService", "code.function": "list_orders"}),
            _otel_span("child", "root", {"db.system": "postgresql", "db.statement": "SELECT * FROM orders"}, kind=3),
        ]}]}]
    }
    records = parse_otel_export(export)
    sinks = [r for r in records if r.sink_tag]
    assert sinks[0].sink_tag == "#db_read"


def test_parse_otel_export_tags_external_io_for_http_client_span():
    export = {
        "resourceSpans": [{"scopeSpans": [{"spans": [
            _otel_span("root", None, {"code.namespace": "orders.OrderService", "code.function": "create_order"}),
            _otel_span("child", "root", {"http.method": "GET", "http.url": "https://payments.internal/charge"}, kind=3),
        ]}]}]
    }
    records = parse_otel_export(export)
    sinks = [r for r in records if r.sink_tag]
    assert sinks[0].sink_tag == "#external_io"
    assert sinks[0].detail == "https://payments.internal/charge"


def test_parse_otel_export_tags_event_producer_and_consumer():
    export = {
        "resourceSpans": [{"scopeSpans": [{"spans": [
            _otel_span("p", None, {"code.namespace": "orders.OrderService", "code.function": "create_order",
                                    "messaging.system": "kafka", "messaging.destination": "orders.created"}, kind=4),
            _otel_span("c", None, {"code.namespace": "orders.Worker", "code.function": "handle",
                                    "messaging.system": "kafka", "messaging.destination": "orders.created"}, kind=5),
        ]}]}]
    }
    records = parse_otel_export(export)
    tags = {r.sink_tag for r in records if r.sink_tag}
    assert tags == {"#event_producer", "#event_consumer"}


def test_parse_otel_export_no_edge_without_parent_symbol():
    export = {
        "resourceSpans": [{"scopeSpans": [{"spans": [
            _otel_span("root", None, {}),
            _otel_span("child", "root", {"code.namespace": "orders.OrderValidator", "code.function": "validate"}),
        ]}]}]
    }
    records = parse_otel_export(export)
    assert not any(r.callee is not None for r in records)


def test_normalize_kind_accepts_int_and_string_forms():
    assert _normalize_kind(3) == "CLIENT"
    assert _normalize_kind("SPAN_KIND_CLIENT") == "CLIENT"
    assert _normalize_kind("client") == "CLIENT"
    assert _normalize_kind(None) == "UNSPECIFIED"


def test_infer_sink_returns_none_for_ordinary_internal_span():
    assert _infer_sink({"code.function": "compute"}, 1) == (None, None)


# --------------------------------------------------------------------- #
# GraphReconciler
# --------------------------------------------------------------------- #
def test_reconcile_promotes_existing_static_edge_to_confirmed(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("orders.OrderService.create_order", "orders.OrderValidator.validate")

    events = [TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderValidator.validate", source="pytest_tracer")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    assert ("orders.OrderService.create_order", "orders.OrderValidator.validate") in result.confirmed_edges
    assert not result.discovered_edges
    edge_data = builder.graph.edges["orders.OrderService.create_order", "orders.OrderValidator.validate"]
    assert edge_data["confidence"] == "CONFIRMED_RUNTIME"
    assert edge_data["runtime_invocation_count"] == 1
    assert edge_data["relation"] == "CALLS"  # original static attribute untouched


def test_reconcile_synthesizes_new_edge_for_call_the_static_pass_missed(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    assert not builder.graph.has_edge("orders.OrderService.create_order", "orders.OrderService.__init__")

    events = [TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderService.__init__", source="pytest_tracer")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    assert ("orders.OrderService.create_order", "orders.OrderService.__init__") in result.discovered_edges
    assert builder.graph.has_edge("orders.OrderService.create_order", "orders.OrderService.__init__")
    edge_data = builder.graph.edges["orders.OrderService.create_order", "orders.OrderService.__init__"]
    assert edge_data["provenance"] == "RUNTIME_DISCOVERED"
    assert edge_data["confidence"] == "CONFIRMED_RUNTIME"


def test_reconcile_counts_multiple_invocations_of_the_same_edge(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    events = [
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderValidator.validate", source="pytest_tracer")
        for _ in range(3)
    ]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    edge = ("orders.OrderService.create_order", "orders.OrderValidator.validate")
    assert result.invocation_counts[edge] == 3
    assert builder.graph.edges[edge]["runtime_invocation_count"] == 3


def test_reconcile_marks_unresolved_events_for_unknown_symbols(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    events = [
        TraceRecord(caller=None, callee="orders.OrderService", source="pytest_tracer"),
        TraceRecord(caller="orders.OrderService.create_order", callee="not.a.real.symbol", source="pytest_tracer"),
    ]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    assert len(result.unresolved_events) == 2
    assert not result.confirmed_edges
    assert not result.discovered_edges


def test_reconcile_attaches_sink_tag_to_caller_symbol_in_tag_matrix_and_graph(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#db_write" not in tag_matrix.get("orders.OrderService.create_order", set())

    events = [TraceRecord(caller="orders.OrderService.create_order", callee=None, source="otel", sink_tag="#db_write", detail="INSERT ...")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    assert result.sink_symbols == {"orders.OrderService.create_order": {"#db_write"}}
    assert "#db_write" in tag_matrix["orders.OrderService.create_order"]
    assert "#db_write" in builder.graph.nodes["orders.OrderService.create_order"]["tags"]


def test_reconcile_sink_event_with_no_resolvable_caller_is_unresolved(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    events = [TraceRecord(caller=None, callee=None, source="otel", sink_tag="#external_io", detail="GET /x")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    assert not result.sink_symbols
    assert len(result.unresolved_events) == 1


def test_reconcile_preserves_existing_tags_when_adding_a_sink_tag(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    tag_matrix["orders.OrderService.create_order"] = {"#state_mutation"}
    events = [TraceRecord(caller="orders.OrderService.create_order", callee=None, source="otel", sink_tag="#db_write")]
    GraphReconciler(builder, tag_matrix).reconcile(events)
    assert tag_matrix["orders.OrderService.create_order"] == {"#state_mutation", "#db_write"}


# --------------------------------------------------------------------- #
# Persisted state
# --------------------------------------------------------------------- #
def test_load_runtime_state_returns_empty_state_when_no_file_exists(tmp_path):
    state = load_runtime_state(str(tmp_path))
    assert state["trace_files"] == []
    assert state["confirmed_edges"] == []
    assert state["unresolved_event_count"] == 0
    assert state["last_updated"] is None


def test_save_and_load_runtime_state_roundtrip(tmp_path):
    state = {"trace_files": ["run_1.jsonl"], "confirmed_edges": [["a", "b"]], "discovered_edges": [],
              "sink_symbols": {"a": ["#db_write"]}, "unresolved_event_count": 2, "last_updated": "2026-01-01T00:00:00Z"}
    save_runtime_state(str(tmp_path), state)
    assert runtime_state_path(str(tmp_path)).exists()
    loaded = load_runtime_state(str(tmp_path))
    assert loaded == state


def test_merge_result_into_state_accumulates_across_runs(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    reconciler = GraphReconciler(builder, tag_matrix)

    state = load_runtime_state(str(repo))
    result1 = reconciler.reconcile([
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderValidator.validate", source="pytest_tracer"),
    ])
    state = merge_result_into_state(state, result1, "run_1.jsonl")

    result2 = reconciler.reconcile([
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderService.__init__", source="pytest_tracer"),
        TraceRecord(caller="orders.OrderService.create_order", callee=None, source="otel", sink_tag="#db_write"),
    ])
    state = merge_result_into_state(state, result2, "run_2.jsonl")

    assert state["trace_files"] == ["run_1.jsonl", "run_2.jsonl"]
    assert ["orders.OrderService.create_order", "orders.OrderValidator.validate"] in state["confirmed_edges"]
    assert ["orders.OrderService.create_order", "orders.OrderService.__init__"] in state["discovered_edges"]
    assert state["sink_symbols"]["orders.OrderService.create_order"] == ["#db_write"]

    # Re-merging the same trace file name doesn't duplicate it.
    state = merge_result_into_state(state, result1, "run_1.jsonl")
    assert state["trace_files"] == ["run_1.jsonl", "run_2.jsonl"]


def test_merge_result_into_state_removes_edge_from_discovered_once_confirmed(tmp_path):
    repo = _order_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    reconciler = GraphReconciler(builder, tag_matrix)

    # First run: static resolver hadn't found this edge, so it's discovered.
    state = load_runtime_state(str(repo))
    result1 = reconciler.reconcile([
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderService.__init__", source="pytest_tracer"),
    ])
    state = merge_result_into_state(state, result1, "run_1.jsonl")
    assert ["orders.OrderService.create_order", "orders.OrderService.__init__"] in state["discovered_edges"]

    # Second run: the edge now exists in G_C (this reconciler's own prior
    # run added it in-memory) - reconciling again should promote it.
    result2 = reconciler.reconcile([
        TraceRecord(caller="orders.OrderService.create_order", callee="orders.OrderService.__init__", source="pytest_tracer"),
    ])
    state = merge_result_into_state(state, result2, "run_2.jsonl")
    assert ["orders.OrderService.create_order", "orders.OrderService.__init__"] in state["confirmed_edges"]
    assert ["orders.OrderService.create_order", "orders.OrderService.__init__"] not in state["discovered_edges"]
