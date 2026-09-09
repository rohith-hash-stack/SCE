"""Tests for Polysemy Scoring & Ambiguity Sentinel Nodes
(`prism.graph.symbol_table.score_candidate`/`UnresolvedPolymorphicNode`)
and Dynamic Dispatch Sentinel & Hazard Tagging
(`prism.graph.call_site.detect_call_site_hazard`/`DynamicEdgeSentinel`/
`has_dynamic_hazard_construct`), wired into `ConcreteGraphBuilder`,
`TaggingEngine`, `ContractExtractor`, `ContextKnapsackPacker`, and
`render_markdown`.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.graph.symbol_table import (
    LocalImportMap,
    POLYSEMY_THRESHOLD,
    SymbolInfo,
    arity_match,
    locality_distance,
    namespace_match,
    score_candidate,
    unresolved_polymorphic_node_id,
)
from prism.serializers.markdown import render_markdown
from prism.slicer.compressor import (
    DYNAMIC_EDGE_SENTINEL_RESOLUTION,
    UNRESOLVED_POLYMORPHIC_RESOLUTION,
    is_infallible,
)
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker
from prism.tagger.engine import TaggingEngine
from prism.tagger.rules import DYNAMIC_HAZARD_TAG


def _run(repo_path: str):
    builder, tag_matrix = build_pipeline(repo_path)
    contracts = compute_contracts(builder)
    return builder, tag_matrix, contracts


def _pack(builder, tag_matrix, contracts, seed: str, budget: int = 4000):
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    packer = ContextKnapsackPacker(token_budget=budget)
    return packer.pack(seed, builder, tag_matrix, distance_engine, contracts=contracts)


def _symbol(qname="pkg.mod.Foo", module="pkg.mod", file="pkg/mod.py", kind="function") -> SymbolInfo:
    return SymbolInfo(qualified_name=qname, kind=kind, file=file, line_range=(1, 1), language_id="python", module=module)


# --------------------------------------------------------------------- #
# Task 1.1: score_candidate and its three components (pure unit tests)
# --------------------------------------------------------------------- #
def test_namespace_match_same_module_is_full_score() -> None:
    candidate = _symbol(module="pkg.mod")
    assert namespace_match(candidate, "pkg.mod", LocalImportMap()) == 1.0


def test_namespace_match_imported_module_is_full_score() -> None:
    candidate = _symbol(module="pkg.other")
    import_map = LocalImportMap()
    import_map.add("other", "pkg.other.Thing")
    assert namespace_match(candidate, "pkg.caller", import_map) == 1.0


def test_namespace_match_unrelated_module_is_zero() -> None:
    candidate = _symbol(module="pkg.other")
    assert namespace_match(candidate, "pkg.caller", LocalImportMap()) == 0.0


def test_locality_distance_tiers() -> None:
    same_file = _symbol(file="pkg/mod.py", module="pkg.mod")
    assert locality_distance(same_file, "pkg/mod.py", "pkg.mod") == 1.0

    same_package = _symbol(file="pkg/other.py", module="pkg.other")
    assert locality_distance(same_package, "pkg/mod.py", "pkg.mod") == 0.6

    external = _symbol(file="elsewhere/other.py", module="elsewhere.other")
    assert locality_distance(external, "pkg/mod.py", "pkg.mod") == 0.2


def test_arity_match_binary() -> None:
    assert arity_match(2, 2) == 1.0
    assert arity_match(2, 3) == 0.0
    assert arity_match(None, 2) == 0.0


def test_score_candidate_weighted_formula() -> None:
    # 0.50*1.0 + 0.35*1.0 + 0.15*1.0 = 1.0
    assert score_candidate(1.0, 1.0, 1.0) == 1.0
    # 0.50*0.0 + 0.35*0.0 + 0.15*0.2 = 0.03 - well under threshold
    assert round(score_candidate(0.0, 0.0, 0.2), 4) == 0.03
    assert score_candidate(0.0, 0.0, 0.2) < POLYSEMY_THRESHOLD


def test_unresolved_polymorphic_node_id_is_stable_and_unique_per_call_site() -> None:
    a = unresolved_polymorphic_node_id("validate", "pkg/mod.py", 10)
    b = unresolved_polymorphic_node_id("validate", "pkg/mod.py", 10)
    c = unresolved_polymorphic_node_id("validate", "pkg/mod.py", 11)
    assert a == b
    assert a != c


# --------------------------------------------------------------------- #
# Task 1.2/1.3: end-to-end - clear winner binds, genuine ambiguity gets a
# sentinel with conservative tag unioning.
# --------------------------------------------------------------------- #
def test_high_confidence_candidate_binds_normally(tmp_path) -> None:
    """Two same-named `validate` functions exist repo-wide, but the caller
    imports one of them directly and passes the exact argument count that
    one declares - NamespaceMatch=1.0, ArityMatch=1.0 clears the 0.85
    threshold on its own, so the call binds to that candidate with a plain
    CALLS edge, no sentinel."""
    repo = tmp_path / "repo"
    (repo / "mod_a").mkdir(parents=True)
    (repo / "mod_b").mkdir(parents=True)
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

    sentinels = [n for n, d in builder.graph.nodes(data=True) if d.get("sentinel_type")]
    assert sentinels == []
    assert builder.graph.has_edge("caller.run", "mod_a.checks.validate")


def test_genuine_ambiguity_emits_sentinel_with_both_candidates(tmp_path) -> None:
    """Neither `validate` candidate is imported or matches the call's own
    arg count exactly, and both live in unrelated packages - every
    candidate scores under 0.85, so the call becomes an
    UnresolvedPolymorphicNode naming both, not a silent drop or a guess."""
    repo = tmp_path / "repo2"
    (repo / "mod_a").mkdir(parents=True)
    (repo / "mod_b").mkdir(parents=True)
    (repo / "mod_a" / "__init__.py").write_text("")
    (repo / "mod_b" / "__init__.py").write_text("")
    (repo / "mod_a" / "checks.py").write_text("def validate(x, y, z):\n    return x == y == z\n")
    (repo / "mod_b" / "checks.py").write_text("def validate(x, y):\n    return x == y\n")
    (repo / "caller.py").write_text(
        "def run(a):\n"
        "    return validate(a)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))

    sentinels = [
        (n, d) for n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "unresolved_polymorphic"
    ]
    assert len(sentinels) == 1
    node_id, data = sentinels[0]
    assert data["identifier"] == "validate"
    assert set(data["candidates"]) == {"mod_a.checks.validate", "mod_b.checks.validate"}
    assert builder.graph.has_edge("caller.run", node_id)
    # Neither candidate itself got a spurious direct edge from the guess.
    assert not builder.graph.has_edge("caller.run", "mod_a.checks.validate")
    assert not builder.graph.has_edge("caller.run", "mod_b.checks.validate")


def test_conservative_tag_unioning_on_ambiguous_node(tmp_path) -> None:
    """Task 1.3: Tags(Unresolved) = union over candidates - one pure
    candidate, one that mutates `self.*` state, so the sentinel must carry
    the state-mutation tag even though the winning-guess-free bind means
    neither candidate's tags alone would show it."""
    repo = tmp_path / "repo3"
    (repo / "mod_a").mkdir(parents=True)
    (repo / "mod_b").mkdir(parents=True)
    (repo / "mod_a" / "__init__.py").write_text("")
    (repo / "mod_b" / "__init__.py").write_text("")
    (repo / "mod_a" / "checks.py").write_text(
        "class A:\n"
        "    def close(self, code):\n"
        "        return code\n"
    )
    (repo / "mod_b" / "checks.py").write_text(
        "class B:\n"
        "    def close(self, code):\n"
        "        self.closed = True\n"
        "        return code\n"
    )
    (repo / "caller.py").write_text(
        "def run(obj, code):\n"
        "    return obj.close(code)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))

    sentinels = [n for n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "unresolved_polymorphic"]
    assert len(sentinels) == 1
    node_id = sentinels[0]
    assert "#state_mutation" in tag_matrix[node_id]
    assert "#state_mutation" in builder.graph.nodes[node_id]["tags"]


# --------------------------------------------------------------------- #
# Task 2: Dynamic Dispatch Sentinel & Hazard Tagging
# --------------------------------------------------------------------- #
def test_getattr_reflection_detected_with_target_object(tmp_path) -> None:
    repo = tmp_path / "repo4"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def dispatch(service, name):\n"
        "    return getattr(service, name)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    dyn = [(n, d) for n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "dynamic_edge"]
    assert len(dyn) == 1
    node_id, data = dyn[0]
    assert data["hazard_type"] == "REFLECTION"
    assert data["target_object"] == "service"
    assert builder.graph.has_edge("sample.dispatch", node_id)
    assert "#dynamic_hazard" in tag_matrix["sample.dispatch"]
    assert tag_matrix[node_id] == {DYNAMIC_HAZARD_TAG}


def test_eval_exec_detected_as_eval_hazard(tmp_path) -> None:
    repo = tmp_path / "repo5"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def run(expr):\n"
        "    return eval(expr)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    dyn = [d for _n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "dynamic_edge"]
    assert len(dyn) == 1
    assert dyn[0]["hazard_type"] == "EVAL"
    assert "#dynamic_hazard" in tag_matrix["sample.run"]


def test_subscript_dispatch_detected(tmp_path) -> None:
    repo = tmp_path / "repo6"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def dispatch(handlers, action):\n"
        "    return handlers[action]()\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    dyn = [(n, d) for n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "dynamic_edge"]
    assert len(dyn) == 1
    node_id, data = dyn[0]
    assert data["hazard_type"] == "SUBSCRIPT_DISPATCH"
    assert data["target_object"] == "handlers"
    assert "#dynamic_hazard" in tag_matrix["sample.dispatch"]


def test_dynamic_hazard_forces_impure_purity_and_excludes_infallible(tmp_path) -> None:
    """Task 2.2: DYNAMIC_HAZARD=True must prevent a `pure`/infallible
    classification even though the function is otherwise trivially simple
    (no branches, no thrown exceptions, no recorded IO effect)."""
    repo = tmp_path / "repo7"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def dispatch(service, name):\n"
        "    return getattr(service, name)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    contract = contracts["sample.dispatch"]
    assert contract.purity == "impure"
    assert is_infallible(contract, tag_matrix.get("sample.dispatch")) is False


def test_ordinary_function_without_hazard_stays_pure(tmp_path) -> None:
    """Control case: confirms the hazard forcing above is targeted, not a
    blanket regression on every simple function's purity."""
    repo = tmp_path / "repo8"
    repo.mkdir()
    (repo / "sample.py").write_text("def add(x, y):\n    return x + y\n")
    builder, tag_matrix = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    assert contracts["sample.add"].purity == "pure"
    assert "#dynamic_hazard" not in tag_matrix.get("sample.add", set())


# --------------------------------------------------------------------- #
# Task 3.1: Graph Traversal Guard - sentinels are true terminal leaves,
# and existing traversal/distance machinery tolerates them without crashing.
# --------------------------------------------------------------------- #
def test_sentinel_nodes_have_no_outgoing_edges(tmp_path) -> None:
    repo = tmp_path / "repo9"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def dispatch(service, name):\n"
        "    return getattr(service, name)\n"
        "\n"
        "def seed():\n"
        "    return dispatch(None, 'x')\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    sentinels = [n for n, d in builder.graph.nodes(data=True) if d.get("sentinel_type")]
    assert sentinels
    for node in sentinels:
        assert builder.calls_graph.out_degree(node) == 0


def test_distance_and_knapsack_tolerate_sentinels_without_crashing(tmp_path) -> None:
    repo = tmp_path / "repo10"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def dispatch(service, name):\n"
        "    return getattr(service, name)\n"
        "\n"
        "def seed():\n"
        "    return dispatch(None, 'x')\n"
    )
    builder, tag_matrix, contracts = _run(str(repo))
    result = _pack(builder, tag_matrix, contracts, "sample.seed")
    sentinel_items = [i for i in result.items if i.resolution == DYNAMIC_EDGE_SENTINEL_RESOLUTION]
    assert len(sentinel_items) == 1
    assert "DYNAMIC BOUNDARY" in sentinel_items[0].content
    assert "REFLECTION" in sentinel_items[0].content


