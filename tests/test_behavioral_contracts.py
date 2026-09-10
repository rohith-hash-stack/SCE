"""Hermetic tests for `prism.graph.contracts`, `prism.graph.call_site`, the
new `EXTENDS`/`INSTANTIATES`/`READS_STATE` edge relations in
`prism.graph.concrete_builder`, the behavioral-contract rendering in
`prism.serializers.markdown`, and disk persistence in
`prism.runtime.contract_cache` - across both Python and TypeScript samples,
built from small, self-contained repos (no network access, no real clones).
"""
from __future__ import annotations

import json

import pytest

from prism.cli import build_pipeline
from prism.graph.call_site import compute_call_site_context
from prism.graph.concrete_builder import TRAVERSABLE_RELATIONS
from prism.graph.contracts import BehavioralContract, Parameter, compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.parser.tree_sitter_loader import get_parser
from prism.runtime.contract_cache import (
    compute_or_load_contracts,
    compute_signature,
    contract_cache_path,
    load_contracts,
    save_contracts,
)
from prism.serializers.markdown import render_markdown
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker


# --------------------------------------------------------------------- #
# Fixtures: small, self-contained repos
# --------------------------------------------------------------------- #
@pytest.fixture
def python_repo(tmp_path):
    repo = tmp_path / "pyrepo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        'class Base:\n'
        '    def greet(self):\n'
        '        return "hi"\n'
        '\n'
        '\n'
        'class OrderService(Base):\n'
        '    def __init__(self):\n'
        '        self.cache = {}\n'
        '        self.repo = Repository()\n'
        '\n'
        '    def process_order(self, order_id: int, amount: float = 0.0) -> "bool | None":\n'
        '        """Process an order. This validates and commits it."""\n'
        '        if amount <= 0:\n'
        '            raise ValueError("bad amount")\n'
        '        for i in range(3):\n'
        '            try:\n'
        '                self.cache[order_id] = amount\n'
        '                self.repo.save(order_id)\n'
        '            except KeyError as e:\n'
        '                raise TimeoutError("cache miss") from e\n'
        '        return True\n'
        '\n'
        '    def _private_helper(self):\n'
        '        return 1\n'
        '\n'
        '    @deprecated("use process_order2")\n'
        '    def process_order_old(self, order_id):\n'
        '        return self.process_order(order_id)\n'
        '\n'
        '    def guarded_call(self):\n'
        '        if self.repo:\n'
        '            self.repo.save(1)\n'
        '\n'
        '    def read_only(self):\n'
        '        return self.cache\n'
        '\n'
        '\n'
        'class Repository:\n'
        '    def save(self, item):\n'
        '        return item\n'
    )
    return repo


@pytest.fixture
def ts_repo(tmp_path):
    repo = tmp_path / "tsrepo"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "/** Validate the MTD panel for the visible month. Checks totals. */\n"
        "export async function validateMTDForVisibleMonth(rawStr: string): Promise<number | null> {\n"
        "    const n = await parseNumber(rawStr);\n"
        "    if (n === null) {\n"
        "        throw new TimeoutError(\"bad\");\n"
        "    }\n"
        "    for (const x of items) {\n"
        "        expect(x).toBeTruthy();\n"
        "    }\n"
        "    return n;\n"
        "}\n"
        "\n"
        "function parseNumber(s: string): number | null {\n"
        "    return s ? parseInt(s, 10) : null;\n"
        "}\n"
        "\n"
        "function _privateHelper(): void {}\n"
    )
    return repo


