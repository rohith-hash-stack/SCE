"""Pre-registered hypotheses, as data, and their automatic evaluation.

H1–H3 are the predictions given in the M1 specification, recorded verbatim.
H4–H8 are proposed by the implementer from the predictions discussed while
designing this benchmark; they are marked `status: "proposed"` and need the
owner's sign-off before any data is seen (a hypothesis added after the
results exist is not pre-registered).

Evaluation (`evaluate_hypothesis`): paired within-repository bootstrap over
tasks of the hypothesis's task type (repositories fixed, seeds averaged per
task), difference = first arm minus second arm in the stated direction.
Confirmed when the one-sided lower bound at `confidence_required` exceeds
`min_effect_pp` (0 by default). Not evaluated in M1: there is no data yet.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import numpy as np
import pandas as pd

from harness import config as C
from harness.scoring.bootstrap import paired_replicates

HYPOTHESES = [
    {"id": "H1", "claim": "RAG (Arm 1) > PRISM (Arm 5) on T1",
     "task_type": "T1", "arms": ["arm1", "arm5"],
     "metric": "task_success", "direction": "arm1 > arm5",
     "confidence_required": 0.95, "status": "specified"},
    {"id": "H2", "claim": "PRISM > RAG on T5 by >15pp",
     "task_type": "T5", "arms": ["arm5", "arm1"],
     "metric": "task_success", "direction": "arm5 > arm1",
     "min_effect_pp": 15, "confidence_required": 0.95, "status": "specified"},
    {"id": "H3", "claim": "PRISM > Oracle on T2",
     "task_type": "T2", "arms": ["arm5", "oracle"],
     "metric": "task_success", "direction": "arm5 > oracle",
     "confidence_required": 0.95, "status": "specified",
     "note": "the Oracle is the ceiling by construction; confirming H3 would mean selection beats the full "
             "ground-truth universe (less, cleaner context helps the SLM)"},
    # ---- proposed: need sign-off before data ----
    {"id": "H4", "claim": "RAG improves over no retrieval on T2",
     "task_type": "T2", "arms": ["arm1", "arm0"], "metric": "task_success", "direction": "arm1 > arm0",
     "confidence_required": 0.95, "status": "proposed"},
    {"id": "H5", "claim": "PRISM improves over no retrieval on T2",
     "task_type": "T2", "arms": ["arm5", "arm0"], "metric": "task_success", "direction": "arm5 > arm0",
     "confidence_required": 0.95, "status": "proposed"},
    {"id": "H6", "claim": "PRISM's context is cleaner than RAG's on T2",
     "task_type": "T2", "arms": ["arm5", "arm1"], "metric": "cleanliness", "direction": "arm5 > arm1",
     "confidence_required": 0.95, "status": "proposed"},
    {"id": "H7", "claim": "PRISM uses less of the budget than RAG on T2",
     "task_type": "T2", "arms": ["arm5", "arm1"], "metric": "budget_utilization", "direction": "arm5 < arm1",
     "confidence_required": 0.95, "status": "proposed"},
    {"id": "H8", "claim": "PRISM hallucinates less than RAG on T5",
     "task_type": "T5", "arms": ["arm5", "arm1"], "metric": "hallucination_rate", "direction": "arm5 < arm1",
     "confidence_required": 0.95, "status": "proposed"},
]

_TYPE_NAMES = {"T1": "T1_conceptual", "T2": "T2_localization", "T3": "T3_codegen", "T4": "T4_edit",
               "T5": "T5_blast_radius"}
_DIRECTION = re.compile(r"^\s*(\w+)\s*([<>])\s*(\w+)\s*$")


def _cells(df: pd.DataFrame, arm: str, task_type: str, metric: str) -> list:
    sub = df[(df.arm == arm) & (df.task_type == task_type)]
    out = []
    for _, r in sub.iterrows():
        v = r[metric]
        v = float(v) if v is not None and not pd.isna(v) else float("nan")
        out.append(SimpleNamespace(arm=arm, task_id=r.task_id, repo_id=r.repo_id, task_type=task_type,
                                   task_specific={}, **{metric: v}))
    return out


def evaluate_hypothesis(h: dict, results_df: pd.DataFrame, n_reps: int = C.BOOTSTRAP_REPS,
                        seed: int = C.BOOTSTRAP_SEED) -> dict:
    """{"confirmed", "effect_pp", "ci_low", "ci_high", "n_tasks", "status"}.
    `effect_pp` is the observed difference in the claimed direction, in
    percentage points (x100); the CI is two-sided 95%; the decision uses the
    one-sided bound at `confidence_required`."""
    m = _DIRECTION.match(h["direction"])
    if not m:
        raise ValueError(f"{h['id']}: cannot parse direction {h['direction']!r}")
    first, op, second = m.groups()
    sign = 1.0 if op == ">" else -1.0
    tt = _TYPE_NAMES.get(h["task_type"], h["task_type"])
    a = _cells(results_df, first, tt, h["metric"])
    b = _cells(results_df, second, tt, h["metric"])
    observed, reps, n = paired_replicates(a, b, h["metric"], n_reps, seed)
    if n == 0:
        return {"id": h["id"], "confirmed": None, "effect_pp": float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan"), "n_tasks": 0, "status": "no_data"}
    eff = sign * reps
    lo95, hi95 = np.percentile(eff, [2.5, 97.5])
    one_sided_low = float(np.percentile(eff, 100 * (1 - h.get("confidence_required", 0.95))))
    threshold = h.get("min_effect_pp", 0) / 100.0
    return {"id": h["id"], "confirmed": bool(one_sided_low > threshold), "effect_pp": 100 * sign * observed,
            "ci_low": 100 * float(lo95), "ci_high": 100 * float(hi95), "n_tasks": n,
            "one_sided_low_pp": 100 * one_sided_low, "status": "evaluated"}


def evaluate_all(results_df: pd.DataFrame, n_reps: int = C.BOOTSTRAP_REPS) -> pd.DataFrame:
    return pd.DataFrame([{**h, **evaluate_hypothesis(h, results_df, n_reps)} for h in HYPOTHESES])
