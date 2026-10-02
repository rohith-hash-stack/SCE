"""Arm-4 and PRISM diagnostics.

`digest_safety_loss` and `tool_fpr` need Arm 4's trajectory format, which
arrives in M3; until then they raise NotImplementedError so no placeholder
number can reach a results table. `verification_lift` and `recovery_rate`
are real.
"""
from __future__ import annotations


def digest_safety_loss(trajectory, final_answer: str, digested_ids) -> float:
    """M3. Share of identifiers the final answer needed that survived only
    in digested (compacted) tool output. Matching must use a word-boundary
    regex (\\bident\\b), not substring, and must exclude identifiers also
    present in preserved (non-digested) output."""
    raise NotImplementedError("digest_safety_loss arrives with Arm 4 in M3")


def tool_fpr(trajectory) -> dict:
    """M3. Returns {total_calls, grep_empty, read_empty, read_nonexistent,
    read_out_of_bounds, timeouts, redundant, fpr}."""
    raise NotImplementedError("tool_fpr arrives with Arm 4 in M3")


def verification_lift(initial_success: bool, final_success: bool) -> float:
    """(final - initial) / initial. NaN when the initial attempt failed: no
    lift is defined from a failure (that case is `recovery_rate`'s)."""
    if not initial_success:
        return float("nan")
    return (int(final_success) - int(initial_success)) / int(initial_success)


def recovery_rate(failed_initially: int, recovered: int) -> float:
    """Recovered / failed-initially; NaN when nothing failed initially."""
    if failed_initially < 0 or recovered < 0 or recovered > failed_initially:
        raise ValueError("need 0 <= recovered <= failed_initially")
    if failed_initially == 0:
        return float("nan")
    return recovered / failed_initially
