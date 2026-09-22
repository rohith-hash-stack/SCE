"""Track 3 (Benchmark Scoring Modernization) - deterministic tests
against real, previously-captured model output. No LLM call and no
corpus indexing here: every response text below is data, reproduced
verbatim from a real DeepSeek completion already on disk, not a
fabricated example that might not reflect what a real model actually
does.

Source: `experiment/noise-filtering-spike`'s own diagnostic re-run
results -
  `git show 9af8941:benchmarks/experiments/results/spike_results.json`
  (django_t02_005 and django_t02_009 @ budget=4000/seed=42), and
  `git show 884249d:benchmarks/experiments/results/spike_results.json`
  (django_t02_009 @ budget=2000/seed=42) -
the exact real anomalies `reports/spike_noise_reduction_debrief.md`'s
Closing Note diagnoses and this fix targets.
"""
from __future__ import annotations

import json

from benchmarks.metrics.cpi import cpi_end_to_end, cpi_strict, cpi_turn1_selection
from benchmarks.tsr.scorer_debug import extract_flat_symbols, score_debug, score_debug_causal

# --------------------------------------------------------------------- #
# Real captured data
# --------------------------------------------------------------------- #
T02_005_PIPELINE = ["django.db.models.base.Model.save", "django.db.models.base.Model.save_base"]
T02_005_TURN1_REQUESTED = [
    "django.db.models.base.Model.save",
    "django.db.models.base.Model.save_base",
    "django.db.models.base.Model._save_parents",
    "django.db.models.base.Model._save_table",
]
T02_005_TURN2_RESPONSE = (
    '{"reasoning":"The pipeline begins with the `Model.save` method, which prepares the instance '
    "for saving and checks for conditions like `force_insert` and `update_fields`. It then calls "
    "`Model.save_base`, where the actual saving logic occurs, including sanity checks and the "
    "dispatching of `pre_save` and `post_save` signals. Finally, `save_base` may call "
    '`_save_parents` and `_save_table` to handle parent model saving and the actual database '
    'operation, respectively.","symbols":["django.db.models.base.Model.save",'
    '"django.db.models.base.Model.save_base","django.db.models.base.Model._save_parents",'
    '"django.db.models.base.Model._save_table"]}'
)

T02_009_PIPELINE = [
    "django.db.models.query.QuerySet.filter",
    "django.db.models.query.QuerySet._not_support_combined_queries",
    "django.db.models.query.QuerySet._filter_or_exclude",
    "django.db.models.query.QuerySet._chain",
    "django.db.models.query.QuerySet._clone",
]
T02_009_TURN1_REQUESTED = [
    "django.db.models.query.QuerySet.filter",
    "django.db.models.query.QuerySet._filter_or_exclude",
    "django.db.models.query.QuerySet._chain",
    "django.db.models.query.QuerySet._clone",
]
T02_009_TURN2_RESPONSE = (
    '{"reasoning":"The pipeline begins with the `QuerySet.filter()` method, which first calls '
    "`_not_support_combined_queries` to ensure that combined queries are not used. It then "
    "proceeds to call `_filter_or_exclude`, passing the necessary arguments. This method "
    "subsequently calls `_chain`, which prepares the current QuerySet for another operation, and "
    "finally, `_clone` is invoked to create a new QuerySet instance that copies the internal state "
    'of the original, ensuring immutability of the original QuerySet.","symbols":'
    '["django.db.models.query.QuerySet.filter","django.db.models.query.QuerySet._not_support_combined_queries",'
    '"django.db.models.query.QuerySet._filter_or_exclude","django.db.models.query.QuerySet._chain",'
    '"django.db.models.query.QuerySet._clone"]}'
)

#: budget=2000/seed=42 - the one cell that stays genuinely wrong (the
#: pre-Track-2 skeleton-downgrade cascade stripped the literal call
#: site the model needed - see `reports/spike_noise_reduction_debrief.
#: md`'s Closing Note). Turn 1's own request is identical to the
#: budget=4000 cell's - T02_009_TURN1_REQUESTED - only Turn 2's render
#: diverges.
T02_009_BUDGET2000_TURN2_RESPONSE = (
    '{"reasoning":"The pipeline begins with the `QuerySet.filter()` method, which is responsible '
    "for initiating the filtering process. It then calls `_filter_or_exclude()`, where the "
    "filtering logic is applied, and subsequently invokes `_chain()` to prepare the queryset for "
    "further operations. Finally, `_clone()` is called to create a new instance of the queryset, "
    'ensuring that the original queryset remains unchanged.","symbols":'
    '["django.db.models.query.QuerySet.filter","django.db.models.query.QuerySet._filter_or_exclude",'
    '"django.db.models.query.QuerySet._chain","django.db.models.query.QuerySet._clone"]}'
)


