"""Seed shards (one results folder per seed): the report merges them, and a run found twice stops it."""
import json
import os

import pytest

from harness.experiments.r4 import rebaseline_report as R


def _run(d, task, arm, seed):
    os.makedirs(d, exist_ok=True)
    run = f"{task}__{arm}__{seed}"
    json.dump({"run": run, "task": task, "version": arm, "seed": seed, "turns": 3, "parse_errors": 0, "end": "done",
               "score": {"resolved": True}}, open(os.path.join(d, run + ".score.json"), "w"))


def test_glob_merges_shards_and_refuses_duplicates(tmp_path):
    tasks = {"r4s-t1": {"id": "r4s-t1", "family": "feature"}}
    for seed in (42, 43):
        for arm in R.ARMS:
            _run(str(tmp_path / f"rebaseline5_seed{seed}"), "r4s-t1", arm, seed)
    _run(str(tmp_path / "rebaseline5_other"), "r4s-t1", "PRISM", 44)      # not a shard folder: not matched
    dirs = R.result_dirs(str(tmp_path / "rebaseline5_seed*"))
    assert [os.path.basename(d) for d in dirs] == ["rebaseline5_seed42", "rebaseline5_seed43"]
    rows = R.load_runs(str(tmp_path / "rebaseline5_seed*"), tasks, None)
    assert len(rows) == 6 and {r["seed"] for r in rows} == {42, 43}
    assert R.load_runs(str(tmp_path / "rebaseline5_seed42"), tasks, None)[0]["path"].endswith("__42.score.json")
    _run(str(tmp_path / "rebaseline5_seed43b"), "r4s-t1", "PRISM", 43)     # the same run in a second folder
    with pytest.raises(SystemExit, match="is in two results folders"):
        R.load_runs(str(tmp_path / "rebaseline5_seed*"), tasks, None)
    with pytest.raises(SystemExit, match="no results folder"):
        R.result_dirs(str(tmp_path / "nothing_*"))
