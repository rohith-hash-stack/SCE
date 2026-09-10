"""Tests for Item 10 (second post-implementation audit): Fuzzy Anchor
Matching - `GraphReconciler._fuzzy_anchor_match` resolves a
`pytest_tracer`-sourced event whose exact `callee` qualified name misses
the static symbol table by finding the nearest static symbol (same file,
within `FUZZY_ANCHOR_LINE_WINDOW` lines) instead, synthesizing a
`kind="TENTATIVE_DYNAMIC_CALL"` edge rather than leaving the event an
unresolved orphan.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.runtime.reconciler import (
    FUZZY_ANCHOR_LINE_WINDOW,
    GraphReconciler,
    load_trace_file,
    merge_result_into_state,
)
from prism.runtime.tracer import Tracer, TraceRecord


def _service_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "class Service:\n"
        "    def process(self, x):\n"          # line 2-3
        "        return x\n"
        "\n"
        "    def other_method(self, x):\n"     # line 5-6
        "        return x * 2\n"
        "\n"
        "\n"
        "class Caller:\n"
        "    def run(self):\n"                 # line 10-11
        "        return None\n"
    )
    return repo


def _mystery_event(caller: str, callee_line: int, callee_file, callee: str = "svc.deco.<locals>.wrapper"):
    return TraceRecord(
        caller=caller, callee=callee, source="pytest_tracer",
        callee_file=str(callee_file), callee_line=callee_line,
    )


def test_fuzzy_match_resolves_to_nearest_symbol_by_line_proximity(tmp_path):
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    event = _mystery_event("svc.Caller.run", callee_line=2, callee_file=repo / "svc.py")

    result = GraphReconciler(builder, tag_matrix).reconcile([event])

    assert ("svc.Caller.run", "svc.Service.process") in result.fuzzy_matched_edges
    assert not result.unresolved_events
    edge_data = builder.graph.edges["svc.Caller.run", "svc.Service.process"]
    assert edge_data["kind"] == "TENTATIVE_DYNAMIC_CALL"
    assert edge_data["provenance"] == "RUNTIME_FUZZY_MATCHED"
    assert edge_data["confidence"] == "TENTATIVE_RUNTIME"
    assert edge_data["relation"] == "CALLS"


def test_fuzzy_match_returns_none_when_no_candidate_within_window(tmp_path):
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    far_line = 2 + FUZZY_ANCHOR_LINE_WINDOW + 100
    event = _mystery_event("svc.Caller.run", callee_line=far_line, callee_file=repo / "svc.py")

    result = GraphReconciler(builder, tag_matrix).reconcile([event])

    assert not result.fuzzy_matched_edges
    assert result.unresolved_events == [event]


def test_fuzzy_match_declines_a_genuine_tie_rather_than_guessing(tmp_path):
    """`process` spans lines 2-3, `other_method` spans lines 5-6 - line 4
    is exactly one line from both (distance 1 either way), a genuine tie
    that must not be resolved by arbitrary tie-break."""
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    event = _mystery_event("svc.Caller.run", callee_line=4, callee_file=repo / "svc.py")

    result = GraphReconciler(builder, tag_matrix).reconcile([event])

    assert not result.fuzzy_matched_edges
    assert result.unresolved_events == [event]


def test_fuzzy_match_prefers_innermost_span_on_nested_containment(tmp_path):
    """A nested function's `line_range` always sits inside its enclosing
    function's - both directly contain a traced line at the closure's own
    location (distance 0 for each), but the narrower (inner) span is the
    correct, more specific answer, not a tie."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "nested.py").write_text(
        "def outer():\n"          # line 1-4
        "    def inner():\n"      # line 2-3
        "        return 1\n"
        "    return inner\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    event = _mystery_event(
        caller=None, callee_line=2, callee_file=repo / "nested.py", callee="nested.outer.<locals>.inner"
    )
    # `_fuzzy_anchor_match` doesn't consult `caller` at all - call it directly.
    matched = GraphReconciler(builder, tag_matrix)._fuzzy_anchor_match(event)
    assert matched == "nested.inner"


