"""Small ASCII-table reporting helpers shared by every benchmark CLI.

Was a flat `benchmarks/reporting.py` module; promoted to a package (v1.1+
Empirical Benchmarking Harness) so `benchmarks.reporting.bootstrap`
(10,000-sample bootstrap confidence intervals) and `benchmarks.reporting.
report_generator` (`eval_results_v11.json`/`.md`, `ablation_report.md`,
`failure_analysis.md`) can live alongside it - `shorten`/`format_table`
stay re-exported here unchanged, so every existing `from benchmarks.
reporting import format_table, shorten` call site (`run_benchmark.py`,
`live_eval.py`, `multi_repo_eval.py`, `polyglot_33_matrix.py`,
`polyglot_prompt_matrix.py`, `large_repo_prompt_matrix.py`,
`validate_llm_accuracy.py`) is unaffected by the move.
"""
from __future__ import annotations


def shorten(text: str, max_len: int = 52) -> str:
    if len(text) <= max_len:
        return text
    keep = max_len - 1
    return "…" + text[-keep:]


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) + " |"

    separator = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    lines = [separator, fmt_row(headers), separator]
    lines.extend(fmt_row(row) for row in rows)
    lines.append(separator)
    return "\n".join(lines)
