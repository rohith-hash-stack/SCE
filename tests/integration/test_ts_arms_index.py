"""M4 integration: Arms 1 and 2 index the real Express and tRPC checkouts
through the TypeScript splitter and retrieve for every real T2 task.

Arm 1's two neural models are replaced by deterministic stand-ins (the real
jina/bge weights load on Kaggle; see smoke_test_cpu); chunking, BM25, RRF,
packing and Arm 2's Priompt packing are the real code. Skipped when the
corpus checkout or the Qwen tokenizer is missing.
"""
import os
import zlib

import numpy as np
import pytest

from harness import config as C
from harness.arms.arm1_rag import Arm1RAG, lexical_tokens
from harness.arms.arm2_priompt import Arm2Priompt, signature_stub
from harness.scoring.fairness import verify_ranking

QWEN = os.environ.get("HARNESS_TOKENIZER_PATH", "/home/user/models/qwen2-tokenizer")
CORPORA = "/home/user/SCE/.benchmarks/corpora"
ROOTS = {"express": f"{CORPORA}/express", "trpc": f"{CORPORA}/trpc/packages/server/src"}

pytestmark = pytest.mark.skipif(not os.path.exists(QWEN), reason="Qwen tokenizer missing")


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
    def score(self, q, docs):
        qs = set(lexical_tokens(q))
        return np.array([len(qs & set(lexical_tokens(d))) for d in docs], dtype=np.float32)


@pytest.fixture(scope="module")
def tok():
    from harness.tokenizer import HuggingFaceTokenizer
    return HuggingFaceTokenizer(QWEN)


def _root(corpus):
    root = ROOTS[corpus]
    if not os.path.isdir(root):
        pytest.skip(f"{corpus} checkout missing")
    return root


def _tasks(corpus, root):
    from harness.tasks.loaders import load_tasks
    tasks = load_tasks(corpus, repo_root=root)
    assert tasks, f"no {corpus} tasks loaded"
    return tasks


@pytest.mark.parametrize("corpus", ["express", "trpc"])
def test_arm1_indexes_ts_corpus_and_retrieves(corpus, tok):
    root = _root(corpus)
    arm = Arm1RAG(tokenizer=tok, embedder=HashEmbedder(), reranker=OverlapReranker(), cache_embeddings=False)
    arm.index(root, {"repo_id": corpus})
    assert arm.language == "typescript"
    assert len(arm.chunks) > 200
    assert all(c.language == "typescript" for c in arm.chunks)
    assert not any(c.file.endswith(".py") for c in arm.chunks)
    for task in _tasks(corpus, root):
        ctx = arm.retrieve(task.query, task.seed_dict())
        verify_ranking(ctx.items)
        assert ctx.items and 0 < ctx.total_tokens <= C.RETRIEVAL_BUDGET
        assert ctx.build_meta["language"] == "typescript"


@pytest.mark.parametrize("corpus", ["express", "trpc"])
def test_arm2_indexes_ts_corpus_packs_and_finds_the_seed_file(corpus, tok):
    root = _root(corpus)
    arm = Arm2Priompt(tokenizer=tok)
    arm.index(root, {"repo_id": corpus})
    assert arm.language == "typescript" and len(arm.chunks) > 200
    stubs = [signature_stub(c) for c in arm.chunks]
    assert sum(1 for s in stubs if s) > 100                              # TypeScript stubs exist
    for task in _tasks(corpus, root):
        ctx = arm.retrieve(task.query, task.seed_dict())
        verify_ranking(ctx.items)
        assert ctx.items and 0 < ctx.total_tokens <= C.RETRIEVAL_BUDGET
        # every real T2 seed (nested ones included, e.g. lib.router.next) resolves to its file
        assert ctx.build_meta["seed_file"] is not None, task.seed_symbol
