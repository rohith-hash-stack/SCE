"""Phase C multi-repo external indexing validation (post-merge, `develop`
@ `d0f5549`): the same in-tree tree-sitter indexing + `prism.external.
index` extraction machinery exercised across 4 independent,
architecturally different real installed packages - httpx (async HTTP
client), pydantic (schema validation with a compiled Rust core),
starlette (ASGI framework), and rich (terminal rendering) - each
package's own installed directory indexed as if it were the target repo
(`PrismEngine.from_repo`), never a git clone: this environment's own
restricted egress rules out cloning a fresh corpus the way the real
Django/FastAPI pilots do, but every package here is either already
installed or one `pip install` away, exactly like Starlette/orjson/
ujson already were in Phase C Steps 1-4.

Real, concrete external boundaries exercised throughout - chosen by
first grepping each package's own real source for a real call or
instantiation into its declared dependency, never invented:

  - **httpx -> httpcore**: `HTTPTransport.__init__`'s own
    `httpcore.ConnectionPool(...)` (and 4 sibling `httpcore.*(...)`
    calls in the same body - `HTTPProxy`, `SOCKSProxy`, `URL`).
  - **pydantic -> pydantic_core**: `BaseModel.parse_raw`'s own
    `pydantic_core.PydanticCustomError(...)` - resolved through
    `pydantic_core`'s own `.pyi` stub (a compiled Rust extension with no
    real `.py` source at all), the exact `.pyi`-dispatch fix from Phase
    C Step 4.
  - **starlette -> anyio / jinja2**: `WebSocketTestSession._run`'s own
    `anyio.sleep_forever()`/`anyio.CancelScope()`/`anyio.create_memory_
    object_stream(...)`, and `Jinja2Templates.__init__`'s own
    `jinja2.FileSystemLoader(...)`/`jinja2.Environment(...)`/
    `jinja2.select_autoescape(...)`.
  - **rich -> markdown_it**: a real, disclosed *negative* result, not a
    fourth positive case - see `TestRichMarkdownItImportStyleGap`'s own
    docstring for why, and what it means for Phase C's current scope.

Every package here is a real, incidental dependency already present (or
installed) in this environment, not a declared Prism dependency -
`pytest.importorskip` guards every class, matching the discipline
`tests/test_external_index.py` already established for Starlette/
orjson/ujson.
"""
from __future__ import annotations

import pathlib

import pytest

from prism.engine import PrismEngine
from prism.packer.submodular_knapsack import split_budget_for_external

RETRIEVAL_BUDGET = 4000


def _package_dir(module) -> str:
    return str(pathlib.Path(module.__file__).parent)


def _deterministic_external_request(manifest_text: str) -> list[str]:
    """Stands in for the caller's own Turn-2b LLM call: accepts every
    real candidate Turn 2a's manifest offered - the same "parse the
    model's answer" shape every other Phase C test uses, deterministic
    here since no real LLM call happens in this validation."""
    lines = [ln for ln in manifest_text.split("\n")[1:-1] if ln]
    return [ln.split("|")[0] for ln in lines]


def _assert_in_tree_indexing_is_real(engine: PrismEngine, seed_id: str) -> None:
    """Shared "Validation Goal 1" check: indexing produced a non-trivial
    symbol table, the chosen seed is really in it, and a real, full
    single-pass retrieval renders a non-empty body and at least one
    genuinely non-blank signature field for the seed - not a silent
    empty-contract placeholder. A generous budget (20x the standard
    4000) is used here specifically so the seed is never itself
    downgraded to a skeleton stub, which would make "is the signature
    real" unanswerable independent of budget pressure."""
    assert len(engine.builder.symbol_table) > 100, "expected a real, non-trivial package symbol table"
    assert seed_id in engine.builder.symbol_table

    pkg = engine.retrieve(seed_id, RETRIEVAL_BUDGET * 5)
    seed_node = next(n for n in pkg.nodes if n.id == seed_id)
    assert seed_node.body, "seed's own body must be real, non-empty source, not an empty placeholder"
    assert seed_node.compression == "L0_full"
    has_real_signature = bool(seed_node.signature.params) or seed_node.signature.returns is not None
    assert has_real_signature, f"{seed_id}'s own rendered signature is completely empty - a real parsing/contract gap"


