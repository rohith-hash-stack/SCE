"""Phase C, Step 3 (`docs/phase_c_architecture_spec.md`): an end-to-end
`t018` simulation against the real, pinned FastAPI corpus and the real,
installed Starlette package - the exact scenario Phase C exists to fix
(`reports/fastapi_seed42_closure_debrief.md`'s own `t018` finding):
`FastAPI.setup`'s real body calls `self.add_route(...)` several times,
and `add_route` has zero definition anywhere in the FastAPI corpus
itself (confirmed directly: `grep -rn "def add_route"` over the corpus
finds nothing) - it is only resolvable through Step 1's external-symbol
extraction and Step 2's conditional three-pass routing.

Marked slow (real corpus indexing), matching `tests/test_two_pass_
engine.py`'s own established convention - not a synthetic fixture,
since the whole point is proving this against the actual code that
originally exposed the gap, not a stand-in for it.
"""
from __future__ import annotations

import json

import pytest

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.base import selected_symbols
from benchmarks.tsr.scorer_debug import score_debug_causal
from prism.engine import PrismEngine
from prism.packer.submodular_knapsack import DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS, split_budget_for_external

pytestmark = pytest.mark.slow

SEED = "fastapi.applications.FastAPI.setup"
ROUTER_ADD_ROUTE = "starlette.routing.Router.add_route"
BUDGET = 4000


@pytest.fixture(scope="module")
def fastapi_engine() -> PrismEngine:
    """One real `PrismEngine`, indexed once against the real, pinned
    FastAPI corpus - indexing is the expensive, seed/budget-independent
    half of any call below."""
    repo_path = str(resolve("fastapi"))
    return PrismEngine.from_repo(repo_path)


@pytest.fixture(scope="module")
def t018_result(fastapi_engine):
    """The full three-pass simulation, computed once and shared across
    every assertion below: Turn 1 answers `needs_external_deps=True`
    with no explicit internal request (the seed itself already carries
    the real `self.add_route(...)` call sites Turn 2a needs); Turn 2b
    deterministically picks `Router.add_route` - the caller's own real
    selection out of the two genuine candidates Turn 2a offers
    (`Router.add_route` and a distinct, delegating `Starlette.
    add_route`), exactly like a production callback would parse a real
    model's JSON answer, not a stand-in for one."""
    def request_symbols(manifest_text, task_prompt):
        return [], True

    def request_external_symbols(manifest_text, task_prompt):
        return [ROUTER_ADD_ROUTE]

    return fastapi_engine.retrieve_two_or_three_pass(
        SEED, BUDGET, request_symbols, request_external_symbols, root_imports=["starlette"],
    )


class TestT018ExternalDependencySimulation:
    def test_seed_resolves_in_the_real_corpus(self, fastapi_engine):
        _manifest_text, candidate_universe = fastapi_engine.build_candidate_manifest(SEED)
        assert SEED in candidate_universe

    def test_turn_2a_resolves_both_real_add_route_definitions(self, fastapi_engine):
        """Starlette itself ships two real `add_route` definitions
        (`Router.add_route` and a delegating `Starlette.add_route`) -
        both must be offered as external candidates, not just whichever
        one an arbitrary file-locate ordering happens to find first."""
        _manifest_text, universe = fastapi_engine.build_external_candidate_manifest(
            [SEED], root_imports=["starlette"],
        )
        assert ROUTER_ADD_ROUTE in universe
        assert "starlette.applications.Starlette.add_route" in universe

    def test_package_contains_both_internal_and_external_nodes(self, t018_result):
        pkg, diagnostics = t018_result
        assert diagnostics["needs_external_deps"] is True
        assert diagnostics["external_candidate_count"] == 2
        assert diagnostics["external_requested_count"] == 1
        assert diagnostics["external_skipped_hallucinated"] == []

        internal_ids = {n.id for n in pkg.nodes if n.role != "external"}
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert SEED in internal_ids, "the seed itself (a real internal FastAPI node) must be present"
        assert len(external_nodes) == 1
        external_node = external_nodes[0]
        assert external_node.id == ROUTER_ADD_ROUTE
        assert external_node.symbol_name == "add_route"
        assert external_node.symbol_kind == "method"
        assert external_node.language == "python"
        assert external_node.compression == "L2_skeleton"
        assert external_node.contract is None

    def test_knapsack_partition_holds_within_the_125_percent_ceiling(self, t018_result):
        """The external stub's real token cost must fit inside its
        reserved sub-budget, and that sub-budget itself must never
        exceed the 12.5% fraction (bounded by the 256/1024 floor/
        ceiling) `split_budget_for_external` reserves - the admitted
        external node can never have evicted or outbid an internal
        node for space outside its own partition."""
        pkg, _diagnostics = t018_result
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        total_external_cost = sum(n.cost for n in external_nodes)

        _internal_budget, external_budget = split_budget_for_external(BUDGET)
        assert external_budget == round(BUDGET * 0.125) == 500, "4000 * 12.5% sits between the 256 floor and 1024 ceiling"
        assert external_budget <= DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS
        assert total_external_cost <= external_budget, "the admitted external stub must fit inside its own reserved partition"

    def test_scorer_recognizes_add_route_as_legitimate_not_hallucinated(self, t018_result):
        """The whole point of Phase C, made concrete: a model response
        naming `add_route` - the literal `t018` symptom that used to
        score as a hallucination - must be recognized as legitimate via
        `selected_symbols(pkg)`, with zero change to `score_debug_
        causal`'s own signature (Section 4.1's own "leaner path" claim,
        verified here empirically, not just argued in the spec)."""
        pkg, _diagnostics = t018_result
        candidate_symbols = selected_symbols(pkg)
        assert ROUTER_ADD_ROUTE in candidate_symbols

        response_text = json.dumps({
            "reasoning": "FastAPI.setup registers the OpenAPI/docs routes via add_route.",
            "symbols": ["FastAPI.setup", "add_route"],
        })
        score = score_debug_causal(response_text, pipeline=[], candidate_symbols=candidate_symbols)
        assert score == 1.0, "add_route must score as a legitimate candidate, not an illegitimate/hallucinated one"

    def test_a_genuinely_invented_symbol_still_scores_as_hallucinated(self, t018_result):
        """Control case: Phase C legitimizes real, resolved external
        symbols - it must not blanket-legitimize anything a model might
        name. A truly invented symbol must still zero out the score."""
        pkg, _diagnostics = t018_result
        candidate_symbols = selected_symbols(pkg)
        response_text = json.dumps({
            "reasoning": "made up",
            "symbols": ["totally_invented_symbol_that_does_not_exist_anywhere"],
        })
        score = score_debug_causal(response_text, pipeline=[], candidate_symbols=candidate_symbols)
        assert score == 0.0


class TestNeedsExternalDepsFalseControlPath:
    def test_declining_external_deps_yields_pure_two_pass_result(self, fastapi_engine):
        """Same seed, but Turn 1 declines external deps - must behave
        exactly like `retrieve_two_pass`: no external node at all, and
        no external diagnostics beyond the zeroed/empty defaults."""
        def request_symbols(manifest_text, task_prompt):
            return [], False

        pkg, diagnostics = fastapi_engine.retrieve_two_or_three_pass(SEED, BUDGET, request_symbols)

        assert diagnostics["needs_external_deps"] is False
        assert diagnostics["external_candidate_count"] == 0
        assert diagnostics["external_requested_count"] == 0
        assert diagnostics["external_skipped_hallucinated"] == []
        assert all(n.role != "external" for n in pkg.nodes)
