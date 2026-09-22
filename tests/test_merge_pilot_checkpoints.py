"""Pilot-4 prep, Script 1: `scripts/merge_pilot_checkpoints.py`. Pure,
stdlib-only, no corpus/LLM - `scripts/` isn't an installed package, so
this imports the script directly via its file path, the same pattern
`benchmarks/run_benchmark.py` itself uses for its own `src/` import.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "merge_pilot_checkpoints.py"
_spec = importlib.util.spec_from_file_location("merge_pilot_checkpoints", _SCRIPT_PATH)
merge_pilot_checkpoints = importlib.util.module_from_spec(_spec)
sys.modules["merge_pilot_checkpoints"] = merge_pilot_checkpoints
_spec.loader.exec_module(merge_pilot_checkpoints)

merge_checkpoints = merge_pilot_checkpoints.merge_checkpoints


def _write_checkpoint(path: Path, cells: dict) -> str:
    path.write_text(json.dumps({"cells": cells}))
    return str(path)


def test_merge_combines_both_harnesses(tmp_path):
    single_pass_path = _write_checkpoint(
        tmp_path / "single_pass.json",
        {
            "django_t02_005_model_save_signals|prism_v11|4000|42": {
                "score": 1.0, "cpi_strict": 1.0, "cpi_fractional": 1.0, "model": "deepseek-v4-flash",
            },
        },
    )
    two_pass_path = _write_checkpoint(
        tmp_path / "two_pass.json",
        {
            "django_t02_005_model_save_signals|4000|42": {
                "task_id": "django_t02_005_model_save_signals", "budget": 4000, "seed": 42,
                "tsr": 1.0, "cpi_turn1_selection": 1.0, "cpi_end_to_end": 1.0, "fpr_gt": 0.0,
                "model": "qwen2.5-coder:14b",
            },
        },
    )

    merged = merge_checkpoints(single_pass_path, two_pass_path)

    assert len(merged["cells"]) == 2
    engines = {row["engine"] for row in merged["cells"].values()}
    assert engines == {"prism_v11", "prism_two_pass"}


def test_merge_normalizes_keys_and_fields(tmp_path):
    single_pass_path = _write_checkpoint(
        tmp_path / "single_pass.json",
        {
            "django_t02_009_queryset_filter_clone|baseline_bfs_bidirectional|2000|43": {
                "score": 0.8, "cpi_strict": 0.6, "cpi_fractional": 0.9, "model": "deepseek-v4-flash",
            },
        },
    )
    two_pass_path = _write_checkpoint(
        tmp_path / "two_pass.json",
        {
            "django_t02_009_queryset_filter_clone|2000|43": {
                "task_id": "django_t02_009_queryset_filter_clone", "budget": 2000, "seed": 43,
                "tsr": 0.5, "cpi_turn1_selection": 0.4, "cpi_end_to_end": 0.6, "fpr_gt": 0.1,
                "model": "qwen2.5-coder:14b",
            },
        },
    )

    merged = merge_checkpoints(single_pass_path, two_pass_path)
    cells = merged["cells"]

    single_key = "django_t02_009_queryset_filter_clone|baseline_bfs_bidirectional|2000|43"
    assert cells[single_key] == {
        "task_id": "django_t02_009_queryset_filter_clone",
        "engine": "baseline_bfs_bidirectional",
        "budget": 2000,
        "seed": 43,
        "tsr": 0.8,
        "cpi_retrieval": 0.6,
        "cpi_answer": 0.6,
        "fpr_gt": None,  # single-pass cells never carry fpr_gt today
        "model": "deepseek-v4-flash",
    }

    two_pass_key = "django_t02_009_queryset_filter_clone|prism_two_pass|2000|43"
    assert cells[two_pass_key] == {
        "task_id": "django_t02_009_queryset_filter_clone",
        "engine": "prism_two_pass",
        "budget": 2000,
        "seed": 43,
        "tsr": 0.5,
        "cpi_retrieval": 0.4,
        "cpi_answer": 0.6,
        "fpr_gt": 0.1,
        "model": "qwen2.5-coder:14b",
    }


def test_merge_defaults_missing_model_to_empty_string(tmp_path):
    """A cell written before Fix 3 (single-pass) has no "model" key at
    all - merge must default it to "" rather than raising a KeyError."""
    single_pass_path = _write_checkpoint(
        tmp_path / "single_pass.json",
        {"django_t02_005_model_save_signals|prism_v11|4000|42": {"score": 1.0, "cpi_strict": 1.0}},
    )

    merged = merge_checkpoints(single_pass_path, None)

    row = merged["cells"]["django_t02_005_model_save_signals|prism_v11|4000|42"]
    assert row["model"] == ""


def test_merge_handles_missing_file_gracefully(tmp_path):
    nonexistent_single = str(tmp_path / "does_not_exist_single.json")
    nonexistent_two_pass = str(tmp_path / "does_not_exist_two_pass.json")

    merged = merge_checkpoints(nonexistent_single, nonexistent_two_pass)

    assert merged == {"cells": {}}


def test_merge_handles_both_paths_none_gracefully():
    assert merge_checkpoints(None, None) == {"cells": {}}


def test_merge_is_idempotent(tmp_path):
    single_pass_path = _write_checkpoint(
        tmp_path / "single_pass.json",
        {"django_t02_005_model_save_signals|prism_v11|4000|42": {"score": 1.0, "cpi_strict": 1.0, "model": "x"}},
    )
    two_pass_path = _write_checkpoint(
        tmp_path / "two_pass.json",
        {
            "django_t02_005_model_save_signals|4000|42": {
                "task_id": "django_t02_005_model_save_signals", "budget": 4000, "seed": 42,
                "tsr": 1.0, "cpi_turn1_selection": 1.0, "cpi_end_to_end": 1.0, "fpr_gt": 0.0, "model": "y",
            },
        },
    )

    first = merge_checkpoints(single_pass_path, two_pass_path)
    second = merge_checkpoints(single_pass_path, two_pass_path)

    assert first == second


def test_merge_skips_malformed_single_pass_key(tmp_path):
    single_pass_path = _write_checkpoint(
        tmp_path / "single_pass.json",
        {"not-a-well-formed-key": {"score": 1.0}},
    )

    merged = merge_checkpoints(single_pass_path, None)

    assert merged == {"cells": {}}
