"""The breadcrumb verification's selection and pre-registered criteria
(`harness/experiments/final_validation/breadcrumb_verification.py`)."""
from __future__ import annotations

import json

from harness.experiments.final_validation import breadcrumb_verification as V

T016 = V.REGRESSION_TASK[1]


def _audit(tmp_path):
    rows = [
        {"arm": "pragmatic_oracle", "task_id": "django_a", "old_fires": True, "new_fires": True},
        {"arm": "pragmatic_oracle", "task_id": "django_b", "old_fires": True, "new_fires": False},
        {"arm": "pragmatic_oracle", "task_id": T016, "old_fires": True, "new_fires": False},
        {"arm": "prism_ask", "task_id": "django_a", "old_fires": True, "new_fires": False},  # removed: changed
        {"arm": "prism_ask", "task_id": "django_c", "old_fires": False, "new_fires": False},
    ]
    path = tmp_path / "audit.json"
    path.write_text(json.dumps({"corpora": [{"repo": "django", "per_task": rows}]}))
    return path


def _write(root, repo, rows):
    (root / repo).mkdir(parents=True, exist_ok=True)
    (root / repo / "cells.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _cells(engine, task, successes, n=10, tokens=0):
    return [{"engine_id": engine, "task_id": task, "seed": 42 + i, "budget": 8000, "status": "ok",
             "tsr": 1 if i < successes else 0, "breadcrumb_tokens": tokens} for i in range(n)]


def test_selection_adds_the_regression_task_and_counts_removals(tmp_path):
    sel = V.select(_audit(tmp_path))
    assert sel["prism_oracle"] == {"django": sorted(["django_a", T016])}
    assert sel["prism_ask"] == {"django": ["django_a"]}


def test_criteria(tmp_path):
    sel = V.select(_audit(tmp_path))
    old, new = tmp_path / "old", tmp_path / "new"
    _write(old, "django", _cells("pragmatic_oracle", "django_a", 9) + _cells("pragmatic_oracle", T016, 10)
           + _cells("prism_oracle", T016, 4) + _cells("prism_ask", "django_a", 3))
    _write(new / "prism_oracle", "django", _cells("prism_oracle", "django_a", 9, tokens=25) + _cells("prism_oracle", T016, 10))
    _write(new / "prism_ask", "django", _cells("prism_ask", "django_a", 4))
    res = V.analyse(new, old, sel)
    assert res["passed"] and res["all_complete"] and res["worst_drop"] == 0
    md = V.render(res, "m", "c")
    assert "**Overall: PASS**" in md and "| 4/10 | 10/10 |" in md  # old vs new prism_oracle on 016

    _write(new / "prism_oracle", "django", _cells("prism_oracle", "django_a", 6) + _cells("prism_oracle", T016, 9))
    res = V.analyse(new, old, sel)
    assert not res["criteria"]["no_task_drops_3_or_more"]  # 9 -> 6
    assert not res["criteria"]["django_t02_016_recovers_to_10_of_10"]
    assert not res["passed"]


def test_missing_cells_are_reported_incomplete(tmp_path):
    sel = V.select(_audit(tmp_path))
    old, new = tmp_path / "old", tmp_path / "new"
    _write(old, "django", _cells("pragmatic_oracle", "django_a", 9) + _cells("pragmatic_oracle", T016, 10)
           + _cells("prism_ask", "django_a", 3))
    _write(new / "prism_oracle", "django", _cells("prism_oracle", "django_a", 9, n=7))
    res = V.analyse(new, old, sel)
    assert not res["all_complete"] and "incomplete" in V.render(res, "m", "c")
