"""False Positive Rate (FPR) - what fraction of a packed set `S_M` is
genuinely irrelevant to the ground-truth answer `G*` (whatever that means
for the task's own type - the pipeline for a chain task, the caller set
for a blast task, the orthogonal-neighbor set for a redundancy task).
"""
from __future__ import annotations


def fpr(selected: set[str], ground_truth: set[str]) -> float:
    """`|S_M \\ G*| / |S_M|` - `0.0` for an empty package (nothing packed,
    nothing false-positive)."""
    if not selected:
        return 0.0
    return len(selected - ground_truth) / len(selected)
