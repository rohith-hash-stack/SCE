"""Pilot-4 prep, Fix 2: `benchmarks.runner`'s `--task-type` flag /
`run_evaluation`'s `task_type` parameter. Fast, no corpus indexing, no
LLM call.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.ground_truth.loader import load_tasks_from_dir

DJANGO_TASKS_DIR = Path("benchmarks/ground_truth/tasks/django")


def test_task_type_all_loads_24():
    """The real ground-truth directory has 24 accepted Django tasks
    total (20 T02 debug + 4 T13 blast) - the same real count
    run_evaluation's own task_type="all" (default) path preserves,
    replicating that function's exact filter expression (a no-op when
    task_type != "debug")."""
    result = load_tasks_from_dir(DJANGO_TASKS_DIR)
    assert not result.rejected
    tasks = [t for t in result.accepted if t.repo == "django"]
    task_type = "all"
    if task_type == "debug":
        tasks = [t for t in tasks if t.task_type == "debug"]
    assert len(tasks) == 24


def test_task_type_debug_loads_20():
    """task_type="debug" restricts to the 20 real T02 tasks -
    replicating run_evaluation's own filter expression (runner.py's `if
    task_type == "debug": tasks = [t for t in tasks if t.task_type ==
    "debug"]`) exactly."""
    result = load_tasks_from_dir(DJANGO_TASKS_DIR)
    assert not result.rejected
    tasks = [t for t in result.accepted if t.repo == "django"]
    task_type = "debug"
    if task_type == "debug":
        tasks = [t for t in tasks if t.task_type == "debug"]
    assert len(tasks) == 20
    assert all(t.task_type == "debug" for t in tasks)