# --------------------------------------------------------------------- #
# 1. httpx -> httpcore
# --------------------------------------------------------------------- #
class TestHttpxToHttpcore:
    SEED = "_transports.default.HTTPTransport.__init__"

    @staticmethod
    @pytest.fixture(scope="class")
    def engine() -> PrismEngine:
        httpx = pytest.importorskip("httpx")
        return PrismEngine.from_repo(_package_dir(httpx))

    def test_in_tree_indexing_is_real(self, engine):
        _assert_in_tree_indexing_is_real(engine, self.SEED)

    def test_external_resolution_finds_real_httpcore_classes(self, engine):
        _manifest_text, universe = engine.build_external_candidate_manifest([self.SEED], root_imports=["httpcore"])
        assert universe, "HTTPTransport.__init__ has 5 real httpcore.*(...) calls - expected real candidates"
        assert any(name.endswith(".ConnectionPool") for name in universe)

    def test_admitted_external_nodes_have_correct_role_compression_and_no_body_leakage(self, engine):
        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            return _deterministic_external_request(manifest_text)

        pkg, diagnostics = engine.retrieve_two_or_three_pass(
            self.SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["httpcore"],
        )
        assert diagnostics["needs_external_deps"] is True
        assert diagnostics["external_skipped_hallucinated"] == []

        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert external_nodes
        for node in external_nodes:
            assert node.role == "external"
            assert node.compression == "L2_skeleton"
            assert node.contract is None
            # Zero body bloat: a bare `class Name:` + placeholder, never
            # httpcore's own real (and much larger) class body.
            assert node.body == f"class {node.symbol_name}:\n    ..."

    def test_budget_partitioning_holds(self, engine):
        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            return _deterministic_external_request(manifest_text)

        pkg, _diagnostics = engine.retrieve_two_or_three_pass(
            self.SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["httpcore"],
        )
        _internal_budget, external_budget = split_budget_for_external(RETRIEVAL_BUDGET)
        total_external_cost = sum(n.cost for n in pkg.nodes if n.role == "external")
        assert total_external_cost <= external_budget


# --------------------------------------------------------------------- #
# 2. pydantic -> pydantic_core (a compiled Rust core, .pyi-only)
# --------------------------------------------------------------------- #
class TestPydanticToPydanticCore:
    SEED = "main.BaseModel.parse_raw"

    @staticmethod
    @pytest.fixture(scope="class")
    def engine() -> PrismEngine:
        pydantic = pytest.importorskip("pydantic")
        return PrismEngine.from_repo(_package_dir(pydantic))

    def test_in_tree_indexing_is_real(self, engine):
        _assert_in_tree_indexing_is_real(engine, self.SEED)

    def test_external_resolution_finds_pydantic_core_via_its_pyi_stub(self, engine):
        """pydantic_core is a compiled extension with no real .py source
        at all - this only resolves at all because of Phase C Step 4's
        .pyi dispatch fix (EXTENSION_LANGUAGE_MAP has no .pyi entry,
        parse_file alone would silently return None for it)."""
        from prism.external.index import extract_external_symbol_all

        results = extract_external_symbol_all("pydantic_core", "PydanticCustomError")
        assert results, "PydanticCustomError must resolve from pydantic_core's own .pyi stub"
        assert all(r.file.endswith(".pyi") for r in results)

        _manifest_text, universe = engine.build_external_candidate_manifest([self.SEED], root_imports=["pydantic_core"])
        assert any(name.endswith(".PydanticCustomError") for name in universe)

    def test_admitted_external_node_has_correct_role_compression_and_no_body_leakage(self, engine):
        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            return _deterministic_external_request(manifest_text)

        pkg, diagnostics = engine.retrieve_two_or_three_pass(
            self.SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["pydantic_core"],
        )
        assert diagnostics["needs_external_deps"] is True
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert external_nodes
        for node in external_nodes:
            assert node.compression == "L2_skeleton"
            assert node.contract is None
            assert "..." in node.body

    def test_budget_partitioning_holds(self, engine):
        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            return _deterministic_external_request(manifest_text)

        pkg, _diagnostics = engine.retrieve_two_or_three_pass(
            self.SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["pydantic_core"],
        )
        _internal_budget, external_budget = split_budget_for_external(RETRIEVAL_BUDGET)
        total_external_cost = sum(n.cost for n in pkg.nodes if n.role == "external")
        assert total_external_cost <= external_budget


