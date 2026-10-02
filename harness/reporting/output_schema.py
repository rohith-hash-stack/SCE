"""Master output schema: one row per (arm, task_type, corpus, seed, task)
cell, written to Parquet.

Columns come from the metric registry, so a metric added there appears
here. Type-specific columns are null when the metric does not apply to the
row's task type (they are never filled with 0). There is no composite
score column anywhere, and nothing is aggregated across task types.

Latency columns: `l_retrieve_ms`, `l_generate_ms`, `l_e2e_ms` are the
cell's own measurements. The registry's percentile columns
(`latency_l_retrieve_p50`, ...) are per-(arm, corpus) warm percentiles over
those cells, filled in by `attach_latency_aggregates`; `latency_l_index` is
the arm's one-time index time for that corpus.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from harness.scoring import registry as R
from harness.scoring.latency import percentiles

ID_COLUMNS = ["arm", "task_type", "corpus", "seed", "task_id", "repo_id"]
UNIVERSAL = ["task_success", "tsr", "uniform_cpi", "cleanliness", "context_precision", "context_recall",
             "mrr_at_5", "mrr_at_10", "ndcg_at_5", "ndcg_at_10", "map", "p_at_5", "r_at_5", "f1_at_5",
             "relevant_token_density", "hallucination_rate"]
TYPE_SPECIFIC = [k for k, v in R.METRICS.items() if v["where"] == "task_specific"]
EFFICIENCY = ["budget_tokens", "total_tokens", "budget_utilization",
              "latency_l_index", "latency_l_retrieve_p50", "latency_l_retrieve_p95",
              "latency_l_generate_p50", "latency_l_generate_p95", "latency_l_e2e_p50", "latency_l_e2e_p95",
              "l_retrieve_ms", "l_generate_ms", "l_e2e_ms"]
DIAGNOSTICS = ["extraction_success", "over_budget", "turn_count", "digest_safety_loss", "tool_fpr",
               "verification_lift", "recovery_rate", "total_tool_output_tokens", "hallucination_breakdown_json",
               "finish_reason", "generation_capped", "repetition_count"]
COLUMNS = ID_COLUMNS + UNIVERSAL + TYPE_SPECIFIC + EFFICIENCY + DIAGNOSTICS

#: Column-name fragments that would indicate a composite / cross-type score.
FORBIDDEN_FRAGMENTS = ("composite", "overall", "aggregate_score", "weighted_score", "total_score")


def _cell_latency(profile: dict, key: str) -> float | None:
    entry = profile.get(key)
    if not entry:
        return None
    if isinstance(entry, list):
        return float(entry[-1]) if entry else None
    for k in ("p50", "mean", "cold_first_ms"):
        v = entry.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return float(v)
    return None


def score_to_row(res, corpus: str | None = None) -> dict:
    d = res.to_dict() if hasattr(res, "to_dict") else dict(res)
    code = R.TASK_TYPE_CODES[d["task_type"]]
    row: dict[str, object] = {c: None for c in COLUMNS}
    row.update({k: d.get(k) for k in ID_COLUMNS if k in d})
    row["corpus"] = corpus or d["repo_id"]
    for c in UNIVERSAL:
        row[c] = d[c]
    for c in TYPE_SPECIFIC:
        applies = R.METRICS[c].get("type") == code
        row[c] = d["task_specific"].get(c) if applies else None
    for c in ("budget_tokens", "total_tokens", "budget_utilization"):
        row[c] = d[c]
    prof = d.get("latency_profile") or {}
    row["l_retrieve_ms"] = _cell_latency(prof, "L_retrieve")
    row["l_generate_ms"] = _cell_latency(prof, "L_generate")
    row["l_e2e_ms"] = _cell_latency(prof, "L_e2e")
    for c in DIAGNOSTICS:
        if c in d:
            row[c] = d[c]
    row["hallucination_breakdown_json"] = json.dumps(d.get("hallucination_breakdown") or {}, sort_keys=True)
    return row


def to_frame(results, corpus_of: dict[str, str] | None = None) -> pd.DataFrame:
    rows = [score_to_row(r, (corpus_of or {}).get(str(getattr(r, "repo_id", "")))) for r in results]
    df = pd.DataFrame(rows, columns=COLUMNS)
    validate_frame(df)
    return df


def attach_latency_aggregates(df: pd.DataFrame, index_ms: dict[tuple[str, str], float] | None = None) -> pd.DataFrame:
    """Fill the registry's latency percentile columns per (arm, corpus)."""
    df = df.copy()
    for (arm, corpus), idx in df.groupby(["arm", "corpus"]).groups.items():
        for layer in ("retrieve", "generate", "e2e"):
            vals = [v for v in df.loc[idx, f"l_{layer}_ms"].tolist() if v is not None and v == v]
            p = percentiles(vals, warm=True)
            df.loc[idx, f"latency_l_{layer}_p50"] = p["p50"]
            df.loc[idx, f"latency_l_{layer}_p95"] = p["p95"]
        if index_ms and (arm, corpus) in index_ms:
            df.loc[idx, "latency_l_index"] = index_ms[(arm, corpus)]
    return df


def validate_frame(df: pd.DataFrame) -> None:
    bad = [c for c in df.columns if any(f in c.lower() for f in FORBIDDEN_FRAGMENTS)]
    if bad:
        raise ValueError(f"composite/aggregate score columns are not allowed: {bad}")
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")


def write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    validate_frame(df)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def read_parquet(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)
