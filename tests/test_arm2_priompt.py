"""Arm 2 (Cursor-style Priompt packing): signature stubs, Priompt <first>
semantics, the binary-searched cutoff, seed-file priorities, delivery."""
import itertools
import os
import random

import pytest

from harness import config as C
from harness.arms.arm2_priompt import Arm2Priompt, signature_stub
from harness.ast_splitter import PythonSplitter
from harness.scoring.adapters import adapt
from harness.scoring.canonical import DeliveredContext
from harness.scoring.fairness import verify_ranking

FIXTURE = "/home/user/SCE/tests/fixtures/python_repo"
FASTAPI = "/home/user/SCE/.benchmarks/corpora/fastapi"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


SRC = b'''import os


class Outer(Base):
    """Outer doc."""
    flag = True

    @property
    def method(self):
        """Return one.

        More text."""
        x = 1
        return x

    def one_liner(self): return 2


def big(n):
    """Big doc ending in a quote: "x\""""
    total = 0
    for i in range(n):
        total += i
    with open("f") as fh:
        data = fh.read()
    return total
'''


def _chunks(cap=800):
    return {(c.qualified_name, c.kind, c.part): c for c in PythonSplitter(Words(), cap).split_source(SRC, "pkg/mod.py")}


def test_signature_stubs_compile_and_keep_context():
    ch = _chunks()
    m = signature_stub(ch[("pkg.mod.Outer.method", "method", 1)])
    assert m.splitlines()[:3] == ["class Outer(Base):", "    @property", "    def method(self):"]
    assert "'Return one.'" in m and m.rstrip().endswith("...") and "x = 1" not in m
    pre = signature_stub(ch[("pkg.mod.Outer", "class_preamble", 1)])
    assert pre.startswith("class Outer(Base):") and "'Outer doc.'" in pre and "flag" not in pre
    assert signature_stub(ch[("pkg.mod", "module_block", 1)]) == ""
    assert signature_stub(ch[("pkg.mod.Outer.one_liner", "method", 1)]) == ""      # nothing to shorten
    for s in (m, pre, signature_stub(ch[("pkg.mod.big", "function", 1)])):
        compile(s, "stub", "exec")
    parts = [c for c in PythonSplitter(Words(), 12).split_source(SRC, "pkg/mod.py") if c.kind == "function_part"]
    assert len(parts) > 1 and signature_stub(parts[1]) == ""                       # only part 1 has a stub
    compile(signature_stub(parts[0]), "stub", "exec")


@pytest.mark.skipif(not os.path.isdir(FASTAPI), reason="FastAPI checkout missing")
def test_every_fastapi_stub_compiles():
    from harness.ast_splitter import iter_python_files
    sp = PythonSplitter(Words(), C.RAG_MAX_CHUNK_TOKENS)
    n = 0
    for f in iter_python_files(FASTAPI):
        for c in sp.split_file(f, FASTAPI):
            s = signature_stub(c)
            if s:
                compile(s, c.source_id, "exec", dont_inherit=True)
                n += 1
    assert n > 3000


def _arm(budget=13_000):
    arm = Arm2Priompt(tokenizer=Words(), budget=budget)
    arm.index(FIXTURE, {})
    return arm


def test_first_semantics_body_then_stub_then_nothing():
    arm = _arm()
    i = next(k for k, c in enumerate(arm.chunks) if signature_stub(c))
    comp = {"chunk": i, "priority": 400, "stub_priority": 400 + C.PRIOMPT_STUB_PRIORITY_BONUS}
    assert arm._choice(comp, 400) == "full" and arm._choice(comp, 401) == "stub"
    assert arm._choice(comp, 400 + C.PRIOMPT_STUB_PRIORITY_BONUS) == "stub"
    assert arm._choice(comp, 401 + C.PRIOMPT_STUB_PRIORITY_BONUS) is None


