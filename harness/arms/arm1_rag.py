"""Arm 1 — Advanced RAG (fidelity HIGH). Written from scratch for this
harness; shares nothing with `benchmarks/baselines/hybrid_rag.py`.

Pipeline:
1. Chunking: `harness.ast_splitter.PythonSplitter`, max 800 tokens per
   chunk (harness tokenizer), functions/methods/classes/module blocks.
2. Sparse: `rank_bm25.BM25Okapi(k1=1.5, b=0.75)` over identifier-aware
   tokens (camelCase / snake_case split, plus the whole identifier).
3. Dense: `jinaai/jina-embeddings-v2-base-code` via sentence-transformers on
   CPU; cosine similarity on L2-normalised vectors.
4. Fusion: Reciprocal Rank Fusion, k = 60, over the top 100 of each
   retriever, 1-based ranks.
5. Rerank: `BAAI/bge-reranker-base` cross-encoder, run with ONNX Runtime on
   CPU (CPUExecutionProvider), over the top 50 fused candidates.
6. Pack: reranked order, greedily into the 13,000-token budget; a chunk
   that does not fit is skipped (lowest-ranked material is what gets
   dropped) and recorded.

Everything runs on CPU: the GPU belongs to the generation server.
Encoders and the reranker are injectable (tests use doubles); the defaults
load the real models and fail loudly if they cannot. There is no silent
substitute model.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import numpy as np

from harness import config as C
from harness.arms.base import RetrievalArm, item_header
from harness.ast_splitter import Chunk, PythonSplitter, iter_python_files
from harness.scoring.canonical import DeliveredContext, DeliveredItem
from harness.scoring.latency import timed

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def lexical_tokens(text: str) -> list[str]:
    """Identifier-aware tokens: each identifier lower-cased, plus its
    snake_case and camelCase parts."""
    out: list[str] = []
    for ident in _IDENT.findall(text):
        low = ident.lower()
        out.append(low)
        parts = [p.lower() for piece in ident.split("_") if piece for p in _CAMEL.findall(piece)]
        if len(parts) > 1:
            out.extend(parts)
    return out


def rrf(rankings: list[list[int]], k: int = C.RAG_RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion (Cormack, Clarke & Büttcher 2009), 1-based ranks."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def available_cpus() -> int:
    """CPUs this process may actually use. `os.cpu_count()` reports the
    host's CPUs inside a container, which oversubscribes ONNX Runtime's
    thread pool. Uses the CPU affinity set, capped by a cgroup CPU quota
    when one is set (v2 `cpu.max`, or v1 `cpu.cfs_quota_us`)."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    quota = None
    try:
        q, period = open("/sys/fs/cgroup/cpu.max").read().split()[:2]
        if q != "max":
            quota = float(q) / float(period)
    except (OSError, ValueError):
        try:
            q_us = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
            period_us = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
            if q_us > 0 and period_us > 0:
                quota = q_us / period_us
        except (OSError, ValueError):
            pass
    if quota:
        n = min(n, max(int(quota), 1))
    return max(n, 1)


class JinaCodeEmbedder:
    """jina-embeddings-v2-base-code on CPU."""

    def __init__(self, model_id: str = C.RAG_EMBED_MODEL, max_seq: int = C.RAG_EMBED_MAX_SEQ) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_id
        self._model = SentenceTransformer(model_id, device=C.RAG_EMBED_DEVICE, trust_remote_code=True)
        self._model.max_seq_length = max_seq

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, batch_size=C.RAG_EMBED_BATCH, normalize_embeddings=True,
                                  show_progress_bar=False, convert_to_numpy=True)
        return np.asarray(vecs, dtype=np.float32)


