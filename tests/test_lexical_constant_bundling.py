"""Two-Tier Visibility Pipeline: lexical constant bundling.

`ContractExtractor._referenced_module_constants` discovers which of a
symbol's own module-level `kind="attribute"` constants it genuinely
reads (never a local variable/parameter/comprehension target that
happens to share the name); `submodular_knapsack._constant_stub`/
`_oversized_literal_summary` render each one (with entry-count
summarization for an oversized container literal); `_bundle_referenced_
constants` (the post-greedy, budget-gated, dedup'd knapsack fixup,
mirroring `fix-include-class-when-method-selected`) decides which of
those actually get attached to a selected function's own rendered
`NodeEntry.body`. Deliberately never a `G_C` graph edge anywhere in this
chain - see `docs/architecture_boundaries.md`'s Category 5 entry.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import BehavioralContract, ContractExtractor, compute_contracts
from prism.packer.submodular_knapsack import (
    SubmodularPackedItem,
    _bundle_referenced_constants,
    _constant_stub,
    _oversized_literal_summary,
    pack_symbol_context,
)
from prism.parser.tree_sitter_loader import parse_source
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.surface.build import build_context_package


# --------------------------------------------------------------------- #
# _register_attribute now populates _def_nodes (Scope 1)
# --------------------------------------------------------------------- #
def test_module_constant_def_node_is_cached_on_cold_build(tmp_path):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    node = builder.def_node("app.MAX_RETRIES")
    assert node is not None
    assert node.type == "assignment"


def test_module_constant_def_node_survives_a_warm_cache_hit(tmp_path):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n")
    build_pipeline(str(tmp_path), use_cache=True)
    builder2, _ = build_pipeline(str(tmp_path), use_cache=True)
    node = builder2.def_node("app.MAX_RETRIES")
    assert node is not None
    assert node.type == "assignment"


def test_ts_module_constant_is_registered_and_cached(tmp_path):
    (tmp_path / "app.ts").write_text("const MAX_RETRIES = 5;\n\nexport function f() {\n    return MAX_RETRIES;\n}\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=True)
    info = builder.symbol_table.get("app.MAX_RETRIES")
    assert info is not None
    assert info.kind == "attribute"
    node = builder.def_node("app.MAX_RETRIES")
    assert node is not None
    assert node.type == "variable_declarator"

    builder2, _ = build_pipeline(str(tmp_path), use_cache=True)
    assert builder2.def_node("app.MAX_RETRIES") is not None


# --------------------------------------------------------------------- #
# Lexical reference discovery (Scope 2): Python
# --------------------------------------------------------------------- #
_PY_FIXTURE = """
MAX_RETRIES = 5
STATUS_CODES = {200: "OK"}

def reads_constant():
    return MAX_RETRIES

def shadowed_by_param(MAX_RETRIES):
    return MAX_RETRIES

def shadowed_by_assignment():
    MAX_RETRIES = 10
    return MAX_RETRIES

def shadowed_by_for_loop():
    for MAX_RETRIES in range(3):
        pass
    return MAX_RETRIES

def shadowed_by_comprehension():
    return [MAX_RETRIES for MAX_RETRIES in range(3)]

def shadowed_by_with():
    with open(MAX_RETRIES) as MAX_RETRIES:
        return MAX_RETRIES

def shadowed_by_except():
    try:
        pass
    except Exception as MAX_RETRIES:
        return MAX_RETRIES

def uses_global_explicitly():
    global MAX_RETRIES
    MAX_RETRIES = 99
    return MAX_RETRIES

def keyword_arg_is_not_a_reference():
    return dict(MAX_RETRIES=1)

def reads_two_constants():
    return MAX_RETRIES, STATUS_CODES

def reads_nothing():
    return 1
"""


def _contracts_for(tmp_path, source: str, filename: str = "app.py") -> dict[str, BehavioralContract]:
    (tmp_path / filename).write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    return compute_contracts(builder)


def test_reads_a_real_module_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.reads_constant"].referenced_constants == ["MAX_RETRIES"]


def test_parameter_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.shadowed_by_param"].referenced_constants == []


def test_local_assignment_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.shadowed_by_assignment"].referenced_constants == []


def test_for_loop_target_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.shadowed_by_for_loop"].referenced_constants == []


def test_comprehension_target_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.shadowed_by_comprehension"].referenced_constants == []


def test_with_as_target_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.shadowed_by_with"].referenced_constants == []


def test_except_as_target_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.shadowed_by_except"].referenced_constants == []


def test_explicit_global_declaration_still_counts_as_a_reference(tmp_path):
    """The one deliberate exception: `global MAX_RETRIES` followed by a
    real assignment must NOT be excluded - the whole point of `global`
    is declaring that the name means the outer/module scope, not a new
    local, even though it's also assigned within the function."""
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.uses_global_explicitly"].referenced_constants == ["MAX_RETRIES"]


