"""The tests fixed in the suite's pre-registration (README, "Tests and decisions").

- `mcnemar(a, b)`: exact McNemar test on paired yes/no outcomes (two-sided binomial test on the pairs
  where only one version succeeds).
- `wilcoxon(a, b)`: Wilcoxon signed-rank test on paired per-task scores (zero differences dropped; None
  when every difference is zero, the case reported descriptively).
- `bootstrap_diff(a, b, groups)`: 95% interval of the mean per-task difference b - a, resampling tasks
  (10,000 resamples, seed 5501). `groups` maps each item to its task, so a task's items move together.
- `holm(pvalues)`: Holm-adjusted p-values.
"""
from __future__ import annotations

import random

from scipy.stats import binomtest
from scipy.stats import wilcoxon as _wilcoxon

RESAMPLES = 10_000
SEED = 5501


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    p = binomtest(only_b, n, 0.5).pvalue if n else 1.0
    return {"only_a": only_a, "only_b": only_b, "p": float(p)}


def wilcoxon(a: list[float], b: list[float]) -> dict | None:
    diffs = [y - x for x, y in zip(a, b)]
    if all(d == 0 for d in diffs):
        return None
    res = _wilcoxon(a, b, zero_method="wilcox", alternative="two-sided")
    return {"statistic": float(res.statistic), "p": float(res.pvalue), "n_nonzero": sum(d != 0 for d in diffs)}


def bootstrap_diff(a: list[float], b: list[float], groups: list[str] | None = None,
                   resamples: int = RESAMPLES, seed: int = SEED) -> dict:
    """Mean per-task difference (b - a) and its 95% percentile interval. Items of one task are summed."""
    groups = groups or [str(i) for i in range(len(a))]
    per: dict[str, float] = {}
    for g, x, y in zip(groups, a, b):
        per[g] = per.get(g, 0.0) + (y - x)
    vals = list(per.values())
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(resamples))
    return {"mean": sum(vals) / len(vals), "low": means[int(0.025 * resamples)],
            "high": means[int(0.975 * resamples) - 1], "tasks": len(vals)}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    order = sorted(pvalues, key=pvalues.get)
    m, out, running = len(order), {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvalues[k]))
        out[k] = running
    return out
