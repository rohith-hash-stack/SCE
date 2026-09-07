"""Regression tests for the D_hybrid distance metric and the metamodel's
handling of untagged nodes in tag-distance computation.
"""
from __future__ import annotations

import networkx as nx

from sce.graph.metamodel import MAX_TAG_DISTANCE, UNTAGGED_TAG_DISTANCE, SemanticMetamodel
from sce.slicer.distance import DistanceConfig, DistanceEngine


def test_untagged_distance_is_less_than_confirmed_max_distance():
    """An untagged node means 'no semantic signal', not a confirmed
    maximal semantic gap - it must carry a smaller penalty than two known,
    genuinely unrelated tags with no path between them in G_T."""
    assert 0 < UNTAGGED_TAG_DISTANCE < MAX_TAG_DISTANCE


def test_get_tag_distance_untagged_vs_shared():
    model = SemanticMetamodel()
    assert model.get_tag_distance(set(), {"#auth_guard"}) == UNTAGGED_TAG_DISTANCE
    assert model.get_tag_distance(set(), set()) == UNTAGGED_TAG_DISTANCE
    assert model.get_tag_distance({"#auth_guard"}, {"#auth_guard"}) == 0.0


def test_direct_callee_beats_a_same_tagged_but_structurally_distant_sibling():
    """Regression test for a real bug caught via `benchmarks/multi_repo_eval.py`
    against a live clone of encode/httpx: `Client.send`'s genuine, untagged
    1-hop direct callee (`_send_handling_auth`) scored *worse* in D_hybrid
    than an unrelated 4-hop-distant sibling method (`AsyncClient.send`)
    purely because that sibling happened to share a tag with the seed -
    dropping a real direct callee from a tight token budget. Reproduces the
    same graph shape: a 1-hop untagged callee vs. a 4-hop same-tagged
    sibling reached only through unrelated bridge nodes.
    """
    g = nx.DiGraph()
    g.add_edge("seed", "direct_callee")  # genuine 1-hop callee, untagged
    g.add_edge("bridge1", "seed")
    g.add_edge("bridge1", "bridge2")
    g.add_edge("bridge2", "bridge3")
    g.add_edge("bridge3", "distant_sibling")  # 4 undirected hops away, tagged like seed

    tag_matrix = {"seed": {"#state_mutation"}, "distant_sibling": {"#state_mutation"}}
    engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    distances = engine.compute_all("seed", g)

    assert distances["direct_callee"] < distances["distant_sibling"], (
        f"direct_callee={distances['direct_callee']}, distant_sibling={distances['distant_sibling']}"
    )
