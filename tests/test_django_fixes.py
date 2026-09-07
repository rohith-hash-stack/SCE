"""Regression tests for the four structural fixes made after the live
33-prompt django/django benchmark run surfaced 8 failures:

1. Module/class/instance-level attribute indexing (concrete_builder.py) -
   so real code like `db_for_write = _router_func(...)` or
   `self._iterable_class(...)` isn't mistaken for a hallucination.
2. Line-range + relative-path annotations in packed headers
   (compressor.py / markdown.py) - exact grounding back to the real file.
3. Adaptive Compact Scaffolding for small contexts (knapsack.py) - SCE's
   own scaffold shouldn't make a small package bigger than a raw dump.
4. Socratic verification robustness (prompt_taxonomy) - accept an
   interrogative response whether or not it uses literal "?" punctuation.

Each fix also has focused tests in its own module's test file
(test_symbol_resolution.py, test_compressor.py, etc.) - this file exercises
them together, close to how they were actually found: a small, real,
attribute-heavy Python fixture standing in for the pattern django/django
uses pervasively.

A separate opt-in suite at the bottom checks the *exact* named symbols from
the live run (`db_for_write`, `_iterable_class`) against the real,
already-cloned django/django repo - skipped unless
`SCE_LIVE_NETWORK_TESTS=1` is set, mirroring every other real-network test
in this suite.
"""
from __future__ import annotations

import os
import re

import pytest

from sce.cli import build_pipeline
from sce.graph.metamodel import SemanticMetamodel
from sce.serializers.markdown import render_markdown
from sce.slicer.distance import DistanceConfig, DistanceEngine
from sce.slicer.knapsack import ContextKnapsackPacker

PYTHON_FIXTURE = "tests/fixtures/python_repo"
STRESS_FIXTURE = "benchmarks/fixtures/stress_repo"


# --------------------------------------------------------------------- #
# Fix 1: module/class/instance-level attribute indexing
# --------------------------------------------------------------------- #
@pytest.fixture
def attribute_heavy_repo(tmp_path):
    """A small, self-contained repo exercising all three attribute shapes
    the live django run found real, correct code using: a module-level
    dynamically-assigned callable (`db_for_write`), a class-body-level one
    (`_iterable_class`), and an instance-level one set in `__init__`
    (`_middleware_chain`) - deliberately named after the real django
    symbols this fix was built to stop flagging as hallucinations.
    """
    repo = tmp_path / "attr_repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "router.py").write_text(
        "def _router_func(action):\n"
        "    def _route(self, model):\n"
        "        return action\n"
        "    return _route\n"
        "\n"
        "\n"
        "class ConnectionRouter:\n"
        "    db_for_read = _router_func('db_for_read')\n"
        "    db_for_write = _router_func('db_for_write')\n"
        "\n"
        "\n"
        "router = ConnectionRouter()\n"
    )
    (repo / "app" / "query.py").write_text(
        "class ModelIterable:\n"
        "    pass\n"
        "\n"
        "\n"
        "class QuerySet:\n"
        "    _iterable_class = ModelIterable\n"
        "\n"
        "    def _fetch_all(self):\n"
        "        return self._iterable_class(self)\n"
    )
    (repo / "app" / "handlers.py").write_text(
        "class BaseHandler:\n"
        "    _middleware_chain = None\n"
        "\n"
        "    def load_middleware(self, handler):\n"
        "        self._middleware_chain = handler\n"
        "\n"
        "    def get_response(self, request):\n"
        "        return self._middleware_chain(request)\n"
    )
    return str(repo)


def test_module_level_dynamic_attribute_is_indexed(attribute_heavy_repo):
    builder, _ = build_pipeline(attribute_heavy_repo)
    symbol = builder.symbol_table.get("app.router.ConnectionRouter.db_for_write")
    assert symbol is not None
    assert symbol.kind == "attribute"
    assert symbol.enclosing_class == "app.router.ConnectionRouter"


def test_class_body_level_attribute_is_indexed(attribute_heavy_repo):
    builder, _ = build_pipeline(attribute_heavy_repo)
    symbol = builder.symbol_table.get("app.query.QuerySet._iterable_class")
    assert symbol is not None
    assert symbol.kind == "attribute"


def test_instance_level_self_attribute_is_indexed(attribute_heavy_repo):
    builder, _ = build_pipeline(attribute_heavy_repo)
    symbol = builder.symbol_table.get("app.handlers.BaseHandler._middleware_chain")
    assert symbol is not None
    assert symbol.kind == "attribute"


def test_real_def_always_wins_over_a_same_named_attribute(attribute_heavy_repo):
    """A real method's qualified name must never be shadowed by an
    attribute registration - `_route_db`-style factories aside, a `def` is
    always authoritative."""
    builder, _ = build_pipeline(attribute_heavy_repo)
    symbol = builder.symbol_table.get("app.handlers.BaseHandler.get_response")
    assert symbol.kind == "method"


