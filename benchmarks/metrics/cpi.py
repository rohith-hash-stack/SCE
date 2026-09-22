"""Causal Pipeline Integrity (CPI) - how completely a retrieval engine's
packed set `S_M` covers the ground-truth causal pipeline `G*_pipeline`
(a Type 1 "chain" task's ordered `pipeline_symbols`).
"""
from __future__ import annotations


def cpi_strict(selected: set[str], pipeline: list[str] | set[str]) -> float:
    """`1.0` if the *entire* pipeline is a subset of `selected`, else
    `0.0` - a pipeline missing even one stage is not "mostly there", it's
    broken. `1.0` (vacuously true, nothing required) if the pipeline
    itself is empty."""
    pipeline_set = set(pipeline)
    if not pipeline_set:
        return 1.0
    return 1.0 if pipeline_set <= selected else 0.0


def cpi_fractional(selected: set[str], pipeline: list[str] | set[str]) -> float:
    """`|S_M ∩ pipeline| / |pipeline|` - the softer, partial-credit
    companion to `cpi_strict`. `1.0` for an empty pipeline (same
    vacuous-truth convention)."""
    pipeline_set = set(pipeline)
    if not pipeline_set:
        return 1.0
    return len(selected & pipeline_set) / len(pipeline_set)


#: Track 3 (metric decoupling fix, `reports/spike_noise_reduction_
#: debrief.md`'s Closing Note): `cpi_strict`/`cpi_fractional` above both
#: measure recall against *the rendered package's own node set*
#: (`selected`) - correct for a single-turn/knapsack-driven protocol,
#: where that set is exactly what the model reasons over. A two-pass
#: hydration protocol (`prism.engine.PrismEngine.retrieve_two_pass`)
#: breaks that equivalence: Turn 1's own `requested_symbols` can omit a
#: real pipeline stage the model then correctly infers anyway, in Turn
#: 2, by reading it as a literal call site inside an already-hydrated
#: caller's own source - real, captured example, `django_t02_009_
#: queryset_filter_clone` @ budget=4000/seed=42 (`git show 9af8941:
#: benchmarks/experiments/results/spike_results.json` on `experiment/
#: noise-filtering-spike`): Turn 1 requested 4 of the 5 real pipeline
#: symbols (never `_not_support_combined_queries`), so `cpi_strict`/
#: `cpi_fractional` against the hydrated set score 0.0/0.8 - but Turn
#: 2's own final answer named all 5, in order (`tsr=1.0`), because the
#: model read the missing one directly out of `QuerySet.filter`'s own
#: hydrated body. `cpi_turn1_selection`/`cpi_end_to_end` below separate
#: "what Turn 1 asked for" from "what the model's own final answer
#: actually claims", so a two-pass run's own metrics can distinguish a
#: real selection gap from a real success `cpi_strict` alone can't see.
def cpi_turn1_selection(requested_symbols: list[str] | set[str], pipeline: list[str] | set[str]) -> float:
    """Recall of `pipeline` against Turn 1's own `requested_symbols` -
    a two-pass protocol's manifest-selection layer, before any Turn-2
    rendering/hydration effects. Thin wrapper over `cpi_fractional`'s
    exact formula, applied to a different set - see this module's own
    "Track 3" note above for why that set matters here."""
    return cpi_fractional(set(requested_symbols), pipeline)


def cpi_end_to_end(answer_symbols: list[str] | set[str], pipeline: list[str] | set[str]) -> float:
    """Recall of `pipeline` against the model's own Turn-2 *final
    answer* (its own named symbols - typically `benchmarks.tsr.
    scorer_debug.extract_flat_symbols`'s own `"symbols"` list, parsed
    the same way scoring already does), not the retrieved/hydrated node
    set `cpi_strict`/`cpi_fractional` measure. See this module's own
    "Track 3" note above for the real, captured case this fixes."""
    return cpi_fractional(set(answer_symbols), pipeline)
