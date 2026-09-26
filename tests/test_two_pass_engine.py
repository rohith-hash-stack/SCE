"""Track 2 (Phase B Two-Pass Engine Integration) validation gate.

Covers the three production pieces graduated from the noise-reduction
spike's Approach A v3 (`experiment/noise-filtering-spike`'s own
`benchmarks/experiments/hydration_loop.py`, validated in `reports/
spike_noise_reduction_debrief.md` and `docs/design_formalism.md` Sec
10.5) into `src/prism`:

  1. `prism.packer.candidate_index.build_candidate_manifest` - the
     hop=3 + scope-filtered Turn-1 manifest builder.
  2. `prism.surface.build._PROTECTED_DOWNGRADE_ROLES` - the seed/
     1-hop-neighbor skeleton-downgrade protection in
     `_enforce_render_budget`, fixing the real `django_t02_009` @
     budget=2000 failure the spike diagnosed (every node, including the
     seed, downgraded to `L2_skeleton`, stripping a call site the model
     needed - not a missing symbol, a missing detail).
  3. `prism.engine.PrismEngine.build_candidate_manifest`/
     `retrieve_requested`/`retrieve_two_pass` - the engine-level
     two-pass execution path wired around both of the above.

Runs the real engine against the real, pinned Django corpus, the same
"no mocking, no synthetic fixture graph" discipline `tests/test_prism_
selection_regressions.py` already established for this codebase - the
production pack/build/render pipeline this exercises has no meaningful
behavior to characterize against a hand-built toy graph. Marked
`@pytest.mark.slow`, module-scoped fixtures, same reasoning as that
file's own docstring.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.corpora.resolver import resolve
from benchmarks.ground_truth.loader import load_tasks_from_dir
from prism.engine import PrismEngine
from prism.packer.submodular_knapsack import SeedNotFoundError

pytestmark = pytest.mark.slow

TASKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "ground_truth" / "tasks" / "django"

#: The spike's own 3 target tasks (`inspect_manifest_sizing.py`'s
#: offline recall=1.000 finding, then confirmed live) - real task ids,
#: not a guess.
SPIKE_TARGET_TASK_IDS = (
    "django_t02_005_model_save_signals",
    "django_t02_009_queryset_filter_clone",
    "django_t02_017_redirect_url_safety_check",
)


@pytest.fixture(scope="module")
def django_repo_path() -> str:
    return str(resolve("django"))


@pytest.fixture(scope="module")
def django_tasks(django_repo_path) -> dict:
    result = load_tasks_from_dir(TASKS_DIR)
    assert not result.rejected, f"ground-truth tasks failed to load: {result.rejected}"
    return {t.task_id: t for t in result.accepted}


@pytest.fixture(scope="module")
def engine(django_repo_path) -> PrismEngine:
    """One real `PrismEngine`, indexed once against the real, pinned
    Django checkout and reused across every test below - indexing is
    the expensive, seed/budget-independent half of any call here."""
    return PrismEngine.from_repo(django_repo_path)


# --------------------------------------------------------------------- #
# Item 1 - Candidate Index & Manifest Generator
# --------------------------------------------------------------------- #
class TestCandidateManifest:
    def test_seed_always_in_candidate_universe(self, engine, django_tasks):
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)
        assert task.seed_symbol in candidate_universe
        assert task.seed_symbol in manifest_text

    def test_manifest_recall_on_three_target_tasks(self, engine, django_tasks):
        """The spike's own offline-verified recall=1.000 claim at
        hop=3: every real ground-truth pipeline symbol for each of the
        spike's own 3 target tasks must appear in the Turn-1 candidate
        universe - the whole point of the scope-filtered hop=3 design
        is zero recall loss versus the unbounded v2 manifest."""
        for task_id in SPIKE_TARGET_TASK_IDS:
            task = django_tasks[task_id]
            _manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)
            missing = set(task.adjudicated.pipeline_symbols) - candidate_universe
            assert not missing, f"{task_id}: pipeline symbols missing from candidate universe: {missing}"

    def test_manifest_line_shape(self, engine, django_tasks):
        """Phase B patch: a `role == "caller"` row carries two additional
        pipe-delimited contract fields (`binds_return=`/
        `nontrivial_args=`) beyond the base 5 - every other role stays
        at exactly 5 fields, unchanged."""
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)
        assert manifest_text.startswith("<candidate_index>\n")
        assert manifest_text.endswith("\n</candidate_index>")
        lines = manifest_text.split("\n")[1:-1]
        assert len(lines) == len(candidate_universe), "one manifest line per real candidate, no more, no fewer"
        seen = set()
        for line in lines:
            parts = line.split("|")
            qname, role, _kind, _signature, calls_field = parts[:5]
            assert role in ("seed", "callee", "caller", "transitive")
            if role == "caller":
                assert len(parts) == 7, f"expected 7 pipe-delimited fields for a caller row, got {line!r}"
                binds_return_field, nontrivial_args_field = parts[5], parts[6]
                assert binds_return_field in ("binds_return=true", "binds_return=false")
                assert nontrivial_args_field in ("nontrivial_args=true", "nontrivial_args=false")
            else:
                assert len(parts) == 5, f"expected 5 pipe-delimited fields (qname|role|kind|signature|calls), got {line!r}"
            assert qname in candidate_universe
            assert calls_field.startswith("calls=[") and calls_field.endswith("]")
            seen.add(qname)
        assert seen == candidate_universe

    def test_unknown_seed_raises_seed_not_found(self, engine):
        with pytest.raises(SeedNotFoundError):
            engine.build_candidate_manifest("not.a.real.symbol.at.all")


# --------------------------------------------------------------------- #
# Item 2 - seed/1-hop-neighbor skeleton-downgrade protection
# --------------------------------------------------------------------- #
class TestSkeletonDowngradeProtection:
    def test_seed_never_downgraded_at_tight_budget(self, engine, django_tasks):
        """Standard single-pass retrieve() path (`build_context_
        package`, knapsack-driven) - budget=2000 is the exact budget
        the spike's own django_t02_009 diagnosis used."""
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        pkg = engine.retrieve(task.seed_symbol, 2000)
        seed_node = next(n for n in pkg.nodes if n.id == pkg.seed.symbol)
        assert seed_node.compression == "L0_full"
        assert seed_node.body, "protected seed must keep a real, non-empty body"

    def test_direct_causal_neighbors_never_downgraded_at_tight_budget(self, engine, django_tasks):
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        pkg = engine.retrieve(task.seed_symbol, 2000)
        protected = [n for n in pkg.nodes if n.role in ("callee", "caller")]
        assert protected, "expected at least one 1-hop neighbor in this pack for this test to be meaningful"
        for node in protected:
            assert node.compression == "L0_full", f"{node.id} (role={node.role}) was downgraded despite protection"

    def test_transitive_nodes_remain_eligible_for_downgrade(self, engine, django_tasks):
        """The protection is scoped to the seed and 1-hop neighbors
        only - a `ROLE_TRANSITIVE` node must still be a real, usable
        downgrade target, or `_enforce_render_budget` would have no way
        left to close the render-budget gap at all."""
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        pkg = engine.retrieve(task.seed_symbol, 2000)
        transitive = [n for n in pkg.nodes if n.role == "transitive"]
        if not transitive:
            pytest.skip("no transitive-role node in this pack at this budget - nothing to characterize")
        assert any(n.compression == "L2_skeleton" for n in transitive), (
            "expected at least one transitive node downgraded to close the render-budget gap at this tight a budget"
        )

    def test_two_pass_seed_retains_full_body_at_budget_2000(self, engine, django_tasks):
        """The exact real failure this protects against
        (`reports/spike_noise_reduction_debrief.md`'s own Closing
        Note): at budget=2000, every node - including the seed - was
        downgraded to `L2_skeleton`, stripping the literal call site
        text the model needed. Not a missing-symbol failure (selection
        was already correct); a missing-*detail* one."""
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        _manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)
        requested = [s for s in task.adjudicated.pipeline_symbols if s in candidate_universe]
        assert requested, "test fixture assumption: at least one real pipeline symbol resolves against the manifest"
        pkg, _skipped = engine.retrieve_requested(task.seed_symbol, 2000, requested, candidate_universe)
        seed_node = next(n for n in pkg.nodes if n.id == pkg.seed.symbol)
        assert seed_node.compression == "L0_full"
        assert seed_node.body


