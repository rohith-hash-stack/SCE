"""The breadth study's power simulation (design only; `breadth/DESIGN.md`, section 8).

    python -B -m harness.experiments.r4.breadth.power_sim [--sims 2000]

**The model:**
- **Baseline resolution:** each task has a baseline log-odds of resolution drawn from Normal(mu0, sd_task).
- **PRISM's effect:** its own log-odds shift, drawn from Normal(delta, sd_effect).
- **Runs:** each of a task's seeds is an independent Bernoulli draw under each arm.

**The test:**
- for each task, PRISM's resolution rate minus the baseline's, over its seeds;
- the mean over tasks, with a 95% percentile bootstrap CI over tasks (1,000 resamples);
- "detected" means the CI's lower bound is above 0.

Power is the share of simulated studies detecting the effect. The script also reports the mean risk difference that
`delta` implies, which is the scale the decision rule uses.

**Limits:** the inputs are assumptions, not estimates. Phase 3 gives only a rough range for `sd_task`. Correlation
between seeds at temperature 0.2 is ignored, which makes the power optimistic.
"""
from __future__ import annotations

import argparse
import itertools

import numpy as np


def study(rng, n_tasks, seeds, mu0, sd_task, delta, sd_effect, boots=1000):
    base = rng.normal(mu0, sd_task, n_tasks)
    eff = rng.normal(delta, sd_effect, n_tasks)
    p0, p1 = 1 / (1 + np.exp(-base)), 1 / (1 + np.exp(-(base + eff)))
    d = rng.binomial(seeds, p1) / seeds - rng.binomial(seeds, p0) / seeds
    idx = rng.integers(0, n_tasks, (boots, n_tasks))
    lo = np.percentile(d[idx].mean(axis=1), 2.5)
    return lo > 0, float((p1 - p0).mean())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--sims", type=int, default=2000)
    a = p.parse_args(argv)
    rng = np.random.default_rng(20261128)
    print("| tasks | seeds | sd_task | sd_effect | delta (logit) | mean risk difference | power |")
    print("|---|---|---|---|---|---|---|")
    for n, s, sdt, sde, dl in itertools.product((16, 24, 32), (3, 5), (1.5,), (0.5, 1.0), (0.4, 0.8, 1.2)):
        res = [study(rng, n, s, 0.0, sdt, dl, sde) for _ in range(a.sims)]
        pw = np.mean([r[0] for r in res])
        rd = np.mean([r[1] for r in res])
        print(f"| {n} | {s} | {sdt} | {sde} | {dl} | {rd:.3f} | {pw:.2f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
