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
from benchmarks.run_two_pass_benchmark import (
    _check_turn1_degeneration,
    _has_repetition_loop,
    _parse_requested_symbols,
    _turn1_user_prompt,
)
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

    def test_parse_requested_symbols_without_candidate_universe_still_degrades_to_empty(self):
        """No `candidate_universe` passed (the default, `None`) - a
        caller that hasn't opted in to the regex fallback keeps the
        original, unconditional degrade-to-`[]` behavior, unchanged."""
        symbols, ok = _parse_requested_symbols('{"requested_symbols": [')
        assert ok is False
        assert symbols == []

    def test_parse_requested_symbols_regex_fallback_salvages_real_names(self):
        """The pilot-4 `django_t02_002_queryset_delete_cascade_pipeline`
        shape: valid-looking JSON up to a point, then a degenerate
        repetition loop that runs past `max_tokens` and truncates
        mid-string - `json.loads` raises, but real, already-real
        qualified names the model *did* emit before breaking must still
        be recovered, intersected against the real candidate universe
        rather than trusted blindly."""
        truncated = (
            '{\n  "thought_process": "...",\n  "requested_symbols": [\n'
            '    "a.b.real_one",\n    "a.b.real_two",\n'
            '    "a.b.repeat",\n    "a.b.repeat",\n    "a.b.repeat",\n    "a.b.rep'
        )
        candidate_universe = {"a.b.real_one", "a.b.real_two", "a.b.unrelated"}
        symbols, ok = _parse_requested_symbols(truncated, candidate_universe)
        assert ok is False, "the JSON genuinely did not parse - this must stay an honest False"
        assert set(symbols) == {"a.b.real_one", "a.b.real_two"}, (
            "the truncated repeat token (never closed, not a real candidate anyway) must not be salvaged"
        )

    def test_parse_requested_symbols_regex_fallback_finds_nothing_returns_empty(self):
        """A parse failure whose text contains no real candidate names
        at all salvages nothing - an empty list, same as the original
        behavior, not a crash or a spurious guess."""
        candidate_universe = {"a.b.real_one"}
        symbols, ok = _parse_requested_symbols("not json at all", candidate_universe)
        assert ok is False
        assert symbols == []

    def test_parse_requested_symbols_regex_fallback_salvages_backtick_quoted_names(self):
        """The real `django_t02_009_queryset_filter_clone` shape (all 13
        DeepSeek failure cells): the model's own prose quotes the
        looping symbol with backticks, never double quotes, so the
        original quote-only regex salvaged nothing even though a real,
        already-listed candidate name was sitting right there in the
        text - `_QUALIFIED_NAME_RE` must now catch both delimiters."""
        candidate_universe = {
            "django.db.models.query.QuerySet.filter",
            "django.db.models.query.QuerySet._filter_or_exclude_inplace",
        }
        truncated = (
            '{"thought_process": "The pipeline starts from '
            "`django.db.models.query.QuerySet.filter`. The "
            "`django.db.models.query.QuerySet._filter_or_exclude_inplace` method also calls "
            "`django.db.models.query.QuerySet._filter_or_exclude_inplace` to perform the actual filtering"
        )
        symbols, ok = _parse_requested_symbols(truncated, candidate_universe)
        assert ok is False, "truncated mid-string - json.loads must still genuinely fail"
        assert set(symbols) == candidate_universe

    def test_parse_requested_symbols_backtick_salvage_still_ignores_unknown_names(self):
        candidate_universe = {"a.b.real"}
        text = '{"thought_process": "See `a.b.real` and also `a.b.not_a_real_candidate`"'
        symbols, ok = _parse_requested_symbols(text, candidate_universe)
        assert ok is False
        assert symbols == ["a.b.real"]


