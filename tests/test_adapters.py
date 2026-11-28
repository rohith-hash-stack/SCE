import pytest

from harness.scoring.adapters import ADAPTERS, adapt, extract_answer, finalize_context
from harness.scoring.canonical import DeliveredContext, DeliveredItem
from harness.tasks.synthetic import synthetic_tasks


class Words:
    name = "words"
    def count(self, t): return len(t.split())


def _task(tt="T2_localization"):
    return {t.task_type: t for t in synthetic_tasks("/nonexistent")}[tt]


def _items(n, words=10, kind="code_chunk"):
    return [DeliveredItem(f"s{i}", "w " * words, 999, i, kind, [f"m.s{i}"]) for i in range(1, n + 1)]


def test_finalize_recounts_and_trims_from_lowest_rank():
    ctx = DeliveredContext("arm5", "t", _items(5), 5 * 999, 35, {})
    out = finalize_context(ctx, Words())
    assert [i.source_id for i in out.items] == ["s1", "s2", "s3"] and out.total_tokens == 30
    assert out.build_meta["over_budget"] is True and out.build_meta["budget_dropped"] == ["s4", "s5"]
    assert out.items[0].provenance["engine_token_count"] == 999       # engine count kept only for audit
    fits = finalize_context(DeliveredContext("arm1", "t", _items(2), 0, 100, {}), Words())
    assert fits.build_meta["over_budget"] is False and fits.total_tokens == 20


def test_extract_answer_contracts():
    txt = 'Here:\n```json\n{"reasoning": "because", "symbols": ["a.b", "`c.d()`", "a.b"]}\n```'
    assert extract_answer(txt, "T5_blast_radius") == ("because", ["a.b", "c.d"], "code_block", True)
    ans_text, syms, method, ok = extract_answer("It is `pkg.mod.func` I think", "T2_localization")
    assert (method, ok) == ("plain_text", False) and syms == ["pkg.mod.func"]
    assert extract_answer("```python\ndef f(): return 1\n```", "T3_codegen")[2:] == ("code_block", True)
    assert extract_answer("", "T1_conceptual")[3] is False


def _raw(arm, task, items, budget=13_000, meta=None):
    total = sum(i.token_count for i in items)
    ctx = DeliveredContext(arm, task.task_id, items, total, budget, meta or {})
    return {"bundle": ctx.to_dict(), "completion": {"text": '{"symbols": ["x.y"]}', "generation_tokens": 4, "latency_seconds": 1.0}}


def test_seven_adapters_registered():
    assert set(ADAPTERS) == {"arm0", "arm1", "arm2", "arm3", "arm4", "arm5", "oracle"}  # no stubs since M3


def test_active_adapters_produce_valid_pairs_and_enforce_kinds():
    t = _task()
    items = [DeliveredItem("s1", "w", 1, 1, "code_chunk", ["a"])]
    ctx, ans = adapt("arm1", _raw("arm1", t, items), t)
    assert ans.answer_symbols == ["x.y"] and ans.extraction_method == "code_block"
    ctx, ans = adapt("arm5", _raw("arm5", t, items, meta={"turn_count": 2, "turn2b_triggered": False}), t)
    assert ans.extraction_method == "prism_final"
    with pytest.raises(ValueError, match="turn2b"):
        adapt("arm5", _raw("arm5", t, items, meta={"turn_count": 2}), t)
    ctx, ans = adapt("arm0", _raw("arm0", t, [], budget=0), t)
    assert ctx.items == []
    oracle_items = [DeliveredItem("s1", "w", 1, 1, "oracle_truth", ["a"])]
    adapt("oracle", _raw("oracle", t, oracle_items), t)
    with pytest.raises(ValueError, match="kinds"):
        adapt("oracle", _raw("oracle", t, items), t)
    with pytest.raises(ValueError, match="expected"):
        adapt("arm1", _raw("arm5", t, items), t)
