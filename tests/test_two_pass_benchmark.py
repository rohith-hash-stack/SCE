"""Track 3 (Harness Wiring) tests for `benchmarks.run_two_pass_benchmark`.

Fast, pure-function tests for the Turn-1 prompt/parse helpers, plus a
real (no-LLM) integration test against the pinned Django corpus proving
the retrieval-side half of the `django_t02_009` @ budget=2000 fix: with
the exact real Turn-1 request captured during the noise-reduction
spike's own diagnostic re-run (`git show 9af8941:benchmarks/
experiments/results/spike_results.json` on `experiment/noise-filtering-
spike`), the hydrated seed body at budget=2000 now contains the literal
`_not_support_combined_queries` call site - present at budget=4000 both
before and after Track 2, previously stripped at budget=2000 by the
unprotected skeleton-downgrade cascade (`reports/spike_noise_reduction_
debrief.md`'s Closing Note), now kept by `_PROTECTED_DOWNGRADE_ROLES`.

This environment cannot make real DeepSeek calls (`api.deepseek.com` is
blocked by this environment's own egress policy, independent of any
key - see `benchmarks/tsr/client.py`'s own module docstring), so this
is the honest ceiling of what can be verified here without a real,
paid LLM call: the exact artifact the model would need to answer
correctly is confirmed present, not that a live model call would in
fact then answer correctly.
"""
from __future__ import annotations

import pytest

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.base import selected_symbols
from benchmarks.run_two_pass_benchmark import _parse_requested_symbols, _turn1_user_prompt
from prism.engine import PrismEngine

pytestmark = pytest.mark.slow

#: The exact real Turn-1 request captured for django_t02_009 @
#: budget=4000/seed=42 (see tests/test_scoring_modernization.py's own
#: header for the full provenance) - identical at budget=2000 per the
#: debrief's own diagnosis ("Turn 1's own request is identical at both
#: budgets; Turn 2's render-budget trim is where the two budgets
#: actually diverge").
T02_009_SEED = "django.db.models.query.QuerySet.filter"
T02_009_TURN1_REQUESTED = [
    "django.db.models.query.QuerySet.filter",
    "django.db.models.query.QuerySet._filter_or_exclude",
    "django.db.models.query.QuerySet._chain",
    "django.db.models.query.QuerySet._clone",
]


class TestPromptHelpers:
    def test_turn1_user_prompt_embeds_manifest_and_task(self):
        prompt = _turn1_user_prompt("<candidate_index>\nfoo|seed|function||calls=[]\n</candidate_index>", "do the thing")
        assert "<candidate_index>" in prompt
        assert "do the thing" in prompt
        assert "requested_symbols" in prompt

    def test_parse_requested_symbols_valid(self):
        symbols, ok = _parse_requested_symbols('{"thought_process": "x", "requested_symbols": ["a.b", "a.c"]}')
        assert ok is True
        assert symbols == ["a.b", "a.c"]

    def test_parse_requested_symbols_malformed_degrades_gracefully(self):
        symbols, ok = _parse_requested_symbols("not json")
        assert ok is False
        assert symbols == []

    def test_parse_requested_symbols_wrong_shape(self):
        symbols, ok = _parse_requested_symbols('{"requested_symbols": "not-a-list"}')
        assert ok is False
        assert symbols == []


@pytest.fixture(scope="module")
def django_repo_path() -> str:
    return str(resolve("django"))


@pytest.fixture(scope="module")
def engine(django_repo_path) -> PrismEngine:
    return PrismEngine.from_repo(django_repo_path)


class TestTrack2ProtectionThroughRealRetrieval:
    def test_budget_2000_now_keeps_the_needed_call_site(self, engine):
        """The real, retrieval-side fix: at budget=2000, with the exact
        real Turn-1 request, the seed's own hydrated body must now
        contain the literal `_not_support_combined_queries` call site -
        stripped before Track 2's skeleton-downgrade protection."""
        _manifest, candidate_universe = engine.build_candidate_manifest(T02_009_SEED)
        pkg, skipped = engine.retrieve_requested(T02_009_SEED, 2000, T02_009_TURN1_REQUESTED, candidate_universe)
        assert skipped == []
        seed_node = next(n for n in pkg.nodes if n.id == pkg.seed.symbol)
        assert seed_node.compression == "L0_full"
        assert "_not_support_combined_queries" in seed_node.body

    def test_budget_4000_already_kept_it_before_and_after(self, engine):
        """Regression guard: the already-working budget=4000 case must
        stay working - the fix must not have been a coincidental side
        effect that only helps the tight-budget case."""
        _manifest, candidate_universe = engine.build_candidate_manifest(T02_009_SEED)
        pkg, skipped = engine.retrieve_requested(T02_009_SEED, 4000, T02_009_TURN1_REQUESTED, candidate_universe)
        assert skipped == []
        seed_node = next(n for n in pkg.nodes if n.id == pkg.seed.symbol)
        assert seed_node.compression == "L0_full"
        assert "_not_support_combined_queries" in seed_node.body


class TestDryRunWiring:
    def test_dry_run_cell_exercises_real_retrieval_with_no_llm_call(self, engine):
        from benchmarks.ground_truth.loader import load_tasks_from_dir
        from pathlib import Path
        from benchmarks.run_two_pass_benchmark import run_two_pass_cell

        tasks_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "ground_truth" / "tasks" / "django"
        loaded = load_tasks_from_dir(tasks_dir)
        task = next(t for t in loaded.accepted if t.task_id == "django_t02_009_queryset_filter_clone")

        result = run_two_pass_cell(engine, None, task, 2000, None, None)
        assert result.tsr is None
        assert result.turn1_response == ""
        assert result.candidate_count == 14
        assert result.requested_count == 0