def test_knapsack_packs_unresolved_polymorphic_as_compact_diagnostic(tmp_path) -> None:
    repo = tmp_path / "repo11"
    (repo / "mod_a").mkdir(parents=True)
    (repo / "mod_b").mkdir(parents=True)
    (repo / "mod_a" / "__init__.py").write_text("")
    (repo / "mod_b" / "__init__.py").write_text("")
    (repo / "mod_a" / "checks.py").write_text("def validate(x, y, z):\n    return x == y == z\n")
    (repo / "mod_b" / "checks.py").write_text("def validate(x, y):\n    return x == y\n")
    (repo / "caller.py").write_text(
        "def seed(a):\n"
        "    return validate(a)\n"
    )
    builder, tag_matrix, contracts = _run(str(repo))
    result = _pack(builder, tag_matrix, contracts, "caller.seed")
    sentinel_items = [i for i in result.items if i.resolution == UNRESOLVED_POLYMORPHIC_RESOLUTION]
    assert len(sentinel_items) == 1
    content = sentinel_items[0].content
    assert "AMBIGUOUS CALL" in content
    assert "mod_a.checks.validate" in content
    assert "mod_b.checks.validate" in content
    # Cheap: a couple of short diagnostic lines, nowhere near a full
    # rendered function body's token cost.
    assert len(content.split()) < 40


