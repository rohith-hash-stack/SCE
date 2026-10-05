"""harness.reporting.compare_runs: per-cell diff of two gate runs, and the
HARNESS_ACTIVE_ARMS run-time arm selection."""
import json
import os
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from harness.reporting.compare_runs import compare

M2 = "/home/user/SCE/reports/harness_m2/kaggle_smoke"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _active_arms(env_value):
    env = {k: v for k, v in os.environ.items() if k != "HARNESS_ACTIVE_ARMS"}
    if env_value is not None:
        env["HARNESS_ACTIVE_ARMS"] = env_value
    return subprocess.run([sys.executable, "-c", "from harness import config as C; print(','.join(C.ACTIVE_ARMS))"],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def test_active_arms_default_and_override():
    assert _active_arms(None).stdout.strip() == "arm0,arm1,arm2,arm3,arm5,oracle"
    assert _active_arms("arm0, arm5 ,oracle").stdout.strip() == "arm0,arm5,oracle"


@pytest.mark.parametrize("bad", ["arm4", "arm0,armX", ","])
def test_active_arms_rejects_stub_unknown_or_empty(bad):
    out = _active_arms(bad)
    assert out.returncode != 0 and "HARNESS_ACTIVE_ARMS" in out.stderr


def _copy_run(tmp_path, name):
    dst = tmp_path / name
    os.makedirs(dst / "bundles")
    shutil.copy(f"{M2}/cells.parquet", dst / "cells.parquet")
    for f in os.listdir(f"{M2}/bundles"):
        if f.startswith(("arm0_", "arm5_", "oracle_")):
            shutil.copy(f"{M2}/bundles/{f}", dst / "bundles" / f)
    return dst


@pytest.mark.skipif(not os.path.isdir(M2), reason="M2 artifacts missing")
def test_identical_runs_have_no_changes(tmp_path):
    out = compare(_copy_run(tmp_path, "new"), M2, ["arm0", "arm5", "oracle"])
    assert out["n_cells_compared"] == 30 and out["changed_cells"] == [] and out["cells_in_only_one_run"] == []
    assert out["mean_tsr_real_tasks"]["arm5"] == {"old": 0.4, "new": 0.4}       # Gate B only, as reported for M2
    cell = next(c for c in out["cells"] if c["arm"] == "arm5" and "t02_002" in c["task_id"])
    assert cell["turn1_parsed_ok"] == {"old": False, "new": False} and len(cell["hydrated"]["new"]) == 13


@pytest.mark.skipif(not os.path.isdir(M2), reason="M2 artifacts missing")
def test_changed_tsr_and_hydration_order_are_reported(tmp_path):
    new = _copy_run(tmp_path, "new")
    tid = "fastapi_t02_002_solve_dependencies_runtime_resolution"
    cells = pd.read_parquet(new / "cells.parquet")
    cells.loc[(cells.arm == "arm5") & (cells.task_id == tid), "tsr"] = 1.0
    cells.to_parquet(new / "cells.parquet", index=False)
    p = new / "bundles" / f"arm5_{tid}_s42.json"
    b = json.loads(p.read_text())
    items = b["bundle"]["items"]
    items[1], items[6] = items[6], items[1]                                   # move a gold item up
    for i, it in enumerate(items, start=1):
        it["rank"] = i
    p.write_text(json.dumps(b))
    out = compare(new, M2, ["arm0", "arm5", "oracle"])
    assert out["changed_cells"] == [f"arm5/{tid}: tsr, hydrated"]
    cell = next(c for c in out["cells"] if c["arm"] == "arm5" and c["task_id"] == tid)
    assert cell["tsr"] == {"old": 0.0, "new": 1.0}
    assert cell["hydrated"]["order_changed"] and not cell["hydrated"]["set_changed"]
    assert out["mean_tsr_real_tasks"]["arm5"]["new"] == pytest.approx(0.6)
