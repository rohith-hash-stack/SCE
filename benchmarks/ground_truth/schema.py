"""v1.1+ Empirical Benchmarking Harness: the ground-truth annotation
schema and inter-annotator-agreement computation.

Two independent annotators (`annotation_a`/`annotation_b`) each produce a
`GroundTruthAnnotation` for one `EvaluationTask` independently
("double-blind": neither sees the other's answer, nor - per this
schema's own field shape - a pre-existing `adjudicated` one); a third,
`adjudicated`, is the reconciled reference truth `benchmarks.metrics`/
`benchmarks.tsr.scorer_*` actually score against. `cohen_kappa` (the
field keeps the spec's own name) records the measured agreement between
`annotation_a` and `annotation_b` before adjudication -
`compute_inter_annotator_agreement` computes it, `loader.py` enforces
what it gates.

**Stated honestly**: `compute_inter_annotator_agreement` computes
positive specific agreement (the Dice/F1 overlap between the two
selection sets), not textbook 2-category Cohen's kappa - see that
function's own docstring for the derivation proving the literal kappa
formula is mathematically degenerate for a set-selection task with no
shared "nothing selected" item pool, and the published methodology
(Hripcsak & Rothschild, 2005) this substitutes instead.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class GroundTruthAnnotation(BaseModel):
    annotator_id: str
    pipeline_symbols: list[str] = Field(default_factory=list, description="Ordered symbols for Type 1 chain")
    critical_callers: set[str] = Field(default_factory=set, description="Symbols that bind/unpack return values for Type 2")
    orthogonal_neighbors: set[str] = Field(default_factory=set, description="Non-redundant subset for Type 3")
    reference_symbols: set[str] = Field(default_factory=set, description="Structural/architectural symbols a correct answer should identify, for Type 4 (architecture)")
    expected_solution: str = Field(..., description="Reference answer or regex pattern for automated scoring")


class EvaluationTask(BaseModel):
    task_id: str
    repo: Literal["django", "gin", "trpc", "express"]
    pinned_commit: str
    seed_symbol: str
    task_type: Literal["chain", "blast", "redundancy", "architecture", "debug"]
    prompt: str
    annotation_a: GroundTruthAnnotation
    annotation_b: GroundTruthAnnotation
    adjudicated: GroundTruthAnnotation
    cohen_kappa: float


#: `compute_inter_annotator_agreement`'s own enforcement thresholds - see
#: `loader.py`'s `AgreementError`/task-filtering logic, the actual place
#: these are acted on.
KAPPA_PROCEED_THRESHOLD = 0.80
KAPPA_ADJUDICATION_THRESHOLD = 0.60


def _selected_symbols(ann: GroundTruthAnnotation) -> set[str]:
    """One annotator's full "what did you select" set across all three
    task-type fields (a task only ever populates the one field relevant
    to its own `task_type`, so this is safe to union unconditionally -
    the other two are empty by construction, never a real disagreement
    source)."""
    return set(ann.pipeline_symbols) | ann.critical_callers | ann.orthogonal_neighbors | ann.reference_symbols


def compute_inter_annotator_agreement(ann_a: GroundTruthAnnotation, ann_b: GroundTruthAnnotation) -> float:
    """Inter-annotator agreement across annotated symbol *selection*
    sets - **not** textbook 2-category Cohen's kappa, despite this
    function's name (kept matching the spec's own literal signature/
    naming) - see the derivation below for why that formula is actively
    wrong here, not merely a stylistic choice.

    A first implementation of this function computed literal Cohen's
    kappa (`(p_o - p_e) / (1 - p_e)`) over the union of both annotators'
    own selections as the classification "universe", "selected"/"not
    selected" as the two categories. That formula is **structurally
    degenerate** for a set-selection task: restricting the universe to
    `selected_a | selected_b` makes a mutual "both said not-selected"
    outcome *impossible by construction* (every universe member was
    selected by at least one annotator, by definition of union) - so the
    2x2 contingency table's true-negative cell is always zero while
    `p_e` still prices in the *chance* of true-negative agreement as if
    it could occur. Solved in closed form (`x = |A|/n`, `y = |B|/n`,
    `n = |A union B|`): `kappa = 2(x + y - 1 - xy) / (x + y - 2xy)`,
    which is provably `<= 0` for every `(x, y)` in `(0, 1) x (0, 1)` and
    reaches exactly `0` only in the limit `x, y -> 1` - confirmed
    directly by exhaustive search over every achievable `(n, a, b)`
    integer triple up to `n = 200`: **not one** produces a kappa in
    `(0, 1)` at all. Every real annotation task short of the two
    annotators picking the exact same set would have been rejected.

    This is a known, published result, not a surprise specific to this
    codebase: Hripcsak & Rothschild (2005), "Agreement, the F-Measure,
    and Reliability in Information Retrieval", documents exactly this
    failure mode for span/set-selection annotation (no well-defined
    shared "negative" item pool) and establishes **positive specific
    agreement** - algebraically the Dice coefficient / symmetric F1 score
    between the two sets, `2|A intersect B| / (|A| + |B|)` - as the
    correct substitute. That is what this function actually computes:
    bounded to `[0, 1]`, `1.0` for identical sets, `0.0` for disjoint
    ones, monotonically increasing in overlap (unlike the degenerate
    formula above) - and `1.0` for the vacuous case where neither
    annotator selected anything at all.
    """
    selected_a = _selected_symbols(ann_a)
    selected_b = _selected_symbols(ann_b)
    if not selected_a and not selected_b:
        return 1.0
    intersection = len(selected_a & selected_b)
    return 2 * intersection / (len(selected_a) + len(selected_b))


def agreement_tier(kappa: float) -> Literal["proceed", "adjudicate", "reject"]:
    """The spec's own three-way gate: `>= 0.80` proceeds as-is, `[0.60,
    0.80)` requires a real adjudicated annotation before the task is
    usable, `< 0.60` rejects the task outright - see `loader.py` for
    where each branch is actually enforced."""
    if kappa >= KAPPA_PROCEED_THRESHOLD:
        return "proceed"
    if kappa >= KAPPA_ADJUDICATION_THRESHOLD:
        return "adjudicate"
    return "reject"


#: The T13 (blast/signature-change) task screening criterion, established
#: after 3 of the 4 originally-suggested seeds for the first blast tasks
#: (`Model.save`, `URLResolver.resolve`, `QuerySet.filter`) turned out to
#: have 0-1 real return-binding callers against the real pinned Django
#: 4.2.30 checkout - BCCR is vacuous (trivially ~1.0 for every engine,
#: capturing "nothing" perfectly) at that thinness, defeating the whole
#: point of a blast task. Going forward, any blast task's seed must have
#: at least this many real (direct + 2-hop) return-binding callers,
#: verified by the exact same static detector BCCR itself scores
#: against - never eyeballed or guessed - before a single annotator
#: looks at it.
BLAST_SEED_MIN_CALLERS = 20


class BlastSeedTooThinError(Exception):
    """Raised by `validate_blast_seed_richness` for a candidate blast-
    task seed whose real caller count falls below `BLAST_SEED_MIN_
    CALLERS` - reject the seed before annotation begins, not after two
    annotators have already worked on a task that BCCR can't actually
    measure anything with."""


def validate_blast_seed_richness(
    direct_callers: set[str] | int,
    transitive_callers: set[str] | int | None = None,
    *,
    minimum: int = BLAST_SEED_MIN_CALLERS,
) -> None:
    """Screens a candidate T13 seed *before* it is annotated. Pass the
    real output of `benchmarks.metrics.bccr.compute_direct_and_
    transitive_callers(builder, candidate_seed)` (either the two sets
    directly, or their pre-counted lengths) - never a guessed or
    estimated count. Raises `BlastSeedTooThinError` if the total falls
    below `minimum`; returns `None` (silently) otherwise.

    This is deliberately a plain function taking real caller data as a
    parameter, not a pydantic field/model validator on `EvaluationTask`
    itself: checking a *candidate* seed requires a real indexed
    `ConcreteGraphBuilder` for the target repo, which schema.py (pure
    data validation, no filesystem/parsing access by design - loading a
    task YAML must never silently require a full corpus clone and index)
    correctly has no access to. Call this during task authoring, before
    a candidate seed is committed to a YAML file - see
    `benchmarks/ground_truth/TASK_AUTHORING.md`.
    """
    direct_count = len(direct_callers) if isinstance(direct_callers, (set, frozenset)) else direct_callers
    transitive_count = 0
    if transitive_callers is not None:
        transitive_count = len(transitive_callers) if isinstance(transitive_callers, (set, frozenset)) else transitive_callers
    total = direct_count + transitive_count
    if total < minimum:
        raise BlastSeedTooThinError(
            f"candidate blast seed has only {total} real direct+transitive return-binding callers "
            f"(minimum {minimum}) - BCCR would be near-vacuous for this seed; reject before annotation"
        )
