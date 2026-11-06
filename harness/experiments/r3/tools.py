"""R3's index-backed tools over a WorkingIndex (amendment R3-5, Revisions 1-2).

    layer = ToolLayer(working_index, tokenizer_path)
    text = layer.call("prism_change_context", symbol="pkg.mod.fn")    # tool name first, then its arguments

Every call:
1. refreshes the WorkingIndex (the drift probe; a cold-build-equivalent rebuild only if the shown bytes moved);
2. runs the tool against the shadow tree's index, through PRISM's own code paths:
   - `prism_change_context`, `prism_blast_radius` and `prism_ask`: the MCP server's tool functions
     (`prism.mcp.server`), given a RepoContext built from the WorkingIndex's builder exactly as
     `GraphCache._index` builds one;
   - `prism_new_function_context`: `prism.impact.newcode` (no MCP tool exists);
   - `prism_digest`: `prism.summaries.digest.DigestBuilder`, digest v2 (no MCP tool exists);
   - `related_code` (the baseline's version): BM25 over the index's production functions, as code, R1's control;
3. turns the shadow's absolute paths into repo-relative ones;
4. prepends the R3-5 notice for every gated file the answer touches (its path, or a symbol defined in it, appears);
5. cuts the answer at the per-call cap (4,000 Qwen2.5 tokens), marking the cut; the notice is placed in front of
   the capped answer, outside the cap, so it never changes what is served.

Settings shared by both versions and fixed here (harness choices, not rules): budgets equal to the 4,000-token cap,
and tests excluded from the change context and blast radius, as in R1 and R2.
"""
from __future__ import annotations

import json
import os

CAP_TOKENS = 4000
DIGEST_MAX = 10
RELATED_MAX = 40
PRISM_TOOLS = ("prism_change_context", "prism_blast_radius", "prism_ask", "prism_new_function_context", "prism_digest")
BASELINE_TOOLS = ("related_code",)


class ToolError(Exception):
    """A tool call the agent made wrongly (unknown symbol, bad argument): its text goes back to the agent."""


