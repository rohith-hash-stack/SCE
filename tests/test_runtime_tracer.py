"""Hermetic tests for `prism.runtime.tracer` - no network, no external
processes beyond a real (local-only) pytest subprocess for the end-to-end
case, matching how `benchmarks/clone_eval.py`-style tools are tested
elsewhere in this suite (only *network*-dependent tests are gated; a
local subprocess is not).
"""
from __future__ import annotations

import subprocess
import sys

from prism.cli import build_pipeline
from prism.graph.metamodel import SemanticMetamodel
from prism.runtime.reconciler import GraphReconciler, load_trace_file
from prism.runtime.tracer import Tracer, TraceRecord, resolve_qualified_name, run_traced_pytest
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


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
        [sys.executable, "-m", "prism.runtime.trace", "--repo", str(repo), "--output", str(output), "--", "pytest", "test_orders.py", "-q"],
        capture_output=True, text=True, cwd=str(repo),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.exists()
    assert '"callee":"orders.OrderService.create_order"' in output.read_text()


# --------------------------------------------------------------------- #
# Dynamic dispatch (getattr) end to end: tracer capture, graph
# reconciliation, and knapsack packing priority. `handlers.handle_create`
# is invoked only through `getattr(handlers, f"handle_{action}")` in
# `dispatcher.dispatch` - a name the static two-pass resolver genuinely
# cannot see (it isn't a literal reference to `handle_create` anywhere in
# the source text), so this exercises the real gap the runtime watcher
# exists to close, not a contrived one.
# --------------------------------------------------------------------- #
def _write_dispatch_fixture(tmp_path):
    repo = tmp_path / "dispatch_repo"
    repo.mkdir()
    (repo / "handlers.py").write_text(
        "def handle_create(amount):\n"
        "    return amount\n"
        "\n"
        "\n"
        "def handle_cancel(amount):\n"
        "    return -amount\n"
    )
    (repo / "dispatcher.py").write_text(
        "import handlers\n"
        "\n"
        "\n"
        "def dispatch(action, amount):\n"
        "    handler = getattr(handlers, f'handle_{action}')\n"
        "    return handler(amount)\n"
    )
    return repo


def _trace_dispatch_fixture(repo, output_path, action="create", amount=5):
    sys.path.insert(0, str(repo))
    try:
        import dispatcher  # noqa: PLC0415 - deliberately imported after sys.path insert

        with Tracer(str(repo), output_path):
            dispatcher.dispatch(action, amount)
    finally:
        sys.path.remove(str(repo))
        sys.modules.pop("dispatcher", None)
        sys.modules.pop("handlers", None)


def test_tracer_captures_getattr_dynamic_dispatch(tmp_path):
    """Tracer Execution: a small multi-module fixture with a dynamic
    `getattr()` dispatch, traced under `prism.runtime.tracer` - the trace
    log must capture the actual concrete handler the dispatch resolved
    to at runtime.
    """
    repo = _write_dispatch_fixture(tmp_path)
    output = tmp_path / "trace.jsonl"

    _trace_dispatch_fixture(repo, output)

    text = output.read_text()
    assert '"caller":"dispatcher.dispatch"' in text
    assert '"callee":"handlers.handle_create"' in text
    # Confirms this is a genuine *dynamic* invocation, not something the
    # tracer just happened to also see: `getattr` itself is stdlib, so it
    # must never appear as a traced callee in its own right.
    assert "getattr" not in text


def test_reconciler_injects_dynamic_dispatch_edge_with_confirmed_runtime_confidence(tmp_path):
    """Graph Reconciliation: `reconciler.reconcile()` must inject the
    dynamic-dispatch call as a new `G_C` edge, since the static two-pass
    resolver provably never found it - and that edge's attributes must
    read `provenance="RUNTIME_DISCOVERED"` /
    `confidence="CONFIRMED_RUNTIME"`.
    """
    repo = _write_dispatch_fixture(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    assert not builder.graph.has_edge("dispatcher.dispatch", "handlers.handle_create")

    output = tmp_path / "trace.jsonl"
    _trace_dispatch_fixture(repo, output)
    events = load_trace_file(output)

    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    assert ("dispatcher.dispatch", "handlers.handle_create") in result.discovered_edges
    assert builder.graph.has_edge("dispatcher.dispatch", "handlers.handle_create")
    edge_data = builder.graph.edges["dispatcher.dispatch", "handlers.handle_create"]
    assert edge_data["confidence"] == "CONFIRMED_RUNTIME"
    assert edge_data["provenance"] == "RUNTIME_DISCOVERED"
    assert edge_data["relation"] == "CALLS"


def test_knapsack_prioritizes_confirmed_runtime_path_under_constrained_budget(tmp_path):
    """Knapsack Priority Check: two statically-equidistant (1-hop)
    candidates, only one of which was actually exercised at runtime - a
    budget tight enough to admit just one of them must pick the
    runtime-confirmed one.
    """
    repo = tmp_path / "priority_repo"
    repo.mkdir()
    (repo / "orders.py").write_text(
        "def helper_a(amount):\n"
        "    return amount + 1\n"
        "\n"
        "\n"
        "def helper_b(amount):\n"
        "    return amount - 1\n"
        "\n"
        "\n"
        "def create_order(amount):\n"
        "    x = helper_a(amount)\n"
        "    y = helper_b(amount)\n"
        "    return x, y\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    seed = "orders.create_order"
    assert builder.graph.has_edge(seed, "orders.helper_a")
    assert builder.graph.has_edge(seed, "orders.helper_b")

    # Only helper_a's call was actually exercised by a real run.
    events = [TraceRecord(caller=seed, callee="orders.helper_a", source="pytest_tracer")]
    GraphReconciler(builder, tag_matrix).reconcile(events)

    engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())

    # A generous budget fits both, confirming the fixture itself is sane
    # (this isn't testing anything about priority yet).
    generous = ContextKnapsackPacker(token_budget=10_000).pack(seed, builder, tag_matrix, engine)
    assert {"orders.helper_a", "orders.helper_b"} <= {i.symbol for i in generous.items}

    # A tight budget (empirically: room for the seed plus exactly one more
    # candidate at this fixture's exact token cost) must admit the
    # runtime-confirmed helper_a, not the unconfirmed helper_b. 120, not
    # 150 - the second post-implementation audit's Item 2 (compound-
    # operator recognition in the fallback tokenizer, e.g. counting `->`
    # as one token instead of two) lowered fallback counts slightly
    # relative to Issue A1's own earlier-calibrated 150, so the "room for
    # exactly one extra candidate" window shifted again; re-measured
    # directly against this fixture, not guessed.
    tight = ContextKnapsackPacker(token_budget=120).pack(seed, builder, tag_matrix, engine)
    packed_symbols = {item.symbol for item in tight.items}
    assert "orders.helper_a" in packed_symbols
    assert "orders.helper_b" not in packed_symbols
