"""Load the repository's adjudicated ground-truth tasks into the harness
schema.

Mapping (config.LEGACY_TASK_TYPE_MAP):
- `debug` -> T2_localization. Gold = adjudicated `pipeline_symbols`.
- `blast` -> T5_blast_radius. Gold affected = adjudicated `critical_callers`.
Every other adjudicated set (required context, boundary symbols, ...) goes
into `context_symbols`, so G*_universe equals the legacy harness's own
ground-truth universe (`benchmarks.runner._ground_truth_universe`).
"""
from __future__ import annotations

from pathlib import Path

from harness import config as C
from harness.tasks.schema import BlastRadiusTask, GroundTruth, LocalizationTask


def _repo_root(repo: str) -> str:
    from benchmarks.corpora.resolver import resolve
    return str(resolve(repo))


def from_legacy(task, repo_root: str):
    adj = task.adjudicated
    every = (set(adj.pipeline_symbols) | adj.critical_callers | adj.orthogonal_neighbors
             | adj.reference_symbols | adj.required_context | adj.boundary_symbols)
    harness_type = C.LEGACY_TASK_TYPE_MAP.get(task.task_type)
    common = dict(
        task_id=task.task_id, repo_id=task.repo, repo_root=repo_root, query=task.prompt,
        seed_symbol=task.seed_symbol, source=f"legacy:{task.task_type}",
        hints={"root_imports": list(task.root_imports), "legacy_task_type": task.task_type},
    )
    if harness_type == "T2_localization":
        gold = list(adj.pipeline_symbols)
        gt = GroundTruth(pipeline_symbols=gold, context_symbols=sorted(every - set(gold)))
        return LocalizationTask(ground_truth=gt, expected_solution=adj.expected_solution, **common)
    if harness_type == "T5_blast_radius":
        gold = sorted(adj.critical_callers)
        gt = GroundTruth(pipeline_symbols=gold, context_symbols=sorted(every - set(gold)))
        return BlastRadiusTask(ground_truth=gt, **common)
    return None


def load_tasks(repo: str, task_types: list[str] | None = None, repo_root: str | None = None,
               tasks_dir: str | None = None, limit: int | None = None) -> list:
    """Tasks for `repo` whose harness type is in `task_types` (default: the
    active types), sorted by task_id."""
    from benchmarks.ground_truth.loader import load_tasks_from_dir

    wanted = set(task_types or C.ACTIVE_TASK_TYPES)
    root = repo_root or _repo_root(repo)
    loaded = load_tasks_from_dir(Path(tasks_dir or C.TASKS_DIR_TEMPLATE.format(repo=repo)))
    out = []
    for legacy in sorted(loaded.accepted, key=lambda t: t.task_id):
        task = from_legacy(legacy, root)
        if task is not None and task.task_type in wanted:
            out.append(task)
    return out[:limit] if limit else out


_GOLD_LOCATIONS: dict[str, dict] = {}


def gold_locations(repo: str) -> dict[str, dict]:
    """{gold name: {"file", "start", "end", "source"}} for the repo's T5 gold
    (`benchmarks/ground_truth/tasks/<repo>/gold_locations.json`, written by
    benchmarks/scripts/derive_t5_from_t2.py). For the Oracle only: it lets the
    Oracle deliver a gold name that PRISM's symbol table does not index.
    {} when the file is absent."""
    if repo not in _GOLD_LOCATIONS:
        import json
        path = Path(C.TASKS_DIR_TEMPLATE.format(repo=repo)) / "gold_locations.json"
        _GOLD_LOCATIONS[repo] = json.loads(path.read_text())["locations"] if path.exists() else {}
    return _GOLD_LOCATIONS[repo]