# --------------------------------------------------------------------- #
# Item 1a - the granularity trap (django_t02_005)
# --------------------------------------------------------------------- #
class TestGranularityTrapFix:
    def test_score_debug_reproduces_the_real_bug(self):
        """The exact-match scorer's own real failure this fix targets -
        a substantively correct, more-thorough answer scored 0.0,
        identically to a wrong one."""
        assert score_debug(T02_005_TURN2_RESPONSE, T02_005_PIPELINE) == 0.0

    def test_score_debug_causal_credits_the_real_thorough_answer(self):
        """The real fix: django_t02_005 now scores 1.0 under causal
        sequence containment - the ordered pipeline is fully present,
        and both extra symbols are real, legitimately-hydrated ones
        (Turn 1's own real request, 0 skipped_hallucinated in the
        captured row), not invented."""
        candidate_symbols = set(T02_005_TURN1_REQUESTED)
        assert score_debug_causal(T02_005_TURN2_RESPONSE, T02_005_PIPELINE, candidate_symbols) == 1.0

    def test_score_debug_causal_still_penalizes_real_hallucination(self):
        """"More thorough" is credited; "makes something up" is not -
        these are deliberately not conflated into one fuzzy score."""
        response = json.dumps({"reasoning": "x", "symbols": T02_005_PIPELINE + ["django.completely.invented.Symbol"]})
        assert score_debug_causal(response, T02_005_PIPELINE, set(T02_005_TURN1_REQUESTED)) == 0.0

    def test_score_debug_causal_full_credit_on_exact_match_unaffected(self):
        """An exact match (the common, already-correct case) must keep
        scoring 1.0 - this fix only changes what happens with a
        legitimate superset, never the baseline exact-match case."""
        response = json.dumps({"reasoning": "x", "symbols": T02_005_PIPELINE})
        assert score_debug_causal(response, T02_005_PIPELINE, set(T02_005_PIPELINE)) == 1.0

    def test_score_debug_causal_partial_credit_on_incomplete_coverage(self):
        response = json.dumps({"reasoning": "x", "symbols": [T02_005_PIPELINE[0]]})
        assert score_debug_causal(response, T02_005_PIPELINE, set(T02_005_PIPELINE)) == 0.5

    def test_score_debug_causal_zero_on_unparseable_response(self):
        assert score_debug_causal("not json at all", T02_005_PIPELINE, set(T02_005_PIPELINE)) == 0.0


# --------------------------------------------------------------------- #
# Item 1a continued - django_t02_009 must not regress
# --------------------------------------------------------------------- #
class TestCausalScorerOnT02009:
    def test_budget_4000_exact_pipeline_match_scores_full_marks(self):
        """009 @ budget>=4000 was already tsr=1.0 under the old strict
        scorer (an exact match) - the new causal scorer must agree,
        not regress a case that was never broken."""
        candidate_symbols = set(T02_009_TURN1_REQUESTED) | {T02_009_PIPELINE[1]}
        assert score_debug_causal(T02_009_TURN2_RESPONSE, T02_009_PIPELINE, candidate_symbols) == 1.0

    def test_budget_2000_genuine_miss_is_not_laundered_into_a_pass(self):
        """The one cell diagnosed as genuinely wrong (not a scoring
        artifact - `reports/spike_noise_reduction_debrief.md`'s Closing
        Note) must stay meaningfully below 1.0 under the new scorer
        too: 4 of 5 real pipeline stages, in order, with the missing
        middle stage (`_not_support_combined_queries`) never
        recovered - real, calibrated partial credit, not a false full
        pass and not a blanket 0.0 either."""
        candidate_symbols = set(T02_009_TURN1_REQUESTED)
        score = score_debug_causal(T02_009_BUDGET2000_TURN2_RESPONSE, T02_009_PIPELINE, candidate_symbols)
        assert score == 0.8
        assert score < 1.0


# --------------------------------------------------------------------- #
# Item 1b - the two-pass metric decoupling (django_t02_009)
# --------------------------------------------------------------------- #
class TestMetricDecouplingFix:
    def test_cpi_strict_under_credits_the_real_success(self):
        """Reproduces the real gap: cpi_strict (against the hydrated/
        selected set) says 0.0 on a cell whose final answer was
        completely correct (tsr=1.0, captured directly)."""
        hydrated_set = set(T02_009_TURN1_REQUESTED)  # what actually got a hydrated node
        assert cpi_strict(hydrated_set, T02_009_PIPELINE) == 0.0

    def test_cpi_turn1_selection_reflects_the_real_selection_gap(self):
        """4 of 5 pipeline stages were in Turn 1's own request - a real,
        honest gap at the selection layer, distinct from what Turn 2's
        final answer goes on to claim."""
        assert cpi_turn1_selection(T02_009_TURN1_REQUESTED, T02_009_PIPELINE) == 4 / 5

    def test_cpi_end_to_end_credits_the_real_success(self):
        """The fix: recall against the model's own final answer (not
        the hydrated set) is 1.0 - matching tsr=1.0 - because the model
        correctly named the missing stage anyway, read directly from an
        already-hydrated caller's own source body."""
        turn2_answer_symbols = extract_flat_symbols(T02_009_TURN2_RESPONSE)
        assert cpi_end_to_end(turn2_answer_symbols, T02_009_PIPELINE) == 1.0

    def test_cpi_end_to_end_reflects_the_real_budget_2000_miss(self):
        """The genuinely wrong cell's cpi_end_to_end must also be < 1.0
        - the fix separates two real metric-decoupling artifacts from a
        real failure, it doesn't paper over the real failure too."""
        turn2_answer_symbols = extract_flat_symbols(T02_009_BUDGET2000_TURN2_RESPONSE)
        assert cpi_end_to_end(turn2_answer_symbols, T02_009_PIPELINE) == 4 / 5
