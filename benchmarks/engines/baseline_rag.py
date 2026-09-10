"""v1.1+ Empirical Benchmarking Harness: Baseline A - lexical/dense RAG.

512-token sliding-window chunking (64-token overlap, real `tiktoken`
`cl100k_base` token boundaries - falling back to a deterministic
whitespace-based approximation only if `tiktoken`'s encoding can't be
loaded, the same "never crash the harness over a transient tokenizer
unavailability" convention `prism.slicer.tokenizer` already establishes),
`rank_bm25.BM25Okapi` lexical ranking against the seed symbol's own name
tokens (the only query text this engine's `retrieve(seed_symbol, ...)`
interface is given - see `AbstractRetrievalEngine`), greedy top-k chunk
admission until `budget_tokens` is filled.

**Deliberately shallow, matching what a real lexical RAG baseline
actually knows**: this engine's own ranking is pure BM25 over raw text -
no call graph, no four-axis semantics, no signatures. AST symbol
definitions are extracted from the *retrieved* chunks purely to attribute
each chunk back to named symbols for `NodeEntry.id`/`symbol_kind`/`file`/
`line` (the spec's own "Extracts AST symbol definitions... to populate
selected_symbols"), which a real production RAG-over-code system
commonly does too (via ctags/tree-sitter) *after* retrieval, for
citation - it is not given Prism's own deeper contract/signature/
four-axis extraction, which this baseline has no equivalent of at all.
`NodeSignature` stays empty and `NodeFeatures` stays `"NONE"` on every
axis for exactly that reason: populating them from Prism's own semantic
analysis would silently hand this baseline a capability it doesn't
really have, defeating the comparison.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from prism.cli import build_pipeline, discover_files
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.language_tiers import precision_tier_for
from prism.slicer.tokenizer import count_tokens
from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    SeedRef,
)
from prism.surface.build import _relative_path

from benchmarks.engines.base import AbstractRetrievalEngine

ENGINE_NAME = "baseline_rag"

CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
CHUNK_STRIDE_TOKENS = CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS

_WORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

try:
    import tiktoken

    _ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - exercised only when tiktoken's blob host is unreachable
    _ENCODING = None


def _lexical_tokens(text: str) -> list[str]:
    """Word-level tokens for BM25's own corpus/query - distinct from the
    BPE token boundaries chunking uses; BM25 scores lexical overlap, not
    subword pieces."""
    return [w.lower() for w in _WORD_PATTERN.findall(text)]


@dataclass
class Chunk:
    file_path: str
    start_line: int
    end_line: int
    text: str


def _line_of_char_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _chunk_file_by_tokens(file_path: str, source: str) -> list[Chunk]:
    """512-BPE-token windows with a 64-token overlap (448-token stride) -
    real `tiktoken` boundaries when available, else a whitespace-token
    approximation that preserves the same window/stride *shape* (still a
    genuine sliding window, just not exactly BPE-aligned)."""
    if not source.strip():
        return []

    if _ENCODING is not None:
        ids = _ENCODING.encode(source, disallowed_special=())
        chunks = []
        i = 0
        while i < len(ids):
            window = ids[i : i + CHUNK_SIZE_TOKENS]
            window_text = _ENCODING.decode(window)
            start_char = len(_ENCODING.decode(ids[:i]))
            end_char = start_char + len(window_text)
            chunks.append(
                Chunk(
                    file_path=file_path,
                    start_line=_line_of_char_offset(source, min(start_char, len(source))),
                    end_line=_line_of_char_offset(source, min(end_char, len(source))),
                    text=window_text,
                )
            )
            if i + CHUNK_SIZE_TOKENS >= len(ids):
                break
            i += CHUNK_STRIDE_TOKENS
        return chunks

    words = source.split(" ")
    chunks = []
    i = 0
    cursor = 0
    while i < len(words):
        window = words[i : i + CHUNK_SIZE_TOKENS]
        window_text = " ".join(window)
        start_char = cursor
        end_char = start_char + len(window_text)
        chunks.append(
            Chunk(
                file_path=file_path,
                start_line=_line_of_char_offset(source, min(start_char, len(source))),
                end_line=_line_of_char_offset(source, min(end_char, len(source))),
                text=window_text,
            )
        )
        if i + CHUNK_SIZE_TOKENS >= len(words):
            break
        cursor += len(" ".join(words[i:i + CHUNK_STRIDE_TOKENS])) + 1
        i += CHUNK_STRIDE_TOKENS
    return chunks


class BaselineRAGEngine(AbstractRetrievalEngine):
    name = ENGINE_NAME

    def __init__(self) -> None:
        self._repo_root: str | None = None
        self._builder: ConcreteGraphBuilder | None = None
        self._chunks: list[Chunk] = []
        self._bm25: BM25Okapi | None = None

    def index(self, repo_path: str) -> None:
        self._repo_root = repo_path
        # `build_pipeline` is used purely for its symbol table (post-hoc
        # chunk->symbol attribution, see this module's own docstring) -
        # this engine's own retrieval never touches `builder.graph`.
        self._builder, _tag_matrix = build_pipeline(repo_path)

        self._chunks = []
        for file_path in discover_files(repo_path):
            try:
                source = open(file_path, "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            self._chunks.extend(_chunk_file_by_tokens(file_path, source))

        corpus = [_lexical_tokens(c.text) for c in self._chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def _symbols_in_chunk(self, chunk: Chunk) -> list[str]:
        if self._builder is None:
            return []
        found = []
        for symbol in self._builder.symbol_table:
            if symbol.file != chunk.file_path or symbol.kind not in ("function", "method", "class"):
                continue
            sym_start, sym_end = symbol.line_range
            if sym_start <= chunk.end_line and sym_end >= chunk.start_line:
                found.append(symbol.qualified_name)
        return found

    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        if self._builder is None or self._repo_root is None:
            raise RuntimeError("BaselineRAGEngine.retrieve called before index()")
        builder = self._builder

        query = _lexical_tokens(seed_symbol.replace(".", " ").replace("_", " "))
        ranked_indices: list[int] = []
        if self._bm25 is not None and query:
            scores = self._bm25.get_scores(query)
            ranked_indices = sorted(range(len(self._chunks)), key=lambda i: scores[i], reverse=True)

        selected_symbols: dict[str, int] = {}  # qname -> rank at which it was first pulled in
        seed_info = builder.symbol_table.get(seed_symbol)
        if seed_info is not None:
            selected_symbols[seed_symbol] = 0

        total_tokens = count_tokens(seed_symbol) if seed_info is None else 0
        for rank, idx in enumerate(ranked_indices, start=1):
            chunk = self._chunks[idx]
            chunk_cost = count_tokens(chunk.text)
            if total_tokens + chunk_cost > budget_tokens and selected_symbols:
                continue
            for qname in self._symbols_in_chunk(chunk):
                if qname not in selected_symbols:
                    selected_symbols[qname] = rank
            total_tokens += chunk_cost
            if total_tokens >= budget_tokens:
                break

        nodes: list[NodeEntry] = []
        files_seen: set[str] = set()
        for qname, rank in selected_symbols.items():
            info = builder.symbol_table.get(qname)
            if info is None:
                continue
            files_seen.add(info.file)
            parsed = builder.parsed_file(info.file)
            body = ""
            if parsed is not None:
                src = parsed.source.decode("utf-8", errors="replace").splitlines()
                start, end = info.line_range
                body = "\n".join(src[max(start - 1, 0):end])
            nodes.append(
                NodeEntry(
                    id=qname,
                    role="seed" if qname == seed_symbol else "transitive",
                    distance=float(rank),
                    compression="L0_full",
                    cost=count_tokens(body),
                    symbol_name=qname.rsplit(".", 1)[-1],
                    symbol_kind=info.kind,
                    language=info.language_id,
                    file=_relative_path(self._repo_root, info.file),
                    line=info.line_range[0],
                    end_line=info.line_range[1],
                    signature=NodeSignature(),
                    features=NodeFeatures(substance="NONE", form="NONE", output="NONE", role="NONE"),
                    body=body,
                )
            )

        edges: list[EdgeEntry] = []  # a lexical baseline has no graph-edge concept at all

        primary_language = seed_info.language_id if seed_info else "python"
        tier = precision_tier_for(primary_language)
        tier_digit = tier.value[-1] if tier is not None else "3"

        return ContextPackage(
            engine=EngineRef(name=ENGINE_NAME, version="1.0", commit="bm25"),
            seed=SeedRef(
                symbol=seed_symbol,
                file=_relative_path(self._repo_root, seed_info.file) if seed_info else "",
                line=seed_info.line_range[0] if seed_info else 0,
            ),
            budget=BudgetRef(tokens=budget_tokens, tokenizer="cl100k_base" if _ENCODING is not None else "whitespace_fallback", exact=_ENCODING is not None),
            language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
            options={"engine": ENGINE_NAME, "chunk_size_tokens": str(CHUNK_SIZE_TOKENS), "chunk_overlap_tokens": str(CHUNK_OVERLAP_TOKENS)},
            manifest=Manifest(
                packed_nodes=len(nodes),
                considered_nodes=len(self._chunks),
                reachable_nodes=len(self._chunks),
                compression=[ManifestCompression(level="L0_full", count=len(nodes))] if nodes else [],
                distance_metric=ManifestDistanceMetric(name="bm25_rank", lambda_data_flow=0.0, lambda_guard=0.0, dist_max=0.0),
            ),
            coverage=CoverageSummary(total_features=0, covered_features=0, omitted_features=0, features=[]),
            nodes=nodes,
            edges=edges,
        )
