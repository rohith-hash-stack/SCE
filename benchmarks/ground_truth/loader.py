"""v1.1+ Empirical Benchmarking Harness: loads `EvaluationTask` YAML
files and enforces Cohen's kappa agreement gating before a task is
usable by the rest of the harness.

One task = one YAML file (or one document within a multi-document YAML
file) shaped like `EvaluationTask`'s own fields. `load_tasks_from_dir`
is the normal entry point `benchmarks.runner` uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from benchmarks.ground_truth.schema import (
    KAPPA_ADJUDICATION_THRESHOLD,
    EvaluationTask,
    agreement_tier,
    compute_inter_annotator_agreement,
)


class TaskLoadError(Exception):
    """Malformed YAML, or a task whose own `EvaluationTask` schema
    doesn't validate - never a bare `yaml.YAMLError`/`ValidationError`
    escaping this module."""


@dataclass
class TaskLoadResult:
    """Every task actually usable (`accepted`), plus a full accounting
    of what was rejected and why - a silent drop would hide a real data-
    quality problem from whoever runs the harness."""

    accepted: list[EvaluationTask]
    rejected: list[tuple[str, str]]  # (task_id_or_path, reason)


def _recompute_kappa_if_missing_or_inconsistent(task_data: dict) -> dict:
    """A task YAML may state its own `cohen_kappa` (e.g. pre-computed
    during annotation) - trusted as-is when present, since
    `compute_inter_annotator_agreement` is deterministic and a stored
    value only ever needs recomputing if genuinely absent. Absent means
    "annotation just happened, kappa not yet recorded" - computed here so
    a task YAML never has to duplicate the formula's own result.
    """
    if "cohen_kappa" in task_data and task_data["cohen_kappa"] is not None:
        return task_data
    from benchmarks.ground_truth.schema import GroundTruthAnnotation

    ann_a = GroundTruthAnnotation(**task_data["annotation_a"])
    ann_b = GroundTruthAnnotation(**task_data["annotation_b"])
    task_data = dict(task_data)
    task_data["cohen_kappa"] = compute_inter_annotator_agreement(ann_a, ann_b)
    return task_data


def load_task_from_dict(raw: dict, source: str = "<dict>") -> EvaluationTask:
    """Validates one task's raw YAML-parsed dict into an `EvaluationTask`,
    computing `cohen_kappa` if the source didn't already state one.
    Raises `TaskLoadError` (never a bare `ValidationError`/`KeyError`) on
    any schema violation.
    """
    try:
        raw = _recompute_kappa_if_missing_or_inconsistent(raw)
        return EvaluationTask(**raw)
    except (ValidationError, KeyError, TypeError) as exc:
        raise TaskLoadError(f"{source}: {exc}") from exc


def enforce_agreement_gate(task: EvaluationTask) -> None:
    """The spec's own three-way gate, enforced:

      - `kappa >= 0.80`: proceeds - `task.adjudicated` is used as-is.
      - `0.60 <= kappa < 0.80`: requires a *real* adjudicated annotation
        - `task.adjudicated` must differ from a plain copy of `annotation_a`
        (the schema requires the field to be populated either way, so
        "did adjudication actually happen" is only checkable by it not
        being a trivial duplicate of one rater's own raw answer).
      - `kappa < 0.60`: rejected outright - raises `TaskLoadError`.

    Raises `TaskLoadError` for the reject case or a missing-adjudication
    case in the middle tier; returns `None` (silently) when the task is
    usable.
    """
    tier = agreement_tier(task.cohen_kappa)
    if tier == "reject":
        raise TaskLoadError(
            f"{task.task_id}: Cohen's kappa {task.cohen_kappa:.3f} < {KAPPA_ADJUDICATION_THRESHOLD} - task rejected"
        )
    if tier == "adjudicate":
        if task.adjudicated == task.annotation_a or task.adjudicated == task.annotation_b:
            raise TaskLoadError(
                f"{task.task_id}: Cohen's kappa {task.cohen_kappa:.3f} requires real adjudication - "
                "'adjudicated' is a verbatim copy of one rater's own annotation, not a reconciled answer"
            )


def load_tasks_from_dir(directory: str | Path) -> TaskLoadResult:
    """Loads every `*.yaml`/`*.yml` file under `directory` (non-recursive
    - one flat directory of task files, the harness's own convention),
    applying `enforce_agreement_gate` to each. A file that fails to parse
    or validate, or a task that fails the agreement gate, is recorded in
    `TaskLoadResult.rejected` rather than aborting the whole load - one
    bad task file shouldn't block every other task in the corpus.
    """
    directory = Path(directory)
    accepted: list[EvaluationTask] = []
    rejected: list[tuple[str, str]] = []

    if not directory.is_dir():
        raise TaskLoadError(f"not a directory: {directory}")

    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            rejected.append((str(path), f"YAML parse error: {exc}"))
            continue
        if raw is None:
            continue

        documents = raw if isinstance(raw, list) else [raw]
        for doc in documents:
            task_id = doc.get("task_id", str(path)) if isinstance(doc, dict) else str(path)
            try:
                task = load_task_from_dict(doc, source=str(path))
                enforce_agreement_gate(task)
            except TaskLoadError as exc:
                rejected.append((task_id, str(exc)))
                continue
            accepted.append(task)

    return TaskLoadResult(accepted=accepted, rejected=rejected)
