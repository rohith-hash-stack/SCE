"""Prototype P1 (harness/experiments/retrieval_pivot/p1.py): arms, re-pricing and metrics."""
from __future__ import annotations

import prism.packer.submodular_knapsack as K
from harness.experiments.query_to_evidence.run import build_engine
from harness.experiments.retrieval_pivot import p1

SRC = '''def leaf(x):
    """Doubles a value."""
    y = x * 2
    y = y + 1
    y = y - 1
    return y


def mid(x):
    a = leaf(x)
    b = leaf(a)
    return a + b


def seed(x):
    return mid(x) + 1


def caller(x):
    return seed(x)


def grand(x):
    return caller(x)


def great(x):
    return grand(x)
'''


def _engine(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app/m.py").write_text(SRC)
    return build_engine(str(tmp_path))


def test_arm_representations():
    assert [p1.arm_rep("A0", r) for r in (0, 1, 2)] == ["full", "full", "full"]
    assert [p1.arm_rep("A1", r) for r in (0, 1, 2)] == ["full", "full", "sig+sum"]
    assert [p1.arm_rep("A1n", r) for r in (1, 3)] == ["full", "sig"]
    assert [p1.arm_rep("A2", r) for r in (1, 2)] == ["sig+sum", "sig+sum"]
    assert [p1.arm_rep("A3", r) for r in (1, 2)] == ["full+sum", "sig+sum"]
    assert [p1.arm_rep("A4", r) for r in (0, 1, 2)] == ["full", "full+prof", "sig"]
    assert [p1.arm_rep("A5", r) for r in (1, 2)] == ["full+prof", "sig+prof"]


def test_rings_and_rendering_and_the_cost_patch_is_restored(tmp_path):
    e = _engine(tmp_path)
    ring = p1.rings(e.builder, "app.m.seed")
    assert ring["app.m.mid"] == 1 and ring["app.m.caller"] == 1 and ring["app.m.leaf"] == 2
    r = p1.Renderer(e.builder, e._contracts, {"app.m.leaf": "Doubles its input."})
    full, rep_full = r.render("app.m.leaf", "full")
    small, rep_small = r.render("app.m.leaf", "sig+sum")
    assert rep_full == "full" and "y = y + 1" in full
    assert rep_small == "sig+sum" and "y = y + 1" not in small and "[summary] Doubles its input." in small
    assert r.render("app.m.mid", "sig+sum")[1] == "sig"  # no summary: the summary part is dropped
    original = K._default_costs
    with p1.repriced(r, "app.m.seed", "A1", ring):
        assert K._default_costs is not original
    assert K._default_costs is original


def test_a_case_reports_gold_tokens_and_representations(tmp_path):
    e = _engine(tmp_path)
    case = {"id": "t/1", "corpus": "t", "seed": "app.m.seed", "gold_nodes": ["app.m.leaf", "app.m.mid"],
            "gold_edges": [("app.m.mid", "app.m.leaf")]}
    rows = p1.run_case(case, e, {"app.m.leaf": "Doubles its input."}, arms=("A0", "A1"), budgets=(2000,))
    a0, a1 = rows
    assert a0["gold_present"] == a1["gold_present"] == 2 and a0["edges_both_present"] == 1
    assert a0["gold_full"] == 2 and a1["gold_full"] == 1 and a1["gold_with_summary"] == 1
    assert a1["tokens"] < a0["tokens"] and a1["summary_tokens"] > 0 and a0["summary_tokens"] == 0
    assert a0["gold_reachable"] == 2


def test_the_profile_names_the_axes_and_the_anchor_link(tmp_path):
    from prism.semantics.bitmask import FeatureBit
    assert p1.axis_names(FeatureBit.SINK_PURE_COMPUTE | FeatureBit.FORM_LINEAR | FeatureBit.ROLE_LEAF_UTILITY) == {
        "substance": ["pure compute"], "form": ["linear"], "role": ["leaf utility"]}
    e = _engine(tmp_path)
    r = p1.Renderer(e.builder, e._contracts, {}, seed="app.m.seed")
    text, rep = r.render("app.m.mid", "full+prof")
    assert rep == "full+prof" and "[profile] " in text and "substance:" in text and "a = leaf(x)" in text


def test_relaxed_reach_keeps_the_production_pack_first_and_adds_farther_callers(tmp_path):
    e = _engine(tmp_path)
    r0 = [i.symbol for i in p1.pack_reach(e.builder, e._contracts, "app.m.seed", 4000, "R0")]
    r1 = p1.pack_reach(e.builder, e._contracts, "app.m.seed", 4000, "R1")
    assert [i.symbol for i in r1][:len(r0)] == r0  # priority preserved
    assert "app.m.grand" not in r0 and "app.m.grand" in {i.symbol for i in r1}
    assert sum(i.cost for i in r1) <= 4000
