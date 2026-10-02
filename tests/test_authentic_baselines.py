"""Tests for the authentic external baselines (`benchmarks/baselines/`).

The Aider test runs only where an `aider-chat` interpreter is available
(`$AIDER_PYTHON` or the conventional venv). Hybrid RAG and Agentless use a
fake encoder and a scripted fake LLM, so they run anywhere.
"""
from __future__ import annotations

import os
from pathlib import Path

import zlib

import numpy as np
import pytest

from benchmarks.baselines import agentless_fl as AG
from benchmarks.baselines.hybrid_rag import HybridIndex, build_chunks, retrieve, rrf
from benchmarks.openai_client import CallResult
from prism.cli import build_pipeline

FIXTURE = str(Path(__file__).parent / "fixtures" / "python_repo")


@pytest.fixture(scope="module")
def builder():
    b, _ = build_pipeline(FIXTURE)
    return b


class HashEncoder:
    """Deterministic bag-of-identifiers encoder - stands in for a real model.
    CRC32 (not `hash()`, which is randomized per process) into 4,096 buckets,
    so collisions don't decide the ranking."""

    name = "test-hash-encoder"

    def encode(self, texts):
        out = np.zeros((len(texts), 4096), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.replace(".", " ").replace("(", " ").split():
                out[i, zlib.crc32(w.lower().encode()) % 4096] += 1.0
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
        return out


def test_rrf_matches_the_published_formula():
    fused = dict(rrf([[1, 2, 3], [3, 1]], k=60))
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[3] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[2] == pytest.approx(1 / 62)


def test_chunks_are_ast_definitions_without_duplicated_method_bodies(builder):
    chunks = {c.symbol: c for c in build_chunks(builder)}
    assert "src.services.pricing.compute_total" in chunks
    cls = chunks["src.services.billing.PaymentProcessor"]
    assert "def charge" not in cls.text and cls.text.startswith("class PaymentProcessor")


def test_hybrid_retrieval_respects_budget_and_finds_the_named_function(builder):
    index = HybridIndex(builder, HashEncoder())
    ctx = retrieve(index, "How does compute_total calculate the order total?", FIXTURE, budget_tokens=300)
    assert ctx.tokens <= 300
    assert ctx.symbols[0] == "src.services.pricing.compute_total"


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, model, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        text = self.responses.pop(0)
        return CallResult(model=model, content=text, prompt_tokens=1, completion_tokens=1, total_tokens=2,
                          cost_usd=0.0, latency_seconds=0.0)


def test_agentless_runs_both_stages_with_upstream_prompts_and_parsers(builder):
    llm = ScriptedLLM(
        "```\nsrc/controllers/checkout.py\nsrc/services/billing.py\n```",
        "```\nsrc/controllers/checkout.py\nfunction: CheckoutController.process_checkout\n\n"
        "src/services/billing.py\nclass: PaymentProcessor\n```",
    )
    res = AG.localize(llm, "m", builder, FIXTURE, "Checkout charges the card twice", budget_tokens=4000)
    assert res.files == ["src/controllers/checkout.py", "src/services/billing.py"]
    assert res.symbols == ["src.controllers.checkout.CheckoutController.process_checkout",
                           "src.services.billing.PaymentProcessor"]
    stage1, stage2 = llm.calls
    assert stage1["user"].startswith("Please look through the following GitHub problem description and Repository structure")
    assert "### Skeleton of Relevant Files ###" in stage2["user"]
    assert all(c["response_format"] is None and c["max_tokens"] == 300 and c["temperature"] == 0.0 for c in llm.calls)
    assert "def process_checkout" in res.text


AIDER_PY = os.environ.get("AIDER_PYTHON", "/home/user/venvs/aider/bin/python")


@pytest.mark.skipif(not os.path.exists(AIDER_PY), reason="aider-chat interpreter not available")
def test_official_aider_map_maps_its_tags_onto_symbols(builder):
    from benchmarks.baselines.aider_official import build_aider_map

    m = build_aider_map(builder, FIXTURE, "Why does process_checkout call verify_session?", map_tokens=1024, python=AIDER_PY)
    assert m.aider_version and m.text and m.tags
    assert "process_checkout" in m.mentioned_idents
    assert m.symbols and not m.unmatched_tags
