"""Symbol provenance accounting: delivered_symbols_resolved vs
delivered_symbols_named_only (additive; no primary metric reads them)."""
import pytest

from harness.reporting.output_schema import COLUMNS, score_to_row
from harness.scoring import registry as R
from harness.scoring.adapters import adapt
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.scoring.scorer import score
from harness.tasks.synthetic import synthetic_tasks


def _task():
    return {t.task_type: t for t in synthetic_tasks("/nonexistent")}["T2_localization"]


def _ans(task, arm):
    return NormalizedAnswer(arm, task.task_id, "", "", [], "code_block", True, 5, 0.1)


def test_hover_symbol_is_resolved_and_outline_symbol_is_named_only():
    task = _task()
    items = [DeliveredItem("a.py:1", "hover", 1, 1, "lsp_hover", ["m.hovered"], {}, "hover"),
             DeliveredItem("b.py#outline", "outline", 1, 2, "lsp_symbol", ["m.outlined"], {}, "documentSymbol")]
    ctx = DeliveredContext("arm3", task.task_id, items, 2, 13_000, {})
    assert ctx.delivered_symbols_resolved == {"m.hovered"}
    assert ctx.delivered_symbols_named_only == {"m.outlined"}
    res = score(task, ctx, _ans(task, "arm3"))
    assert res.delivered_symbols_resolved == 1 and res.delivered_symbols_named_only == 1
    row = score_to_row(res)
    assert row["delivered_symbols_resolved"] == 1 and row["delivered_symbols_named_only"] == 1


def test_a_symbol_also_resolved_is_not_named_only_and_primary_metrics_ignore_provenance():
    task = _task()
    both = [DeliveredItem("a.py:1", "h", 1, 1, "lsp_hover", ["m.x"], {}, "hover"),
            DeliveredItem("a.py#outline", "o", 1, 2, "lsp_symbol", ["m.x", "m.y"], {}, "documentSymbol")]
    ctx = DeliveredContext("arm3", task.task_id, both, 2, 13_000, {})
    assert ctx.delivered_symbols_resolved == {"m.x"} and ctx.delivered_symbols_named_only == {"m.y"}
    # the same items with other provenances score identically on every primary metric
    as_bodies = [DeliveredItem(i.source_id, i.content, 1, i.rank, "code_chunk", i.symbols, {}, "body") for i in both]
    a = score(task, ctx, _ans(task, "arm3")).to_dict()
    b = score(task, DeliveredContext("arm3", task.task_id, as_bodies, 2, 13_000, {}), _ans(task, "arm3")).to_dict()
    for m in R.universal_metrics():
        if m not in ("delivered_symbols_resolved", "delivered_symbols_named_only"):
            assert a[m] == b[m] or (a[m] != a[m] and b[m] != b[m]), m
    assert (a["delivered_symbols_resolved"], b["delivered_symbols_resolved"]) == (1, 2)


def test_unknown_provenance_rejected_and_registered_in_schema():
    with pytest.raises(ValueError, match="symbol_provenance"):
        DeliveredItem("x", "c", 1, 1, "code_chunk", [], {}, "guess")
    for m in ("delivered_symbols_resolved", "delivered_symbols_named_only"):
        assert m in R.METRICS and m in COLUMNS and R.METRICS[m]["where"] == "row"


def _raw(arm, task, items, meta):
    ctx = DeliveredContext(arm, task.task_id, items, sum(i.token_count for i in items), 13_000, meta)
    return {"bundle": ctx.to_dict(), "completion": {"text": '{"symbols": []}', "generation_tokens": 1,
                                                    "latency_seconds": 0.1}}


@pytest.mark.parametrize("arm,kind,meta,expected", [
    ("arm1", "code_chunk", {}, "body"),
    ("arm2", "code_chunk", {"cutoff": 1, "n_full": 1, "n_stub": 0, "n_dropped": 0}, "body"),
    ("arm2", "signature_stub", {"cutoff": 1, "n_full": 0, "n_stub": 1, "n_dropped": 0}, "signature_stub"),
    ("arm5", "code_chunk", {"turn_count": 2, "turn2b_triggered": False}, "body"),
    ("arm5", "signature_stub", {"turn_count": 2, "turn2b_triggered": False}, "signature_stub"),
    ("oracle", "oracle_truth", {}, "oracle"),
])
def test_adapters_populate_provenance_from_item_kind(arm, kind, meta, expected):
    task = _task()
    ctx, _ = adapt(arm, _raw(arm, task, [DeliveredItem("s", "c", 1, 1, kind, ["m.a"])], meta), task)
    assert [i.symbol_provenance for i in ctx.items] == [expected]


def test_arm3_adapter_maps_the_lsp_method():
    task = _task()
    meta = {"hop1_definitions": 0, "hop2_files": 0, "ready": {}}
    items = [DeliveredItem("a", "h", 1, 1, "lsp_hover", ["m.a"], {"hop": 1, "lsp_method": "textDocument/hover"}),
             DeliveredItem("b", "o", 1, 2, "lsp_symbol", ["m.b"],
                           {"hop": 1, "lsp_method": "textDocument/documentSymbol"}),
             DeliveredItem("c", "d", 1, 3, "lsp_symbol", ["m.c"], {"hop": 1, "lsp_method": "textDocument/definition"})]
    ctx, _ = adapt("arm3", _raw("arm3", task, items, meta), task)
    assert [i.symbol_provenance for i in ctx.items] == ["hover", "documentSymbol", "definition"]
    with pytest.raises(ValueError, match="lsp_method"):
        adapt("arm3", _raw("arm3", task, [DeliveredItem("a", "h", 1, 1, "lsp_hover", [], {"hop": 1})], meta), task)
    with pytest.raises(ValueError, match="expected"):     # an arm's own value must agree with its adapter
        adapt("arm1", _raw("arm1", task, [DeliveredItem("s", "c", 1, 1, "code_chunk", [], {}, "hover")], {}), task)