def test_attribute_call_resolves_to_a_real_graph_node(attribute_heavy_repo):
    """`self._iterable_class(self)` inside `_fetch_all` must resolve to the
    real attribute node, not dangle as an unresolved external call."""
    builder, _ = build_pipeline(attribute_heavy_repo)
    assert builder.graph.has_edge("app.query.QuerySet._fetch_all", "app.query.QuerySet._iterable_class")
    assert "app.query.QuerySet._iterable_class" in builder.symbol_table


def test_attribute_calls_are_not_flagged_as_hallucinated(attribute_heavy_repo):
    """The exact scenario the live run found: code that calls a real
    attribute-bound reference must not be flagged as calling something
    fabricated, using the same hallucination-checker logic
    validate_llm_accuracy.py and large_repo_prompt_matrix.py both use."""
    builder, _ = build_pipeline(attribute_heavy_repo)
    known_simple_names = frozenset(qname.rsplit(".", 1)[-1] for qname in builder.symbol_table.all_qualified_names())
    assert "_iterable_class" in known_simple_names
    assert "_middleware_chain" in known_simple_names
    assert "db_for_write" in known_simple_names


def test_attribute_symbols_are_excluded_from_knapsack_candidates(attribute_heavy_repo):
    """Attributes are indexed for hallucination-checking purposes, not
    packed as their own context items - only functions/methods compete for
    knapsack slots (unchanged design)."""
    builder, tag_matrix = build_pipeline(attribute_heavy_repo)
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=2000).pack(
        "app.query.QuerySet._fetch_all", builder, tag_matrix, distance_engine
    )
    packed_symbols = {item.symbol for item in pack_result.items}
    assert "app.query.QuerySet._iterable_class" not in packed_symbols


# --------------------------------------------------------------------- #
# Fix 2: line-range + relative-path headers
# --------------------------------------------------------------------- #
_HEADER_LINES_RE = re.compile(r"\(L\d - lines \d+-\d+ in [\w./-]+\.py\)")


def test_headers_include_line_range_and_relative_path():
    # A richly-connected target (not one small enough to trigger Adaptive
    # Compact Scaffolding - see the Fix 3 tests below for that case).
    builder, tag_matrix = build_pipeline(STRESS_FIXTURE)
    target = "app.controllers.orders.OrderController.process_order"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=4000).pack(target, builder, tag_matrix, distance_engine)
    markdown_text = render_markdown(pack_result, tag_matrix)
    assert not pack_result.compact, "this target should be large enough to render the full (non-compact) headers"
    matches = _HEADER_LINES_RE.findall(markdown_text)
    assert matches, f"expected at least one '(L<n> - lines X-Y in path.py)' header, got:\n{markdown_text}"
    # Every packed item's own line_range/relative_path must show up verbatim.
    for item in pack_result.items:
        start, end = item.line_range
        assert f"lines {start}-{end} in {item.relative_path}" in markdown_text


def test_seed_target_header_is_grounded_to_its_real_file():
    builder, tag_matrix = build_pipeline(STRESS_FIXTURE)
    target = "app.controllers.orders.OrderController.process_order"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=4000).pack(target, builder, tag_matrix, distance_engine)
    markdown_text = render_markdown(pack_result, tag_matrix)
    symbol = builder.symbol_table.get(target)
    expected_path = os.path.relpath(symbol.file, builder.repo_root)
    start, end = symbol.line_range
    assert f"[TARGET] {target} (L0 - lines {start}-{end} in {expected_path})" in markdown_text


# --------------------------------------------------------------------- #
# Fix 3: Adaptive Compact Scaffolding for small contexts
# --------------------------------------------------------------------- #
def test_small_context_triggers_compact_mode_and_drops_architectural_path():
    builder, tag_matrix = build_pipeline(PYTHON_FIXTURE)
    # A near-leaf method with a tiny call-chain footprint.
    target = "src.services.billing.PaymentProcessor.charge"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=3000).pack(target, builder, tag_matrix, distance_engine)
    assert pack_result.compact is True

    markdown_text = render_markdown(pack_result, tag_matrix)
    assert "## 1. Architectural Path" not in markdown_text
    assert f"### [TARGET] {target} (L0)" in markdown_text
    # No line-range/path suffix in compact mode.
    assert not _HEADER_LINES_RE.search(markdown_text)


def test_compact_mode_achieves_positive_or_near_parity_compression_on_small_fixture():
    from benchmarks.raw_context import build_raw_context
    from benchmarks.tokenizer import count_tokens

    builder, tag_matrix = build_pipeline(PYTHON_FIXTURE)
    target = "src.services.billing.PaymentProcessor.charge"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=3000).pack(target, builder, tag_matrix, distance_engine)
    assert pack_result.compact is True

    markdown_text = render_markdown(pack_result, tag_matrix)
    raw_text = build_raw_context(builder, target).text
    sce_tokens = count_tokens(markdown_text)
    raw_tokens = count_tokens(raw_text)
    # "Matches or beats" with a small allowance for the document's fixed
    # preamble (title/budget/allocation lines), which no amount of content
    # trimming removes - see benchmarks/README.md for the measured margin
    # on this exact target (155 raw vs. 157 sce tokens).
    assert sce_tokens <= raw_tokens + 10, (
        f"compact-mode package ({sce_tokens} tok) should match or nearly match the raw dump ({raw_tokens} tok)"
    )