def test_cost_is_monotone_and_binary_search_finds_the_minimal_fitting_cutoff():
    arm = _arm()
    rng = random.Random(0)
    for trial in range(30):
        comps = [{"chunk": rng.randrange(len(arm.chunks)), "priority": rng.choice([1000, 999, 990, 490, 480, 300, 10])}
                 for _ in range(rng.randint(1, 12))]
        for c in comps:
            c["stub_priority"] = c["priority"] + C.PRIOMPT_STUB_PRIORITY_BONUS
        cands = sorted({c["priority"] for c in comps} | {c["stub_priority"] for c in comps}) + [float("inf")]
        costs = [arm.packed_cost(comps, x) for x in cands]
        assert all(a >= b for a, b in itertools.pairwise(costs))                 # non-increasing in the cutoff
        arm.budget = rng.randint(0, max(costs[0], 1))
        cutoff, total = arm.find_cutoff(comps)
        brute = next(x for x in cands if arm.packed_cost(comps, x) <= arm.budget)
        assert cutoff == brute and total <= arm.budget


def test_seed_file_priorities_and_retrieved_band():
    arm = _arm()
    comps, seed_file = arm.components("compute order total", "src.services.pricing.compute_total")
    assert seed_file == "src/services/pricing.py"
    seed = [c for c in comps if c["origin"] == "seed_file"]
    anchor = next(c for c in seed if arm.chunks[c["chunk"]].qualified_name == "src.services.pricing.compute_total")
    assert anchor["priority"] == C.PRIOMPT_SEED_PRIORITY
    assert all(C.PRIOMPT_SEED_PRIORITY_FLOOR <= c["priority"] <= 1000 for c in seed)
    ret = [c for c in comps if c["origin"] == "bm25"]
    assert [c["priority"] for c in ret] == [500 - 10 * r for r in range(1, len(ret) + 1)]
    assert not {c["chunk"] for c in ret} & {c["chunk"] for c in seed}          # no chunk twice
    assert all(c["stub_priority"] == c["priority"] + C.PRIOMPT_STUB_PRIORITY_BONUS for c in comps)


@pytest.mark.parametrize("budget", [13_000, 120, 40, 0])
def test_retrieve_never_exceeds_budget_and_adapts(budget):
    arm = _arm(budget)
    ctx = arm.retrieve("How does compute_total calculate the order total?",
                       {"task_id": "t", "task_type": "T2_localization", "seed_symbol": "src.services.pricing.compute_total"})
    verify_ranking(ctx.items)
    assert ctx.total_tokens == sum(i.token_count for i in ctx.items) <= budget
    meta = ctx.build_meta
    assert meta["n_full"] + meta["n_stub"] + meta["n_dropped"] == meta["n_components"] and meta["fidelity"] == "MEDIUM"
    assert {i.kind for i in ctx.items} <= {"code_chunk", "signature_stub"}
    effective = [i.provenance["priority"] if i.kind == "code_chunk" else i.provenance["stub_priority"] for i in ctx.items]
    assert effective == sorted(effective, reverse=True)                        # delivered in priority order
    if budget == 13_000:
        assert meta["n_stub"] == 0 and "src.services.pricing.compute_total" in ctx.delivered_symbols
    raw = {"bundle": ctx.to_dict(), "completion": {"text": '{"symbols": []}'}}
    from harness.tasks.synthetic import synthetic_tasks
    task = synthetic_tasks(FIXTURE)[1].model_copy(update={"task_id": "t"})
    c2, ans = adapt("arm2", raw, task)
    from dataclasses import replace
    # the adapter only adds symbol_provenance (accounting); the delivered items are otherwise unchanged
    assert [replace(i, symbol_provenance="") for i in c2.items] == ctx.items and ans.extraction_success
    assert [i.symbol_provenance for i in c2.items] == [
        "body" if i.kind == "code_chunk" else "signature_stub" for i in ctx.items]


def test_adapter_rejects_bundle_without_priompt_meta():
    from harness.tasks.synthetic import synthetic_tasks
    task = synthetic_tasks(FIXTURE)[1].model_copy(update={"task_id": "t"})
    raw = {"bundle": DeliveredContext("arm2", "t", [], 0, 13_000, {}).to_dict(), "completion": {"text": ""}}
    with pytest.raises(ValueError, match="cutoff"):
        adapt("arm2", raw, task)


def test_no_seed_symbol_packs_retrieved_only():
    arm = _arm()
    ctx = arm.retrieve("Write a function f that returns 1", {"task_id": "t", "task_type": "T3_codegen", "seed_symbol": None})
    assert ctx.build_meta["seed_file"] is None
    assert all(i.provenance["origin"] == "bm25" for i in ctx.items)