def test_keyword_argument_name_is_not_a_reference(tmp_path):
    """`dict(MAX_RETRIES=1)` - the keyword name belongs to `dict`'s own
    (nonexistent) parameter namespace, never a read of this scope's own
    module constant, even though it's a bare `identifier` node with the
    same text."""
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.keyword_arg_is_not_a_reference"].referenced_constants == []


def test_multiple_real_references_are_all_found_and_sorted(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.reads_two_constants"].referenced_constants == ["MAX_RETRIES", "STATUS_CODES"]


def test_no_references_at_all_is_an_empty_list_not_none(tmp_path):
    contracts = _contracts_for(tmp_path, _PY_FIXTURE)
    assert contracts["app.reads_nothing"].referenced_constants == []


def test_referenced_constants_round_trips_through_to_dict_from_dict():
    contract = BehavioralContract(qualified_name="app.f", referenced_constants=["MAX_RETRIES", "STATUS_CODES"])
    restored = BehavioralContract.from_dict(contract.to_dict())
    assert restored.referenced_constants == ["MAX_RETRIES", "STATUS_CODES"]


def test_referenced_constants_from_dict_defaults_to_empty_for_an_older_cached_payload():
    """A `contracts_cache.json` written before this field existed has no
    `referenced_constants` key at all - `from_dict` must degrade to `[]`,
    never a `KeyError`."""
    payload = BehavioralContract(qualified_name="app.f").to_dict()
    del payload["referenced_constants"]
    restored = BehavioralContract.from_dict(payload)
    assert restored.referenced_constants == []


# --------------------------------------------------------------------- #
# Lexical reference discovery (Scope 2): JS/TS conservative subset
# --------------------------------------------------------------------- #
_TS_FIXTURE = """
const MAX_RETRIES = 5;

export function readsConstant() {
    return MAX_RETRIES;
}

export function shadowedByParam(MAX_RETRIES: number) {
    return MAX_RETRIES;
}

export function shadowedByConst() {
    const MAX_RETRIES = 10;
    return MAX_RETRIES;
}

export function shadowedByForOf() {
    for (const MAX_RETRIES of [1, 2, 3]) {
        return MAX_RETRIES;
    }
    return 0;
}

export function shadowedByCatch() {
    try {
        return 1;
    } catch (MAX_RETRIES) {
        return MAX_RETRIES;
    }
}
"""


def test_ts_reads_a_real_module_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _TS_FIXTURE, filename="app.ts")
    assert contracts["app.readsConstant"].referenced_constants == ["MAX_RETRIES"]


def test_ts_parameter_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _TS_FIXTURE, filename="app.ts")
    assert contracts["app.shadowedByParam"].referenced_constants == []


def test_ts_const_declaration_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _TS_FIXTURE, filename="app.ts")
    assert contracts["app.shadowedByConst"].referenced_constants == []


def test_ts_for_of_target_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _TS_FIXTURE, filename="app.ts")
    assert contracts["app.shadowedByForOf"].referenced_constants == []


def test_ts_catch_target_shadow_excludes_the_constant(tmp_path):
    contracts = _contracts_for(tmp_path, _TS_FIXTURE, filename="app.ts")
    assert contracts["app.shadowedByCatch"].referenced_constants == []


def test_go_returns_no_referenced_constants_disclosed_gap():
    """Go/Java/C# are a disclosed, out-of-scope gap - `[]`, never a
    guess or a crash."""
    source = b"package pkg\n\nfunc f() int {\n\treturn 1\n}\n"
    parsed = parse_source("pkg.go", source)
    def_node = parsed.root_node.children[0]
    contract = ContractExtractor().extract_symbol(
        def_node, parsed, None, "f", module_constant_names=frozenset({"SOMETHING"}),
    )
    assert contract.referenced_constants == []


