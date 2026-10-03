"""Arm 2 — Cursor-style Priompt packing (fidelity MEDIUM).

The Cursor-style contribution under test is the Priompt packing algorithm:
priority ordering, a binary-searched priority cutoff, and the `<first>`
fallback from a full body to a signature stub. The retriever is a stand-in.

Pipeline
1. Chunks: `harness.ast_splitter.PythonSplitter` (the same splitter and
   800-token cap as Arm 1, unchanged).
2. Retrieval: BM25 (`rank_bm25.BM25Okapi`, k1=1.5, b=0.75) over the chunks,
   identifier-aware tokens. Chosen for speed and determinism, and so the
   packing is the isolated variable (no second embedder on CPU). Cursor's
   own codebase retrieval is not public: this is a declared stand-in.
3. Components, one per chunk, each a Priompt `<first>` with two children:
   - the full chunk at priority p;
   - its signature stub (header and first docstring line, then `...`)
     at priority p + PRIOMPT_STUB_PRIORITY_BONUS.
   `<first>` renders the first child whose priority is at least the cutoff:
   above p the body drops and the stub stays, until the cutoff also passes
   the stub's priority. Because each stub outranks its own body, a
   component's cost never grows as the cutoff rises, so the packed total
   is monotone in the cutoff and binary search is valid.
   - Every chunk of the seed symbol's file: p = 1000 at the seed symbol's
     own chunk, minus 1 per chunk of distance from it, floored at 501 so
     the seed file stays above every retrieved chunk. (The spec says "seed
     file priority = 1000"; a flat 1000 makes the file all-or-nothing:
     either the whole file fits or none of it does. Priompt's README
     recommends priority falling with distance from the point of interest
     for long files.)
   - The BM25 top-K other chunks: p = 500 - 10*rank (rank 1-based; K = 50,
     so p stays positive).
4. Cutoff: the lowest candidate cutoff (from the distinct priorities)
   whose packed total, counted with the harness tokenizer, is at most the
   13,000-token budget. Binary search, since the total is monotone.
5. Delivery: included components in descending priority (ties keep source
   order): full chunks as `code_chunk`, stubs as `signature_stub`.

Stubs are built from the chunk's own AST and compile on their own (checked
on all 40,052 FastAPI and Django chunks). Chunks without a meaningful
signature (module blocks, later parts of split functions, one-line defs)
have no stub, and neither does a chunk whose stub would not be strictly
shorter than its body: below the cutoff they drop. With every stub shorter
than its body, a component's cost never grows as the cutoff rises, so the
packed total is monotone and the binary search exact (Priompt's README,
caveat 5, describes the anomaly this avoids).
"""
from __future__ import annotations

import ast

from harness import config as C
from harness.arms.base import RetrievalArm, item_header
from harness.ast_splitter import Chunk, PythonSplitter, iter_python_files
from harness.scoring.canonical import DeliveredContext, DeliveredItem
from harness.scoring.latency import timed


def _doc_first_line(node) -> str | None:
    body = getattr(node, "body", None) or []
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        lines = body[0].value.value.strip().splitlines()
        return lines[0].strip() if lines else ""
    return None


def signature_stub(chunk: Chunk) -> str:
    """The chunk's signature: enclosing class headers, decorators, the
    def/class header, the first docstring line, then `...`. "" when the
    chunk has no signature of its own."""
    if chunk.kind == "module_block" or chunk.part > 1:
        return ""
    try:
        tree = ast.parse(chunk.content)
    except SyntaxError:
        return ""
    lines = chunk.content.split("\n")
    name = chunk.qualified_name.rsplit(".", 1)[-1]
    node, body = None, tree.body
    while body:   # descend the class-header prefix to the owning def/class
        defs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if not defs:
            break
        target = next((n for n in defs if n.name == name), None)
        if target is not None:
            node = target
            break
        body = defs[0].body if isinstance(defs[0], ast.ClassDef) else []
    if node is None or not node.body:
        return ""
    first = node.body[0]
    if first.lineno == node.lineno:          # one-line def: nothing to shorten
        return ""
    first_line = min([d.lineno for d in getattr(first, "decorator_list", [])] + [first.lineno])
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    prefix = lines[:chunk.prefix_lines] if chunk.prefix_lines else []
    # a class preamble's or split function's prefix already holds its header
    header = [] if chunk.kind in ("class_preamble", "function_part") else lines[start - 1:first_line - 1]
    indent = " " * (node.col_offset + 4)
    out = prefix + header
    doc = _doc_first_line(node)
    if doc:
        out.append(f"{indent}{doc!r}")
    out.append(f"{indent}...")
    return "\n".join(out)