def test_fuzzy_match_only_searches_the_same_file_module(tmp_path):
    repo = _service_repo(tmp_path)
    (repo / "other.py").write_text(
        "class Unrelated:\n"
        "    def process(self, x):\n"  # same line number as svc.Service.process, different file
        "        return x\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    # A line number that matches `other.Unrelated.process` exactly, but the
    # event's `callee_file` points at `svc.py`, where line 2 is the real
    # `Service.process` - matching must stay scoped to the event's own file.
    event = _mystery_event("svc.Caller.run", callee_line=2, callee_file=repo / "svc.py")

    result = GraphReconciler(builder, tag_matrix).reconcile([event])

    assert ("svc.Caller.run", "svc.Service.process") in result.fuzzy_matched_edges
    assert ("svc.Caller.run", "other.Unrelated.process") not in result.fuzzy_matched_edges


def test_fuzzy_match_skipped_when_callee_location_is_missing(tmp_path):
    """An `otel`-sourced event (or any event with no captured source
    location) can't be fuzzy-matched - it must fall back to the ordinary
    unresolved path without crashing."""
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    event = TraceRecord(caller="svc.Caller.run", callee="not.a.real.symbol", source="otel")

    result = GraphReconciler(builder, tag_matrix).reconcile([event])

    assert not result.fuzzy_matched_edges
    assert result.unresolved_events == [event]


def test_fuzzy_match_never_downgrades_an_existing_static_edge(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "class Service:\n"
        "    def process(self, x):\n"   # line 2-3
        "        return x\n"
        "\n"
        "\n"
        "class Caller:\n"
        "    def run(self):\n"
        "        s = Service()\n"       # constructor-instance-binding (Rule A)
        "        return s.process(1)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("svc.Caller.run", "svc.Service.process")
    original_relation = dict(builder.graph.edges["svc.Caller.run", "svc.Service.process"])

    # A fuzzy-match event landing on the very same real, already-static
    # edge must be a no-op on that edge's attributes.
    event = _mystery_event("svc.Caller.run", callee_line=2, callee_file=repo / "svc.py")
    result = GraphReconciler(builder, tag_matrix).reconcile([event])

    assert ("svc.Caller.run", "svc.Service.process") in result.fuzzy_matched_edges
    edge_data = builder.graph.edges["svc.Caller.run", "svc.Service.process"]
    assert edge_data["relation"] == "CALLS"
    assert edge_data.get("kind") != "TENTATIVE_DYNAMIC_CALL"
    assert edge_data.get("provenance") == original_relation.get("provenance")


def test_orphan_resolution_ratio_reflects_fuzzy_matched_fraction(tmp_path):
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    resolved_event = _mystery_event("svc.Caller.run", callee_line=2, callee_file=repo / "svc.py")
    unresolved_event = _mystery_event(
        "svc.Caller.run", callee_line=2 + FUZZY_ANCHOR_LINE_WINDOW + 100, callee_file=repo / "svc.py"
    )

    result = GraphReconciler(builder, tag_matrix).reconcile([resolved_event, unresolved_event])

    assert result.orphan_resolution_ratio == 0.5


def test_orphan_resolution_ratio_is_vacuously_one_with_no_callee_orphans(tmp_path):
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    result = GraphReconciler(builder, tag_matrix).reconcile([])
    assert result.orphan_resolution_ratio == 1.0


def test_merge_result_into_state_accumulates_fuzzy_matched_edges_and_drops_when_exactly_resolved(tmp_path):
    repo = _service_repo(tmp_path)
    builder, tag_matrix = build_pipeline(str(repo))
    edge = ("svc.Caller.run", "svc.Service.process")

    event = _mystery_event("svc.Caller.run", callee_line=2, callee_file=repo / "svc.py")
    result1 = GraphReconciler(builder, tag_matrix).reconcile([event])
    assert edge in result1.fuzzy_matched_edges

    state = merge_result_into_state({}, result1, "run1.jsonl")
    assert list(edge) in state["fuzzy_matched_edges"]

    # A later run resolves the same pair by its real, exact name.
    exact_event = TraceRecord(caller="svc.Caller.run", callee="svc.Service.process", source="pytest_tracer")
    result2 = GraphReconciler(builder, tag_matrix).reconcile([exact_event])
    state = merge_result_into_state(state, result2, "run2.jsonl")

    assert list(edge) not in state["fuzzy_matched_edges"]
    assert list(edge) in state["confirmed_edges"]


def test_end_to_end_tracer_and_reconciler_resolve_a_decorator_wrapped_dispatch(tmp_path):
    """Full pipeline, real `sys.settrace`: a decorator defined in the same
    file as the method it wraps means the frame that actually executes at
    call time is the `wrapper` closure, whose `co_qualname`
    ("deco.<locals>.wrapper") matches no static symbol at all -
    `functools.wraps` copies `__qualname__` onto the *function object*, not
    onto the underlying code object `co_qualname` this tracer reads.

    Pass 1's own definitions query registers nested closures like `wrapper`
    as flat top-level symbols (`svc.wrapper`, not scoped under `svc.deco`
    the way the real Python scope nests it) - a genuine, pre-existing
    static-indexing gap this test incidentally surfaces, not something
    Item 10 is scoped to fix. Fuzzy Anchor Matching's honest job is only
    "find the real static symbol nearest to where this frame actually
    ran" - and the frame *did* really run at `wrapper`'s own line, so
    `svc.wrapper` (distance 0) is the correct nearest-anchor answer, not
    `svc.Service.process` (several lines away) - proving the matcher picks
    the true nearest candidate rather than the one a human might expect.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "import functools\n"
        "\n"
        "\n"
        "def deco(f):\n"
        "    @functools.wraps(f)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        return f(*args, **kwargs)\n"
        "    return wrapper\n"
        "\n"
        "\n"
        "class Service:\n"
        "    @deco\n"
        "    def process(self, x):\n"
        "        return x\n"
        "\n"
        "\n"
        "class Caller:\n"
        "    def run(self):\n"
        "        return Service().process(1)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    # The static resolver never finds this edge - `process` was replaced by
    # `wrapper` at class-definition time.
    assert not builder.graph.has_edge("svc.Caller.run", "svc.Service.process")

    output = tmp_path / "trace.jsonl"
    with Tracer(str(repo), output):
        import sys
        sys.path.insert(0, str(repo))
        try:
            import svc  # noqa: PLC0415
            svc.Caller().run()
        finally:
            sys.path.remove(str(repo))
            sys.modules.pop("svc", None)

    events = load_trace_file(output)
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    assert ("svc.Caller.run", "svc.wrapper") in result.fuzzy_matched_edges
    assert builder.graph.has_edge("svc.Caller.run", "svc.wrapper")
    edge_data = builder.graph.edges["svc.Caller.run", "svc.wrapper"]
    assert edge_data["kind"] == "TENTATIVE_DYNAMIC_CALL"
    assert edge_data["provenance"] == "RUNTIME_FUZZY_MATCHED"
