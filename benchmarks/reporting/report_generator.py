"""v1.1+ Empirical Benchmarking Harness: aggregates raw per-(engine,
task, budget) run records into `eval_results_v11.json`/`.md`,
`ablation_report.md`, and `failure_analysis.md`.

This module never runs an engine or calls an LLM itself - it's a pure
aggregation/formatting layer over `TaskRunRecord`s `benchmarks.runner`
(or a test) already collected, which is exactly what makes it testable
against synthetic records with no network/API dependency at all.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from benchmarks.reporting import format_table
from benchmarks.reporting.bootstrap import BootstrapCI, bootstrap_ci

DEFAULT_ORACLE_GAP_THRESHOLD = 0.20


@dataclass
class TaskRunRecord:
    """One `(engine, task, budget)` cell's full result."""

    task_id: str
    task_type: str
    repo: str
    engine_name: str
    budget_tokens: int
    tsr_scores: list[float] = field(default_factory=list)  # one per seed run
    #: cpi_strict, src, bccr_direct, fcc, fpr_gt, fpr_oracle, ... - `None`
    #: (never a fabricated `0.0`) for a metric that is either
    #: uncomputable for this cell (no Oracle package to diverge from) or
    #: structurally inapplicable to this engine (`fcc` for an engine
    #: with no four-axis feature vectors at all).
    diagnostics: dict[str, float | None] = field(default_factory=dict)
    selected_symbols: list[str] = field(default_factory=list)
    ground_truth_symbols: list[str] = field(default_factory=list)

    @property
    def tsr_mean(self) -> float:
        return sum(self.tsr_scores) / len(self.tsr_scores) if self.tsr_scores else 0.0


@dataclass
class EvaluationRun:
    records: list[TaskRunRecord] = field(default_factory=list)

    def by_engine_and_budget(self) -> dict[tuple[str, int], list[TaskRunRecord]]:
        grouped: dict[tuple[str, int], list[TaskRunRecord]] = {}
        for record in self.records:
            grouped.setdefault((record.engine_name, record.budget_tokens), []).append(record)
        return grouped


def compute_tsr_summary(
    run: EvaluationRun, n_resamples: int = 10_000, random_seed: int | None = None
) -> dict[tuple[str, int], BootstrapCI]:
    """`{(engine, budget): BootstrapCI}` - every individual seed-run TSR
    score across every task in that cell pooled into one bootstrap
    sample (matching `TSR(M, B, tau)`'s own definition: an aggregate
    rate over many runs, not averaged per-task first)."""
    summary: dict[tuple[str, int], BootstrapCI] = {}
    for (engine, budget), records in run.by_engine_and_budget().items():
        pooled_scores = [score for record in records for score in record.tsr_scores]
        summary[(engine, budget)] = bootstrap_ci(pooled_scores, n_resamples=n_resamples, random_seed=random_seed)
    return summary


