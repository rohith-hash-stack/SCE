import math

import pytest

from harness.scoring.agent_diagnostics import digest_safety_loss, recovery_rate, tool_fpr, verification_lift


def test_verification_lift():
    assert math.isnan(verification_lift(False, True))
    assert verification_lift(True, True) == 0.0
    assert verification_lift(True, False) == -1.0


def test_recovery_rate():
    assert math.isnan(recovery_rate(0, 0))
    assert recovery_rate(4, 1) == 0.25
    with pytest.raises(ValueError):
        recovery_rate(1, 2)


def _traj(*outcomes):
    return [{"turn": 1, "calls": [{"tool": "grep", "outcome": o} for o in outcomes]}]


def test_tool_fpr_counts_false_positives_over_dispatched_calls():
    # 3 empty greps, 1 timeout, 1 redundant: 5 calls -> (3 + 1) / 5
    out = tool_fpr(_traj("grep_empty", "grep_empty", "grep_empty", "timeout", "redundant"))
    assert out == {"total_calls": 5, "grep_empty": 3, "read_empty": 0, "read_nonexistent": 0,
                   "read_out_of_bounds": 0, "timeouts": 1, "redundant": 1, "fpr": 0.8}
    assert tool_fpr(_traj("read_empty", "read_nonexistent", "read_out_of_bounds", "ok"))["fpr"] == 0.75
    assert tool_fpr([])["fpr"] == 0.0 and tool_fpr([])["total_calls"] == 0


READ_A = {"msg_id": "r1.1", "text": "10: def compute_total(items):\n11:     return sum(items)"}
READ_B = {"msg_id": "r2.1", "text": "40: def unrelated():\n41:     pass"}
ANSWER = '```json\n{"reasoning": "r", "symbols": ["shop.pricing.compute_total"]}\n```'


def test_digest_safety_loss_when_the_named_symbol_survives_only_in_a_digest():
    assert digest_safety_loss([READ_A, READ_B], ANSWER, {"r1.1"}) == 1.0


def test_digest_safety_loss_is_zero_when_a_preserved_read_still_has_it():
    also = {"msg_id": "r3.1", "text": "12: x = compute_total(cart)"}
    assert digest_safety_loss([READ_A, READ_B, also], ANSWER, {"r1.1"}) == 0.0
    assert digest_safety_loss([READ_A], ANSWER, {"r1.1"}, preserved_text="see compute_total") == 0.0   # the prompt
    assert digest_safety_loss([READ_A], ANSWER, set()) == 0.0                     # nothing digested
    assert digest_safety_loss([READ_A], "No identifiers here.", {"r1.1"}) == 0.0  # nothing named


def test_digest_safety_loss_matches_whole_words_not_substrings():
    longer = {"msg_id": "r3.1", "text": "def compute_totals_v2(): ..."}      # contains the name as a substring
    assert digest_safety_loss([READ_A, longer], ANSWER, {"r1.1"}) == 1.0


def test_diagnostics_are_none_for_other_arms_and_set_for_arm4():
    from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
    from harness.scoring.scorer import score
    from harness.tasks.synthetic import synthetic_tasks
    task = {t.task_type: t for t in synthetic_tasks("/nonexistent")}["T2_localization"]

    def run(arm, kind, meta):
        items = [DeliveredItem("r1.1", "# r1.1\ndef f(): ...", 4, 1, kind, [], {"msg_id": "r1.1"})]
        ctx = DeliveredContext(arm, task.task_id, items, 4, 13_000, {"query": "q", **meta})
        return score(task, ctx, NormalizedAnswer(arm, task.task_id, ANSWER, ANSWER, [], "code_block", True, 5, 0.1))

    other = run("arm1", "code_chunk", {})
    assert other.tool_fpr is None and other.digest_safety_loss is None and other.tool_fpr_breakdown is None
    agent = run("arm4", "tool_result_read", {"trajectory": _traj("ok", "grep_empty")})
    assert agent.tool_fpr == 0.5 and agent.tool_fpr_breakdown["total_calls"] == 2 and agent.digest_safety_loss == 0.0
    from harness.reporting.output_schema import COLUMNS, score_to_row
    row = score_to_row(agent)
    assert {"tool_fpr", "digest_safety_loss", "tool_fpr_json"} <= set(COLUMNS)
    assert row["tool_fpr"] == 0.5 and '"grep_empty": 1' in row["tool_fpr_json"] and score_to_row(other)["tool_fpr_json"] is None
