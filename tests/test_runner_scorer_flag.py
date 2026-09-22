"""Pilot-4 prep, Fix 1: `benchmarks.runner`'s `--scorer` flag /
`score_tsr_response`'s `scorer` parameter. Fast, no corpus indexing, no
LLM call.
"""
from __future__ import annotations

import json
from pathlib import Path

from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.runner import score_tsr_response

DJANGO_TASKS_DIR = Path("benchmarks/ground_truth/tasks/django")

# django_t02_005_model_save_signals @ budget=4000/seed=42 - real
# DeepSeek completion (see tests/test_scoring_modernization.py's own
# header for full provenance): the real, captured example of the
# granularity trap score_debug_causal fixes and score_debug reproduces.
_T02_005_PIPELINE = ["django.db.models.base.Model.save", "django.db.models.base.Model.save_base"]
_T02_005_REQUESTED = [
    "django.db.models.base.Model.save",
    "django.db.models.base.Model.save_base",
    "django.db.models.base.Model._save_parents",
    "django.db.models.base.Model._save_table",
]
_T02_005_TURN2_RESPONSE = json.dumps(
    {
        "reasoning": "Model.save calls save_base, which calls _save_parents and _save_table.",
        "symbols": _T02_005_REQUESTED,
    }
)


def _t02_005_task():
    result = load_tasks_from_dir(DJANGO_TASKS_DIR)
    assert not result.rejected
    return next(t for t in result.accepted if t.task_id == "django_t02_005_model_save_signals")


def test_scorer_flag_strict_default():
    """No scorer= argument (the pre-existing call shape every current
    caller uses) keeps score_debug's exact-match behavior - a
    more-thorough-but-correct answer scores 0.0, reproducing the real
    granularity-trap bug."""
    task = _t02_005_task()
    assert task.adjudicated.pipeline_symbols == _T02_005_PIPELINE
    candidate_symbols = set(_T02_005_REQUESTED)

    assert score_tsr_response(task, _T02_005_TURN2_RESPONSE, candidate_symbols) == 0.0
    assert score_tsr_response(task, _T02_005_TURN2_RESPONSE, candidate_symbols, scorer="strict") == 0.0


def test_scorer_flag_causal_uses_score_debug_causal():
    """scorer="causal" switches the debug branch to score_debug_causal -
    the same thorough-but-correct answer now scores 1.0, matching that
    function's own real, verified fix."""
    task = _t02_005_task()
    candidate_symbols = set(_T02_005_REQUESTED)

    assert score_tsr_response(task, _T02_005_TURN2_RESPONSE, candidate_symbols, scorer="causal") == 1.0


def test_scorer_flag_does_not_affect_blast_tasks():
    """scorer's value only ever changes the debug-type branch - a
    blast-type task's own scoring (score_blast, via
    extract_mentioned_symbols against candidate_symbols) is identical
    either way."""
    result = load_tasks_from_dir(DJANGO_TASKS_DIR)
    task = next(t for t in result.accepted if t.task_id == "django_t13_001_blast_reverse")
    correct_response = " and ".join(sorted(c.rsplit(".", 1)[-1] for c in task.adjudicated.critical_callers))
    candidate_symbols = set(task.adjudicated.critical_callers)

    strict_score = score_tsr_response(task, correct_response, candidate_symbols, scorer="strict")
    causal_score = score_tsr_response(task, correct_response, candidate_symbols, scorer="causal")
    assert strict_score == causal_score == 1.0
