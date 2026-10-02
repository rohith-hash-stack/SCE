"""Every metric the harness reports, defined once. The scorer, the bootstrap
and the reporting layer all read this dict; a metric that isn't here isn't
reported. If you add a metric anywhere, add it here.

Keys per entry:
- `range`: [low, high] (None = unbounded), when the metric is bounded.
- `higher_is_better`: direction, when one exists.
- `type`: the task type ("T1".."T5") or arm family ("arm4", "prism") the
  metric applies to. Absent = universal (every arm, every task type).
- `where`: "row" (one value per cell, in the ScoreResult / output row),
  "diagnostic" (per cell, reported but never bootstrapped),
  "task_specific" (inside ScoreResult.task_specific), "aggregate"
  (computed over cells: latency percentiles) or "report" (reporting layer
  only, from per-arm means).
"""
from __future__ import annotations

METRICS: dict[str, dict] = {
    # Universal
    "uniform_cpi":            {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "cleanliness":            {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "context_precision":      {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "context_recall":         {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "mrr_at_5":               {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "mrr_at_10":              {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "ndcg_at_5":              {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "ndcg_at_10":             {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "map":                    {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "p_at_5":                 {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "r_at_5":                 {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "f1_at_5":                {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "relevant_token_density": {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "task_success":           {"range": [0, 1], "higher_is_better": True, "where": "row"},
    #: The continuous per-type success score task_success binarises (>= 0.5).
    #: T5: fractional recall of the gold affected set (the T5 primary metric).
    "tsr":                    {"range": [0, 1], "higher_is_better": True, "where": "row"},
    "hallucination_rate":     {"range": [0, 1], "higher_is_better": False, "where": "row"},
    "budget_utilization":     {"range": [0, None], "higher_is_better": False, "where": "row"},
    # Type-specific
    "faithfulness":           {"type": "T1", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "answer_relevancy":       {"type": "T1", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    #: retrieval diagnostic: every gold symbol in the top-5 delivered items
    #: (not part of task_success)
    "acc_at_5_retrieval":     {"type": "T2", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "answer_names_gold":      {"type": "T2", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "answer_names_any_gold":  {"type": "T2", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    #: every gold named, accepting a subclass's inherited member (diagnostic)
    "answer_names_inherited_gold": {"type": "T2", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "answer_gold_recall":     {"type": "T2", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "pass_at_1":              {"type": "T3", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "codebleu":               {"type": "T3", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "patch_exact_match":      {"type": "T4", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "regression_rate":        {"type": "T4", "range": [0, 1], "higher_is_better": False, "where": "task_specific"},
    "recall_at_5":            {"type": "T5", "range": [0, 1], "higher_is_better": True, "where": "task_specific"},
    "false_negative_rate":    {"type": "T5", "range": [0, 1], "higher_is_better": False, "where": "task_specific"},
    # Latency (all arms)
    "latency_l_index":            {"higher_is_better": False, "where": "aggregate"},
    "latency_l_retrieve_p50":     {"higher_is_better": False, "where": "aggregate"},
    "latency_l_retrieve_p95":     {"higher_is_better": False, "where": "aggregate"},
    "latency_l_generate_p50":     {"higher_is_better": False, "where": "aggregate"},
    "latency_l_generate_p95":     {"higher_is_better": False, "where": "aggregate"},
    "latency_l_e2e_p50":          {"higher_is_better": False, "where": "aggregate"},
    "latency_l_e2e_p95":          {"higher_is_better": False, "where": "aggregate"},
    # Diagnostics (arm-specific)
    "digest_safety_loss":     {"type": "arm4", "range": [0, 1], "higher_is_better": False, "where": "row"},
    "tool_fpr":               {"type": "arm4", "range": [0, 1], "higher_is_better": False, "where": "row"},
    "verification_lift":      {"type": "prism", "higher_is_better": True, "where": "row"},
    "recovery_rate":          {"type": "prism", "range": [0, 1], "higher_is_better": True, "where": "row"},
    "total_tool_output_tokens": {"type": "arm4", "range": [0, None], "where": "row"},
    # Per-cell generation diagnostics (not bootstrapped)
    "generation_capped":      {"range": [0, 1], "higher_is_better": False, "where": "diagnostic"},
    "repetition_count":       {"range": [0, None], "higher_is_better": False, "where": "diagnostic"},
    # Reporting-only
    "retrieval_lift":         {"higher_is_better": True, "where": "report"},
    "retrieval_efficiency":   {"higher_is_better": True, "where": "report"},
    #: T2 only: lift on answer_names_any_gold, for when strict Arm 0 T2 tsr is 0
    "retrieval_lift_any_gold": {"higher_is_better": True, "where": "report"},
}

TASK_TYPE_CODES = {"T1_conceptual": "T1", "T2_localization": "T2", "T3_codegen": "T3",
                   "T4_edit": "T4", "T5_blast_radius": "T5"}


def universal_metrics() -> list[str]:
    return [k for k, v in METRICS.items() if "type" not in v and v["where"] == "row"]


def task_specific_metrics(task_type: str) -> list[str]:
    code = TASK_TYPE_CODES.get(task_type, task_type)
    return [k for k, v in METRICS.items() if v.get("type") == code]


def bootstrappable_metrics() -> list[str]:
    """Per-cell metrics a CI can be computed for."""
    return [k for k, v in METRICS.items() if v["where"] in ("row", "task_specific")]


def check_value(name: str, value: float | None) -> None:
    """Raise if a computed value falls outside its declared range. NaN and
    None (not applicable) are always allowed."""
    if value is None or value != value:  # None or NaN
        return
    rng = METRICS[name].get("range")
    if not rng:
        return
    lo, hi = rng
    eps = 1e-9
    if (lo is not None and value < lo - eps) or (hi is not None and value > hi + eps):
        raise ValueError(f"metric {name}={value} outside declared range {rng}")