# --------------------------------------------------------------------- #
# Literal skeletonization (Scope 3)
# --------------------------------------------------------------------- #
def test_small_constant_renders_in_full(tmp_path):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert _constant_stub(builder, "app.MAX_RETRIES") == "MAX_RETRIES = 5"


def test_oversized_dict_literal_is_entry_count_summarized(tmp_path):
    entries = ", ".join(f'{i}: "v{i}"' for i in range(200))
    (tmp_path / "app.py").write_text(f"STATUS_CODES = {{{entries}}}\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert _constant_stub(builder, "app.STATUS_CODES") == "STATUS_CODES = {...}  # 200 entries"


def test_oversized_list_and_tuple_and_set_are_all_summarized(tmp_path):
    items = ", ".join(str(i) for i in range(100))
    source = f"L = [{items}]\nT = ({items})\nS = {{{items}}}\n"
    (tmp_path / "app.py").write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert _constant_stub(builder, "app.L") == "L = [...]  # 100 entries"
    assert _constant_stub(builder, "app.T") == "T = (...)  # 100 entries"
    assert _constant_stub(builder, "app.S") == "S = {...}  # 100 entries"


def test_oversized_non_container_rhs_falls_back_to_full_text(tmp_path):
    """A long computed expression (not a container literal) has nothing
    for `_oversized_literal_summary` to summarize - the real, full text
    is used even past the token threshold, never a fabricated summary."""
    full_line = "BIG_STRING = " + repr("x" * 500)
    (tmp_path / "app.py").write_text(full_line + "\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    stub = _constant_stub(builder, "app.BIG_STRING")
    assert stub == full_line
    assert "entries" not in stub


def test_single_entry_uses_singular_noun():
    """`_oversized_literal_summary` itself (bypassing `_constant_stub`'s
    own token-threshold gate) - a real, if unusual, one-entry container
    still gets the grammatically correct "entry" (singular), not
    "entries"."""
    src = b"L = [1]\n"
    parsed = parse_source("app.py", src)
    assign = parsed.root_node.children[0].children[0]
    summary = _oversized_literal_summary(assign, parsed.language_id, parsed.source)
    assert summary == "L = [...]  # 1 entry"


def test_missing_symbol_returns_none(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert _constant_stub(builder, "app.DOES_NOT_EXIST") is None


def test_oversized_literal_summary_ts_object_and_array():
    src = b"const D = {a: 1, b: 2, c: 3};\nconst L = [1, 2, 3, 4];\n"
    parsed = parse_source("app.ts", src)
    d_declarator = parsed.root_node.children[0].named_children[0]
    l_declarator = parsed.root_node.children[1].named_children[0]
    d_summary = _oversized_literal_summary(d_declarator, parsed.language_id, parsed.source)
    l_summary = _oversized_literal_summary(l_declarator, parsed.language_id, parsed.source)
    assert d_summary == "D = {...}  # 3 entries"
    assert l_summary == "L = [...]  # 4 entries"


# --------------------------------------------------------------------- #
# Atomic knapsack bundling (Scope 4): budget gating, dedup, skip-and-log
# --------------------------------------------------------------------- #
def test_bundle_referenced_constants_is_a_noop_with_no_map(tmp_path):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    bundled, running_cost = _bundle_referenced_constants(builder, ["app.f"], {"app.f"}, None, {"app.f": 5}, 5, 100)
    assert bundled == {}
    assert running_cost == 5


def test_bundle_referenced_constants_admits_within_budget(tmp_path):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    costs = {"app.f": 5}
    bundled, running_cost = _bundle_referenced_constants(
        builder, ["app.f"], {"app.f"}, {"app.f": ["MAX_RETRIES"]}, costs, 5, 100,
    )
    assert bundled == {"app.f": ["MAX_RETRIES = 5"]}
    assert running_cost > 5
    assert costs["app.f"] > 5


def test_bundle_referenced_constants_skips_when_budget_too_tight(tmp_path, capsys):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    costs = {"app.f": 5}
    bundled, running_cost = _bundle_referenced_constants(
        builder, ["app.f"], {"app.f"}, {"app.f": ["MAX_RETRIES"]}, costs, 5, 5,
    )
    assert bundled == {}
    assert running_cost == 5
    assert costs["app.f"] == 5
    assert "fix-lexical-constant-bundling" in capsys.readouterr().err


def test_bundle_referenced_constants_dedups_across_two_functions(tmp_path):
    source = (
        "MAX_RETRIES = 5\n\n"
        "def f():\n    return MAX_RETRIES\n\n"
        "def g():\n    return MAX_RETRIES\n"
    )
    (tmp_path / "app.py").write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    costs = {"app.f": 5, "app.g": 5}
    referenced = {"app.f": ["MAX_RETRIES"], "app.g": ["MAX_RETRIES"]}
    bundled, running_cost = _bundle_referenced_constants(
        builder, ["app.f", "app.g"], {"app.f", "app.g"}, referenced, costs, 10, 1000,
    )
    # Only the first symbol to reference it in `selected`'s own order
    # gets the constant bundled - never rendered twice.
    assert bundled == {"app.f": ["MAX_RETRIES = 5"]}
    assert "app.g" not in bundled


def test_bundle_referenced_constants_skips_a_constant_already_independently_selected(tmp_path):
    source = "MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n"
    (tmp_path / "app.py").write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    costs = {"app.f": 5, "app.MAX_RETRIES": 3}
    bundled, _ = _bundle_referenced_constants(
        builder, ["app.f", "app.MAX_RETRIES"], {"app.f", "app.MAX_RETRIES"},
        {"app.f": ["MAX_RETRIES"]}, costs, 8, 1000,
    )
    assert bundled == {}


def test_requested_path_bundles_with_no_budget_cap(tmp_path):
    (tmp_path / "app.py").write_text("MAX_RETRIES = 5\n\ndef f():\n    return MAX_RETRIES\n")
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    costs = {"app.f": 5}
    bundled, running_cost = _bundle_referenced_constants(
        builder, ["app.f"], {"app.f"}, {"app.f": ["MAX_RETRIES"]}, costs, 5, None,
    )
    assert bundled == {"app.f": ["MAX_RETRIES = 5"]}


# --------------------------------------------------------------------- #
# End-to-end: real build_context_package / pack_symbol_context wiring
# --------------------------------------------------------------------- #
def test_end_to_end_bundles_a_real_constant_into_the_rendered_body(tmp_path):
    source = (
        "MAX_RETRIES = 5\n"
        "STATUS_CODES = {200: 'OK', 404: 'Not Found', 500: 'Error'}\n\n"
        "def call_api():\n"
        "    if STATUS_CODES.get(200):\n"
        "        return MAX_RETRIES\n"
        "    return None\n"
    )
    (tmp_path / "app.py").write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    contracts = compute_or_load_contracts(builder, str(tmp_path))
    pkg = build_context_package(builder, "app.call_api", str(tmp_path), 4000, contracts=contracts)

    seed_node = next(n for n in pkg.nodes if n.id == "app.call_api")
    assert "def call_api" in seed_node.body
    assert "# Referenced module constants:" in seed_node.body
    assert "MAX_RETRIES = 5" in seed_node.body
    assert "STATUS_CODES = " in seed_node.body


def test_end_to_end_without_contracts_never_bundles_and_never_crashes(tmp_path):
    """`build_context_package` with `contracts=None` (a real, common
    caller shape) must behave exactly as it always did - no bundling,
    no crash from a missing `referenced_constants` map."""
    source = "MAX_RETRIES = 5\n\ndef call_api():\n    return MAX_RETRIES\n"
    (tmp_path / "app.py").write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    pkg = build_context_package(builder, "app.call_api", str(tmp_path), 4000)
    seed_node = next(n for n in pkg.nodes if n.id == "app.call_api")
    assert "# Referenced module constants:" not in seed_node.body


def test_pack_symbol_context_derives_referenced_constants_from_contracts_when_not_given_explicitly(tmp_path):
    """A caller that passes `contracts` directly to `pack_symbol_context`
    itself (not via the separate `referenced_constants` parameter) still
    gets the map derived automatically as a convenience default."""
    source = "MAX_RETRIES = 5\n\ndef call_api():\n    return MAX_RETRIES\n"
    (tmp_path / "app.py").write_text(source)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    contracts = compute_or_load_contracts(builder, str(tmp_path))
    result = pack_symbol_context(builder, "app.call_api", 4000, contracts=contracts)
    item = next(i for i in result.items if i.symbol == "app.call_api")
    assert item.bundled_constants == ["MAX_RETRIES = 5"]


def test_node_entry_without_bundled_constants_is_unaffected():
    item = SubmodularPackedItem(symbol="app.f", cost=5, feature_mask=0, dist_w=0.0)
    assert item.bundled_constants == []
