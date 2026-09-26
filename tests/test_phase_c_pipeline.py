"""Phase C, Step 2 (`docs/phase_c_architecture_spec.md` Section 1):
`PrismEngine.retrieve_two_or_three_pass`'s conditional routing branch,
and `build_external_candidate_manifest`'s own real, leaf-only external
resolution - against a small, self-contained synthetic repo (the same
"no network, no real clone" fixture discipline
`tests/test_behavioral_contracts.py` already establishes for the rest
of this engine's own machinery), not the real Django/FastAPI pilot
corpora: what's under test here is the routing/plumbing itself, not
retrieval quality on any one real corpus.
"""
from __future__ import annotations

import pytest

from prism.engine import PrismEngine


@pytest.fixture
def synthetic_engine(tmp_path):
    """One method (`dispatch`) whose real body calls `self.add_route(...)`
    - a method this tiny repo defines nowhere at all, the exact `t018`
    shape (a Starlette-inherited call Prism's own symbol table has no
    entry for) - plus a second, ordinary in-repo method (`helper`) that
    must never be mistaken for an external candidate."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "app.py").write_text(
        "class Router:\n"
        "    def dispatch(self, path):\n"
        "        self.add_route(path)\n"
        "        return path\n"
        "\n"
        "    def helper(self):\n"
        "        return 1\n"
    )
    return PrismEngine.from_repo(str(repo))


# --------------------------------------------------------------------- #
# retrieve_two_or_three_pass: conditional routing
# --------------------------------------------------------------------- #
class TestConditionalRouting:
    def test_needs_external_deps_false_never_calls_turn_2b(self, synthetic_engine):
        """Zero overhead: `request_external_symbols` must never be
        invoked at all when Turn 1 answers `needs_external_deps=False` -
        the whole reason for a conditional branch rather than an
        always-on three-pass."""
        turn_2b_calls = []

        def request_symbols(manifest_text, task_prompt):
            return ["app.Router.helper"], False

        def request_external_symbols(manifest_text, task_prompt):
            turn_2b_calls.append((manifest_text, task_prompt))
            return []

        pkg, diagnostics = synthetic_engine.retrieve_two_or_three_pass(
            "app.Router.dispatch", 4000, request_symbols, request_external_symbols,
            root_imports=["starlette"],
        )

        assert turn_2b_calls == [], "Turn 2b must never run when needs_external_deps=False"
        assert diagnostics["needs_external_deps"] is False
        assert diagnostics["external_candidate_count"] == 0
        assert diagnostics["external_requested_count"] == 0
        assert diagnostics["external_skipped_hallucinated"] == []
        assert all(n.role != "external" for n in pkg.nodes)

    def test_legacy_plain_list_callback_also_takes_the_false_branch(self, synthetic_engine):
        """Backward compatibility: a pre-Phase-C callback returning a
        plain `list[str]` (no tuple at all) is treated as
        `needs_external_deps=False`, not a crash or a misrouted call."""
        def legacy_request_symbols(manifest_text, task_prompt):
            return []

        pkg, diagnostics = synthetic_engine.retrieve_two_or_three_pass(
            "app.Router.dispatch", 4000, legacy_request_symbols,
        )
        assert diagnostics["needs_external_deps"] is False
        assert "app.Router.dispatch" in {n.id for n in pkg.nodes}

    def test_needs_external_deps_true_pulls_external_stub_into_package(self, synthetic_engine):
        """The real `t018`-shaped case: `dispatch`'s own
        `self.add_route(...)` call has zero in-repo definition, so Turn
        2a resolves it against the real, installed `starlette` package
        and Turn 3 renders it as a `role="external"` node in the final
        `ContextPackage`."""
        pytest.importorskip("starlette")

        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            lines = [ln for ln in manifest_text.split("\n")[1:-1] if ln]
            return [ln.split("|")[0] for ln in lines]

        pkg, diagnostics = synthetic_engine.retrieve_two_or_three_pass(
            "app.Router.dispatch", 4000, request_symbols, request_external_symbols,
            root_imports=["starlette"],
        )

        assert diagnostics["needs_external_deps"] is True
        assert diagnostics["external_candidate_count"] >= 1
        assert diagnostics["external_requested_count"] >= 1
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert external_nodes, "expected at least one external node in the final package"
        assert any(n.symbol_name == "add_route" for n in external_nodes)
        for n in external_nodes:
            assert n.compression == "L2_skeleton"
            assert n.contract is None

    def test_needs_external_deps_true_without_callback_raises(self, synthetic_engine):
        def request_symbols(manifest_text, task_prompt):
            return [], True

        with pytest.raises(ValueError):
            synthetic_engine.retrieve_two_or_three_pass("app.Router.dispatch", 4000, request_symbols)

    def test_external_budget_cannot_be_exceeded(self, synthetic_engine):
        """A `request_external_symbols` answer that keeps repeating a
        name already admitted must never grow the running external cost
        past `external_budget` - the sub-budget partition holds even
        under a pathological/repeated request."""
        pytest.importorskip("starlette")

        def request_symbols(manifest_text, task_prompt):
            return [], True

        def request_external_symbols(manifest_text, task_prompt):
            lines = [ln for ln in manifest_text.split("\n")[1:-1] if ln]
            names = [ln.split("|")[0] for ln in lines]
            return names * 50  # pathological over-request

        pkg, diagnostics = synthetic_engine.retrieve_two_or_three_pass(
            "app.Router.dispatch", 2000, request_symbols, request_external_symbols,
            root_imports=["starlette"],
        )
        from prism.packer.submodular_knapsack import split_budget_for_external

        _internal_budget, external_budget = split_budget_for_external(2000)
        external_nodes = [n for n in pkg.nodes if n.role == "external"]
        assert sum(n.cost for n in external_nodes) <= external_budget


# --------------------------------------------------------------------- #
# build_external_candidate_manifest: leaf-only resolution
# --------------------------------------------------------------------- #
class TestBuildExternalCandidateManifest:
    def test_no_root_imports_returns_empty_manifest(self, synthetic_engine):
        manifest_text, universe = synthetic_engine.build_external_candidate_manifest(["app.Router.dispatch"])
        assert universe == set()
        assert manifest_text == "<external_candidate_index>\n</external_candidate_index>"

    def test_resolves_self_call_with_zero_in_repo_candidates(self, synthetic_engine):
        pytest.importorskip("starlette")
        manifest_text, universe = synthetic_engine.build_external_candidate_manifest(
            ["app.Router.dispatch"], root_imports=["starlette"],
        )
        assert universe, "expected add_route to resolve against the real installed starlette package"
        assert any(name.endswith(".add_route") for name in universe)
        assert "<external_candidate_index>" in manifest_text

    def test_does_not_resolve_in_repo_method_as_external(self, synthetic_engine):
        """`helper` is a real in-repo definition - it must never appear
        in the external universe, guarding against the leaf-name
        resolution being too permissive."""
        _manifest_text, universe = synthetic_engine.build_external_candidate_manifest(
            ["app.Router.dispatch"], root_imports=["starlette"],
        )
        assert not any("helper" in name for name in universe)

    def test_unresolvable_package_yields_empty_universe(self, synthetic_engine):
        """A `root_imports` entry that resolves to no installed package
        at all degrades to no external candidates found - never a
        crash (Section 2.1's own fail-closed discipline)."""
        _manifest_text, universe = synthetic_engine.build_external_candidate_manifest(
            ["app.Router.dispatch"], root_imports=["this_package_does_not_exist_anywhere_xyz"],
        )
        assert universe == set()