# --------------------------------------------------------------------- #
# 3. starlette -> anyio and jinja2 (two independent boundaries)
# --------------------------------------------------------------------- #
class TestStarletteToAnyioAndJinja2:
    ANYIO_SEED = "testclient.WebSocketTestSession._run"
    JINJA2_SEED = "templating.Jinja2Templates.__init__"

    @staticmethod
    @pytest.fixture(scope="class")
    def engine() -> PrismEngine:
        starlette = pytest.importorskip("starlette")
        return PrismEngine.from_repo(_package_dir(starlette))

    def test_in_tree_indexing_is_real(self, engine):
        _assert_in_tree_indexing_is_real(engine, self.ANYIO_SEED)
        _assert_in_tree_indexing_is_real(engine, self.JINJA2_SEED)

    def test_anyio_boundary_resolves_real_symbols_without_body_leakage(self, engine):
        pytest.importorskip("anyio")

        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            return _deterministic_external_request(manifest_text)

        pkg, diagnostics = engine.retrieve_two_or_three_pass(
            self.ANYIO_SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["anyio"],
        )
        assert diagnostics["needs_external_deps"] is True
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert external_nodes
        assert any(n.id.endswith(".sleep_forever") for n in external_nodes)
        for node in external_nodes:
            assert node.role == "external"
            assert node.compression == "L2_skeleton"
            assert node.contract is None
            # The real anyio.sleep_forever() body (`await sleep(math.inf)`)
            # must never leak into the rendered stub.
            assert "await sleep(math.inf)" not in node.body

        _internal_budget, external_budget = split_budget_for_external(RETRIEVAL_BUDGET)
        assert sum(n.cost for n in external_nodes) <= external_budget

    def test_jinja2_boundary_resolves_real_symbols_without_body_leakage(self, engine):
        pytest.importorskip("jinja2")

        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            return _deterministic_external_request(manifest_text)

        pkg, diagnostics = engine.retrieve_two_or_three_pass(
            self.JINJA2_SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["jinja2"],
        )
        assert diagnostics["needs_external_deps"] is True
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert external_nodes
        assert any(n.id.endswith(".select_autoescape") for n in external_nodes)
        assert any(n.id.endswith(".FileSystemLoader") for n in external_nodes)
        for node in external_nodes:
            assert node.compression == "L2_skeleton"
            assert node.contract is None
            # The real jinja2.select_autoescape's own inner closure/logic
            # must never leak into the rendered stub.
            assert "enabled_patterns" not in node.body

        _internal_budget, external_budget = split_budget_for_external(RETRIEVAL_BUDGET)
        assert sum(n.cost for n in external_nodes) <= external_budget


# --------------------------------------------------------------------- #
# 4. rich -> markdown_it: a real, disclosed negative result
# --------------------------------------------------------------------- #
class TestRichMarkdownItImportStyleGap:
    """Real finding, not a fourth positive case: `rich.markdown.Markdown.
    __init__`'s real body calls `MarkdownIt().enable(...)` - but rich
    imports it as `from markdown_it import MarkdownIt`, not
    `import markdown_it`. `call_callee_segments` on that call therefore
    returns a single-segment `["MarkdownIt"]`, not a two-segment
    `["markdown_it", "MarkdownIt"]` - neither of `build_external_
    candidate_manifest`'s two resolution paths (a `root_imports`-package
    receiver, or a `self`/`this` receiver) matches a bare, single-
    segment call at all, by construction (`len(segments) != 2: continue`
    in both branches).

    This is a genuine, previously undiscovered scope boundary of Phase
    C's current leaf-only design, found here by testing against real
    code (rich consistently uses `from X import Y` throughout its own
    dependency usage - a real, common, and entirely reasonable Python
    style this design does not yet reach) - not a bug in the sense of
    "raises" or "crashes" (it fails closed, exactly as designed), but a
    real coverage gap worth recording rather than silently working
    around by cherry-picking a different, resolvable call site instead.
    Extending resolution to a directly-imported name would mean tracking
    each file's own `from X import Y` aliases back to their real origin
    module - real, additional work, not something this validation
    changes on its own.
    """

    SEED = "markdown.Markdown.__init__"

    @staticmethod
    @pytest.fixture(scope="class")
    def engine() -> PrismEngine:
        rich = pytest.importorskip("rich")
        return PrismEngine.from_repo(_package_dir(rich))

    def test_in_tree_indexing_is_real(self, engine):
        _assert_in_tree_indexing_is_real(engine, self.SEED)

    def test_from_import_style_call_is_not_resolved_but_fails_closed(self, engine):
        pytest.importorskip("markdown_it")

        manifest_text, universe = engine.build_external_candidate_manifest([self.SEED], root_imports=["markdown_it"])

        assert universe == set(), (
            "Markdown.__init__'s MarkdownIt() call is a bare, single-segment call to a "
            "from-imported name - not reachable by either of build_external_candidate_manifest's "
            "two resolution paths. This is the expected, disclosed result, not a bug to silence."
        )
        assert manifest_text == "<external_candidate_index>\n</external_candidate_index>"

    def test_needs_external_deps_true_with_no_resolvable_candidates_still_completes_cleanly(self, engine):
        """Even when Turn 1 asks for external deps but Turn 2a resolves
        nothing at all, the pipeline must still complete cleanly - an
        empty candidate universe means Turn 2b is never called at all
        (nothing to select from), and the final package is exactly the
        internal-only result, never a crash or a stuck state."""
        turn_2b_calls = []

        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            turn_2b_calls.append(manifest_text)
            return []

        pkg, diagnostics = engine.retrieve_two_or_three_pass(
            self.SEED, RETRIEVAL_BUDGET, request_symbols, request_external_symbols, root_imports=["markdown_it"],
        )

        assert turn_2b_calls == [], "Turn 2b must never be called against an empty external candidate universe"
        assert diagnostics["external_candidate_count"] == 0
        assert diagnostics["external_requested_count"] == 0
        assert all(n.role != "external" for n in pkg.nodes)
        assert any(n.id == self.SEED for n in pkg.nodes)
