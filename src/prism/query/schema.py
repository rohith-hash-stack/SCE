"""Phase G, Issue #31 / Invariant 1: `PrismQuery` - a single, validated
query contract wrapping the `(seed/seeds, budget, task_type, d_max)`
parameter set every real caller of this engine already threads
independently, with nowhere that validated them together before any
traversal/packing machinery ran.

**Reuses rather than reimplements** (the brief's own pre-flight
instruction: "check existing query structures first; harden in-place
rather than introducing duplicate classes" - this module *is* that
audit's outcome):

  - Budget validation defers to `prism.mcp.security.validate_token_
    budget` - already a real, tested, `pydantic.Field`-bounded positive-
    integer check (`1..MAX_TOKEN_BUDGET`), not a second, possibly-
    drifting bound. This means `PrismQuery` also rejects an
    unreasonably *large* budget (a resource-exhaustion vector against
    the knapsack packer, the exact concern that function's own module
    docstring already documents) - a real superset of the brief's own
    "must be positive" ask, not a narrower reimplementation of it.
  - The seed/seeds mutual-exclusivity wording matches `prism.traversal.
    continuous_dijkstra.compute_topological_distances`'s own existing
    `ValueError` messages verbatim - the real precedent this codebase's
    own Phase C already established, not a newly-invented phrasing.

**A real deviation from the brief, documented here rather than
silently**: the brief's own `task_type` enum (`["debug", "blast",
"architecture", "redundancy"]`) omits `"chain"` - a real value in
`benchmarks.ground_truth.schema.EvaluationTask.task_type`'s own literal
enum (the canonical source: `{"chain", "blast", "redundancy",
"architecture", "debug"}`) already handled by `prism.surface.build.
causal_path_applies_to_task_type` (Phase D). Validating against the
brief's own narrower 4-value list would reject a real, already-supported
task_type for no reason; `PrismQuery` validates against the real,
canonical 5-value enum instead.

A plain frozen dataclass, not a `pydantic.BaseModel`: pydantic v2's
`model_validator` catches a `ValueError` raised inside it and re-wraps
it as its own `pydantic.ValidationError`, which would silently swallow
`QueryValidationError`'s own type from a caller's perspective - Invariant
4 wants callers to see `QueryValidationError` itself, not a generic
wrapped one, so validation happens in a plain `__post_init__` instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from prism.mcp.security import SecurityError, validate_token_budget
from prism.query.errors import QueryValidationError

#: The real, canonical enum - see this module's own docstring for why it
#: is 5 values, not the brief's own narrower 4.
VALID_TASK_TYPES = frozenset({"chain", "blast", "redundancy", "architecture", "debug"})


@dataclass(frozen=True)
class PrismQuery:
    """A validated `(seed(s), budget, task_type, d_max)` query - raises
    `QueryValidationError` at construction time if anything is wrong,
    so a caller never has to separately remember to check `budget > 0`
    or `seed`-vs-`seeds` exclusivity before handing this off to
    `prism.surface.build.build_context_package`/`prism.traversal.
    continuous_dijkstra.compute_topological_distances`."""

    budget: int
    seed: Optional[str] = None
    seeds: Optional[list[str]] = None
    task_type: Optional[str] = None
    d_max: Optional[float] = None

    def __post_init__(self) -> None:
        if (self.seed is None) == (self.seeds is None):
            raise QueryValidationError("Specify exactly one of 'seed' or 'seeds', not both.")
        if self.seeds is not None and not self.seeds:
            raise QueryValidationError("Must provide at least one seed symbol.")
        try:
            validate_token_budget(self.budget)
        except SecurityError as exc:
            raise QueryValidationError(str(exc)) from exc
        if self.task_type is not None and self.task_type not in VALID_TASK_TYPES:
            raise QueryValidationError(
                f"task_type must be one of {sorted(VALID_TASK_TYPES)}, got {self.task_type!r}"
            )
        if self.d_max is not None and self.d_max <= 0:
            raise QueryValidationError(f"d_max must be positive, got {self.d_max!r}")

    @property
    def resolved_seeds(self) -> list[str]:
        """Canonical list form, whichever of `seed`/`seeds` was given -
        the same normalization `compute_topological_distances` already
        does internally, exposed here so a caller only has to do it
        once, before either `seed` or `seeds` is threaded further."""
        return [self.seed] if self.seed is not None else list(self.seeds)
