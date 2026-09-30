"""`baseline_hybrid_dense_bm25`: hybrid dense + BM25 retrieval fused with
Reciprocal Rank Fusion - the standard code-RAG recipe (e.g. LangChain's
`EnsembleRetriever`, which implements weighted RRF with c = 60).

1. **Chunks.** One chunk per function, method and class definition from
   the tree-sitter AST index (`ConcreteGraphBuilder.symbol_table`, the
   same index every other arm uses). Functions and methods contribute their
   full source. Classes contribute their header up to the first nested
   definition, so a method's code is never duplicated in its class's chunk.
2. **Sparse.** `rank_bm25.BM25Okapi(k1=1.5, b=0.75)` over identifier-aware
   tokens (`benchmarks.final_sweep.context_ops.lexical_tokens`).
3. **Dense.** A pluggable encoder over `"<qualified name>\\n<chunk>"`.
   Default `sentence-transformers/all-MiniLM-L6-v2`; alternative
   `text-embedding-3-small` via the OpenAI API. Exact inner-product
   search on L2-normalized vectors (cosine), identical to a FAISS
   `IndexFlatIP` without the extra dependency.
4. **Fusion.** Top `TOP_K` from each retriever;
   `RRF(d) = sum_r 1 / (k + rank_r(d))` with `k = 60` and 1-based ranks
   (Cormack, Clarke & Büttcher, SIGIR 2009).
5. **Pack.** Chunks in descending RRF score, each rendered as a
   `# <file>::<qualified name>` header plus its code, greedily until
   `budget_tokens` (exact `cl100k_base` count of the rendered text).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.slicer.tokenizer import count_tokens

RRF_K = 60
TOP_K = 100
BM25_K1 = 1.5
BM25_B = 0.75
DEFAULT_BUDGET_TOKENS = 4000
DEFAULT_ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_KINDS = frozenset({"function", "method", "class"})
CACHE_DIR = Path(__file__).resolve().parents[2] / ".benchmarks" / "cache" / "hybrid_rag"


@dataclass(frozen=True)
class Chunk:
    symbol: str
    file: str
    text: str


class Encoder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEncoder:
    def __init__(self, model_name: str = DEFAULT_ENCODER) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False))


class OpenAIEmbeddingEncoder:
    def __init__(self, model_name: str = "text-embedding-3-small", api_key_env: str = "OPENAI_API_KEY") -> None:
        from openai import OpenAI

        self.name = model_name
        self._client = OpenAI(api_key=os.environ[api_key_env])

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs: list[list[float]] = []
        for i in range(0, len(texts), 256):
            resp = self._client.embeddings.create(model=self.name, input=[t[:8000] for t in texts[i:i + 256]])
            vecs.extend(d.embedding for d in resp.data)
        arr = np.asarray(vecs, dtype=np.float32)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def build_chunks(builder: ConcreteGraphBuilder) -> list[Chunk]:
    chunks = []
    for qname in sorted(builder.symbol_table._symbols):
        info = builder.symbol_table.get(qname)
        if info is None or info.kind not in CHUNK_KINDS:
            continue
        parsed = builder.parsed_file(info.file)
        if parsed is None:
            continue
        lines = parsed.source.decode("utf-8", errors="replace").splitlines()
        start, end = info.line_range
        body = lines[max(start - 1, 0):end]
        if info.kind == "class":
            # header up to the first nested def/function
            cut = next((i for i, ln in enumerate(body[1:], 1)
                        if ln.lstrip().startswith(("def ", "async def ", "function ", "public ", "private "))), len(body))
            body = body[:cut]
        text = "\n".join(body).strip()
        if text:
            chunks.append(Chunk(qname, info.file, text))
    return chunks


class HybridIndex:
    def __init__(self, builder: ConcreteGraphBuilder, encoder: Encoder, cache_key: str | None = None) -> None:
        from rank_bm25 import BM25Okapi

        from benchmarks.final_sweep.context_ops import lexical_tokens

        self.chunks = build_chunks(builder)
        self._tokens = lexical_tokens
        self.bm25 = BM25Okapi([lexical_tokens(f"{c.symbol} {c.text}") for c in self.chunks], k1=BM25_K1, b=BM25_B)
        self.encoder = encoder
        self.vectors = self._embed(cache_key)

    def _embed(self, cache_key: str | None) -> np.ndarray:
        texts = [f"{c.symbol}\n{c.text}" for c in self.chunks]
        path = None
        if cache_key:
            digest = hashlib.sha256(("\0".join(texts) + self.encoder.name).encode()).hexdigest()[:16]
            path = CACHE_DIR / f"{cache_key}-{self.encoder.name.replace('/', '_')}-{digest}.npy"
            if path.exists():
                return np.load(path)
        vecs = self.encoder.encode(texts)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, vecs)
        return vecs

    def sparse_ranking(self, query: str) -> list[int]:
        scores = self.bm25.get_scores(self._tokens(query))
        return sorted(range(len(scores)), key=lambda i: (-scores[i], self.chunks[i].symbol))[:TOP_K]

    def dense_ranking(self, query: str) -> list[int]:
        q = self.encoder.encode([query])[0]
        scores = self.vectors @ q
        return sorted(range(len(scores)), key=lambda i: (-float(scores[i]), self.chunks[i].symbol))[:TOP_K]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion over several ranked id lists (1-based ranks)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class HybridContext:
    text: str
    tokens: int
    symbols: list[str]
    sparse_top: list[str]
    dense_top: list[str]


def retrieve(index: HybridIndex, query: str, repo_root: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> HybridContext:
    sparse, dense = index.sparse_ranking(query), index.dense_ranking(query)
    blocks, symbols, total = [], [], 0
    for idx, _score in rrf([sparse, dense]):
        c = index.chunks[idx]
        block = f"# {os.path.relpath(c.file, repo_root)}::{c.symbol}\n{c.text}\n"
        cost = count_tokens(block)
        if total + cost > budget_tokens:
            continue
        blocks.append(block)
        symbols.append(c.symbol)
        total += cost
    text = "\n".join(blocks)
    return HybridContext(
        text=text, tokens=count_tokens(text), symbols=symbols,
        sparse_top=[index.chunks[i].symbol for i in sparse[:10]],
        dense_top=[index.chunks[i].symbol for i in dense[:10]],
    )
