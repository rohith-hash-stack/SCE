#!/usr/bin/env python3
"""Compares two `benchmarks.runner` checkpoint.json files from an A/B
pilot run of the `<causal_path>` envelope block (see `kaggle/ab_tasks/`
and `PRISM_ENABLE_CAUSAL_PATH` - `src/prism/surface/build.py`'s
`_causal_path_enabled`) and reports whether the block helped, hurt, or
made no measurable difference to Prism's own TSR.

Usage:
    python scripts/compare_ab_runs.py \\
        reports/ab_with_block/checkpoint.json \\
        reports/ab_without_block/checkpoint.json

A checkpoint.json is `{"cells": {"<task_id>|<engine>|<budget>|<seed>":
{"score": 0.0 or 1.0, ...}, ...}}` - exactly what `benchmarks.runner`
writes via its `--checkpoint` path. No external dependencies - stdlib
only, so this runs anywhere Python 3 does, no venv required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRISM_ENGINE = "prism_v11"
ENGINE_ORDER = [
    "prism_v11",
    "baseline_bfs_bidirectional",
    "baseline_bfs_forward",
    "baseline_rag",
    "oracle",
]
WIN_THRESHOLD = 1.0
KEEP_DELTA = 0.05
REVERT_DELTA = -0.05


def load_checkpoint(path: str) -> tuple[dict[str, dict], str | None]:
    """Returns `(cells, error)` - `cells` is `{}` and `error` is a plain
    human-readable message if `path` can't be read, isn't valid JSON,
    or doesn't have the expected `{"cells": {...}}` shape. Never raises:
    the caller always gets something it can render a report section
    from, even for a totally missing/malformed input."""
    p = Path(path)
    if not p.exists():
        return {}, f"file not found: {path}"
    try:
        raw = p.read_text()
    except OSError as exc:
        return {}, f"could not read {path}: {exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"malformed JSON in {path}: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict):
        return {}, f"{path} does not have the expected {{'cells': {{...}}}} shape"
    cells = {}
    for key, value in data["cells"].items():
        if isinstance(value, dict) and "score" in value:
            cells[key] = value
    return cells, None


def parse_key(key: str) -> tuple[str, str, str, str] | None:
    parts = key.split("|")
    if len(parts) != 4:
        return None
    task_id, engine, budget, seed = parts
    return task_id, engine, budget, seed


def cells_for_engine(cells: dict[str, dict], engine: str) -> dict[str, dict]:
    """`{(task_id, budget, seed): cell}` for every cell in `cells`
    belonging to `engine` - the finer key (dropping `engine` itself) is
    what lets a Prism cell in run A line up with the *same* task/budget/
    seed cell in run B for the win/loss comparison below."""
    out: dict[tuple[str, str, str], dict] = {}
    for key, value in cells.items():
        parsed = parse_key(key)
        if parsed is None:
            continue
        task_id, cell_engine, budget, seed = parsed
        if cell_engine == engine:
            out[(task_id, budget, seed)] = value
    return out


def tsr(cells: dict[str, dict], engine: str) -> float | None:
    scores = [v["score"] for k, v in cells.items() if (parsed := parse_key(k)) is not None and parsed[1] == engine]
    if not scores:
        return None
    return sum(scores) / len(scores)


def is_win(cell: dict) -> bool:
    return cell.get("score", 0.0) >= WIN_THRESHOLD


def fmt_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "N/A"


def fmt_delta(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "N/A"
    return f"{(a - b) * 100:+.1f}pp"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: python {argv[0] if argv else 'scripts/compare_ab_runs.py'} <checkpoint_A> <checkpoint_B>", file=sys.stderr)
        return 2

    path_a, path_b = argv[1], argv[2]
    cells_a, err_a = load_checkpoint(path_a)
    cells_b, err_b = load_checkpoint(path_b)

    lines: list[str] = []

    if err_a:
        lines.append(f"WARNING: A (block ON) — {err_a} — treating as 0 cells.")
    if err_b:
        lines.append(f"WARNING: B (block OFF) — {err_b} — treating as 0 cells.")
    if lines:
        lines.append("")

    lines.append("## Coverage")
    lines.append(f"Cells in A: {len(cells_a)}, Cells in B: {len(cells_b)}")
    lines.append("")

    lines.append("## TSR per engine")
    header = f"{'Engine':<32}{'A (block ON)':<14}{'B (block OFF)':<15}{'Δ (A−B)':<10}"
    lines.append(header)
    engines_seen = {parse_key(k)[1] for cells in (cells_a, cells_b) for k in cells if parse_key(k) is not None}
    ordered_engines = [e for e in ENGINE_ORDER if e in engines_seen] + sorted(engines_seen - set(ENGINE_ORDER))
    for engine in ordered_engines:
        tsr_a = tsr(cells_a, engine)
        tsr_b = tsr(cells_b, engine)
        lines.append(f"{engine:<32}{fmt_pct(tsr_a):<14}{fmt_pct(tsr_b):<15}{fmt_delta(tsr_a, tsr_b):<10}")
    lines.append("")

    lines.append("## Prism-specific")
    prism_a = cells_for_engine(cells_a, PRISM_ENGINE)
    prism_b = cells_for_engine(cells_b, PRISM_ENGINE)
    wins_a = sum(1 for c in prism_a.values() if is_win(c))
    wins_b = sum(1 for c in prism_b.values() if is_win(c))
    lines.append(f"Prism wins in A: {wins_a}")
    lines.append(f"Prism wins in B: {wins_b}")

    common_keys = set(prism_a) & set(prism_b)
    only_a = both_win = both_lose = only_b = 0
    for key in common_keys:
        a_win = is_win(prism_a[key])
        b_win = is_win(prism_b[key])
        if a_win and b_win:
            both_win += 1
        elif a_win and not b_win:
            only_a += 1
        elif b_win and not a_win:
            only_b += 1
        else:
            both_lose += 1
    lines.append(f"Prism only wins in A (block helps): {only_a}")
    lines.append(f"Prism only wins in B (block hurts): {only_b}")
    lines.append(f"Both win: {both_win}")
    lines.append(f"Both lose: {both_lose}")
    if len(common_keys) < max(len(prism_a), len(prism_b)):
        missing = max(len(prism_a), len(prism_b)) - len(common_keys)
        lines.append(f"(note: {missing} Prism cell(s) present in only one of A/B, excluded from the win/loss comparison above)")
    lines.append("")

    tsr_prism_a = tsr(cells_a, PRISM_ENGINE)
    tsr_prism_b = tsr(cells_b, PRISM_ENGINE)
    lines.append("## Recommendation")
    if tsr_prism_a is None or tsr_prism_b is None:
        lines.append("UNDETERMINED — Prism TSR missing from one or both runs; expand the sample.")
    else:
        delta = tsr_prism_a - tsr_prism_b
        lines.append(f"Prism TSR(A) − TSR(B) = {delta * 100:+.1f}pp")
        if delta >= KEEP_DELTA:
            lines.append("KEEP the block.")
        elif delta <= REVERT_DELTA:
            lines.append("REVERT the block.")
        else:
            lines.append("UNDETERMINED — expand the sample.")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
