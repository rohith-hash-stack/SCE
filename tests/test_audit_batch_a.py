"""Tests for audit Issues #5 (Go query grammar), #14/#15 (orphan
classification + RuntimeTrust), and #16 (property descriptors /
#dynamic_attribute).
"""
from __future__ import annotations

import tempfile

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable, SymbolInfo
from prism.parser.queries import get_query, run_query
from prism.parser.tree_sitter_loader import LanguageID, parse_file
from prism.runtime.reconciler import (
    RUNTIME_TRUST_HIGH_THRESHOLD,
    RUNTIME_TRUST_LOW_THRESHOLD,
    GraphReconciler,
    OrphanReason,
    classify_orphan,
    merge_result_into_state,
)
from prism.runtime.tracer import TraceRecord
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.graph.metamodel import SemanticMetamodel


# --------------------------------------------------------------------- #
# Issue #5: Go decorator/annotation query
# --------------------------------------------------------------------- #
def _write_go(tmp_path, src: str):
    path = tmp_path / "x.go"
    path.write_text(src)
    return parse_file(str(path))


def test_go_decorators_query_compiles() -> None:
    assert get_query(LanguageID.GO, "decorators") is not None


def test_go_directive_comment_and_field_tag_captured(tmp_path) -> None:
    parsed = _write_go(
        tmp_path,
        'package main\n\n//go:generate mockgen -source=foo.go\n\n'
        'type User struct {\n\tName string `json:"name"`\n}\n\n'
        'func main() {\n\t// plain comment, not a directive\n}\n',
    )
    captures = run_query(LanguageID.GO, "decorators", parsed.root_node)
    texts = {parsed.source[n.start_byte:n.end_byte].decode() for n in captures.get("decorator", [])}
    assert "//go:generate mockgen -source=foo.go" in texts
    assert '`json:"name"`' in texts
    assert not any("plain comment" in t for t in texts)


# --------------------------------------------------------------------- #
# Issue #14/#15: Orphan classification + RuntimeTrust
# --------------------------------------------------------------------- #
def _builder_with_symbol(qname="sample.f") -> ConcreteGraphBuilder:
    table = GlobalSymbolTable()
    table.add(SymbolInfo(qualified_name=qname, kind="function", file="sample.py", line_range=(1, 1), language_id="python", module="sample"))
    builder = ConcreteGraphBuilder("/tmp/repo", table)
    builder.graph.add_node(qname)
    return builder


def test_classify_orphan_no_static_match_when_caller_none() -> None:
    builder = _builder_with_symbol()
    event = TraceRecord(caller=None, callee=None, source="otel", sink_tag="#external_io")
    assert classify_orphan(event, builder) is OrphanReason.NO_STATIC_MATCH


def test_classify_orphan_anonymous_closure() -> None:
    builder = _builder_with_symbol()
    event = TraceRecord(caller="sample.f", callee="sample.<lambda>", source="otel")
    assert classify_orphan(event, builder) is OrphanReason.ANONYMOUS_CLOSURE


def test_classify_orphan_ambiguous_signature_when_simple_name_matches() -> None:
    builder = _builder_with_symbol(qname="sample.mod_a.validate")
    builder.symbol_table.add(SymbolInfo(qualified_name="sample.mod_b.validate", kind="function", file="b.py", line_range=(1, 1), language_id="python", module="sample.mod_b"))
    event = TraceRecord(caller="sample.f", callee="sample.mod_c.validate", source="pytest_tracer")
    assert classify_orphan(event, builder) is OrphanReason.AMBIGUOUS_SIGNATURE


def test_classify_orphan_native_or_c_extension_for_otel_unknown() -> None:
    builder = _builder_with_symbol()
    event = TraceRecord(caller="sample.f", callee="numpy.core.multiarray.dot", source="otel")
    assert classify_orphan(event, builder) is OrphanReason.NATIVE_OR_C_EXTENSION


def test_classify_orphan_dynamic_dispatch_for_pytest_unknown() -> None:
    builder = _builder_with_symbol()
    event = TraceRecord(caller="sample.f", callee="sample.injected_method", source="pytest_tracer")
    assert classify_orphan(event, builder) is OrphanReason.DYNAMIC_DISPATCH


def test_reconcile_zero_silent_orphans_every_event_classified() -> None:
    """Invariant #6: every unresolved event is accounted for under exactly
    one OrphanReason - orphan_reasons' total count equals len(unresolved_events)."""
    builder = _builder_with_symbol()
    tag_matrix: dict[str, set[str]] = {}
    events = [
        TraceRecord(caller=None, callee=None, source="otel", sink_tag="#external_io"),
        TraceRecord(caller="sample.f", callee="sample.<lambda>", source="otel"),
        TraceRecord(caller="sample.f", callee="numpy.dot", source="otel"),
        TraceRecord(caller="sample.f", callee="sample.injected", source="pytest_tracer"),
        TraceRecord(caller="sample.f", callee="sample.f", source="pytest_tracer"),  # resolves fine
    ]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    assert sum(result.orphan_reasons.values()) == len(result.unresolved_events)
    assert len(result.unresolved_events) == 4