# --------------------------------------------------------------------- #
# Python: signatures, purity, complexity, exceptions, docs, visibility
# --------------------------------------------------------------------- #
def test_python_params_return_type_and_docstring(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.OrderService.process_order"]
    assert [p.render() for p in c.params] == ["order_id: int", "amount: float = 0.0"]
    assert c.return_type == '"bool | None"'
    assert c.doc_summary == "Process an order."


def test_python_purity_pure_when_no_mutation_or_io(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    assert contracts["sample.Repository.save"].purity == "pure"
    assert contracts["sample.OrderService.read_only"].purity == "pure"


def test_python_purity_impure_on_self_attribute_mutation(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.OrderService.process_order"]
    assert c.purity == "impure"
    assert "self.cache" in c.state_mutations


def test_python_state_mutation_detects_subscript_and_mutating_call(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.OrderService.__init__"]
    assert "self.repo" in c.state_mutations  # self.repo = Repository()
    process = contracts["sample.OrderService.process_order"]
    assert "self.cache" in process.state_mutations  # self.cache[order_id] = amount


def test_python_thrown_exceptions(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.OrderService.process_order"]
    assert set(c.thrown_exceptions) == {"ValueError", "TimeoutError"}


def test_python_cyclomatic_complexity_counts_branches(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    # 1 (base) + if + for + except = 4
    assert contracts["sample.OrderService.process_order"].cyclomatic_complexity == 4
    assert contracts["sample.Repository.save"].cyclomatic_complexity == 1


def test_python_visibility_private_vs_public_vs_dunder(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    assert contracts["sample.OrderService.__init__"].visibility == "public"  # dunder, not private
    assert contracts["sample.OrderService._private_helper"].visibility == "private"
    assert contracts["sample.OrderService.process_order"].visibility == "public"


def test_python_is_deprecated_from_decorator(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    assert contracts["sample.OrderService.process_order_old"].is_deprecated is True
    assert contracts["sample.OrderService.process_order"].is_deprecated is False


# --------------------------------------------------------------------- #
# TypeScript: async, effects, purity, exceptions, docs, visibility
# --------------------------------------------------------------------- #
def test_ts_params_return_type_and_async(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.validateMTDForVisibleMonth"]
    assert [p.render() for p in c.params] == ["rawStr: string"]
    assert c.return_type == "Promise<number | null>"
    assert c.is_async is True


def test_ts_effects_include_asserts_and_async_wait(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.validateMTDForVisibleMonth"]
    assert "ASSERTS" in c.effects
    assert "ASYNC_WAIT" in c.effects


def test_ts_thrown_exceptions(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    assert "TimeoutError" in contracts["sample.validateMTDForVisibleMonth"].thrown_exceptions


def test_ts_pure_function_has_no_effects_or_mutations(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    c = contracts["sample.parseNumber"]
    assert c.purity == "pure"
    assert c.effects == []
    assert c.state_mutations == []


def test_ts_doc_summary_from_jsdoc(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    assert contracts["sample.validateMTDForVisibleMonth"].doc_summary == "Validate the MTD panel for the visible month."


def test_ts_visibility_exported_vs_private_vs_public(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    assert contracts["sample.validateMTDForVisibleMonth"].visibility == "exported"
    assert contracts["sample.parseNumber"].visibility == "public"
    assert contracts["sample._privateHelper"].visibility == "private"


def test_ts_cyclomatic_complexity(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    contracts = compute_contracts(builder)
    # 1 (base) + if + for = 3
    assert contracts["sample.validateMTDForVisibleMonth"].cyclomatic_complexity == 3


# --------------------------------------------------------------------- #
# Call-site context (prism.graph.call_site) via real resolved CALLS edges
# --------------------------------------------------------------------- #
def _call_edges(builder, caller):
    return {v: data for _u, v, data in builder.graph.out_edges(caller, data=True) if data.get("relation") == "CALLS"}


def test_call_site_inside_loop_and_try_catch(python_repo):
    builder, _ = build_pipeline(python_repo)
    edges = _call_edges(builder, "sample.OrderService.process_order")
    data = edges["sample.Repository.save"]
    assert data["inside_loop"] is True
    assert data["inside_try_catch"] is True
    assert data["args_passed_count"] == 1
    assert data["argument_flow"] == "reference"


def test_call_site_guarded_by_null_check_from_enclosing_if(python_repo):
    builder, _ = build_pipeline(python_repo)
    edges = _call_edges(builder, "sample.OrderService.guarded_call")
    assert edges["sample.Repository.save"]["guarded_by_null_check"] is True


def test_call_site_not_guarded_when_no_null_check(python_repo):
    builder, _ = build_pipeline(python_repo)
    edges = _call_edges(builder, "sample.OrderService.process_order")
    assert edges["sample.Repository.save"]["guarded_by_null_check"] is False


def test_call_site_fire_and_forget_when_result_discarded(python_repo):
    builder, _ = build_pipeline(python_repo)
    edges = _call_edges(builder, "sample.OrderService.process_order")
    assert edges["sample.Repository.save"]["call_kind"] == "fire_and_forget"


def test_call_site_awaited_kind_for_ts_await_expression(ts_repo):
    builder, _ = build_pipeline(ts_repo)
    edges = _call_edges(builder, "sample.validateMTDForVisibleMonth")
    assert edges["sample.parseNumber"]["call_kind"] == "awaited"


def test_compute_call_site_context_directly_on_a_snippet():
    src = b"def f():\n    for x in y:\n        bar(x)\n"
    parser = get_parser("python")
    tree = parser.parse(src)
    def_node = tree.root_node.children[0]
    call_node = None
    stack = [def_node]
    while stack:
        n = stack.pop()
        if n.type == "call":
            call_node = n
            break
        stack.extend(n.children)
    assert call_node is not None
    ctx = compute_call_site_context(call_node, def_node, "python", src)
    assert ctx.inside_loop is True
    assert ctx.args_passed_count == 1


# --------------------------------------------------------------------- #
# New edge relations: EXTENDS / INSTANTIATES / READS_STATE
# --------------------------------------------------------------------- #
def _relations_from(builder, source):
    return {(v, d["relation"]) for _u, v, d in builder.graph.out_edges(source, data=True)}


def test_extends_edge_for_python_subclass(python_repo):
    builder, _ = build_pipeline(python_repo)
    assert ("sample.Base", "EXTENDS") in _relations_from(builder, "sample.OrderService")


def test_instantiates_edge_for_constructor_call(python_repo):
    builder, _ = build_pipeline(python_repo)
    assert ("sample.Repository", "INSTANTIATES") in _relations_from(builder, "sample.OrderService.__init__")


def test_reads_state_edge_for_attribute_read(python_repo):
    builder, _ = build_pipeline(python_repo)
    assert ("sample.OrderService.cache", "READS_STATE") in _relations_from(builder, "sample.OrderService.read_only")


def test_extends_edge_for_typescript_class(tmp_path):
    repo = tmp_path / "ts_extends"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "export class Base {\n"
        "    greet(): string { return \"hi\"; }\n"
        "}\n"
        "export class Child extends Base implements IThing {\n"
        "    work(): void {}\n"
        "}\n"
    )
    builder, _ = build_pipeline(repo)
    relations = _relations_from(builder, "sample.Child")
    assert ("sample.Base", "EXTENDS") in relations


def test_new_expression_instantiates_edge_for_typescript(tmp_path):
    repo = tmp_path / "ts_new"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "export class Repository {\n"
        "    save(x: number): void {}\n"
        "}\n"
        "export class Service {\n"
        "    constructor() {\n"
        "        this.repo = new Repository();\n"
        "    }\n"
        "}\n"
    )
    builder, _ = build_pipeline(repo)
    relations = _relations_from(builder, "sample.Service.constructor")
    assert ("sample.Repository", "INSTANTIATES") in relations


def test_only_traversable_relations_appear_in_calls_graph(python_repo):
    """`calls_graph` (Issue #9) now includes CALLS/INSTANTIATES/EXTENDS/
    IMPLEMENTS/OVERRIDES - a base-class method must stay reachable for
    self.<inherited_method>() resolution - but still excludes READS_STATE,
    which pulls in unrelated attribute nodes with no comparable
    reachability requirement to justify it."""
    builder, _ = build_pipeline(python_repo)
    for _u, _v, data in builder.calls_graph.edges(data=True):
        assert data.get("relation", "CALLS") in TRAVERSABLE_RELATIONS
    assert any(d.get("relation") == "EXTENDS" for _u, _v, d in builder.graph.edges(data=True))
    assert any(d.get("relation") == "EXTENDS" for _u, _v, d in builder.calls_graph.edges(data=True))
    if any(d.get("relation") == "READS_STATE" for _u, _v, d in builder.graph.edges(data=True)):
        assert not any(d.get("relation") == "READS_STATE" for _u, _v, d in builder.calls_graph.edges(data=True))


# --------------------------------------------------------------------- #
# Serializer: compact contracts for L1/L2, dependencies for the seed
# --------------------------------------------------------------------- #
def test_render_markdown_renders_real_code_with_arguments_for_l1_callee(python_repo):
    """Issue #10: Level 1 (Pruned) - the tier a close, directly-relevant
    non-seed callee like `sample.Repository.save` actually lands at here -
    now shows real code (with real call arguments) instead of the compact
    YAML contract block, precisely so a debugging agent can see what a
    nearby dependency actually does, not just its bare interface."""
    builder, tag_matrix = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    result = ContextKnapsackPacker(token_budget=2000).pack(
        "sample.OrderService.process_order", builder, tag_matrix, distance_engine
    )
    text = render_markdown(result, tag_matrix, contracts=contracts, graph=builder.graph)
    assert "### sample.Repository.save (L1)" in text
    assert "def save(self, item):" in text
    assert "Purity: pure" not in text
    assert "Outgoing Dependencies:" in text
    assert "target: sample.Repository.save" in text


def test_render_markdown_still_renders_yaml_contract_at_l2(python_repo):
    """L2 (Skeleton) is the one resolution the compact YAML contract block
    still supersedes (`markdown._CONTRACT_RESOLUTIONS`) - checked directly
    against a manually-forced L2 PackedItem rather than hunting for a real
    seed/callee pair that happens to land there, since Issue #10's whole
    point was moving *most* nearby callees off L2 and onto L1 instead."""
    from prism.slicer.knapsack import PackedItem, PackResult

    builder, tag_matrix = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    symbol = builder.symbol_table.get("sample.Repository.save")
    content = "def save(self, item): ..."
    result = PackResult(
        seed="sample.OrderService.process_order",
        budget=2000,
        allocated_tokens=50,
        items=[
            PackedItem("sample.OrderService.process_order", 0, "def process_order(self): ...", "python", (1, 1), "sample.py"),
            PackedItem("sample.Repository.save", 2, content, "python", symbol.line_range, "sample.py"),
        ],
    )
    text = render_markdown(result, tag_matrix, contracts=contracts, graph=builder.graph)
    assert "### sample.Repository.save (L2" in text
    assert "Purity: pure" in text


def test_render_markdown_backward_compatible_without_contracts(python_repo):
    """Every pre-existing call site omits `contracts`/`graph` entirely -
    confirms the new parameters are truly optional and don't change output
    for callers that don't opt in."""
    builder, tag_matrix = build_pipeline(python_repo)
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    result = ContextKnapsackPacker(token_budget=2000).pack(
        "sample.OrderService.process_order", builder, tag_matrix, distance_engine
    )
    text = render_markdown(result, tag_matrix)
    assert "Outgoing Dependencies:" not in text
    assert "# SEMANTIC REPOSITORY CONTEXT" in text


# --------------------------------------------------------------------- #
# Disk persistence (prism.runtime.contract_cache)
# --------------------------------------------------------------------- #
def test_contract_cache_round_trip(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    signature = compute_signature(builder)
    save_contracts(str(python_repo), signature, contracts)

    loaded = load_contracts(str(python_repo), signature)
    assert loaded is not None
    assert loaded["sample.Repository.save"].purity == "pure"
    assert set(loaded) == set(contracts)


def test_contract_cache_invalidated_on_signature_mismatch(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_contracts(builder)
    save_contracts(str(python_repo), "stale-signature", contracts)
    assert load_contracts(str(python_repo), compute_signature(builder)) is None


def test_contract_cache_miss_returns_none(tmp_path):
    assert load_contracts(str(tmp_path), "whatever") is None


def test_compute_or_load_contracts_writes_cache_file(python_repo):
    builder, _ = build_pipeline(python_repo)
    contracts = compute_or_load_contracts(builder, str(python_repo))
    assert "sample.Repository.save" in contracts
    cache_path = contract_cache_path(str(python_repo))
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text())
    assert "sample.Repository.save" in payload["contracts"]


def test_compute_or_load_contracts_hits_cache_on_second_call(python_repo):
    builder, _ = build_pipeline(python_repo)
    first = compute_or_load_contracts(builder, str(python_repo))
    # A second builder over the same unchanged source: the cache should
    # serve identical contracts without needing the second builder's own
    # symbol table to be re-walked differently.
    builder2, _ = build_pipeline(python_repo)
    second = compute_or_load_contracts(builder2, str(python_repo))
    assert first.keys() == second.keys()
    assert first["sample.Repository.save"].to_dict() == second["sample.Repository.save"].to_dict()


# --------------------------------------------------------------------- #
# BehavioralContract / Parameter (de)serialization
# --------------------------------------------------------------------- #
def test_behavioral_contract_to_dict_from_dict_round_trip():
    c = BehavioralContract(
        qualified_name="a.b.c",
        params=[Parameter("x", type="int", default="0")],
        return_type="int",
        is_async=True,
        purity="pure",
        state_mutations=["self.x"],
        thrown_exceptions=["ValueError"],
        effects=["ASSERTS"],
        cyclomatic_complexity=2,
        doc_summary="Does a thing.",
        visibility="private",
        is_deprecated=True,
    )
    restored = BehavioralContract.from_dict(c.to_dict())
    assert restored == c


def test_parameter_render_forms():
    assert Parameter("x").render() == "x"
    assert Parameter("x", type="int").render() == "x: int"
    assert Parameter("x", type="int", default="0").render() == "x: int = 0"
    assert Parameter("x", default="0").render() == "x = 0"
