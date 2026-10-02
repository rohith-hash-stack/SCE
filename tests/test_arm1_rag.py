"""Arm 1 pipeline with deterministic stand-ins for the two neural models
(the real jina/bge weights load on Kaggle; see smoke_test_cpu)."""
import zlib

import numpy as np
import pytest

from harness.arms.arm1_rag import Arm1RAG, lexical_tokens, rrf
from harness.scoring.fairness import verify_ranking

FIXTURE = "/home/user/SCE/tests/fixtures/python_repo"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


class HashEmbedder:
    name = "test-hash"
    def encode(self, texts):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in lexical_tokens(t):
                out[i, zlib.crc32(w.encode()) % 64] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


class OverlapReranker:
    name = "test-overlap"
    def __init__(self): self.calls = []
    def score(self, q, docs):
        self.calls.append(len(docs))
        qs = set(lexical_tokens(q))
        return np.array([len(qs & set(lexical_tokens(d))) for d in docs], dtype=np.float32)


def test_rrf_and_lexical_tokens():
    fused = dict(rrf([[1, 2, 3], [3, 1]], k=60))
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62) and fused[3] == pytest.approx(1 / 63 + 1 / 61)
    assert lexical_tokens("getUserName snake_case_x") == ["getusername", "get", "user", "name", "snake_case_x", "snake", "case", "x"]


def _arm(budget=13_000):
    rr = OverlapReranker()
    arm = Arm1RAG(tokenizer=Words(), embedder=HashEmbedder(), reranker=rr, budget=budget, cache_embeddings=False)
    arm.index(FIXTURE, {"repo_id": "fixture"})
    return arm, rr


def test_end_to_end_retrieval_ranked_within_budget():
    arm, rr = _arm()
    ctx = arm.retrieve("How does compute_total calculate the order total?", {"task_id": "t", "task_type": "T2_localization"})
    verify_ranking(ctx.items)
    assert ctx.items[0].symbols == ["src.services.pricing.compute_total"]
    assert rr.calls == [min(50, ctx.build_meta["n_fused_candidates"])]       # reranks the fused top-50
    assert ctx.total_tokens == sum(i.token_count for i in ctx.items) <= 13_000
    assert ctx.items[0].content.startswith("# src/services/pricing.py:")       # header is counted content
    assert {"L_bm25", "L_dense", "L_rrf", "L_rerank", "L_embed_query"} <= set(ctx.build_meta["latency_ms"])
    assert ctx.build_meta["fidelity"] == "HIGH"
    prompt = arm.build_prompt(ctx)
    assert "## Repository context" in prompt and '"symbols"' in prompt


def test_budget_skips_lowest_ranked_material():
    arm, _ = _arm(budget=60)
    ctx = arm.retrieve("compute_total order", {"task_id": "t", "task_type": "T2_localization"})
    assert ctx.total_tokens <= 60 and ctx.build_meta["skipped_for_budget"]
    verify_ranking(ctx.items)


def _tiny_cross_encoder(path):
    """A randomly initialised 2-layer BERT cross-encoder with a word-level
    tokenizer, built offline: exercises the real ONNX export + ONNX Runtime
    inference path without the bge weights."""
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    from transformers import BertConfig, BertForSequenceClassification, PreTrainedTokenizerFast

    vocab = {t: i for i, t in enumerate(["[PAD]", "[UNK]", "[CLS]", "[SEP]", "query", "document"] + [f"w{i}" for i in range(50)])}
    tk = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tk.pre_tokenizer = pre_tokenizers.Whitespace()
    tk.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
    fast = PreTrainedTokenizerFast(tokenizer_object=tk, unk_token="[UNK]", pad_token="[PAD]", cls_token="[CLS]", sep_token="[SEP]")
    fast.save_pretrained(path)
    cfg = BertConfig(vocab_size=len(vocab), hidden_size=32, num_hidden_layers=2, num_attention_heads=2,
                     intermediate_size=64, num_labels=1, max_position_embeddings=600)
    BertForSequenceClassification(cfg).save_pretrained(path)


def test_onnx_cross_encoder_export_and_inference(tmp_path):
    pytest.importorskip("onnx")
    import time

    from harness.arms.arm1_rag import OnnxCrossEncoder
    model_dir = tmp_path / "tiny"
    _tiny_cross_encoder(str(model_dir))
    ce = OnnxCrossEncoder(model_id=str(model_dir), cache_dir=tmp_path / "cache")
    assert "torch.onnx.export" in ce.provenance                    # no repo ONNX offline -> exported
    docs = [" ".join(f"w{(i * 7 + j) % 50}" for j in range(40)) for i in range(50)]
    t0 = time.perf_counter()
    s = ce.score("query w1 w2", docs)
    assert s.shape == (50,) and np.isfinite(s).all() and len(set(np.round(s, 6))) > 1
    assert time.perf_counter() - t0 < 3.0
    ragged = [" ".join(f"w{j % 50}" for j in range(n)) for n in (3, 90, 17, 400, 1)]   # dynamic batch/seq axes
    assert ce.score("query w3", ragged).shape == (5,)
    ce2 = OnnxCrossEncoder(model_id=str(model_dir), cache_dir=tmp_path / "cache")   # reuses the export
    assert np.allclose(ce2.score("query w1 w2", docs), s, atol=1e-5)