# --------------------------------------------------------------------- #
# Item 3 - PrismEngine two-pass execution path
# --------------------------------------------------------------------- #
class TestTwoPassEngine:
    def test_retrieve_requested_drops_hallucinated_names(self, engine, django_tasks):
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        _manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)
        real = next(iter(candidate_universe - {task.seed_symbol}))
        fake = "django.totally.made.up.Symbol.that.does.not.exist"
        pkg, skipped = engine.retrieve_requested(task.seed_symbol, 4000, [real, fake], candidate_universe)
        selected = {n.id for n in pkg.nodes}
        assert real in selected
        assert fake not in selected
        assert skipped == [fake]

    def test_retrieve_two_pass_end_to_end_with_deterministic_callback(self, engine, django_tasks):
        """No LLM in this test: `request_symbols` is a deterministic
        stand-in for the caller's own model call, proving the engine
        never makes that call itself - only wires the manifest in and
        the caller's answer back out."""
        task = django_tasks["django_t02_009_queryset_filter_clone"]
        seen_calls = []

        def deterministic_request(manifest_text: str, task_prompt: str) -> list[str]:
            seen_calls.append((manifest_text, task_prompt))
            return list(task.adjudicated.pipeline_symbols)

        pkg, diagnostics = engine.retrieve_two_pass(
            task.seed_symbol, 4000, deterministic_request, task_prompt=task.prompt,
        )

        assert len(seen_calls) == 1, "request_symbols must be called exactly once per retrieve_two_pass call"
        manifest_seen, prompt_seen = seen_calls[0]
        assert task.seed_symbol in manifest_seen
        assert prompt_seen == task.prompt

        selected = {n.id for n in pkg.nodes}
        assert task.seed_symbol in selected
        requested_and_resolvable = set(task.adjudicated.pipeline_symbols) & set(diagnostics["skipped_hallucinated"])
        assert not requested_and_resolvable, "no real ground-truth pipeline symbol should ever be reported as hallucinated"
        assert diagnostics["candidate_count"] > 0
        assert diagnostics["requested_count"] == len(task.adjudicated.pipeline_symbols)

    def test_retrieve_requested_auto_includes_seed_direct_callees_even_with_empty_request(self, engine, django_tasks):
        """Pilot-4 autopsy (`django_t02_001_request_middleware_chain`):
        `check_response` was directly listed in the seed's own
        `calls=[...]` manifest field - visible, not a manifest gap - yet
        Turn 1 didn't request it and Turn 2 never hydrated it. The fix:
        the seed's own direct 1-hop callees are unioned into the
        hydrated package regardless of what was actually requested,
        proven here with a deliberately empty request list."""
        task = django_tasks["django_t02_001_request_middleware_chain"]
        check_response = "django.core.handlers.base.BaseHandler.check_response"
        _manifest_text, candidate_universe = engine.build_candidate_manifest(task.seed_symbol)
        assert check_response in candidate_universe, "test fixture assumption: check_response resolves in the manifest"

        pkg, skipped = engine.retrieve_requested(task.seed_symbol, 4000, [], candidate_universe)
        selected = {n.id for n in pkg.nodes}
        assert check_response in selected
        assert skipped == [], "a direct callee that resolves cleanly is never a hallucination"

    def test_retrieve_two_pass_diagnostics_exclude_auto_included_callees(self, engine, django_tasks):
        """`requested_count` (and the caller's own `requested_symbols`
        binding) must still reflect what Turn 1 actually asked for -
        the direct-callee union happens inside `retrieve_requested`'s
        own hydration call, never leaking back into the harness's own
        bookkeeping of what was requested."""
        task = django_tasks["django_t02_001_request_middleware_chain"]
        check_response = "django.core.handlers.base.BaseHandler.check_response"

        def empty_request(manifest_text: str, task_prompt: str) -> list[str]:
            return []

        pkg, diagnostics = engine.retrieve_two_pass(task.seed_symbol, 4000, empty_request, task_prompt=task.prompt)
        selected = {n.id for n in pkg.nodes}
        assert check_response in selected, "auto-included despite an empty Turn-1 request"
        assert diagnostics["requested_count"] == 0
