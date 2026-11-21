import math

import pandas as pd
import pytest

from harness.reporting.output_schema import (COLUMNS, attach_latency_aggregates, read_parquet, to_frame,
                                             validate_frame, write_parquet)
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.scoring.scorer import score
from harness.tasks.synthetic import TARGET, synthetic_tasks


def _results(tmp_path):
    tasks = {t.task_type: t for t in synthetic_tasks(str(tmp_path))}
    out = []
    for arm in ("arm0", "arm1"):
        for i, (tt, task) in enumerate(tasks.items()):
            items = [] if arm == "arm0" else [DeliveredItem("s", "c", 10, 1, "code_chunk", [TARGET])]
            ctx = DeliveredContext(arm, task.task_id, items, sum(i.token_count for i in items), 0 if arm == "arm0" else 13000, {})
            ans = NormalizedAnswer(arm, task.task_id, "", "", [TARGET], "code_block", True, 3, 0.5)
            out.append(score(task, ctx, ans, seed=42,
                             latency_profile={"L_retrieve": [10.0 + i], "L_generate": [100.0 * (i + 1)], "L_e2e": [120.0 * (i + 1)]}))
    return out


def test_round_trip_preserves_nulls_and_nan(tmp_path):
    df = to_frame(_results(tmp_path))
    assert list(df.columns) == COLUMNS
    t5 = df[(df.arm == "arm1") & (df.task_type == "T5_blast_radius")].iloc[0]
    assert not pd.isna(t5.recall_at_5) and pd.isna(t5.acc_at_5_retrieval) and pd.isna(t5.pass_at_1)   # N/A types are null, not 0
    back = read_parquet(write_parquet(df, tmp_path / "cells.parquet"))
    assert len(back) == len(df) and list(back.columns) == COLUMNS
    a0 = back[(back.arm == "arm0") & (back.task_type == "T2_localization")].iloc[0]
    assert math.isnan(a0.cleanliness)                        # arm0 NaN survives, not 1.0
    b5 = back[(back.arm == "arm1") & (back.task_type == "T5_blast_radius")].iloc[0]
    assert pd.isna(b5.acc_at_5_retrieval) and pd.isna(b5.pass_at_1) and not pd.isna(b5.recall_at_5)
    assert back.l_generate_ms.notna().all()


def test_no_composite_columns_allowed():
    df = pd.DataFrame(columns=COLUMNS + ["overall_tsr"])
    with pytest.raises(ValueError, match="composite"):
        validate_frame(df)
    assert not any("composite" in c or "overall" in c for c in COLUMNS)


def test_latency_aggregates_per_arm_corpus(tmp_path):
    df = attach_latency_aggregates(to_frame(_results(tmp_path)), {("arm1", "fastapi"): 1234.0})
    arm1 = df[df.arm == "arm1"]
    # warm p50 drops the first (cold) cell: generate values 100..500 -> warm 200..500
    assert arm1.latency_l_generate_p50.iloc[0] == pytest.approx(350.0)
    assert (arm1.latency_l_index == 1234.0).all() and df[df.arm == "arm0"].latency_l_index.isna().all()
