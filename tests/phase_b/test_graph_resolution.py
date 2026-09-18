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


# ============================================================
# G44: repo-wide fallback for a fully unresolved receiver
# ============================================================

def test_g44_unique_candidate_links_at_tentative_weight(tmp_path):
    """An unresolved receiver (unknown/untyped `obj`) whose method name
    is unique repo-wide - links, but as kind=TENTATIVE_CALL, not a
    confidently-resolved call."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class Widget:\n"
        "    def render_unique_widget_view(self):\n"
        "        return 'ok'\n"
        "\n"
        "def run(obj):\n"
        "    return obj.render_unique_widget_view()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("mod.run", "mod.Widget.render_unique_widget_view")
    edge = builder.graph.get_edge_data("mod.run", "mod.Widget.render_unique_widget_view")
    assert edge.get("kind") == "TENTATIVE_CALL"


def test_g44_multiple_candidates_leave_existing_sentinel_path_untouched():
    """count>1 for a fully-untyped receiver, both candidates scoring
    below POLYSEMY_THRESHOLD, is the pre-existing scored polysemy path's
    own job (test_genuine_ambiguity_emits_sentinel_with_both_candidates
    in tests/test_polysemy_and_dynamic_hazards.py) - it must keep
    producing its own UnresolvedPolymorphic sentinel edge exactly as
    before. G44 only ever fires for the count==1 case that used to be
    silently dropped (see test_g44_unique_candidate_links_at_tentative_
    weight above); it never replaces this sentinel with "zero edges" -
    doing so would have discarded a real, tested, more informative
    diagnostic (see the phase-b-g44 commit message for the concrete
    regression this would otherwise have caused)."""
    builder = ConcreteGraphBuilder("/tmp/repo")
    from prism.graph.symbol_table import SymbolInfo

    for qname, mod in [("pkg_a.Thing.get", "pkg_a"), ("pkg_b.Other.get", "pkg_b")]:
        builder.symbol_table._symbols[qname] = SymbolInfo(
            qualified_name=qname, kind="method", file=f"{mod}.py",
            line_range=(1, 2), language_id="python", module=mod,
        )
        builder.symbol_table._simple_name_index.setdefault("get", []).append(qname)
    builder.symbol_table._symbols["caller.run"] = SymbolInfo(
        qualified_name="caller.run", kind="function", file="caller.py",
        line_range=(1, 2), language_id="python", module="caller",
    )
    builder.graph.add_node("caller.run")

    class _FakeCallNode:
        def child_by_field_name(self, name):
            return None

        start_point = (0, 0)

    class _FakeParsed:
        path = "caller.py"

    from prism.graph.symbol_table import LocalImportMap

    builder._resolve_ambiguous_call(
        "caller.run", _FakeCallNode(), _FakeParsed(), "caller", LocalImportMap(), ["obj", "get"]
    )
    edges = list(builder.graph.out_edges("caller.run", data=True))
    assert len(edges) == 1
    _src, target, data = edges[0]
    assert data.get("relation") == "CALLS"
    assert data.get("kind") is None  # a sentinel edge, not a tentative guess
    target_data = builder.graph.nodes[target]
    assert target_data.get("sentinel_type") == "unresolved_polymorphic"
    assert set(target_data.get("candidates", [])) == {"pkg_a.Thing.get", "pkg_b.Other.get"}
    # Neither real candidate itself got a spurious direct edge.
    assert not builder.graph.has_edge("caller.run", "pkg_a.Thing.get")
    assert not builder.graph.has_edge("caller.run", "pkg_b.Other.get")


def test_g44_scoped_polysemy_still_resolves_via_normal_scored_edge(tmp_path):
    """A call that clears POLYSEMY_THRESHOLD through the pre-existing
    scored path is completely unaffected by G44 - it never even reaches
    the count==1/count>=2 branches this phase touches."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod_a").mkdir()
    (repo / "mod_b").mkdir()
    (repo / "mod_a" / "__init__.py").write_text("")
    (repo / "mod_b" / "__init__.py").write_text("")
    (repo / "mod_a" / "checks.py").write_text("def validate(x, y):\n    return x == y\n")
    (repo / "mod_b" / "checks.py").write_text("def validate(x):\n    return bool(x)\n")
    (repo / "caller.py").write_text(
        "from mod_a.checks import validate\n"
        "\n"
        "def run(a, b):\n"
        "    return validate(a, b)\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("caller.run", "mod_a.checks.validate")
    edge = builder.graph.get_edge_data("caller.run", "mod_a.checks.validate")
    assert edge.get("kind") is None