def write_json_results(run: EvaluationRun, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": [asdict(record) for record in run.records]}
    path.write_text(json.dumps(payload, indent=2))


def render_tsr_markdown_table(run: EvaluationRun, n_resamples: int = 10_000, random_seed: int | None = None) -> str:
    summary = compute_tsr_summary(run, n_resamples=n_resamples, random_seed=random_seed)
    headers = ["Engine", "Budget", "TSR", "95% CI", "N"]
    rows = []
    for (engine, budget), ci in sorted(summary.items()):
        n = sum(len(r.tsr_scores) for r in run.records if r.engine_name == engine and r.budget_tokens == budget)
        rows.append([engine, str(budget), f"{ci.point_estimate:.3f}", f"[{ci.lower:.3f}, {ci.upper:.3f}]", str(n)])
    return format_table(headers, rows)


#: Diagnostics computed for transparency but not meant for the headline
#: per-engine table - `fpr_gt` (the original, ground-truth-scope-limited
#: FPR definition - Gap 2) is still in every JSON record, just not
#: repeated here alongside its replacement, `fpr_oracle`.
_MARKDOWN_HEADLINE_EXCLUDED_METRICS = frozenset({"fpr_gt"})


def render_diagnostics_markdown_table(run: EvaluationRun) -> str:
    """Mean per (engine, metric), skipping `None` cells rather than
    letting one uncomputable/inapplicable value poison the average of
    every real one. A metric that is `None` for *every* record of an
    engine (e.g. `fcc` for a baseline with no four-axis coordinate
    space at all - Gap 3) renders as `"—"`, not `"0.000"` - a
    structurally-absent dimension must never look like a measured zero.
    """
    by_engine: dict[str, dict[str, list[float]]] = {}
    for record in run.records:
        bucket = by_engine.setdefault(record.engine_name, {})
        for key, value in record.diagnostics.items():
            if key in _MARKDOWN_HEADLINE_EXCLUDED_METRICS:
                continue
            bucket.setdefault(key, [])
            if value is not None:
                bucket[key].append(value)

    metric_names = sorted({key for bucket in by_engine.values() for key in bucket})
    headers = ["Engine", *metric_names]
    rows = []
    for engine in sorted(by_engine):
        row = [engine]
        for metric in metric_names:
            values = by_engine[engine].get(metric, [])
            row.append(f"{sum(values) / len(values):.3f}" if values else "—")
        rows.append(row)
    return format_table(headers, rows)


def write_markdown_report(run: EvaluationRun, path: str | Path, n_resamples: int = 10_000, random_seed: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Prism v1.1+ Empirical Benchmark Results",
        "",
        f"{len(run.records)} (engine, task, budget) cells evaluated.",
        "",
        "## Task Success Rate (TSR)",
        "",
        "Bootstrap 95% CI, `n_resamples=" + str(n_resamples) + "`.",
        "",
        render_tsr_markdown_table(run, n_resamples=n_resamples, random_seed=random_seed),
        "",
        "## Diagnostic Metrics (mean per engine)",
        "",
        render_diagnostics_markdown_table(run),
        "",
        "### Notes on Diagnostic Metrics",
        "",
        "- **FPR redefined (divergence from Oracle).** `fpr` used to mean "
        "`|S_M \\ G*| / |S_M|` against the human-annotated ground truth "
        "`G*` alone - typically only 3-4 symbols per task, so *any* other "
        "real context an engine pulled in (imports, helpers, callers) "
        "counted as a false positive, regardless of whether it was "
        "actually relevant. Two metrics are now reported: `fpr_gt` (the "
        "original definition, kept in the raw JSON for transparency, "
        "excluded from this table) and **`fpr_oracle`** (shown above) - "
        "`|S_M \\ S_Oracle| / |S_M|`, divergence from the Oracle engine's "
        "own package for the same (task, budget). `fpr_oracle` is `—` "
        "wherever no Oracle run was configured/available for that cell.",
        "- **FCC (`—` = n/a, not zero).** FCC is an internal packing-density "
        "diagnostic defined over Prism's four-axis coordinate space. It is "
        "structurally inapplicable to topological and lexical baselines "
        "that do not operate over that space - their rows show `—`, never "
        "a fabricated `0.000`.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n")


def identify_failures(
    run: EvaluationRun,
    prism_engine_name: str = "prism_v11",
    bfs_engine_names: tuple[str, ...] = ("baseline_bfs_forward", "baseline_bfs_bidirectional"),
    oracle_engine_name: str = "oracle",
    oracle_gap_threshold: float = DEFAULT_ORACLE_GAP_THRESHOLD,
) -> list[dict]:
    """Every `(task_id, budget)` cell where Prism's own TSR is *worse*
    than either BFS baseline's, or more than `oracle_gap_threshold` (20
    points, the spec's own default) below Oracle's - the two conditions
    `failure_analysis.md` exists to drill into."""
    by_task_budget: dict[tuple[str, int], dict[str, TaskRunRecord]] = {}
    for record in run.records:
        by_task_budget.setdefault((record.task_id, record.budget_tokens), {})[record.engine_name] = record

    failures: list[dict] = []
    for (task_id, budget), by_engine in sorted(by_task_budget.items()):
        prism_record = by_engine.get(prism_engine_name)
        if prism_record is None:
            continue
        prism_tsr = prism_record.tsr_mean

        for bfs_name in bfs_engine_names:
            bfs_record = by_engine.get(bfs_name)
            if bfs_record is not None and prism_tsr < bfs_record.tsr_mean:
                failures.append(
                    {
                        "task_id": task_id,
                        "budget_tokens": budget,
                        "reason": f"prism TSR ({prism_tsr:.3f}) < {bfs_name} TSR ({bfs_record.tsr_mean:.3f})",
                        "prism_tsr": prism_tsr,
                        "comparison_tsr": bfs_record.tsr_mean,
                        "comparison_engine": bfs_name,
                    }
                )

        oracle_record = by_engine.get(oracle_engine_name)
        if oracle_record is not None and (oracle_record.tsr_mean - prism_tsr) > oracle_gap_threshold:
            failures.append(
                {
                    "task_id": task_id,
                    "budget_tokens": budget,
                    "reason": (
                        f"prism TSR ({prism_tsr:.3f}) more than {oracle_gap_threshold:.0%} below "
                        f"oracle TSR ({oracle_record.tsr_mean:.3f})"
                    ),
                    "prism_tsr": prism_tsr,
                    "comparison_tsr": oracle_record.tsr_mean,
                    "comparison_engine": oracle_engine_name,
                }
            )
    return failures


def write_failure_analysis(run: EvaluationRun, path: str | Path, **kwargs) -> list[dict]:
    """Writes `failure_analysis.md` and returns the same failure list
    (so a caller/test can assert on it directly rather than re-parsing
    the Markdown)."""
    failures = identify_failures(run, **kwargs)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Failure Analysis", ""]
    if not failures:
        lines.append("No failures detected under the configured thresholds.")
    else:
        lines.append(f"{len(failures)} failure(s) detected:")
        lines.append("")
        for failure in failures:
            lines.append(f"- **{failure['task_id']}** (budget={failure['budget_tokens']}): {failure['reason']}")
    path.write_text("\n".join(lines) + "\n")
    return failures


# --------------------------------------------------------------------- #
# Ablation reporting
# --------------------------------------------------------------------- #
def render_heatmap_markdown(row_label: str, col_label: str, grid: dict[tuple[float, float], float]) -> str:
    """A 2D sensitivity heatmap (e.g. `lambda_1 x lambda_2`) as a
    Markdown table - `grid` keyed by `(row_value, col_value)`."""
    row_values = sorted({r for r, _c in grid})
    col_values = sorted({c for _r, c in grid})
    headers = [f"{row_label} \\ {col_label}"] + [str(c) for c in col_values]
    rows = []
    for r in row_values:
        row = [str(r)]
        for c in col_values:
            value = grid.get((r, c))
            row.append(f"{value:.3f}" if value is not None else "-")
        rows.append(row)
    return format_table(headers, rows)


def write_ablation_report(path: str | Path, sections: dict[str, str]) -> None:
    """`sections`: `{heading: markdown_body}` - the caller (`benchmarks.
    runner --mode=ablation`) assembles each grid/sweep's own rendered
    body (via `render_heatmap_markdown` or plain text) and hands the
    whole ordered set here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Prism v1.1+ Ablation Report", ""]
    for heading, body in sections.items():
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body)
        lines.append("")
    path.write_text("\n".join(lines) + "\n")
