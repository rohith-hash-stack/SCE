"""Stage 2 arms of the two-stage validation (`kaggle/run_validation_cell.py`)
in the final-sweep runner: `arm0_no_retrieval`, `arm1_hybrid_rag`, `prism_ask`
and `prism_oracle` (entry-point breadcrumbs over `pragmatic_oracle`)."""
from __future__ import annotations

import hashlib
import re

import numpy as np
import pytest

from benchmarks.final_sweep import config as C
from benchmarks.final_sweep.arms import CorpusState, hybrid_ranking, render_breadcrumbs
from benchmarks.final_sweep.runner import NO_CONTEXT_TEXT, SweepConfig, run_sweep, validate_record
from benchmarks.final_sweep.summary import summarize
from prism.graph.render_breadcrumbs import strip
from tests.test_final_sweep_8arms import FIXTURE_REPO, PIPELINE, FakeClient, _task

STAGE2 = list(C.STAGE2_ARMS.values())


class HashEncoder:
    """Deterministic bag-of-words vectors (stands in for jina)."""

    name = "hash-encoder"

    def encode(self, texts):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z]+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class OverlapReranker:
    """Query-word overlap (stands in for bge-reranker-base)."""

    def __init__(self):
        self.calls = 0

    def score(self, query, docs):
        self.calls += 1
        q = set(re.findall(r"[a-z]+", query.lower()))
        return np.array([len(q & set(re.findall(r"[a-z]+", d.lower()))) for d in docs], dtype=np.float32)


@pytest.fixture(scope="module")
def corpus() -> CorpusState:
    state = CorpusState("django", FIXTURE_REPO)
    state.hybrid_encoder_factory = HashEncoder
    state.reranker_factory = OverlapReranker
    return state


def test_stage2_arms_resolve_outside_the_frozen_protocol():
    assert list(C.ARM_ORDER)[:1] == ["baseline_bfs_bidirectional"] and len(C.ARM_ORDER) == 8  # unchanged
    for name, engine in C.STAGE2_ARMS.items():
        spec = C.resolve_arm(engine)
        assert spec.engine_id == engine, name
    assert C.resolve_arm("prism_oracle").breadcrumbs and C.resolve_arm("prism_oracle").kind == "oracle"
    assert not C.resolve_arm("pragmatic_oracle").breadcrumbs


def test_hybrid_ranking_fuses_reranks_and_is_deterministic(corpus):
    query = "charge the payment and mark the order paid"
    first = hybrid_ranking(corpus, query)
    assert first == hybrid_ranking(corpus, query)
    assert set(first[:5]) & {"src.services.billing.PaymentProcessor.charge", "src.repositories.orders.OrderRepository.mark_paid"}
    assert corpus._reranker.calls >= 1


def test_breadcrumbs_are_inlined_and_strip_exactly(corpus):
    from benchmarks.final_sweep.arms import build_oracle
    from benchmarks.final_sweep.context_ops import render_context
    from prism.graph.render_breadcrumbs import strip

    rendered = render_context(build_oracle(corpus, _task(), 4000, False, "pragmatic_oracle").pkg)
    out, placed = render_breadcrumbs(corpus, PIPELINE, rendered)
    assert out.count("<entry_context>") == len(placed)
    assert strip(out) == rendered


def test_end_to_end_stage2_arms(tmp_path, corpus):
    client = FakeClient()
    cfg = SweepConfig(budget=4000, token_ceiling=4400)
    rows = run_sweep("django", STAGE2, (42,), tmp_path, cfg, workers=1, client=client, corpus=corpus, tasks=[_task()])
    assert len(rows) == len(STAGE2)
    for row in rows:
        assert validate_record(row) == [], (row["engine_id"], validate_record(row))
        assert row["status"] == "ok", row["error"]
    by_arm = {r["engine_id"]: r for r in rows}
    none = by_arm["arm0_no_retrieval"]
    assert none["selected_symbols"] == [] and none["context_tokens"] is not None
    answer_users = {c["engine"].split("/")[0]: c["user"] for c in client.calls if c["engine"].endswith("/answer")}
    assert NO_CONTEXT_TEXT in answer_users["arm0_no_retrieval"]
    # prism_oracle is pragmatic_oracle's exact context plus breadcrumbs only
    assert by_arm["prism_oracle"]["selected_symbols"] == by_arm["pragmatic_oracle"]["selected_symbols"]
    with_block = answer_users["prism_oracle"]
    assert "<entry_context>" in with_block and by_arm["prism_oracle"]["n_breadcrumb_symbols"] >= 1
    assert strip(with_block) == answer_users["pragmatic_oracle"]  # nothing else differs
    assert by_arm["prism_oracle"]["breadcrumb_tokens"] > 0
    assert by_arm["pragmatic_oracle"]["breadcrumb_tokens"] == 0
    assert by_arm["prism_ask"]["selected_symbols"] and by_arm["arm1_hybrid_rag"]["selected_symbols"]
    assert summarize(rows)["schema"]["n_invalid"] == 0


def test_records_from_before_the_breadcrumb_fields_still_validate():
    problems = validate_record({"repo": "x"})
    assert problems and not any("breadcrumb" in p for p in problems)