def test_large_context_does_not_trigger_compact_mode():
    """A richly-connected target (the stress fixture's whole point) must
    keep the full scaffold - compact mode is for small neighborhoods only."""
    builder, tag_matrix = build_pipeline(STRESS_FIXTURE)
    target = "app.controllers.orders.OrderController.process_order"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=4000).pack(target, builder, tag_matrix, distance_engine)
    assert pack_result.compact is False
    markdown_text = render_markdown(pack_result, tag_matrix)
    assert "## 1. Architectural Path" in markdown_text


def test_compact_mode_still_packs_a_requires_target_contract():
    """The knapsack's "requires" injection (a confirmed metamodel
    obligation like #db_write REQUIRES_BEFORE #auth_guard) must still get a
    real contract block even in compact mode - dropping it would resurrect
    the exact live-model failure that motivated packing it in the first
    place (see benchmarks/README.md's validate_llm_accuracy.py section)."""
    builder, tag_matrix = build_pipeline("benchmarks/fixtures/accuracy_repo")
    target = "app.orders.OrderService.checkout_order"
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=2000).pack(target, builder, tag_matrix, distance_engine)
    packed_symbols = {item.symbol for item in pack_result.items}
    assert "app.auth.verify_session" in packed_symbols


# --------------------------------------------------------------------- #
# Fix 4: Socratic verification robustness (required_any_regexes)
# --------------------------------------------------------------------- #
def test_socratic_archetype_accepts_declarative_interrogative_phrasing():
    from benchmarks.prompt_taxonomy import ARCHETYPES_BY_SLUG
    from benchmarks.prompt_taxonomy.spec import check_required_substrings

    archetype = ARCHETYPES_BY_SLUG["socratic"]
    declarative_response = (
        "I want to know how should transaction.atomic() behave when nested - "
        "does the inner block get its own savepoint."
    )
    checks = check_required_substrings(
        declarative_response, archetype.required_any_substrings, archetype.required_all_substrings,
        archetype.required_regexes, archetype.required_any_regexes,
    )
    assert checks.get("required_any_regex_matched") is True


def test_socratic_archetype_still_accepts_literal_question_mark():
    from benchmarks.prompt_taxonomy import ARCHETYPES_BY_SLUG
    from benchmarks.prompt_taxonomy.spec import check_required_substrings

    archetype = ARCHETYPES_BY_SLUG["socratic"]
    response = "Does the inner atomic block get its own savepoint?"
    checks = check_required_substrings(
        response, archetype.required_any_substrings, archetype.required_all_substrings,
        archetype.required_regexes, archetype.required_any_regexes,
    )
    assert checks.get("required_any_regex_matched") is True


def test_socratic_archetype_rejects_a_flat_statement():
    from benchmarks.prompt_taxonomy import ARCHETYPES_BY_SLUG
    from benchmarks.prompt_taxonomy.spec import check_required_substrings

    archetype = ARCHETYPES_BY_SLUG["socratic"]
    response = "Nested atomic blocks use savepoints."
    checks = check_required_substrings(
        response, archetype.required_any_substrings, archetype.required_all_substrings,
        archetype.required_regexes, archetype.required_any_regexes,
    )
    assert checks.get("required_any_regex_matched") is False


# --------------------------------------------------------------------- #
# Opt-in: the exact named symbols from the live run, against real django
# --------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("SCE_LIVE_NETWORK_TESTS") != "1",
    reason="set SCE_LIVE_NETWORK_TESTS=1 to index the real django/django clone (large, network-dependent)",
)
def test_real_django_db_for_write_and_iterable_class_are_indexed():
    from benchmarks.large_repo_prompt_matrix import DEFAULT_CACHE_DIR, index_repo

    builder, _tag_matrix, _metrics = index_repo("django", DEFAULT_CACHE_DIR, force_clone=False)
    db_for_write = builder.symbol_table.get("django.db.utils.ConnectionRouter.db_for_write")
    assert db_for_write is not None
    assert db_for_write.kind == "attribute"

    iterable_class = builder.symbol_table.get("django.db.models.query.QuerySet._iterable_class")
    assert iterable_class is not None
    assert iterable_class.kind == "attribute"

    middleware_chain = builder.symbol_table.get("django.core.handlers.base.BaseHandler._middleware_chain")
    assert middleware_chain is not None
    assert middleware_chain.kind == "attribute"
