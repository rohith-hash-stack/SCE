"""Phase B: graph resolution layer (G40 inherited-method resolution, G41
attribute-chain disambiguation, G44 tentative fallback, data-flow wiring).

Import-time note: `prism.graph.weights` is a theoretical reference table,
NOT the live traversal cost model - see that module's own docstring. Tests
against it (test_w_tentative_dominance_bound) verify the algebraic
derivation only; tests against real Dijkstra behavior
(test_tentative_call_loses_to_one_confident_hop) exercise the actual,
already-wired `prism.slicer.distance` cost model instead.
"""
from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.weights import (
    EDGE_WEIGHTS,
    MAX_EDGE_COST,
    MAX_INHERITANCE_DEPTH,
    W_TENTATIVE,
)


# ============================================================
# G44 dominance derivation (reference table only)
# ============================================================

def test_w_tentative_dominance_bound():
    """W_TENTATIVE, as derived in prism.graph.weights, strictly exceeds
    the worst-case structural path cost over every edge weight the
    reference table defines - a pure algebraic property of the constants
    themselves, independent of whether any real edge is currently priced
    this way (see module docstring: it isn't, yet)."""
    worst_case_structural_path = MAX_INHERITANCE_DEPTH * MAX_EDGE_COST
    assert worst_case_structural_path == 12.5
    assert W_TENTATIVE > worst_case_structural_path
    assert W_TENTATIVE == 15.0
    # Re-derive MAX_EDGE_COST independently from every weight in the
    # table, rather than trusting the module's own computation - this is
    # the "against all possible edge weights" check.
    recomputed_max = max(1.0 / w for w in EDGE_WEIGHTS.values())
    assert recomputed_max == MAX_EDGE_COST


# ============================================================
# G40: inherited-method resolution (hardened _mro_ancestors, live path)
# ============================================================

