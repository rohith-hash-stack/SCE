#!/usr/bin/env python3
"""Merges a `benchmarks.runner` (single-pass, 4 engines + Oracle) and a
`benchmarks.run_two_pass_benchmark` (two-pass, Prism only) checkpoint
into one normalized comparison dataset - pilot-4 prep, Script 1.

The two harnesses write structurally different checkpoint schemas (see
the Part 1 planning review this graduates from): different field names
for the same concept (`score` vs `tsr`; `cpi_strict`/`cpi_fractional`
vs `cpi_turn1_selection`/`cpi_end_to_end`, which aren't even the same
computation), different key shapes (`task_id|engine|budget|seed` vs
`task_id|budget|seed`, since the two-pass harness only ever runs one
engine). This script normalizes both onto one common row shape, keyed
uniformly as `task_id|engine|budget|seed`, so a downstream comparison
(`scripts/apply_gate.py`) never has to know which harness a row came
from.

Usage:
    python scripts/merge_pilot_checkpoints.py \\
        --single-pass reports/pilot4/checkpoint_single_pass.json \\
        --two-pass reports/pilot4/checkpoint_two_pass.json \\
        --output reports/pilot4/checkpoint_merged.json

No external dependencies - stdlib only, so this runs anywhere Python 3
does, no venv required (same discipline `scripts/compare_ab_runs.py`
already establishes for this same `scripts/` directory).

Idempotent: a pure function of its two input files' own content - the
same two inputs always produce byte-identical output, no incremental
"merge into an existing output" state to accumulate or drift.

Handles a missing input file gracefully: a `--single-pass`/`--two-pass`
path that doesn't exist on disk is treated as "that harness has no
cells yet" (a real, ordinary state - not every engine has necessarily
run at every point in a multi-session pilot), never a crash. Passing
neither `--single-pass` nor a `--two-pass` file that exists yields a
merged checkpoint with zero cells - a legitimate, reportable "nothing
to compare yet" state, not an error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_cells(path: str | None) -> dict:
    """`{"cells": {...}}`'s own `cells` dict, or `{}` for a `None` path,
    a path that doesn't exist, or a file that isn't valid JSON /
    doesn't have the expected `{"cells": {...}}` shape - never raises,
    matching `benchmarks.runner.load_checkpoint`'s own "checkpointing
    is a resumability convenience, not a correctness dependency"
    contract, extended here to "merging is a reporting convenience,
    never a crash risk either"."""
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        print(f"[merge] {path}: not found - treating as zero cells", file=sys.stderr)
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[merge] {path}: could not read/parse ({exc}) - treating as zero cells", file=sys.stderr)
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict):
        print(f"[merge] {path}: not a {{'cells': {{...}}}} checkpoint - treating as zero cells", file=sys.stderr)
        return {}
    return data["cells"]


def _normalize_single_pass_cell(key: str, cell: dict) -> tuple[str, dict] | None:
    """`key` is `task_id|engine|budget|seed` - all four fields are
    parsed from it (the single-pass checkpoint never duplicates them
    into the cell's own value the way the two-pass one does). Returns
    `None` (skipped, logged) for a key that doesn't split into exactly
    4 parts - a malformed/foreign entry, never guessed at."""
    parts = key.split("|")
    if len(parts) != 4:
        print(f"[merge] single-pass: malformed cell key {key!r} (expected 4 '|'-separated parts) - skipped", file=sys.stderr)
        return None
    task_id, engine, budget_s, seed_s = parts
    try:
        budget, seed = int(budget_s), int(seed_s)
    except ValueError:
        print(f"[merge] single-pass: non-integer budget/seed in key {key!r} - skipped", file=sys.stderr)
        return None
    row = {
        "task_id": task_id,
        "engine": engine,
        "budget": budget,
        "seed": seed,
        "tsr": cell.get("score"),
        "cpi_retrieval": cell.get("cpi_strict"),
        # A single-pass engine's own retrieved set IS what the model
        # answers from directly - there is no separate "what it asked
        # for" vs "what it answered from" distinction the way two-pass
        # has, so cpi_answer is the same cpi_strict value, not a
        # separately-tracked metric.
        "cpi_answer": cell.get("cpi_strict"),
        "fpr_gt": cell.get("fpr_gt"),
        "model": cell.get("model", ""),
    }
    return f"{task_id}|{engine}|{budget}|{seed}", row


def _normalize_two_pass_cell(key: str, cell: dict) -> tuple[str, dict] | None:
    """`task_id`/`budget`/`seed` come from the cell's own value fields
    (`TwoPassCellResult`'s own dataclass shape already carries them),
    not parsed from `key` - the two-pass checkpoint's own key
    (`task_id|budget|seed`) has no engine component to begin with,
    since exactly one engine (Prism, two-pass) is ever in it. Returns
    `None` (skipped, logged) for a cell missing any of the three
    identifying fields - malformed, never guessed at.
    """
    task_id, budget, seed = cell.get("task_id"), cell.get("budget"), cell.get("seed")
    if task_id is None or budget is None:
        print(f"[merge] two-pass: cell at key {key!r} is missing task_id/budget - skipped", file=sys.stderr)
        return None
    engine = "prism_two_pass"
    row = {
        "task_id": task_id,
        "engine": engine,
        "budget": budget,
        "seed": seed,
        "tsr": cell.get("tsr"),
        "cpi_retrieval": cell.get("cpi_turn1_selection"),
        "cpi_answer": cell.get("cpi_end_to_end"),
        "fpr_gt": cell.get("fpr_gt"),
        "model": cell.get("model", ""),
    }
    return f"{task_id}|{engine}|{budget}|{seed}", row


def merge_checkpoints(single_pass_path: str | None, two_pass_path: str | None) -> dict:
    """`{"cells": {normalized_key: row}}` - the real merge. A key
    collision (the same normalized `task_id|engine|budget|seed` present
    in both inputs, only possible if a two-pass cell's own engine field
    were ever anything other than "prism_two_pass", which it never is)
    would have the two-pass source win, since it's merged in second -
    not expected to occur in practice given the two harnesses' disjoint
    engine namespaces today, but deterministic either way rather than
    silently ambiguous."""
    merged: dict[str, dict] = {}

    single_pass_cells = _load_cells(single_pass_path)
    for key, cell in single_pass_cells.items():
        normalized = _normalize_single_pass_cell(key, cell)
        if normalized is not None:
            merged[normalized[0]] = normalized[1]

    two_pass_cells = _load_cells(two_pass_path)
    for key, cell in two_pass_cells.items():
        normalized = _normalize_two_pass_cell(key, cell)
        if normalized is not None:
            merged[normalized[0]] = normalized[1]

    return {"cells": merged}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/merge_pilot_checkpoints.py",
        description="Merge a single-pass and a two-pass pilot checkpoint into one normalized comparison dataset.",
    )
    parser.add_argument("--single-pass", default=None, help="Path to a benchmarks.runner checkpoint.json. Missing/absent is treated as zero cells.")
    parser.add_argument("--two-pass", default=None, help="Path to a benchmarks.run_two_pass_benchmark checkpoint.json. Missing/absent is treated as zero cells.")
    parser.add_argument("--output", required=True, help="Path to write the merged {'cells': {...}} JSON to.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    merged = merge_checkpoints(args.single_pass, args.two_pass)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2, sort_keys=True))

    engines = sorted({row["engine"] for row in merged["cells"].values()})
    print(f"[merge] wrote {len(merged['cells'])} cells ({', '.join(engines) or 'none'}) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