def test_markdown_renders_resolution_boundaries_section(tmp_path) -> None:
    repo = tmp_path / "repo12"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def dispatch(service, name):\n"
        "    return getattr(service, name)\n"
        "\n"
        "def seed():\n"
        "    return dispatch(None, 'x')\n"
    )
    builder, tag_matrix, contracts = _run(str(repo))
    result = _pack(builder, tag_matrix, contracts, "sample.seed")
    graph = builder.graph
    text = render_markdown(result, tag_matrix, contracts=contracts, graph=graph)
    assert "### Resolution Boundaries (ambiguous / dynamic dispatch)" in text
    assert "DYNAMIC BOUNDARY" in text


# --------------------------------------------------------------------- #
# Regression guard: Go generic-function instantiation (`getTyped[string]
# (c, key)`) must resolve to a normal CALLS edge, not be misclassified as
# SUBSCRIPT_DISPATCH - Go's grammar makes the two syntactically identical
# (see `ConcreteGraphBuilder._resolve_go_generic_call`'s own docstring),
# so this is the one case worth pinning down explicitly.
# --------------------------------------------------------------------- #
def test_go_generic_call_resolves_normally_not_as_hazard(tmp_path) -> None:
    repo = tmp_path / "repo13"
    repo.mkdir()
    # Two arguments, matching gin's real `getTyped[string](c, key)` shape:
    # tree-sitter-go parses a *single*-argument `Foo[Bar](x)` as a
    # `type_conversion_expression` (indistinguishable from a real type
    # conversion at the grammar level) rather than a `call_expression` at
    # all, so it never reaches `_resolve_go_generic_call` in the first
    # place either way - this fixture matches the shape that actually
    # produces the `call_expression` + `index_expression` collision this
    # test exists to pin down.
    (repo / "main.go").write_text(
        "package main\n"
        "\n"
        "func getTyped[T any](key string, extra string) T {\n"
        "\tvar zero T\n"
        "\treturn zero\n"
        "}\n"
        "\n"
        "func caller() string {\n"
        "\treturn getTyped[string](\"k\", \"x\")\n"
        "}\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    dyn = [d for _n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "dynamic_edge"]
    assert dyn == []
    assert builder.graph.has_edge("main.caller", "main.getTyped")
    assert builder.graph.edges["main.caller", "main.getTyped"]["relation"] == "CALLS"


def test_go_real_map_dispatch_still_flagged_as_hazard(tmp_path) -> None:
    """Contrast case for the fix above: a real map-indexed function-value
    call (base identifier resolves to nothing callable) must still become
    a SUBSCRIPT_DISPATCH sentinel, not silently resolve to nothing."""
    repo = tmp_path / "repo14"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n"
        "\n"
        "func dispatch(handlers map[string]func(), action string) {\n"
        "\thandlers[action]()\n"
        "}\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    dyn = [d for _n, d in builder.graph.nodes(data=True) if d.get("sentinel_type") == "dynamic_edge"]
    assert len(dyn) == 1
    assert dyn[0]["hazard_type"] == "SUBSCRIPT_DISPATCH"
