"""M4 gate runner (harness/kaggle_m1.py, M4 mode): flag parsing, checkpoint
keys / resume rule / atomic writes, the per-corpus .prism/ clear, and an
end-to-end dry run on Express T5 (scripted model, stand-in encoders) that
checks the cell count, the checkpoint, resume and the bias-control JSON."""
import json
import os

import pytest

from harness import kaggle_m1 as K

QWEN = os.environ.get("HARNESS_TOKENIZER_PATH", "/home/user/models/qwen2-tokenizer")
EXPRESS = "/home/user/SCE/.benchmarks/corpora/express"


def test_cell_key_and_done_rule():
    assert K.cell_key("django", 43, "django_t5_001_x", "arm3") == "django|43|django_t5_001_x|arm3"
    assert K.done({"row": {}, "result": {"tsr": 0.0}})
    assert K.done({"row": {}, "result": {"tsr": float("nan")}})          # NaN is a value; None is not
    assert not K.done({"row": {"status": "FAIL"}, "result": None})        # a failed cell is re-run
    assert not K.done({"row": {}, "result": {"tsr": None}})
    assert not K.done(None)


def test_checkpoint_round_trip_is_atomic(tmp_path):
    path = tmp_path / "checkpoint.json"
    assert K.load_checkpoint(path) == {}
    cells = {K.cell_key("trpc", 42, "t", "arm1"): {"row": {"status": "PASS"}, "result": {"tsr": 1.0}}}
    K.save_checkpoint(path, "trpc", cells)
    assert K.load_checkpoint(path) == cells
    assert not (tmp_path / "checkpoint.json.tmp").exists()
    assert json.loads(path.read_text())["n_cells"] == 1


def test_clear_prism_cache(tmp_path):
    (tmp_path / ".prism" / "cache").mkdir(parents=True)
    (tmp_path / ".prism" / "cache" / "index.db").write_text("x")
    (tmp_path / "keep.py").write_text("x = 1\n")
    assert K.clear_prism_cache(tmp_path) == {"path": str(tmp_path / ".prism"), "existed": True, "removed": True}
    assert not (tmp_path / ".prism").exists() and (tmp_path / "keep.py").exists()
    assert K.clear_prism_cache(tmp_path)["existed"] is False


def test_parity_samples_use_the_corpus_language(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n" * 50)
    (tmp_path / "b.ts").write_text("export const x = 1;\n" * 80)
    (tmp_path / "c.ts").write_text("export const y = 2;\n")
    assert K.parity_samples_files(str(tmp_path), "express", n=2) == ["b.ts", "c.ts"]
    assert K.parity_samples_files(str(tmp_path), "django") == ["a.py"]


def test_task_type_flag_rejects_deferred_types(capsys):
    with pytest.raises(SystemExit):
        K.main(["--task-types", "T1", "--dry-run", "--out", "/tmp/never"])
    assert "deferred" in capsys.readouterr().err


@pytest.mark.skipif(not (os.path.exists(QWEN) and os.path.isdir(EXPRESS)), reason="tokenizer / Express checkout missing")
def test_m4_dry_run_on_express_t5_checkpoints_resumes_and_writes_bias_control(tmp_path, monkeypatch):
    import harness.tokenizer as T
    from harness import config as C
    monkeypatch.setattr(C, "TOKENIZER_LOCAL_PATH", QWEN)       # read at import time: patch the value
    monkeypatch.setattr(T, "_ACTIVE", None)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    out = tmp_path / "m4_express"
    argv = ["--corpus", "express", "--seeds", "42", "--task-types", "T5", "--dry-run", "--fake-encoders",
            "--out", str(out)]
    assert K.main(argv) == 0
    report = json.loads((out / "gate_report.json").read_text())
    n_arms = len(report["rows"]) // 2
    assert report["mode"] == "m4" and report["n_tasks"] == {"T5_blast_radius": 2}
    assert report["totals"]["cells_checkpointed"] == 2 * n_arms and report["totals"]["rows_fail"] == 0
    assert report["prism_cache_clear"]["path"].endswith("express/.prism")
    assert json.loads((out / "checkpoint.json").read_text())["n_cells"] == 2 * n_arms
    bias = json.loads((out / "t5_bias_control.json").read_text())
    assert bias["n_tasks"] == 2 and "arm3_ratio" in bias
    # resume: every cell is skipped, the parquet is rebuilt from the checkpoint
    assert K.main(argv) == 0
    again = json.loads((out / "gate_report.json").read_text())
    assert again["resumed_cells"] == 2 * n_arms
    import pandas as pd
    assert len(pd.read_parquet(out / "cells.parquet")) == 2 * n_arms