def test_dynamic_dispatch_orphan_tags_resolvable_caller() -> None:
    builder = _builder_with_symbol()
    tag_matrix: dict[str, set[str]] = {}
    events = [TraceRecord(caller="sample.f", callee="sample.injected_thing", source="pytest_tracer")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    assert "sample.f" in result.dynamic_tagged_symbols
    assert "#dynamic" in tag_matrix["sample.f"]


def test_runtime_trust_score_and_low_trust_warning() -> None:
    builder = _builder_with_symbol()
    tag_matrix: dict[str, set[str]] = {}
    events = [TraceRecord(caller="sample.f", callee="sample.unknown", source="pytest_tracer") for _ in range(9)]
    events.append(TraceRecord(caller="sample.f", callee="sample.f", source="pytest_tracer"))
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    assert result.trust_score == 0.1
    assert result.trust_score < RUNTIME_TRUST_LOW_THRESHOLD
    assert result.trust_warning is not None


def test_runtime_trust_no_events_is_vacuously_trustworthy() -> None:
    builder = _builder_with_symbol()
    result = GraphReconciler(builder, {}).reconcile([])
    assert result.trust_score == 1.0
    assert result.trust_warning is None


def test_high_trust_confirms_boost_distance_discount() -> None:
    """RuntimeTrust >= 0.9 marks confirmed edges high_trust_runtime=True,
    and DistanceEngine gives those an even smaller hop cost than a normal
    confirmed edge."""
    table = GlobalSymbolTable()
    for name in ("sample.a", "sample.b"):
        table.add(SymbolInfo(qualified_name=name, kind="function", file="sample.py", line_range=(1, 1), language_id="python", module="sample"))
    builder = ConcreteGraphBuilder("/tmp/repo", table)
    builder.graph.add_edge("sample.a", "sample.b", relation="CALLS")
    tag_matrix: dict[str, set[str]] = {}
    events = [TraceRecord(caller="sample.a", callee="sample.b", source="pytest_tracer")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    assert result.trust_score >= RUNTIME_TRUST_HIGH_THRESHOLD
    assert builder.graph.edges["sample.a", "sample.b"]["high_trust_runtime"] is True

    metamodel = SemanticMetamodel()
    engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    undirected = engine._weighted_undirected(builder.calls_graph)
    weight = undirected.get_edge_data("sample.a", "sample.b")["weight"]
    assert weight == DistanceConfig().runtime_confidence_weight * DistanceConfig().high_trust_extra_discount
    assert weight < DistanceConfig().runtime_confidence_weight


def test_merge_result_into_state_persists_orphan_and_trust_fields() -> None:
    builder = _builder_with_symbol()
    tag_matrix: dict[str, set[str]] = {}
    events = [TraceRecord(caller="sample.f", callee="sample.injected", source="pytest_tracer")]
    result = GraphReconciler(builder, tag_matrix).reconcile(events)
    state = merge_result_into_state({}, result, "run_1.jsonl")
    assert state["orphan_reasons"].get(OrphanReason.DYNAMIC_DISPATCH.value) == 1
    assert state["trust_score"] == result.trust_score
    assert "sample.f" in state["dynamic_tagged_symbols"]


# --------------------------------------------------------------------- #
# Issue #16: property descriptors + #dynamic_attribute
# --------------------------------------------------------------------- #
def test_property_and_cached_property_get_property_tag(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "from functools import cached_property\n"
        "\n"
        "class Model:\n"
        "    @property\n"
        "    def full_name(self):\n"
        "        return self.first\n"
        "\n"
        "    @cached_property\n"
        "    def cache_key(self):\n"
        "        return 1\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#property" in tag_matrix["sample.Model.full_name"]
    assert "#property" in tag_matrix["sample.Model.cache_key"]


def test_property_access_still_resolves_via_reads_state(tmp_path) -> None:
    repo = tmp_path / "repo2"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "class Model:\n"
        "    @property\n"
        "    def full_name(self):\n"
        "        return self.first\n"
        "\n"
        "    def render(self):\n"
        "        return self.full_name\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("sample.Model.render", "sample.Model.full_name")
    assert builder.graph.edges["sample.Model.render", "sample.Model.full_name"]["relation"] == "READS_STATE"


def test_setattr_marks_dynamic_attribute(tmp_path) -> None:
    repo = tmp_path / "repo3"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "class Dynamic:\n"
        "    def configure(self, name, value):\n"
        "        setattr(self, name, value)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#dynamic_attribute" in tag_matrix["sample.Dynamic.configure"]


def test_dunder_dict_subscript_marks_dynamic_attribute(tmp_path) -> None:
    repo = tmp_path / "repo4"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "class Dynamic:\n"
        "    def configure(self, name, value):\n"
        "        self.__dict__[name] = value\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#dynamic_attribute" in tag_matrix["sample.Dynamic.configure"]


def test_ordinary_self_assignment_does_not_get_dynamic_attribute_tag(tmp_path) -> None:
    repo = tmp_path / "repo5"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "class Plain:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#dynamic_attribute" not in tag_matrix.get("sample.Plain.__init__", set())
