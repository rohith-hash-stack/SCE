"""Paired within-repository cluster bootstrap, and Holm–Bonferroni.

- Repository is a FIXED stratum: with 4 repositories (2 for Arms 2/3),
  resampling repositories is meaningless. Tasks are resampled with
  replacement within each repository.
- The task is the cluster: repeated seeds of one (arm, task) are averaged
  into one value before resampling, so seeds never count as independent
  tasks.
- PAIRED: one resampling plan (task indices per repetition) is drawn from
  the task set and the bootstrap seed, and every arm is evaluated on that
  same plan. Two arms over the same tasks see identical resampled indices.
- WITHIN task type only: mixing task types raises.
- A task whose metric is NaN for every seed (not applicable) is left out;
  pairwise comparisons use tasks defined for both arms.
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from harness import config as C

ResamplePlan = dict[str, np.ndarray]   # repo -> (n_reps, n_tasks_in_repo) index matrix


def _check_single_type(scores) -> str:
    types = {s.task_type for s in scores}
    if len(types) > 1:
        raise ValueError(f"bootstrap must run within one task type; got {sorted(types)}")
    return next(iter(types)) if types else ""


def _value(s, metric: str) -> float:
    v = getattr(s, metric) if hasattr(s, metric) else s.task_specific.get(metric, float("nan"))
    if v is None:
        return float("nan")
    return float(v)


def task_values(scores, metric: str) -> dict[str, dict[str, float]]:
    """repo -> task_id -> mean over seeds (NaN seeds ignored; tasks with no
    defined value dropped)."""
    acc: dict[tuple[str, str], list[float]] = defaultdict(list)
    for s in scores:
        acc[(s.repo_id, s.task_id)].append(_value(s, metric))
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for (repo, tid), vals in acc.items():
        finite = [v for v in vals if v == v]
        if finite:
            out[repo][tid] = float(np.mean(finite))
    return dict(out)


def resample_plan(task_ids_by_repo: dict[str, list[str]], n_reps: int = C.BOOTSTRAP_REPS,
                  seed: int = C.BOOTSTRAP_SEED) -> ResamplePlan:
    """Index matrices, a deterministic function of (sorted repos, sorted task
    ids per repo, n_reps, seed): any two arms with the same tasks get the
    same plan."""
    rng = np.random.default_rng(seed)
    plan: ResamplePlan = {}
    for repo in sorted(task_ids_by_repo):
        n = len(task_ids_by_repo[repo])
        plan[repo] = rng.integers(0, n, size=(n_reps, n)) if n else np.zeros((n_reps, 0), dtype=int)
    return plan


def _replicates(values_by_repo: dict[str, dict[str, float]], tasks_by_repo: dict[str, list[str]],
                plan: ResamplePlan) -> np.ndarray:
    blocks = []
    for repo in sorted(tasks_by_repo):
        arr = np.asarray([values_by_repo[repo][t] for t in tasks_by_repo[repo]], dtype=float)
        if arr.size:
            blocks.append(arr[plan[repo]])
    if not blocks:
        return np.full(next(iter(plan.values())).shape[0] if plan else 0, np.nan)
    return np.concatenate(blocks, axis=1).mean(axis=1)


def _tasks(values_by_repo: dict[str, dict[str, float]]) -> dict[str, list[str]]:
    return {repo: sorted(v) for repo, v in values_by_repo.items()}


def bootstrap_ci(scores, metric: str, n_reps: int = C.BOOTSTRAP_REPS,
                 seed: int = C.BOOTSTRAP_SEED) -> tuple[float, float, float]:
    """(point estimate, 2.5th, 97.5th percentile) for one arm, one task type.
    The point estimate is the observed mean over tasks (each task = mean of
    its seeds), not the mean of the replicates."""
    _check_single_type(scores)
    vals = task_values(scores, metric)
    tasks = _tasks(vals)
    if not any(tasks.values()):
        nan = float("nan")
        return nan, nan, nan
    observed = float(np.mean([v for repo in vals.values() for v in repo.values()]))
    reps = _replicates(vals, tasks, resample_plan(tasks, n_reps, seed))
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return observed, float(lo), float(hi)


@dataclass
class PairedResult:
    arm_a: str
    arm_b: str
    metric: str
    n_tasks: int
    diff: float          # mean(a) - mean(b)
    ci_low: float
    ci_high: float
    p_value: float       # two-sided bootstrap p
    p_holm: float = float("nan")


def paired_replicates(scores_a, scores_b, metric: str, n_reps: int = C.BOOTSTRAP_REPS,
                      seed: int = C.BOOTSTRAP_SEED) -> tuple[float, np.ndarray, int]:
    """(observed mean difference a - b, bootstrap replicates of it, n tasks)
    over the tasks both arms have a value for, on one shared plan."""
    type_a, type_b = _check_single_type(scores_a), _check_single_type(scores_b)
    if type_a and type_b and type_a != type_b:
        raise ValueError(f"paired comparison across task types: {type_a} vs {type_b}")
    va, vb = task_values(scores_a, metric), task_values(scores_b, metric)
    common = {repo: sorted(set(va.get(repo, {})) & set(vb.get(repo, {}))) for repo in set(va) | set(vb)}
    common = {r: t for r, t in common.items() if t}
    n = sum(len(t) for t in common.values())
    if n == 0:
        return float("nan"), np.full(n_reps, np.nan), 0
    diffs = {r: {t: va[r][t] - vb[r][t] for t in ts} for r, ts in common.items()}
    observed = float(np.mean([d for r in diffs.values() for d in r.values()]))
    return observed, _replicates(diffs, common, resample_plan(common, n_reps, seed)), n


def paired_difference(scores_a, scores_b, metric: str, n_reps: int = C.BOOTSTRAP_REPS,
                      seed: int = C.BOOTSTRAP_SEED) -> PairedResult:
    arm_a = scores_a[0].arm if scores_a else "?"
    arm_b = scores_b[0].arm if scores_b else "?"
    observed, reps, n = paired_replicates(scores_a, scores_b, metric, n_reps, seed)
    if n == 0:
        nan = float("nan")
        return PairedResult(arm_a, arm_b, metric, 0, nan, nan, nan, nan)
    lo, hi = np.percentile(reps, [2.5, 97.5])
    # two-sided percentile-bootstrap p-value, floored at 1/(n_reps+1)
    p = 2 * min(np.mean(reps <= 0), np.mean(reps >= 0))
    p = float(min(1.0, max(p, 1.0 / (n_reps + 1))))
    return PairedResult(arm_a, arm_b, metric, n, observed, float(lo), float(hi), p)


def holm_bonferroni(p_values: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order. NaN stays NaN
    and does not count toward m."""
    idx = [i for i, p in enumerate(p_values) if p == p]
    m = len(idx)
    adjusted = [float("nan")] * len(p_values)
    running = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda i: p_values[i])):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def pairwise_comparisons(scores_by_arm: dict[str, list], metric: str, n_reps: int = C.BOOTSTRAP_REPS,
                         seed: int = C.BOOTSTRAP_SEED) -> list[PairedResult]:
    """Every arm pair (8 arms -> 28 pairs), one task type, Holm-adjusted
    across the pairs."""
    results = [paired_difference(scores_by_arm[a], scores_by_arm[b], metric, n_reps, seed)
               for a, b in itertools.combinations(sorted(scores_by_arm), 2)]
    for r, adj in zip(results, holm_bonferroni([r.p_value for r in results])):
        r.p_holm = adj
    return results
