#!/usr/bin/env python3
"""Applies the pre-registered pilot-4 gate (`docs/pilot/stop_condition.md`
Section 4's own ΔTSR/ΔCPI rule) to a merged pilot checkpoint
(`scripts/merge_pilot_checkpoints.py`'s own output) - pilot-4 prep,
Script 2.

Usage:
    python scripts/apply_gate.py \\
        --checkpoint reports/pilot4/checkpoint_merged.json \\
        --baseline-engine baseline_bfs_bidirectional \\
        --prism-engine prism_two_pass \\
        --delta-tsr-threshold 15 \\
        --delta-cpi-threshold 15

Unlike `scripts/merge_pilot_checkpoints.py` (stdlib-only), this needs
`benchmarks.reporting.bootstrap.bootstrap_ci` (numpy-backed - the same
10,000-resample percentile-bootstrap machinery every other TSR/CPI
confidence interval in this project already uses) and so must run from
inside this repo's own Python environment, not standalone.

**The decision rule - five outcomes, a complete partition of every
possible (delta_tsr, delta_cpi, CI) combination** (an earlier version
of this script had only PASS/EXPAND/STOP/UNDETERMINED, whose given
rule didn't cover every case - e.g. one delta clearing its own
threshold while the other sits below the STOP floor - and filled the
gap with an EXPAND catch-all; MIXED below is the real, named outcome
for exactly that gap, not a re-labeled catch-all):

    PASS:  ΔTSR >= threshold AND ΔTSR's CI excludes zero
           AND ΔCPI_answer >= threshold AND ΔCPI_answer's CI excludes zero

    STOP:  ΔTSR < 5  AND  ΔCPI_answer < 5

    MIXED: (ΔTSR clears its threshold with its CI excluding zero
            AND ΔCPI_answer < 5)
           OR
           (ΔCPI_answer clears its threshold with its CI excluding zero
            AND ΔTSR < 5)
           - one metric shows a clear effect, the other shows none; a
           real, reportable finding distinct from both PASS (both
           clear) and EXPAND (neither clearly resolved) - reported
           with an explicit one-line statement of which metric was
           which, never folded silently into EXPAND.

    EXPAND: every other combination - genuinely ambiguous either way,
           more seeds/tasks needed before a decision, the only bucket
           that's still a judgment call by nature rather than a
           threshold crossing.

    UNDETERMINED: any input missing (zero cells, or zero paired
           task_id/budget/seed cells, for either engine) or a CI that
           couldn't be computed at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_benchmarks_importable() -> None:
    """Makes `import benchmarks` work when this script is invoked
    directly (`python scripts/apply_gate.py`, not `python -m
    benchmarks.something`) from a checkout that hasn't been `pip
    install -e`'d - the same problem `benchmarks/run_benchmark.py`'s
    own `_ensure_prism_importable` solves for `import prism`, applied
    here for the one real dependency this script (unlike `scripts/
    merge_pilot_checkpoints.py`) actually needs."""
    try:
        import benchmarks  # noqa: F401
    except ImportError:
        if str(_PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(_PROJECT_ROOT))


_ensure_benchmarks_importable()

from benchmarks.reporting.bootstrap import BootstrapCI, bootstrap_ci  # noqa: E402

DEFAULT_N_RESAMPLES = 10_000
#: A fixed default (not None, bootstrap_ci's own "fresh, non-
#: reproducible" default) - a pre-registered pass/fail gate should give
#: the same verdict on the same data every time it's run, not one that
#: can flip near a threshold boundary purely from resampling noise.
#: Override with --bootstrap-seed for a different draw.
DEFAULT_BOOTSTRAP_SEED = 42
STOP_THRESHOLD_PP = 5.0


class GateInputError(Exception):
    """A user-facing input problem (bad checkpoint path/shape) - the
    CLI prints `str(exc)` and exits non-zero rather than a traceback."""


def load_merged_checkpoint(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise GateInputError(f"checkpoint not found: {path}")
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GateInputError(f"could not read/parse {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict):
        raise GateInputError(f"{path} is not a {{'cells': {{...}}}} checkpoint")
    return data["cells"]


def _pair_key(row: dict) -> tuple:
    return (row["task_id"], row["budget"], row["seed"])


def _rows_by_engine(cells: dict, engine: str) -> dict[tuple, dict]:
    """`{(task_id, budget, seed): row}` for every cell whose own
    `engine` field matches - the pairing key the paired bootstrap below
    resamples over."""
    return {_pair_key(row): row for row in cells.values() if row.get("engine") == engine}


def _paired_deltas(baseline_rows: dict[tuple, dict], prism_rows: dict[tuple, dict], field: str) -> list[float]:
    """One `prism[field] - baseline[field]` per (task_id, budget, seed)
    present in both engines with a non-None `field` value in both -
    the exact array a paired bootstrap resamples (with replacement)
    over: resampling this array and taking the mean of each resample
    is the correct paired-bootstrap distribution for the mean
    difference, since each element already IS one matched pair's own
    delta."""
    shared_keys = baseline_rows.keys() & prism_rows.keys()
    deltas = []
    for key in shared_keys:
        b, p = baseline_rows[key].get(field), prism_rows[key].get(field)
        if b is None or p is None:
            continue
        deltas.append(p - b)
    return deltas


def compute_engine_summary(cells: dict) -> dict[str, dict]:
    """`{engine: {"n": int, "mean_tsr": float | None, "mean_cpi_answer": float | None}}`
    over every engine present in `cells` - `None` for a metric with no
    non-None values to average (never a fabricated 0.0)."""
    by_engine: dict[str, list[dict]] = {}
    for row in cells.values():
        by_engine.setdefault(row.get("engine", "<unknown>"), []).append(row)

    summary = {}
    for engine, rows in by_engine.items():
        tsr_values = [r["tsr"] for r in rows if r.get("tsr") is not None]
        cpi_values = [r["cpi_answer"] for r in rows if r.get("cpi_answer") is not None]
        summary[engine] = {
            "n": len(rows),
            "mean_tsr": sum(tsr_values) / len(tsr_values) if tsr_values else None,
            "mean_cpi_answer": sum(cpi_values) / len(cpi_values) if cpi_values else None,
        }
    return summary


def compute_gate_metrics(
    cells: dict, baseline_engine: str, prism_engine: str, n_resamples: int, bootstrap_seed: int | None,
) -> dict | None:
    """`None` (never a raised exception) whenever there isn't enough
    data to compute a real delta - zero cells for either engine, or
    zero (task_id, budget, seed) pairs shared between them with a
    non-None value for a given metric - the "any input missing" gate
    for UNDETERMINED. Otherwise `{"delta_tsr_pp": ..., "tsr_ci": BootstrapCI,
    "tsr_excludes_zero": bool, "delta_cpi_pp": ..., "cpi_ci": ..., "cpi_excludes_zero": bool}`.
    """
    baseline_rows = _rows_by_engine(cells, baseline_engine)
    prism_rows = _rows_by_engine(cells, prism_engine)
    if not baseline_rows or not prism_rows:
        return None

    tsr_deltas = _paired_deltas(baseline_rows, prism_rows, "tsr")
    cpi_deltas = _paired_deltas(baseline_rows, prism_rows, "cpi_answer")
    if not tsr_deltas or not cpi_deltas:
        return None

    tsr_ci = bootstrap_ci(tsr_deltas, n_resamples=n_resamples, random_seed=bootstrap_seed)
    cpi_ci = bootstrap_ci(cpi_deltas, n_resamples=n_resamples, random_seed=bootstrap_seed)

    return {
        "n_paired_tsr": len(tsr_deltas),
        "n_paired_cpi": len(cpi_deltas),
        "delta_tsr_pp": tsr_ci.point_estimate * 100,
        "tsr_ci": tsr_ci,
        "tsr_excludes_zero": tsr_ci.lower > 0 or tsr_ci.upper < 0,
        "delta_cpi_pp": cpi_ci.point_estimate * 100,
        "cpi_ci": cpi_ci,
        "cpi_excludes_zero": cpi_ci.lower > 0 or cpi_ci.upper < 0,
    }


def apply_decision_rule(gate_metrics: dict | None, delta_tsr_threshold: float, delta_cpi_threshold: float) -> tuple[str, str]:
    """`(decision, reason)` - PASS/STOP/MIXED/EXPAND/UNDETERMINED, per
    this module's own docstring. A complete partition: every
    (delta_tsr, delta_cpi, CI) combination lands in exactly one of the
    five branches below, in the order given (PASS and STOP are mutually
    exclusive by construction - PASS requires both deltas above
    threshold, STOP requires both below 5 - so their check order
    doesn't matter; MIXED is checked only once neither of those held)."""
    if gate_metrics is None:
        return "UNDETERMINED", "missing input: zero cells (or zero paired task_id/budget/seed cells) for one or both engines"

    dt, dc = gate_metrics["delta_tsr_pp"], gate_metrics["delta_cpi_pp"]
    tsr_strong = dt >= delta_tsr_threshold and gate_metrics["tsr_excludes_zero"]
    cpi_strong = dc >= delta_cpi_threshold and gate_metrics["cpi_excludes_zero"]
    tsr_weak = dt < STOP_THRESHOLD_PP
    cpi_weak = dc < STOP_THRESHOLD_PP

    if tsr_strong and cpi_strong:
        return "PASS", f"both deltas >= threshold (ΔTSR={dt:.2f}pp, ΔCPI_answer={dc:.2f}pp) and both CIs exclude zero"

    if tsr_weak and cpi_weak:
        return "STOP", f"both deltas below {STOP_THRESHOLD_PP:.0f}pp (ΔTSR={dt:.2f}pp, ΔCPI_answer={dc:.2f}pp)"

    if tsr_strong and cpi_weak:
        return "MIXED", (
            f"ΔTSR shows a clear effect ({dt:.2f}pp, clears the {delta_tsr_threshold:.0f}pp threshold, CI excludes zero) "
            f"but ΔCPI_answer does not ({dc:.2f}pp, below the {STOP_THRESHOLD_PP:.0f}pp floor)"
        )
    if cpi_strong and tsr_weak:
        return "MIXED", (
            f"ΔCPI_answer shows a clear effect ({dc:.2f}pp, clears the {delta_cpi_threshold:.0f}pp threshold, CI excludes zero) "
            f"but ΔTSR does not ({dt:.2f}pp, below the {STOP_THRESHOLD_PP:.0f}pp floor)"
        )

    return "EXPAND", f"neither metric clearly resolved (ΔTSR={dt:.2f}pp, ΔCPI_answer={dc:.2f}pp) - more data needed"


def _fmt_ci(ci: BootstrapCI) -> str:
    return f"[{ci.lower * 100:.2f}, {ci.upper * 100:.2f}]"


def render_report(
    cells: dict, baseline_engine: str, prism_engine: str, gate_metrics: dict | None,
    decision: str, reason: str, delta_tsr_threshold: float, delta_cpi_threshold: float,
) -> str:
    lines = []
    engine_summary = compute_engine_summary(cells)

    lines.append("## Coverage")
    lines.append("")
    lines.append(f"Total cells: {len(cells)}")
    for engine in sorted(engine_summary):
        lines.append(f"  {engine}: {engine_summary[engine]['n']} cells")
    lines.append("")

    lines.append("## TSR per engine")
    lines.append("")
    lines.append("| engine | n | mean TSR |")
    lines.append("|---|---|---|")
    for engine in sorted(engine_summary):
        s = engine_summary[engine]
        mean_str = f"{s['mean_tsr']:.3f}" if s["mean_tsr"] is not None else "n/a"
        lines.append(f"| {engine} | {s['n']} | {mean_str} |")
    lines.append("")

    lines.append("## CPI_answer per engine")
    lines.append("")
    lines.append("| engine | n | mean CPI_answer |")
    lines.append("|---|---|---|")
    for engine in sorted(engine_summary):
        s = engine_summary[engine]
        mean_str = f"{s['mean_cpi_answer']:.3f}" if s["mean_cpi_answer"] is not None else "n/a"
        lines.append(f"| {engine} | {s['n']} | {mean_str} |")
    lines.append("")

    lines.append(f"## Gate metrics ({prism_engine} vs {baseline_engine})")
    lines.append("")
    if gate_metrics is None:
        lines.append("UNDETERMINED - insufficient paired data to compute deltas (see Decision below).")
    else:
        lines.append(
            f"ΔTSR:        {gate_metrics['delta_tsr_pp']:.2f} pp, 95% CI {_fmt_ci(gate_metrics['tsr_ci'])}, "
            f"excludes zero: {'yes' if gate_metrics['tsr_excludes_zero'] else 'no'} (n={gate_metrics['n_paired_tsr']} paired cells)"
        )
        lines.append(
            f"ΔCPI_answer: {gate_metrics['delta_cpi_pp']:.2f} pp, 95% CI {_fmt_ci(gate_metrics['cpi_ci'])}, "
            f"excludes zero: {'yes' if gate_metrics['cpi_excludes_zero'] else 'no'} (n={gate_metrics['n_paired_cpi']} paired cells)"
        )
    lines.append("")

    lines.append("## Decision")
    lines.append("")
    lines.append(f"{decision}")
    lines.append(f"({reason}; thresholds: ΔTSR>={delta_tsr_threshold}pp, ΔCPI_answer>={delta_cpi_threshold}pp)")

    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/apply_gate.py",
        description="Apply the pre-registered pilot-4 PASS/EXPAND/STOP gate to a merged pilot checkpoint.",
    )
    parser.add_argument("--checkpoint", required=True, help="Path to a scripts/merge_pilot_checkpoints.py output file.")
    parser.add_argument("--baseline-engine", default="baseline_bfs_bidirectional")
    parser.add_argument("--prism-engine", default="prism_two_pass")
    parser.add_argument("--delta-tsr-threshold", type=float, default=15.0, help="Percentage points.")
    parser.add_argument("--delta-cpi-threshold", type=float, default=15.0, help="Percentage points.")
    parser.add_argument("--n-resamples", type=int, default=DEFAULT_N_RESAMPLES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED, help="Pass a different int, or -1 for a fresh non-reproducible draw.")
    parser.add_argument("--output", default=None, help="Also write the report to this path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        cells = load_merged_checkpoint(args.checkpoint)
    except GateInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    bootstrap_seed = None if args.bootstrap_seed == -1 else args.bootstrap_seed
    gate_metrics = compute_gate_metrics(
        cells, args.baseline_engine, args.prism_engine, args.n_resamples, bootstrap_seed,
    )
    decision, reason = apply_decision_rule(gate_metrics, args.delta_tsr_threshold, args.delta_cpi_threshold)

    report = render_report(
        cells, args.baseline_engine, args.prism_engine, gate_metrics,
        decision, reason, args.delta_tsr_threshold, args.delta_cpi_threshold,
    )
    print(report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)
        print(f"Wrote report to {output_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
