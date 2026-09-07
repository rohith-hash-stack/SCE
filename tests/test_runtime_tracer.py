"""Hermetic tests for `sce.runtime.tracer` - no network, no external
processes beyond a real (local-only) pytest subprocess for the end-to-end
case, matching how `benchmarks/clone_eval.py`-style tools are tested
elsewhere in this suite (only *network*-dependent tests are gated; a
local subprocess is not).
"""
from __future__ import annotations

import subprocess
import sys

from sce.runtime.tracer import Tracer, resolve_qualified_name, run_traced_pytest


def _write_traced_module(tmp_path):
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


def test_resolve_qualified_name_skips_synthetic_and_out_of_repo_frames(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    class FakeCode:
        def __init__(self, filename, name, qualname=None):
            self.co_filename = filename
            self.co_name = name
            if qualname is not None:
                self.co_qualname = qualname

    class FakeFrame:
        def __init__(self, code):
            self.f_code = code

    # A synthetic pseudo-filename (frozen stdlib bootstrap machinery) -
    # must never resolve, regardless of what `os.path.abspath` would do
    # with the bracketed string.
    assert resolve_qualified_name(FakeFrame(FakeCode("<frozen importlib._bootstrap>", "_find_and_load")), str(repo)) is None
    # A module-level frame - not a real "symbol" a static index registers.
    in_repo_file = str(repo / "orders.py")
    assert resolve_qualified_name(FakeFrame(FakeCode(in_repo_file, "<module>")), str(repo)) is None
    # Genuinely outside the repo root (e.g. stdlib, site-packages).
    assert resolve_qualified_name(FakeFrame(FakeCode("/usr/lib/python3.11/json/__init__.py", "dumps")), str(repo)) is None
    # A real in-repo method frame, with co_qualname (Python 3.11+) set.
    assert (
        resolve_qualified_name(FakeFrame(FakeCode(in_repo_file, "validate", "OrderValidator.validate")), str(repo))
        == "orders.OrderValidator.validate"
    )


def test_tracer_records_real_in_process_calls_with_correct_qualified_names(tmp_path):
    repo = _write_traced_module(tmp_path)
    sys.path.insert(0, str(repo))
    try:
        import orders  # noqa: PLC0415 - deliberately imported after sys.path insert

        output = tmp_path / "trace.jsonl"
        with Tracer(str(repo), output):
            svc = orders.OrderService()
            svc.create_order(5)

        lines = output.read_text().strip().splitlines()
        events = [line for line in lines]
        assert any('"callee":"orders.OrderService.__init__"' in e for e in events)
        assert any('"callee":"orders.OrderService.create_order"' in e for e in events)
        assert any('"callee":"orders.OrderValidator.validate"' in e for e in events)
        assert any(
            '"caller":"orders.OrderService.create_order"' in e and '"callee":"orders.OrderValidator.validate"' in e
            for e in events
        )
    finally:
        sys.path.remove(str(repo))
        sys.modules.pop("orders", None)


def test_tracer_excludes_stdlib_frames_even_when_called_from_in_repo_code(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "formatter.py").write_text(
        "import json\n\n\n" "def render(data):\n" "    return json.dumps(data)\n"
    )
    sys.path.insert(0, str(repo))
    try:
        import formatter  # noqa: PLC0415

        output = tmp_path / "trace.jsonl"
        with Tracer(str(repo), output):
            formatter.render({"a": 1})

        text = output.read_text()
        assert '"callee":"formatter.render"' in text
        assert "json.dumps" not in text
        assert "encoder" not in text.lower()
    finally:
        sys.path.remove(str(repo))
        sys.modules.pop("formatter", None)


def test_tracer_stop_restores_previous_trace_function(tmp_path):
    import sys as _sys

    previous = _sys.gettrace()
    tracer = Tracer(str(tmp_path), tmp_path / "trace.jsonl")
    tracer.start()
    # Bound-method identity isn't stable across separate attribute
    # accesses (`instance.method is instance.method` is False even for
    # the same underlying function) - `==` is the correct comparison,
    # since bound methods define equality by (function, instance) pair.
    assert _sys.gettrace() == tracer._trace_calls
    tracer.stop()
    assert _sys.gettrace() is previous


def test_run_traced_pytest_end_to_end(tmp_path):
    repo = _write_traced_module(tmp_path)
    (repo / "test_orders.py").write_text(
        "from orders import OrderService\n\n\n"
        "def test_create_order():\n"
        "    svc = OrderService()\n"
        "    assert svc.create_order(5) == 5\n"
    )
    output = tmp_path / "trace.jsonl"

    exit_code = run_traced_pytest(str(repo), output, ["test_orders.py", "-q"])

    assert exit_code == 0
    assert output.exists()
    text = output.read_text()
    assert '"callee":"orders.OrderService.create_order"' in text
    assert '"callee":"orders.OrderValidator.validate"' in text


def test_module_entry_point_runs_traced_pytest(tmp_path):
    repo = _write_traced_module(tmp_path)
    (repo / "test_orders.py").write_text(
        "from orders import OrderService\n\n\n"
        "def test_create_order():\n"
        "    assert OrderService().create_order(5) == 5\n"
    )
    output = tmp_path / "trace.jsonl"

    result = subprocess.run(
        [sys.executable, "-m", "sce.runtime.trace", "--repo", str(repo), "--output", str(output), "--", "pytest", "test_orders.py", "-q"],
        capture_output=True, text=True, cwd=str(repo),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.exists()
    assert '"callee":"orders.OrderService.create_order"' in output.read_text()
