"""Phase B spike, Approach B ("Frontier Index + Spine Hydration"):
verifies `RenderOptions.two_zone` - an additive, default-off flag added
for the `experiment/noise-filtering-spike` branch, in the real
`prism.surface.renderer` module rather than a copy under
`benchmarks/experiments/`, since it only ever changes behavior when a
caller opts in.

The one invariant that must hold regardless of the spike's own outcome:
`two_zone=False` (the default) leaves every existing caller's rendered
output exactly as before - this is checked directly, not just implied
by "the code path is unchanged".
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from tests.surface.test_renderer_unit import _minimal_pkg, _seed_node
from prism.surface.renderer import RenderOptions, render


def test_two_zone_default_off_is_byte_identical_to_explicit_false():
    pkg = _minimal_pkg(nodes=[_seed_node()])
    default_xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
    explicit_false_xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False, two_zone=False))
    assert default_xml == explicit_false_xml
    assert "<nodes>" in default_xml
    assert "<ReachableFrontier" not in default_xml
    assert "<HydratedSpine" not in default_xml


def test_two_zone_true_produces_valid_xml_with_both_zones():
    pkg = _minimal_pkg(nodes=[_seed_node()])
    options = RenderOptions(
        include_timestamp=False,
        include_run_id=False,
        two_zone=True,
        frontier_index=[
            {"id": "svc.helper_b", "role": "callee", "kind": "function"},
            {"id": "svc.HelperClass", "role": "callee", "kind": "class"},
        ],
    )
    xml = render(pkg, options)
    # Valid, well-formed XML - not just substring matches.
    root = ET.fromstring(xml)
    frontier = root.find("ReachableFrontier")
    spine = root.find("HydratedSpine")
    assert frontier is not None
    assert spine is not None
    assert root.find("nodes") is None  # the single-zone <nodes> element is absent, not merely renamed
    frontier_ids = {s.get("id") for s in frontier.findall("symbol")}
    assert frontier_ids == {"svc.helper_b", "svc.HelperClass"}
    spine_ids = {n.get("id") for n in spine.findall("node")}
    assert spine_ids == {"svc.foo"}  # the seed node from _seed_node(), unchanged from single-zone rendering


def test_two_zone_frontier_entries_are_sorted_by_id_for_determinism():
    pkg = _minimal_pkg(nodes=[_seed_node()])
    options = RenderOptions(
        include_timestamp=False, include_run_id=False, two_zone=True,
        frontier_index=[{"id": "z.last", "role": "callee", "kind": "function"}, {"id": "a.first", "role": "callee", "kind": "function"}],
    )
    xml = render(pkg, options)
    assert xml.index('id="a.first"') < xml.index('id="z.last"')


def test_two_zone_empty_frontier_renders_self_closing_element():
    pkg = _minimal_pkg(nodes=[_seed_node()])
    options = RenderOptions(include_timestamp=False, include_run_id=False, two_zone=True, frontier_index=[])
    xml = render(pkg, options)
    assert "<ReachableFrontier/>" in xml


def test_two_zone_does_not_change_trailer_node_count():
    """`<trailer node_count=...>` reflects `len(pkg.nodes)` (the spine) -
    the frontier index is explicitly not counted as packed nodes, since
    it was never admitted by the knapsack at all."""
    pkg = _minimal_pkg(nodes=[_seed_node()])
    single_zone_xml = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
    two_zone_xml = render(
        pkg,
        RenderOptions(
            include_timestamp=False, include_run_id=False, two_zone=True,
            frontier_index=[{"id": "extra.one", "role": "callee", "kind": "function"}, {"id": "extra.two", "role": "callee", "kind": "function"}],
        ),
    )
    single_root = ET.fromstring(single_zone_xml)
    two_zone_root = ET.fromstring(two_zone_xml)
    assert single_root.find("trailer").get("node_count") == two_zone_root.find("trailer").get("node_count") == "1"
