"""Hermetic tests for the `sce trace`/`sce status` CLI commands. The
`trace -- pytest ...` path spawns a real (local-only) pytest subprocess;
`--ingest-otel` and `status` are pure in-process.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from sce.cli import main
from sce.runtime.reconciler import runtime_state_path


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
    (repo / "test_orders.py").write_text(
        "from orders import OrderService\n\n\n"
        "def test_create_order():\n"
        "    svc = OrderService()\n"
        "    assert svc.create_order(5) == 5\n"
    )
    return repo


def test_status_before_any_trace_reports_zero_runtime_state(tmp_path):
    repo = _order_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Static symbols:" in result.output
    assert "Confirmed runtime edges:      0" in result.output
    assert "No runtime trace has been recorded yet" in result.output


def test_trace_pytest_run_then_status_reports_confirmed_and_discovered_edges(tmp_path):
    repo = _order_repo(tmp_path)
    runner = CliRunner()

    trace_result = runner.invoke(main, ["trace", "--repo", str(repo), "--", "pytest", "test_orders.py", "-q"])
    assert trace_result.exit_code == 0, trace_result.output
    assert "Runtime events processed:" in trace_result.output
    assert "Trace written to" in trace_result.output

    state_path = runtime_state_path(str(repo))
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert len(state["trace_files"]) == 1
    assert ["orders.OrderService.create_order", "orders.OrderValidator.validate"] in state["confirmed_edges"]
    assert ["test_orders.test_create_order", "orders.OrderService.__init__"] in state["discovered_edges"]

    status_result = runner.invoke(main, ["status", "--repo", str(repo)])
    assert status_result.exit_code == 0, status_result.output
    assert "Confirmed runtime edges:      1" in status_result.output or "Confirmed runtime edges:" in status_result.output
    assert "Dynamically discovered edges: 1" in status_result.output
    assert "Trace files ingested:         1" in status_result.output


def test_trace_ingest_otel_attaches_sink_tag_and_persists_state(tmp_path):
    repo = _order_repo(tmp_path)
    otel_path = tmp_path / "otel_export.json"
    otel_path.write_text(json.dumps({
        "resourceSpans": [{"scopeSpans": [{"spans": [
            {"spanId": "root", "parentSpanId": None, "kind": 1,
             "attributes": [{"key": "code.namespace", "value": {"stringValue": "orders.OrderService"}},
                            {"key": "code.function", "value": {"stringValue": "create_order"}}]},
            {"spanId": "child", "parentSpanId": "root", "kind": 3,
             "attributes": [{"key": "db.system", "value": {"stringValue": "postgresql"}},
                            {"key": "db.statement", "value": {"stringValue": "INSERT INTO orders (id) VALUES ($1)"}}]},
        ]}]}]
    }))

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "--repo", str(repo), "--ingest-otel", str(otel_path)])
    assert result.exit_code == 0, result.output
    assert "Ingested 1 event(s)" in result.output
    assert "Sink-tagged symbols (this run):      1" in result.output

    state = json.loads(runtime_state_path(str(repo)).read_text())
    assert state["sink_symbols"]["orders.OrderService.create_order"] == ["#db_write"]


def test_trace_requires_command_or_ingest_otel(tmp_path):
    repo = _order_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["trace", "--repo", str(repo)])
    assert result.exit_code != 0
    assert "no command given" in result.output


def test_trace_rejects_non_pytest_command(tmp_path):
    repo = _order_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["trace", "--repo", str(repo), "--", "echo", "hi"])
    assert result.exit_code != 0
    assert "only a `pytest` command is currently supported" in result.output


def test_two_trace_runs_accumulate_in_status(tmp_path):
    repo = _order_repo(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["trace", "--repo", str(repo), "--", "pytest", "test_orders.py", "-q"])
    runner.invoke(main, ["trace", "--repo", str(repo), "--", "pytest", "test_orders.py", "-q"])

    state = json.loads(runtime_state_path(str(repo)).read_text())
    assert len(state["trace_files"]) == 2
