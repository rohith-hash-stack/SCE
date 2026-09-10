"""Unit tests for `prism.surface.renderer.render` - each of the 7
top-level blocks in isolation, CDATA `]]>` splitting, Unicode content,
and the empty/oversized package edge cases.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageGap,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    EnvelopeWarning,
    FeatureCoverage,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeContract,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    NodeSignatureParam,
    NodeSignatureReturn,
    SeedRef,
)
from prism.surface.renderer import RenderOptions, render


def _minimal_pkg(**overrides) -> ContextPackage:
    base = dict(
        engine=EngineRef(name="prism-causal", version="0.2.0", commit="abc123"),
        seed=SeedRef(symbol="svc.foo", file="svc.py", line=1),
        budget=BudgetRef(tokens=4000, tokenizer="cl100k_base", exact=True),
        language=LanguageRef(tier="1", primary="python", files=1),
        manifest=Manifest(
            packed_nodes=0, considered_nodes=0, reachable_nodes=0, compression=[],
            distance_metric=ManifestDistanceMetric(name="causal_dijkstra", lambda_data_flow=0.25, lambda_guard=0.15, dist_max=6.0),
        ),
        coverage=CoverageSummary(total_features=0, covered_features=0, omitted_features=0, features=[]),
        nodes=[],
        edges=[],
    )
    base.update(overrides)
    return ContextPackage(**base)


def _seed_node(**overrides) -> NodeEntry:
    base = dict(
        id="svc.foo", role="seed", distance=0.0, compression="L0_full", cost=5,
        symbol_name="foo", symbol_kind="function", language="python", file="svc.py", line=1, end_line=2,
        signature=NodeSignature(), features=NodeFeatures(substance="PURE_COMPUTE", form="LINEAR", output="QUERY", role="NONE"),
        body="def foo():\n    return 1\n",
    )
    base.update(overrides)
    return NodeEntry(**base)


# --------------------------------------------------------------------- #
# Every block in isolation
# --------------------------------------------------------------------- #
def test_metadata_block_renders_engine_seed_budget_language_options():
    pkg = _minimal_pkg(options={"engine": "prism-causal", "max_hops": "6.0"})
    xml = render(pkg)
    assert '<engine commit="abc123" name="prism-causal" version="0.2.0"/>' in xml
    assert '<seed file="svc.py" line="1" symbol="svc.foo"/>' in xml
    assert '<budget exact="true" tokenizer="cl100k_base" tokens="4000"/>' in xml
    assert '<language files="1" primary="python" tier="1"/>' in xml
    assert '<option key="engine" value="prism-causal"/>' in xml
    assert '<option key="max_hops" value="6.0"/>' in xml


def test_manifest_block_renders_compression_breakdown_and_distance_metric():
    pkg = _minimal_pkg(
        manifest=Manifest(
            packed_nodes=3, considered_nodes=10, reachable_nodes=8,
            compression=[ManifestCompression(level="L0_full", count=2), ManifestCompression(level="L2_skeleton", count=1)],
            distance_metric=ManifestDistanceMetric(name="causal_dijkstra", lambda_data_flow=0.25, lambda_guard=0.15, dist_max=6.0),
        )
    )
    xml = render(pkg)
    assert '<manifest considered_nodes="10" packed_nodes="3" reachable_nodes="8">' in xml
    assert '<compression count="2" level="L0_full"/>' in xml
    assert '<compression count="1" level="L2_skeleton"/>' in xml
    assert '<distance_metric dist_max="6.0" lambda_data_flow="0.25" lambda_guard="0.15" name="causal_dijkstra"/>' in xml


def test_coverage_block_renders_features_and_gaps():
    pkg = _minimal_pkg(
        coverage=CoverageSummary(
            total_features=2, covered_features=1, omitted_features=1,
            features=[FeatureCoverage(id="SINK_NETWORK_IO", present=True, count=2), FeatureCoverage(id="SINK_DATABASE_IO", present=False, count=0)],
            gaps=[CoverageGap(feature="SINK_DATABASE_IO", reason="no_candidate_in_budget")],
        )
    )
    xml = render(pkg)
    assert '<coverage covered_features="1" omitted_features="1" total_features="2">' in xml
    assert '<feature count="2" id="SINK_NETWORK_IO" present="true"/>' in xml
    assert '<feature count="0" id="SINK_DATABASE_IO" present="false"/>' in xml
    assert '<gap feature="SINK_DATABASE_IO" reason="no_candidate_in_budget"/>' in xml


def test_warnings_block_renders_message_and_typed_details():
    pkg = _minimal_pkg(
        warnings=[
            EnvelopeWarning(code="GRAPH_INCOMPLETE", severity="medium", message="one file failed to parse", details={"file": "bad.py", "line": 12}),
        ]
    )
    xml = render(pkg)
    assert '<warning code="GRAPH_INCOMPLETE" severity="medium">' in xml
    assert "<message>one file failed to parse</message>" in xml
    assert '<detail key="file" type="str" value="bad.py"/>' in xml
    assert '<detail key="line" type="int" value="12"/>' in xml


def test_nodes_block_renders_signature_features_contract_body():
    node = _seed_node(
        role="callee", distance=1.0,
        signature=NodeSignature(params=[NodeSignatureParam(name="x", type="int", optional=True)], returns=NodeSignatureReturn(type="bool", kind="Predicate")),
        contract=NodeContract(target_id="svc.bar", call_line=7, unpacks="y", passes_args="x, 1"),
    )
    pkg = _minimal_pkg(nodes=[node])
    xml = render(pkg)
    assert '<param name="x" optional="true" type="int"/>' in xml
    assert '<returns kind="Predicate" type="bool"/>' in xml
    assert '<features form="LINEAR" output="QUERY" role="NONE" substance="PURE_COMPUTE"/>' in xml
    assert '<contract call_line="7" passes_args="x, 1" target_id="svc.bar" unpacks="y"/>' in xml
    assert "<![CDATA[def foo():" in xml


def test_edges_block_renders_all_fields():
    pkg = _minimal_pkg(
        edges=[EdgeEntry(from_node="svc.a", to_node="svc.b", type="CALLS", weight=1.25, data_flow=True, guard=False, back_edge=True)]
    )
    xml = render(pkg)
    assert '<edge back_edge="true" data_flow="true" from="svc.a" guard="false" to="svc.b" type="CALLS" weight="1.25"/>' in xml


def test_trailer_block_has_sha256_and_counts():
    pkg = _minimal_pkg(nodes=[_seed_node()], edges=[])
    xml = render(pkg)
    trailer = xml.split("<trailer")[1].split("/>")[0]
    assert 'node_count="1"' in trailer
    assert 'edge_count="0"' in trailer
    assert "sha256=" in trailer
    assert "token_count=" in trailer


# --------------------------------------------------------------------- #
# CDATA ]]> splitting
# --------------------------------------------------------------------- #
def test_cdata_splits_embedded_close_sequence_losslessly():
    body = "x = 1\nif x[0:2] == 'ab]]>cd':\n    pass\n"
    node = _seed_node(body=body)
    pkg = _minimal_pkg(nodes=[node])
    xml = render(pkg)
    # never dropped to unescaped text, never crashed
    ET.fromstring(xml)
    assert "]]]]><![CDATA[>" in xml
    # and the parser can recover the exact original text
    from prism.surface.parser import parse_context

    parsed = parse_context(xml)
    assert parsed.nodes[0].body == body


def test_cdata_handles_multiple_close_sequences_in_one_body():
    body = "]]>first]]>second]]>third"
    node = _seed_node(body=body)
    pkg = _minimal_pkg(nodes=[node])
    xml = render(pkg)
    ET.fromstring(xml)
    from prism.surface.parser import parse_context

    assert parse_context(xml).nodes[0].body == body


# --------------------------------------------------------------------- #
# Unicode: CJK, emoji, RTL
# --------------------------------------------------------------------- #
def test_unicode_cjk_emoji_rtl_roundtrip_through_body_and_attributes():
    body = "def foo():\n    return '日本語のコメント 🎉 مرحبا بالعالم'\n"
    node = _seed_node(body=body, symbol_name="日本語_fn")
    pkg = _minimal_pkg(
        nodes=[node],
        warnings=[EnvelopeWarning(code="GRAPH_INCOMPLETE", severity="low", message="部分的な解析 🎉 مرحبا")],
    )
    xml = render(pkg)
    ET.fromstring(xml)
    assert "日本語のコメント 🎉 مرحبا بالعالم" in xml
    assert "部分的な解析 🎉 مرحبا" in xml

    from prism.surface.parser import parse_context

    parsed = parse_context(xml)
    assert parsed.nodes[0].body == body
    assert parsed.nodes[0].symbol_name == "日本語_fn"
    assert parsed.warnings[0].message == "部分的な解析 🎉 مرحبا"


def test_xml_special_characters_in_attributes_are_escaped():
    node = _seed_node(symbol_name='a<b>c&d"e')
    pkg = _minimal_pkg(nodes=[node])
    xml = render(pkg)
    ET.fromstring(xml)  # would raise on unescaped < > & "
    from prism.surface.parser import parse_context

    assert parse_context(xml).nodes[0].symbol_name == 'a<b>c&d"e'


# --------------------------------------------------------------------- #
# Empty (seed only) and oversized (budget overflow) packages
# --------------------------------------------------------------------- #
def test_empty_package_seed_only_renders_empty_nodes_and_edges():
    pkg = _minimal_pkg()
    xml = render(pkg)
    ET.fromstring(xml)
    assert "<nodes/>" in xml
    assert "<edges/>" in xml
    assert "<warnings/>" in xml


def test_oversized_package_auto_appends_budget_overflow_warning():
    big_body = "x = 1\n" * 2000
    node = _seed_node(body=big_body)
    pkg = _minimal_pkg(budget=BudgetRef(tokens=10, tokenizer="cl100k_base", exact=True), nodes=[node])
    xml = render(pkg)
    ET.fromstring(xml)
    assert 'code="BUDGET_OVERFLOW"' in xml
    assert 'severity="high"' in xml


def test_oversized_package_does_not_duplicate_an_existing_budget_overflow_warning():
    big_body = "x = 1\n" * 2000
    node = _seed_node(body=big_body)
    pkg = _minimal_pkg(
        budget=BudgetRef(tokens=10, tokenizer="cl100k_base", exact=True),
        nodes=[node],
        warnings=[EnvelopeWarning(code="BUDGET_OVERFLOW", severity="high", message="already flagged upstream")],
    )
    xml = render(pkg)
    assert xml.count('code="BUDGET_OVERFLOW"') == 1
    assert "already flagged upstream" in xml


# --------------------------------------------------------------------- #
# RenderOptions mechanics
# --------------------------------------------------------------------- #
def test_generated_at_and_run_id_omitted_by_default():
    pkg = _minimal_pkg(run_id="run-1", generated_at="2026-01-01T00:00:00Z")
    xml = render(pkg)
    assert "run_id" not in xml
    assert "generated_at" not in xml


def test_generated_at_and_run_id_included_when_requested():
    pkg = _minimal_pkg(run_id="run-1", generated_at="2026-01-01T00:00:00Z")
    xml = render(pkg, RenderOptions(include_timestamp=True, include_run_id=True))
    assert 'run_id="run-1"' in xml
    assert 'generated_at="2026-01-01T00:00:00Z"' in xml


def test_include_bodies_false_omits_body_element():
    pkg = _minimal_pkg(nodes=[_seed_node()])
    xml = render(pkg, RenderOptions(include_bodies=False))
    assert "<body>" not in xml
    assert "CDATA" not in xml


def test_max_body_lines_truncates_body():
    body = "\n".join(f"line{i}" for i in range(10))
    node = _seed_node(body=body)
    pkg = _minimal_pkg(nodes=[node])
    xml = render(pkg, RenderOptions(max_body_lines=3))
    assert "line0" in xml and "line1" in xml and "line2" in xml
    assert "line5" not in xml


def test_document_ends_with_exactly_one_trailing_newline():
    pkg = _minimal_pkg()
    xml = render(pkg)
    assert xml.endswith("\n")
    assert not xml.endswith("\n\n")


def test_attributes_sorted_alphabetically_within_every_element():
    pkg = _minimal_pkg(edges=[EdgeEntry(from_node="svc.a", to_node="svc.b", type="CALLS", weight=1.0, data_flow=False, guard=False)])
    xml = render(pkg)
    edge_line = [line for line in xml.splitlines() if "<edge " in line][0]
    # back_edge, data_flow, from, guard, to, type, weight
    assert edge_line.index("back_edge") < edge_line.index("data_flow") < edge_line.index("from=") < edge_line.index("guard") < edge_line.index("to=") < edge_line.index("type") < edge_line.index("weight")