class Arm2Priompt(RetrievalArm):
    arm_id = "arm2"

    def __init__(self, tokenizer=None, budget: int = C.RETRIEVAL_BUDGET) -> None:
        super().__init__()
        from harness.tokenizer import get_tokenizer

        self.tok = tokenizer or get_tokenizer()
        self.budget = budget
        self.simplifications = [
            "retrieval is BM25 over the AST chunks (a declared stand-in: Cursor's codebase retrieval is not public)",
            f"stub priority = body priority + {C.PRIOMPT_STUB_PRIORITY_BONUS} (inferred; the spec fixes only "
            "the body priorities 1000 and 500-10*rank)",
            "seed-file priority 1000 minus chunk distance from the seed symbol, floor "
            f"{C.PRIOMPT_SEED_PRIORITY_FLOOR} (inferred; a flat 1000 is all-or-nothing)",
            "Python files only (FastAPI and Django)",
        ]
        self.index_latency: dict[str, list[float]] = {}

    def index(self, repo_path: str, config: dict | None = None) -> None:
        from rank_bm25 import BM25Okapi

        from harness.arms.arm1_rag import lexical_tokens

        config = config or {}
        self.repo_root = repo_path
        with timed(self.index_latency, "L_index"):
            with timed(self.index_latency, "L_ast_parse"):
                splitter = PythonSplitter(self.tok, C.RAG_MAX_CHUNK_TOKENS)
                files = config.get("files") or iter_python_files(repo_path)
                self.chunks: list[Chunk] = [c for f in files for c in splitter.split_file(f, repo_path)]
            if not self.chunks:
                raise RuntimeError(f"arm2: no Python chunks under {repo_path}")
            self.bm25 = BM25Okapi([lexical_tokens(f"{c.qualified_name} {c.content}") for c in self.chunks],
                                  k1=C.RAG_BM25_K1, b=C.RAG_BM25_B)
            self._file_of: dict[str, str] = {}
            for c in self.chunks:
                if c.kind != "module_block":
                    self._file_of.setdefault(c.qualified_name, c.file)
            self._by_file: dict[str, list[int]] = {}
            for i, c in enumerate(self.chunks):
                self._by_file.setdefault(c.file, []).append(i)
        self._lexical_tokens = lexical_tokens
        self._cost: dict[tuple[int, str], int] = {}
        self._stub: dict[int, str] = {}

    # ---------------------------------------------------------------- parts
    def _seed_file(self, seed_symbol: str | None) -> str | None:
        if not seed_symbol:
            return None
        if seed_symbol in self._file_of:
            return self._file_of[seed_symbol]
        # a method's chunk may be named by its class (preamble) only
        parts = seed_symbol.split(".")
        for k in range(len(parts) - 1, 0, -1):
            f = self._file_of.get(".".join(parts[:k]))
            if f:
                return f
        return None

    def _seed_chunk_position(self, seed_idx: list[int], seed_symbol: str | None) -> int:
        """Position, within the seed file's chunk list, of the chunk that
        defines the seed symbol (or its nearest enclosing definition); 0
        when it can't be found."""
        if not seed_symbol:
            return 0
        names = [self.chunks[i].qualified_name for i in seed_idx]
        parts = seed_symbol.split(".")
        for k in range(len(parts), 0, -1):
            prefix = ".".join(parts[:k])
            for pos, q in enumerate(names):
                if q == prefix:
                    return pos
        return 0

    def _render(self, i: int, which: str) -> str:
        c = self.chunks[i]
        if which == "full":
            return f"{item_header(c.source_id, c.qualified_name)}\n{c.content}"
        stub = self._stub.setdefault(i, signature_stub(c))
        return f"{item_header(c.source_id, c.qualified_name + ' [signature]')}\n{stub}" if stub else ""

    def _cost_of(self, i: int, which: str) -> int:
        """Tokens of the rendered component; 0 for a stub that does not
        exist or is not strictly shorter than its body (a fallback that is
        not shorter is pointless, and would make the packed total
        non-monotone in the cutoff: Priompt's own caveat 5)."""
        key = (i, which)
        if key not in self._cost:
            text = self._render(i, which)
            cost = self.tok.count(text) if text else 0
            if which == "stub" and cost >= self._cost_of(i, "full"):
                cost = 0
            self._cost[key] = cost
        return self._cost[key]

    def components(self, query: str, seed_symbol: str | None) -> tuple[list[dict], str | None]:
        """[{chunk, priority, stub_priority, order}] in source / rank order."""
        seed_file = self._seed_file(seed_symbol)
        comps: list[dict] = []
        seed_idx = self._by_file.get(seed_file, []) if seed_file else []
        anchor = self._seed_chunk_position(seed_idx, seed_symbol)
        for n, i in enumerate(seed_idx):
            # 1000 at the seed symbol's own chunk, falling with distance
            priority = max(C.PRIOMPT_SEED_PRIORITY - abs(n - anchor), C.PRIOMPT_SEED_PRIORITY_FLOOR)
            comps.append({"chunk": i, "priority": priority, "order": n, "origin": "seed_file"})
        scores = self.bm25.get_scores(self._lexical_tokens(query))
        seed_set = set(seed_idx)
        ranked = [i for i in sorted(range(len(scores)), key=lambda i: (-scores[i], i))
                  if i not in seed_set and scores[i] > 0][:C.PRIOMPT_RETRIEVE_K]
        for rank, i in enumerate(ranked, start=1):
            comps.append({"chunk": i, "priority": C.PRIOMPT_CHUNK_BASE_PRIORITY - C.PRIOMPT_CHUNK_RANK_STEP * rank,
                          "order": len(seed_idx) + rank, "origin": "bm25", "bm25_rank": rank})
        for comp in comps:
            comp["stub_priority"] = comp["priority"] + C.PRIOMPT_STUB_PRIORITY_BONUS
        return comps, seed_file

    def _choice(self, comp: dict, cutoff: float) -> str | None:
        """Priompt <first>: the first child (full, then stub) whose priority
        is >= cutoff; None if neither."""
        if comp["priority"] >= cutoff:
            return "full"
        if comp["stub_priority"] >= cutoff and self._cost_of(comp["chunk"], "stub"):
            return "stub"
        return None

    def packed_cost(self, comps: list[dict], cutoff: float) -> int:
        total = 0
        for comp in comps:
            which = self._choice(comp, cutoff)
            if which:
                total += self._cost_of(comp["chunk"], which)
        return total

    def find_cutoff(self, comps: list[dict]) -> tuple[float, int]:
        """Lowest cutoff, among the distinct priorities (and +inf: nothing),
        whose packed total fits the budget. The total is non-increasing in
        the cutoff, so binary search."""
        cands = sorted({c["priority"] for c in comps} | {c["stub_priority"] for c in comps}) + [float("inf")]
        lo, hi = 0, len(cands) - 1          # cands[hi] (= inf) always fits: nothing packed
        while lo < hi:
            mid = (lo + hi) // 2
            if self.packed_cost(comps, cands[mid]) <= self.budget:
                hi = mid
            else:
                lo = mid + 1
        return cands[lo], self.packed_cost(comps, cands[lo])

    # ---------------------------------------------------------------- arm
    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        lat: dict[str, list[float]] = {}
        with timed(lat, "L_retrieve"):
            with timed(lat, "L_priority_sort"):
                comps, seed_file = self.components(query, seed.get("seed_symbol"))
            with timed(lat, "L_binary_search_tokenize"):
                cutoff, total = self.find_cutoff(comps)
            chosen: list[tuple[dict, str]] = []
            for comp in comps:
                w = self._choice(comp, cutoff)
                if w:
                    chosen.append((comp, w))
            # delivery order: descending effective priority, ties in source/rank order
            chosen.sort(key=lambda cw: (-(cw[0]["priority"] if cw[1] == "full" else cw[0]["stub_priority"]),
                                        cw[0]["order"]))
            items: list[DeliveredItem] = []
            for comp, which in chosen:
                assert which is not None
                c = self.chunks[comp["chunk"]]
                content = self._render(comp["chunk"], which)
                items.append(DeliveredItem(
                    source_id=c.source_id + ("#signature" if which == "stub" else ""), content=content,
                    token_count=self._cost_of(comp["chunk"], which), rank=len(items) + 1,
                    kind="code_chunk" if which == "full" else "signature_stub",
                    symbols=[] if c.kind == "module_block" else [c.qualified_name],
                    provenance={"origin": comp["origin"], "priority": comp["priority"],
                                "stub_priority": comp["stub_priority"], "rendered": which,
                                "bm25_rank": comp.get("bm25_rank"), "chunk_kind": c.kind},
                ))
        n_full = sum(1 for _, w in chosen if w == "full")
        meta = {
            **self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"), "turn_count": 1,
            "over_budget": False, "ranking_method": "priompt_priority", "seed_file": seed_file,
            "cutoff": cutoff, "n_components": len(comps), "n_full": n_full, "n_stub": len(chosen) - n_full,
            "n_dropped": len(comps) - len(chosen), "packed_tokens": total, "latency_ms": lat,
        }
        return DeliveredContext("arm2", seed["task_id"], items, sum(i.token_count for i in items), self.budget, meta)

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return self.build_prompt_default(ctx)


__all__ = ["Arm2Priompt", "signature_stub"]