class ToolLayer:
    def __init__(self, wi, tokenizer_path: str | None = None):
        self.wi = wi
        self._ctx_builder = None
        self._key = None
        self._tok = None
        if tokenizer_path:
            from tokenizers import Tokenizer
            self._tok = Tokenizer.from_file(tokenizer_path)
        self._install()

    # ------------------------------------------------------------------ the index the tools read
    def _install(self) -> None:
        """A RepoContext for the shadow tree, built from the WorkingIndex's builder the way GraphCache._index does,
        placed in the MCP server's cache so its tool functions answer from it."""
        from prism.graph.hierarchy import compute_hierarchical_profile
        from prism.graph.metamodel import SemanticMetamodel
        from prism.mcp import server
        from prism.mcp.cache import GraphCache, RepoContext
        from prism.runtime.contract_cache import compute_or_load_contracts
        from prism.slicer.distance import DistanceConfig, DistanceEngine
        from prism.traversal._cache_keys import evict_derived_caches, snapshot_signature
        b, tags, root = self.wi.builder, self.wi.tag_matrix, self.wi.shadow
        evict_derived_caches()
        metamodel = SemanticMetamodel()
        contracts = compute_or_load_contracts(b, root)
        ctx = RepoContext(repo_root=root, builder=b, tag_matrix=tags, metamodel=metamodel,
                          distance_engine=DistanceEngine(metamodel, tags, DistanceConfig()), runtime_state={},
                          contracts=contracts, hierarchy=compute_hierarchical_profile(b, contracts, root),
                          snapshot_signature=snapshot_signature(b))
        self._key = GraphCache.canonical_path(root)
        server._cache._entries[self._key] = ctx
        server._nlq_indexes.clear()
        self._ctx_builder = b
        self._newfn = None
        self._digests = None
        self._nlq = None

    def close(self) -> None:
        """Remove this run's index from PRISM's server cache. Every run has its own shadow path, so without this the
        indexes of all finished runs in one process stay in memory (the dry run was killed at 4.2 GB after about
        30 runs; a Kaggle cell runs many tasks in one process)."""
        from prism.mcp import server
        from prism.traversal._cache_keys import evict_derived_caches
        from prism.parser import cache as parse_cache
        if self._key is not None:
            server._cache._entries.pop(self._key, None)
        server._nlq_indexes.clear()
        evict_derived_caches()
        prefix = self.wi.shadow.replace("\\", "/").rstrip("/") + "/"      # the parse cache is keyed "path:sha256"
        with parse_cache._lock:
            for k in [k for k in parse_cache._cache if k.startswith(prefix)]:
                del parse_cache._cache[k]
        self._ctx_builder = self._newfn = self._digests = self._nlq = None

    def _sync(self):
        from prism.mcp import server
        r = self.wi.refresh()
        if r.rebuilt or self.wi.builder is not self._ctx_builder:
            self._install()
        elif server._cache._entries.get(self._key) is None:   # another layer cleared the server's cache
            self._install()
        return r

    # ------------------------------------------------------------------ the tools
    def call(self, tool: str, **args) -> str:
        refresh = self._sync()
        self.last_refresh = refresh
        try:
            body = getattr(self, "_" + tool)(**args)
        except ToolError as exc:
            body = f"error: {exc}"
        body = body.replace(self.wi.shadow + os.sep, "").replace(self.wi.shadow, ".")
        # The cap applies to the tool's own answer; the R3-5 notice goes in front of the capped answer (a few
        # dozen tokens per gated file, the same in both versions), so a notice never changes what is served.
        return self._notice_for(body) + self._cap(body)

    def _mcp(self, fn, **kw):
        from mcp.server.mcpserver.exceptions import ToolError as McpToolError
        from mcp.shared.exceptions import MCPError
        try:
            return fn(repo_path=self.wi.shadow, **kw)
        except (MCPError, McpToolError) as exc:
            raise ToolError(getattr(exc, "message", None) or str(exc)) from exc

    def _prism_change_context(self, symbol: str) -> str:
        from prism.mcp import server
        out = self._mcp(server.prism_change_context, symbol=symbol, budget_tokens=CAP_TOKENS, include_tests=False)
        return out["context"]

    def _prism_blast_radius(self, symbol: str) -> str:
        from prism.mcp import server
        out = self._mcp(server.prism_blast_radius, seed_symbol=symbol, budget_tokens=CAP_TOKENS, include_tests=False)
        head = [f"Blast radius of `{out['seed']}`: {out['callers_total']} transitive callers"
                + (" (list cut)" if out["callers_truncated"] else "") + ", nearest first:"]
        head += [f"- hop {c['hop']}: `{c['symbol']}` ({c['file']}:{c['line']})" for c in out["callers"]]
        return "\n".join(head) + "\n\n" + out["envelope"]

    def _prism_ask(self, question: str) -> str:
        from prism.mcp import server
        out = self._mcp(server.prism_ask, question=question, top_k=10)
        return json.dumps(out, indent=1, sort_keys=True, default=str)

    def _prism_new_function_context(self, file: str, name: str, signature: str = "", purpose: str = "",
                                    cls: str | None = None) -> str:
        from prism.impact.newcode import NewFunctionContextBuilder, render
        if self._newfn is None:
            self._newfn = NewFunctionContextBuilder(self.wi.builder, self.wi.shadow)
        try:
            return render(self._newfn.build(file, name, signature=signature, purpose=purpose, cls=cls,
                                            budget_tokens=CAP_TOKENS))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    def _prism_digest(self, symbols: list[str]) -> str:
        from prism.summaries.digest import DigestBuilder
        if self._digests is None:
            self._digests = DigestBuilder(self.wi.builder)
        if len(symbols) > DIGEST_MAX:
            raise ToolError(f"at most {DIGEST_MAX} symbols per call")
        out = []
        for q in symbols:
            if q not in self.wi.builder.symbol_table:
                out.append(f"`{q}`: not found in the index")
                continue
            out.append(self._digests.digest(q).render())
        return "\n\n".join(out)

    def _related_code(self, query: str) -> str:
        """R1's control in the loop: the functions a BM25 search over the repository's production functions ranks
        highest for `query`, as code (each: its name and location, then up to 60 lines)."""
        from harness.experiments.suite.bugs import _hit_text
        from prism.query.nlq import NLQIndex, tokenize
        b = self.wi.builder
        if self._nlq is None:
            self._nlq = NLQIndex(b, self.wi.shadow, summaries=None)
        prod = {q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None
                and s.kind in ("function", "method") and not b._in_test_file(s.file)}
        ranked = [self._nlq.names[i] for i in self._nlq.bm25.rank(tokenize(query))]
        hits = [q for q in ranked if q in prod][:RELATED_MAX]
        if not hits:
            return "No matching functions."
        return "\n\n".join(_hit_text(b, self.wi.shadow, q) for q in hits)

    # ------------------------------------------------------------------ notice and cap
    def _notice_for(self, body: str) -> str:
        touched = []
        b = self.wi.builder
        for rel in self.wi.gated_files():
            if rel in body:
                touched.append(rel)
                continue
            path = os.path.join(self.wi.shadow, rel)
            if any(f"`{q}`" in body or f'"{q}"' in body for q in b._def_nodes
                   if (s := b.symbol_table.get(q)) is not None and s.file == path):
                touched.append(rel)
        return self.wi.notice(touched)

    def _cap(self, text: str) -> str:
        if self._tok is None:
            return text
        ids = self._tok.encode(text).ids
        if len(ids) <= CAP_TOKENS:
            return text
        marker = f"\n... [cut at {CAP_TOKENS:,} tokens]"
        keep = CAP_TOKENS - len(self._tok.encode(marker).ids)
        return self._tok.decode(ids[:keep]) + marker