class TestRepetitionLoopDetection:
    """Layer 1 gateway hardening: `_has_repetition_loop` is a pure
    text-shape check, independent of `_parse_requested_symbols` - it
    must fire on the DeepSeek `t02_009` shape (a long clause repeated
    verbatim) without false-positiving on the qwen `t02_005` shape (a
    short repeated name/token, already handled by `repeat_penalty` and
    deliberately below this function's word-count floor)."""

    def test_clean_text_no_loop(self):
        assert _has_repetition_loop("A perfectly normal sentence with no repetition at all.") is False

    def test_short_repeated_phrase_below_floor_not_flagged(self):
        text = "QuerySet filter clone " * 5
        assert _has_repetition_loop(text) is False

    def test_long_phrase_repeated_three_times_detected(self):
        unit = " ".join(f"word{i}" for i in range(20))  # 20 words, within [16, 32]
        text = f"{unit} {unit} {unit}"
        assert _has_repetition_loop(text) is True

    def test_long_phrase_repeated_only_twice_not_enough(self):
        unit = " ".join(f"word{i}" for i in range(20))
        text = f"{unit} {unit}"
        assert _has_repetition_loop(text) is False

    def test_real_t02_009_shaped_text_detected(self):
        unit = (
            "The QuerySet._filter_or_exclude_inplace method also calls "
            "QuerySet._filter_or_exclude_inplace to perform the actual "
            "filtering operation in place safely and correctly every time"
        )
        text = "The pipeline starts from QuerySet.filter. " + (unit + " ") * 4
        assert _has_repetition_loop(text) is True


class TestTurn1DegenerationCheck:
    """Layer 1 + Layer 2a combined, as `run_two_pass_cell` actually
    calls them - pulled into its own function specifically so this is
    testable without a fake LLM client."""

    def test_clean_short_response_not_flagged(self):
        symbols, degenerate = _check_turn1_degeneration(
            '{"requested_symbols": ["a.b"]}', 500, 2048, ["a.b"], {"a.b"},
        )
        assert degenerate is False
        assert symbols == ["a.b"]

    def test_repetition_without_cap_breach_flagged_but_not_mutated(self):
        """Repetition alone (no cap breach) is recorded as degenerate
        for observability, but must not trigger the salvage-append -
        that's Layer 2a's own trigger, deliberately kept separate."""
        unit = " ".join(f"word{i}" for i in range(20))
        text = f"{unit} {unit} {unit}"
        symbols, degenerate = _check_turn1_degeneration(text, 500, 2048, ["a.b"], {"a.b"})
        assert degenerate is True
        assert symbols == ["a.b"]

    def test_cap_breach_flags_and_appends_salvaged_names_without_dropping_existing(self):
        candidate_universe = {"a.b.existing", "a.b.new_from_salvage"}
        text = '{"thought_process": "mentions `a.b.new_from_salvage` here"'
        symbols, degenerate = _check_turn1_degeneration(
            text, 2300, 2048, ["a.b.existing"], candidate_universe,
        )
        assert degenerate is True
        assert symbols == ["a.b.existing", "a.b.new_from_salvage"], (
            "a cap breach must ADD a real salvaged name, never drop the one already present"
        )

    def test_cap_breach_does_not_duplicate_already_present_names(self):
        candidate_universe = {"a.b.existing"}
        text = '{"thought_process": "mentions `a.b.existing` again"'
        symbols, degenerate = _check_turn1_degeneration(
            text, 2300, 2048, ["a.b.existing"], candidate_universe,
        )
        assert degenerate is True
        assert symbols == ["a.b.existing"]

    def test_cap_breach_margin_boundary(self):
        """2048 * 1.10 = 2252.8 - strictly above the margin, not at or
        below it."""
        _, at_margin = _check_turn1_degeneration('{"requested_symbols": []}', 2252, 2048, [], set())
        assert at_margin is False
        _, over_margin = _check_turn1_degeneration('{"requested_symbols": []}', 2253, 2048, [], set())
        assert over_margin is True


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