def test_inherited_method_resolves_single_level(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class Base:\n"
        "    def greet(self):\n"
        "        return 'hi'\n"
        "\n"
        "class Derived(Base):\n"
        "    def run(self):\n"
        "        return self.greet()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("mod.Derived.run", "mod.Base.greet")


def test_deep_mixin_chain_resolves_depth_8(tmp_path):
    """8 stacked mixins (M8..M1 each extending the next), M1 extends the
    Root which defines the method; Caller extends M8. Root is 9 EXTENDS
    hops from Caller - within MAX_INHERITANCE_DEPTH (10) - so self.target()
    must still resolve to Root.target."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["class Root:", "    def target(self):", "        return 'ok'", ""]
    lines += ["class M1(Root):", "    pass", ""]
    for i in range(2, 9):
        lines += [f"class M{i}(M{i - 1}):", "    pass", ""]
    lines += ["class Caller(M8):", "    def run(self):", "        return self.target()"]
    (repo / "mod.py").write_text("\n".join(lines) + "\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("mod.Caller.run", "mod.Root.target")


def test_inheritance_depth_cap_exceeded(tmp_path):
    """The same shape as the depth-8 test, but 3 levels deeper: Root is
    now 12 EXTENDS hops from Caller, past MAX_INHERITANCE_DEPTH (10) - the
    method must NOT resolve to Root.target (rejected, not found)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["class Root:", "    def target(self):", "        return 'ok'", ""]
    lines += ["class M1(Root):", "    pass", ""]
    for i in range(2, 12):
        lines += [f"class M{i}(M{i - 1}):", "    pass", ""]
    lines += ["class Caller(M11):", "    def run(self):", "        return self.target()"]
    (repo / "mod.py").write_text("\n".join(lines) + "\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    assert not builder.graph.has_edge("mod.Caller.run", "mod.Root.target")
    # Confirmed rejected, not silently resolved to some other real symbol:
    # the unresolved "Caller.target" name (never actually defined on
    # Caller) is not itself in the symbol table.
    assert "mod.Caller.target" not in builder.symbol_table


def test_inheritance_circular_reference_terminates():
    """A synthetic cyclic EXTENDS graph (A extends B extends A) - can't
    arise from real Python source (B must exist before `class A(B)` can
    reference it), but a hand-built graph can construct one; _mro_ancestors
    must terminate rather than recurse forever."""
    builder = ConcreteGraphBuilder("/tmp/repo")
    builder.graph.add_node("m.A")
    builder.graph.add_node("m.B")
    builder.graph.add_edge("m.A", "m.B", relation="EXTENDS")
    builder.graph.add_edge("m.B", "m.A", relation="EXTENDS")
    ancestors = builder._mro_ancestors("m.A")
    assert ancestors == ["m.B"]


def test_diamond_inheritance_deterministic_order(tmp_path):
    """D(B, C) where both B and C extend A: A must appear exactly once,
    and sibling order must be canonical (file, line, name) - B is
    declared before C in the source, so B's branch (and therefore A) is
    explored before C's."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class A:\n"
        "    def m(self):\n"
        "        pass\n"
        "\n"
        "class B(A):\n"
        "    pass\n"
        "\n"
        "class C(A):\n"
        "    pass\n"
        "\n"
        "class D(B, C):\n"
        "    pass\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    ancestors = builder._mro_ancestors("mod.D")
    assert ancestors == ["mod.B", "mod.A", "mod.C"]
    assert ancestors.count("mod.A") == 1


# ============================================================
# G41: attribute-chain disambiguation
# ============================================================

def test_attribute_chain_resolved_via_init_binding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class Service:\n"
        "    def call(self):\n"
        "        return 'ok'\n"
        "\n"
        "class Client:\n"
        "    def __init__(self):\n"
        "        self.service = Service()\n"
        "    def run(self):\n"
        "        return self.service.call()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("mod.Client.run", "mod.Service.call")
    edge = builder.graph.get_edge_data("mod.Client.run", "mod.Service.call")
    assert edge.get("kind") is None  # clean single-type resolution, full confidence


def test_attribute_chain_resolved_via_type_annotation(tmp_path):
    """A bare type-annotated instance attribute with no constructor call
    at all (self.service: Service, no `= ...`) - the annotation alone
    must be enough to bind the receiver's type."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class Service:\n"
        "    def call(self):\n"
        "        return 'ok'\n"
        "\n"
        "class Client:\n"
        "    def __init__(self):\n"
        "        self.service: Service\n"
        "    def run(self):\n"
        "        return self.service.call()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("mod.Client.run", "mod.Service.call")
    edge = builder.graph.get_edge_data("mod.Client.run", "mod.Service.call")
    assert edge.get("kind") is None


def test_attribute_chain_ambiguous_yields_reduced_confidence(tmp_path):
    """self.service is bound to two different concrete classes across two
    methods (branching initialization) - the call must still resolve
    (never silently dropped), but marked as reduced-confidence, not a
    clean single-type resolution."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class RealService:\n"
        "    def call(self):\n"
        "        return 'real'\n"
        "\n"
        "class MockService:\n"
        "    def call(self):\n"
        "        return 'mock'\n"
        "\n"
        "class Client:\n"
        "    def use_real(self):\n"
        "        self.service = RealService()\n"
        "    def use_mock(self):\n"
        "        self.service = MockService()\n"
        "    def run(self):\n"
        "        return self.service.call()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    # Last-bound wins (same "most recent assignment" convention
    # InstanceTypeMap.bind already used before this phase) - the
    # invariant under test is *reduced confidence*, not which of the two
    # branching candidates gets picked.
    assert not builder.graph.has_edge("mod.Client.run", "mod.RealService.call")
    assert builder.graph.has_edge("mod.Client.run", "mod.MockService.call")
    edge = builder.graph.get_edge_data("mod.Client.run", "mod.MockService.call")
    assert edge.get("kind") == "TENTATIVE_CALL"
