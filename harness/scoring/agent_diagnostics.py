"""Arm-4 and PRISM diagnostics.

`tool_fpr` and `digest_safety_loss` read Arm 4's trajectory
(`build_meta["trajectory"]`, and the delivered tool results with their
digest originals); `verification_lift` and `recovery_rate` are PRISM's.
"""
from __future__ import annotations

import re

from harness import config as C
from harness.scoring.hallucination import extract_identifiers

#: tool-call outcomes that count as a false positive (the call produced
#: nothing usable)
FPR_OUTCOMES = ("grep_empty", "read_empty", "read_nonexistent", "read_out_of_bounds", "timeout")


def tool_fpr(trajectory) -> dict:
    """Per-cell tool-call outcomes from an Arm 4 trajectory: {total_calls,
    grep_empty, read_empty, read_nonexistent, read_out_of_bounds, timeouts,
    redundant, fpr}. A call counts once it is dispatched (executed, or
    suppressed as a duplicate); calls rejected by the per-turn cap never
    are. fpr = (grep_empty + read_empty + read_nonexistent +
    read_out_of_bounds + timeouts) / total_calls, 0 when there are none."""
    counts = {k: 0 for k in ("grep_empty", "read_empty", "read_nonexistent", "read_out_of_bounds", "timeouts",
                             "redundant")}
    total = 0
    for step in trajectory or []:
        for call in step.get("calls") or []:
            total += 1
            outcome = call.get("outcome", "ok")
            key = "timeouts" if outcome == "timeout" else outcome
            if key in counts:
                counts[key] += 1
    false_pos = sum(counts["timeouts" if o == "timeout" else o] for o in FPR_OUTCOMES)
    return {"total_calls": total, **counts, "fpr": false_pos / total if total else 0.0}


def _leaf(ident: str) -> str:
    """The name a tool output would show for an identifier: the last part
    of a dotted name or path (`pkg.mod.func` -> `func`)."""
    return re.split(r"[./]", ident.removesuffix(".py"))[-1] or ident


def _present(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text) is not None   # word boundary, not substring


def digest_safety_loss(trajectory, final_answer: str, digested_ids, preserved_text: str = "") -> float:
    """1.0 if the final answer names an identifier that, at answer time,
    survived only in digested tool output; else 0.0.

    `trajectory`: the tool results, [{"msg_id", "text"}] with each digested
    result's original text. Identifiers (at least HALLUCINATION_MIN_IDENT_LEN
    characters) come from the final answer; each is matched by its leaf
    name with a word-boundary regex, never as a substring. An identifier also
    present in any preserved (non-digested) result, or in `preserved_text`
    (e.g. the task prompt, which is never digested), is still available.
    0.0 when nothing was digested or the answer names no identifier."""
    digested_ids = set(digested_ids or [])
    if not digested_ids:
        return 0.0
    leaves = {_leaf(i) for i in extract_identifiers(final_answer or "")}
    leaves = {x for x in leaves if len(x) >= C.HALLUCINATION_MIN_IDENT_LEN}
    if not leaves:
        return 0.0
    digested = "\n".join(r["text"] for r in trajectory if r["msg_id"] in digested_ids)
    preserved = "\n".join([preserved_text] + [r["text"] for r in trajectory if r["msg_id"] not in digested_ids])
    lost = {x for x in leaves if _present(x, digested) and not _present(x, preserved)}
    return 1.0 if lost else 0.0


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
