"""Phase G, Invariant 4: domain exceptions for the unified query layer -
a single, greppable exception type per concern, the same pattern
`prism.mcp.security.SecurityError` and `prism.packer.submodular_
knapsack.SeedNotFoundError` already establish elsewhere in this
codebase. Never a bare `ValueError`/`KeyError`/`IndexError`/
`AttributeError` escaping to an external caller (CLI/MCP) from anywhere
in `prism.query`."""
from __future__ import annotations


class QueryValidationError(ValueError):
    """Raised by `PrismQuery` when its own input fails validation - a
    non-positive (or over-cap) budget, both or neither of `seed`/`seeds`
    given, an empty `seeds` list, or an unrecognized `task_type`. A
    `ValueError` subclass so an existing `except ValueError` call site
    keeps working, while still being a distinct, catchable type for a
    caller that wants to distinguish "this query was rejected" from an
    unrelated `ValueError` elsewhere."""


class SymbolNotFoundError(KeyError):
    """Raised by `prism.query.locate.locate_symbol_by_name` when a bare
    name matches zero real symbols - a `KeyError` subclass, matching
    `prism.packer.submodular_knapsack.SeedNotFoundError`'s own
    precedent, so an existing `except KeyError` call site keeps working.
    Carries `.name` (the query as given) and `.candidates` (up to 5 real
    qualified names, possibly empty) - ranked suggestions, the same
    "did you mean" shape `SeedNotFoundError` already uses.

    `locate_symbol_at` (the coordinate-based lookup) never raises this:
    "no symbol encloses this line" (a blank line, an import, a comment)
    is a normal, common, non-exceptional outcome for a real coordinate,
    so that function returns `None` instead - see its own docstring.
    """

    def __init__(self, name: str, candidates: list[str] | None = None) -> None:
        self.name = name
        self.candidates = candidates or []
        message = f"no symbol matches {name!r}"
        if self.candidates:
            message += f" - did you mean: {', '.join(self.candidates)}?"
        super().__init__(message)
