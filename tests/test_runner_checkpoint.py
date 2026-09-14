"""Phase 1.4 (DeepSeek pilot setup) regression coverage for
`benchmarks.runner`'s checkpointing helpers: `_cell_key`,
`load_checkpoint`, `save_checkpoint`. Pure, filesystem-only - no network,
no LLM client, no real corpus.
"""
from __future__ import annotations

import json

from benchmarks.runner import _cell_key, load_checkpoint, save_checkpoint


def test_cell_key_is_a_plain_pipe_joined_string():
    assert _cell_key("django_t02_001", "prism_v11", 4000, 42) == "django_t02_001|prism_v11|4000|42"


def test_load_checkpoint_missing_file_returns_empty():
    checkpoint = load_checkpoint("/nonexistent/path/checkpoint.json")
    assert checkpoint == {"cells": {}}


def test_load_checkpoint_corrupt_json_returns_empty(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("{not valid json")

    assert load_checkpoint(str(path)) == {"cells": {}}


def test_load_checkpoint_wrong_shape_returns_empty(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(["not", "a", "dict", "with", "cells"]))

    assert load_checkpoint(str(path)) == {"cells": {}}


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "checkpoint.json")
    checkpoint = {
        "cells": {
            "django_t02_001|prism_v11|4000|42": {
                "score": 1.0, "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.0002,
            }
        }
    }

    save_checkpoint(path, checkpoint)
    loaded = load_checkpoint(path)

    assert loaded == checkpoint


def test_save_checkpoint_creates_parent_directories(tmp_path):
    path = tmp_path / "reports" / "pilot" / "checkpoint.json"
    assert not path.parent.exists()

    save_checkpoint(str(path), {"cells": {}})

    assert path.exists()