class OnnxCrossEncoder:
    """bge-reranker-base through ONNX Runtime on CPU. The ONNX graph comes
    from the model repository's own `onnx/model.onnx` when it has one,
    otherwise it is exported once with `torch.onnx.export` and cached."""

    def __init__(self, model_id: str = C.RAG_RERANK_MODEL, cache_dir: Path | None = None,
                 max_len: int = C.RAG_RERANK_MAX_LEN) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self.name = f"{model_id}+onnxruntime"
        self.max_len = max_len
        export_dir = Path(cache_dir or C.RAG_CACHE_DIR) / (model_id.replace("/", "__") + "-onnx")
        onnx_path = export_dir / "model.onnx"
        if not onnx_path.exists():
            self._materialize(model_id, export_dir)
        self.tok = AutoTokenizer.from_pretrained(str(export_dir))
        opts = ort.SessionOptions()
        self.threads = available_cpus()
        opts.intra_op_num_threads = self.threads
        self.session = ort.InferenceSession(str(onnx_path), opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.session.get_inputs()}
        self.provenance = (export_dir / "SOURCE").read_text().strip() if (export_dir / "SOURCE").exists() else "unknown"

    @staticmethod
    def _materialize(model_id: str, export_dir: Path) -> None:
        from transformers import AutoTokenizer

        export_dir.mkdir(parents=True, exist_ok=True)
        AutoTokenizer.from_pretrained(model_id).save_pretrained(str(export_dir))
        try:
            import shutil

            from huggingface_hub import hf_hub_download
            src = hf_hub_download(model_id, "onnx/model.onnx")
            shutil.copy(src, export_dir / "model.onnx")
            (export_dir / "SOURCE").write_text(f"{model_id}: onnx/model.onnx from the model repository\n")
            return
        except Exception:
            pass
        import torch
        from transformers import AutoModelForSequenceClassification

        model = AutoModelForSequenceClassification.from_pretrained(model_id).eval()
        tok = AutoTokenizer.from_pretrained(model_id)
        dummy = tok(["query"], ["document"], return_tensors="pt")
        names = list(dummy.keys())
        torch.onnx.export(
            model, tuple(dummy[n] for n in names), str(export_dir / "model.onnx"),
            input_names=names, output_names=["logits"],
            dynamic_axes={**{n: {0: "batch", 1: "seq"} for n in names}, "logits": {0: "batch"}},
            opset_version=17,
        )
        (export_dir / "SOURCE").write_text(f"{model_id}: exported with torch.onnx.export (opset 17)\n")

    def score(self, query: str, docs: list[str]) -> np.ndarray:
        # Long task prompts would crowd out the document in a 512-token
        # window: the query keeps at most a quarter of it.
        q_ids = self.tok(query, add_special_tokens=False)["input_ids"][: self.max_len // 4]
        query = self.tok.decode(q_ids)
        out = []
        for i in range(0, len(docs), C.RAG_RERANK_BATCH):
            batch = docs[i:i + C.RAG_RERANK_BATCH]
            enc = self.tok([query] * len(batch), batch, truncation="only_second", max_length=self.max_len,
                           padding=True, return_tensors="np")
            feed = {k: v.astype(np.int64) for k, v in enc.items() if k in self._inputs}
            logits = self.session.run(None, feed)[0]
            out.append(logits.reshape(len(batch), -1)[:, 0])
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


class Arm1RAG(RetrievalArm):
    arm_id = "arm1"

    def __init__(self, tokenizer=None, embedder=None, reranker=None, budget: int = C.RETRIEVAL_BUDGET,
                 cache_embeddings: bool = True) -> None:
        super().__init__()
        from harness.tokenizer import get_tokenizer

        self.tok = tokenizer or get_tokenizer()
        self._embedder = embedder
        self._reranker = reranker
        self.budget = budget
        self.cache_embeddings = cache_embeddings
        self.simplifications = [
            "Python files only in M1 (TypeScript chunking arrives with the multi-corpus pipeline)",
            "chunk symbols = the definition each chunk belongs to (module blocks carry none)",
            "reranker sees at most a quarter of its 512-token window for the query",
        ]
        self.index_latency: dict[str, list[float]] = {}

    # ------------------------------------------------------------ models
    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = JinaCodeEmbedder()
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = OnnxCrossEncoder()
        return self._reranker

    # ------------------------------------------------------------- index
    def index(self, repo_path: str, config: dict | None = None) -> None:
        from rank_bm25 import BM25Okapi

        config = config or {}
        self.repo_root = repo_path
        self.repo_id = config.get("repo_id", os.path.basename(repo_path.rstrip("/")))
        with timed(self.index_latency, "L_index"):
            # The cross-encoder loads here, first (cheap, before the corpus
            # is embedded): a missing dependency fails once, at index time,
            # with one clear message, not once per retrieval.
            with timed(self.index_latency, "L_rerank_load"):
                try:
                    _ = self.reranker
                except Exception as exc:  # noqa: BLE001 - re-raised with context
                    raise RuntimeError(f"arm1: reranker unavailable: {type(exc).__name__}: {exc}") from exc
            with timed(self.index_latency, "L_chunk"):
                splitter = PythonSplitter(self.tok, C.RAG_MAX_CHUNK_TOKENS)
                files = config.get("files") or iter_python_files(repo_path)
                self.chunks: list[Chunk] = [c for f in files for c in splitter.split_file(f, repo_path)]
            if not self.chunks:
                raise RuntimeError(f"arm1: no Python chunks under {repo_path}")
            with timed(self.index_latency, "L_bm25_build"):
                self.bm25 = BM25Okapi([lexical_tokens(f"{c.qualified_name} {c.content}") for c in self.chunks],
                                      k1=C.RAG_BM25_K1, b=C.RAG_BM25_B)
            with timed(self.index_latency, "L_embed_corpus"):
                try:
                    self.vectors = self._embed_corpus()
                except Exception as exc:  # noqa: BLE001 - re-raised with context
                    raise RuntimeError(f"arm1: embedder unavailable: {type(exc).__name__}: {exc}") from exc

    def _embed_corpus(self) -> np.ndarray:
        texts = [c.content for c in self.chunks]
        path = None
        if self.cache_embeddings:
            digest = hashlib.sha256(("\0".join(texts) + self.embedder.name).encode()).hexdigest()[:16]
            path = C.RAG_CACHE_DIR / f"arm1-{self.repo_id}-{self.embedder.name.replace('/', '_')}-{digest}.npy"
            if path.exists():
                return np.load(path)
        vecs = self.embedder.encode(texts)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, vecs)
        return vecs

    # ---------------------------------------------------------- retrieve
    def _symbols(self, c: Chunk) -> list[str]:
        return [] if c.kind == "module_block" else [c.qualified_name]

    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        lat: dict[str, list[float]] = {}
        with timed(lat, "L_retrieve"):
            with timed(lat, "L_bm25"):
                bm = self.bm25.get_scores(lexical_tokens(query))
                sparse = sorted(range(len(bm)), key=lambda i: (-bm[i], i))[:C.RAG_FUSION_DEPTH]
            with timed(lat, "L_embed_query"):
                q = self.embedder.encode([query])[0]
            with timed(lat, "L_dense"):
                sims = self.vectors @ q
                dense = sorted(range(len(sims)), key=lambda i: (-float(sims[i]), i))[:C.RAG_FUSION_DEPTH]
            with timed(lat, "L_rrf"):
                fused = rrf([sparse, dense])[:C.RAG_RERANK_DEPTH]
            with timed(lat, "L_rerank"):
                cand = [i for i, _ in fused]
                rr = self.reranker.score(query, [self.chunks[i].content for i in cand])
                order = sorted(range(len(cand)), key=lambda j: (-float(rr[j]), j))
            items: list[DeliveredItem] = []
            skipped: list[str] = []
            total = 0
            for j in order:
                c = self.chunks[cand[j]]
                content = f"{item_header(c.source_id, c.qualified_name)}\n{c.content}"
                cost = self.tok.count(content)
                if total + cost > self.budget:
                    skipped.append(c.source_id)
                    continue
                items.append(DeliveredItem(
                    source_id=c.source_id, content=content, token_count=cost, rank=len(items) + 1,
                    kind="code_chunk", symbols=self._symbols(c),
                    provenance={"chunk_kind": c.kind, "fused_rank": j + 1, "rerank_score": float(rr[j]),
                                "bm25_rank": (sparse.index(cand[j]) + 1) if cand[j] in sparse else None,
                                "dense_rank": (dense.index(cand[j]) + 1) if cand[j] in dense else None,
                                "oversized_chunk": c.oversized},
                ))
                total += cost
        meta = {
            **self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"),
            "n_chunks_indexed": len(self.chunks), "n_fused_candidates": len(fused),
            "skipped_for_budget": skipped, "over_budget": False, "turn_count": 1,
            "embedder": self.embedder.name, "reranker": self.reranker.name,
            "ranking_method": "cross_encoder", "latency_ms": lat,
        }
        return DeliveredContext("arm1", seed["task_id"], items, total, self.budget, meta)

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return self.build_prompt_default(ctx)
