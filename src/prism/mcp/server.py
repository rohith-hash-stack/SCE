"""Prism as a Model Context Protocol server (roadmap Step 4).

Five tools, each a thin wrapper around the same engine `prism.cli` already
exposes as `index`/`query`/`trace`/`status`, backed by a per-repository
`GraphCache` (`prism.mcp.cache`) so an agent session that calls these tools
repeatedly against the same repository only pays the indexing cost once:

  - `get_symbol_context`: the variable-resolution Markdown package
    `prism query` renders (L0 target, L1/L2/L3 neighbors, architectural
    path) - `ContextKnapsackPacker`'s own `D_hybrid` distance metric
    already discounts `CONFIRMED_RUNTIME` edges (see
    `prism.slicer.distance`), so a runtime-confirmed path is preferentially
    packed automatically whenever `.prism/runtime_state.json` exists for
    the repository, with no extra flag needed here.
  - `get_architectural_invariants`: a target's own tags, its incoming/
    outgoing edges' runtime confidence, and any `REQUIRES_BEFORE`
    metamodel relation (e.g. `#db_write` requires `#auth_guard`
    somewhere upstream in its callers) that nothing in its ancestry
    satisfies.
  - `find_symbols_by_tag`: every symbol matrix `M` maps to a given tag,
    with its file/line-range and runtime invocation count.
  - `get_graph_status`: the same summary `prism status` prints, as
    structured data.
  - `reindex_repo`: forces `GraphCache` to drop and rebuild one
    repository's entry, re-merging `.prism/runtime_state.json`.

All five raise `ToolError` (never a bare, opaque crash) for the two
foreseeable failure modes - an unknown repository path and an unknown
symbol/tag - which the SDK turns into a graceful `isError` tool result
for the calling agent rather than a transport-level failure.

Two more tools, `prism.slice`/`prism.explain` (v1.1+ Agent Surface), wrap
the causal engine (`prism.packer.submodular_knapsack.pack_symbol_context`)
in the standardized `<prism_context>` envelope (`prism.surface`) instead
of `get_symbol_context`'s own Markdown package - see `prism.surface.
build`'s own module docstring for why the two tool families deliberately
target different engines rather than one replacing the other. These two
raise a real protocol-level `MCPError` (not `ToolError`) with the spec's
own numeric codes for every anticipated failure - see each tool's own
docstring.
"""
from __future__ import annotations

import difflib
import os
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from prism.graph.metamodel import TagRelation
from prism.language_tiers import precision_tier_for
from prism.mcp.auth import enforce as enforce_auth
from prism.mcp.cache import GraphCache, RepoNotFoundError
from prism.mcp.security import SecurityError, validate_symbol_name, validate_tag, validate_token_budget
from prism.serializers.markdown import render_markdown
from prism.slicer.blueprint import mine_sibling_blueprint
from prism.slicer.knapsack import ContextKnapsackPacker
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import _node_body, build_context_package
from prism.surface.renderer import RenderOptions, render

DEFAULT_TOKEN_BUDGET = 2000

server = MCPServer(
    "prism",
    version="0.2.0",
    instructions=(
        "Deterministic, offline dual-layer semantic context engine for a cloned repository. "
        "Call get_symbol_context first for any 'explain/modify/trace X' task - it returns a "
        "token-budgeted Markdown package (full source for the target, progressively lighter "
        "skeletons/contracts/stubs for its call-graph neighbors) instead of dumping whole files. "
        "Use get_architectural_invariants to check a symbol's own semantic tags and whether an "
        "expected upstream guarantee (e.g. auth before a write) is actually present in its "
        "callers. Use find_symbols_by_tag to enumerate every route handler / db write / auth "
        "guard / external call in the repository. get_graph_status reports index size and how "
        "much of the graph runtime tracing has actually confirmed. Every tool indexes lazily and "
        "caches per repository - call reindex_repo after the source or a new `prism trace` run "
        "changes underfoot."
    ),
)

_cache = GraphCache()


def _repo_context(repo_path: str | None):
    try:
        return _cache.get_or_index(repo_path)
    except (RepoNotFoundError, SecurityError) as exc:
        raise ToolError(str(exc)) from exc


def _relative_path(repo_root: str, file_path: str) -> str:
    return os.path.relpath(file_path, repo_root)


@server.tool()
def get_symbol_context(target_symbol: str, repo_path: str | None = None, token_budget: int = DEFAULT_TOKEN_BUDGET) -> str:
    """Return a token-budgeted, variable-resolution Markdown context
    package for `target_symbol`: full source (L0) for the target itself,
    progressively lighter control-flow skeletons (L1), interface
    contracts (L2), or signature stubs (L3) for its call-graph and
    tag-similarity neighbors, plus an architectural-path diagram. A
    runtime-confirmed call path (see get_graph_status) is preferred over
    an equally-distant unexercised static one when both compete for the
    budget.

    Args:
        target_symbol: Fully qualified symbol name, exactly as indexed -
            e.g. `django.db.models.base.Model.save` for Python, or
            `src.router.Hono.use`-shaped for the other supported
            languages (TypeScript/Go/Java/C#). Use find_symbols_by_tag or
            get_graph_status first if the exact spelling is unknown.
        repo_path: Absolute path to the repository root. Defaults to the
            server's current working directory.
        token_budget: Approximate token budget for the packed context
            (default 2000).
    """
    try:
        target_symbol = validate_symbol_name(target_symbol)
        token_budget = validate_token_budget(token_budget)
    except SecurityError as exc:
        raise ToolError(str(exc)) from exc
    ctx = _repo_context(repo_path)
    if target_symbol not in ctx.symbol_table:
        raise ToolError(
            f"symbol '{target_symbol}' was not found in '{ctx.repo_root}' - "
            "use find_symbols_by_tag or get_graph_status to inspect what was indexed"
        )
    try:
        pack_result = ContextKnapsackPacker(
            token_budget=token_budget, reserved_overhead_tokens=ContextKnapsackPacker.DEFAULT_RESERVED_OVERHEAD_TOKENS
        ).pack(target_symbol, ctx.builder, ctx.tag_matrix, ctx.distance_engine, contracts=ctx.contracts)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    blueprint = mine_sibling_blueprint(ctx.builder, target_symbol)
    return render_markdown(
        pack_result, ctx.tag_matrix, contracts=ctx.contracts, graph=ctx.builder.graph, hierarchy=ctx.hierarchy,
        blueprint=blueprint,
    )


@server.tool()
def get_architectural_invariants(target_symbol: str, repo_path: str | None = None) -> dict[str, Any]:
    """Return `target_symbol`'s active semantic tags, its incoming/
    outgoing call edges with their runtime confidence
    (`CONFIRMED_RUNTIME` vs. `STATIC`), and any metamodel
    `REQUIRES_BEFORE` relation none of its callers satisfy (e.g. a
    `#db_write` with no `#auth_guard` anywhere upstream in its ancestry).

    Args:
        target_symbol: Fully qualified symbol name, exactly as indexed.
        repo_path: Absolute path to the repository root. Defaults to the
            server's current working directory.
    """
    try:
        target_symbol = validate_symbol_name(target_symbol)
    except SecurityError as exc:
        raise ToolError(str(exc)) from exc
    ctx = _repo_context(repo_path)
    if target_symbol not in ctx.symbol_table:
        raise ToolError(f"symbol '{target_symbol}' was not found in '{ctx.repo_root}'")

    g = ctx.builder.graph
    active_tags = sorted(ctx.tag_matrix.get(target_symbol, set()))

    def _edge_view(edges):
        view = []
        for u, v, data in edges:
            view.append(
                {
                    "caller": u,
                    "callee": v,
                    "confidence": "CONFIRMED_RUNTIME" if data.get("confidence") == "CONFIRMED_RUNTIME" else "STATIC",
                    "provenance": data.get("provenance", "STATIC_ANALYSIS"),
                    "runtime_invocation_count": data.get("runtime_invocation_count", 0),
                }
            )
        return view

    incoming = _edge_view(g.in_edges(target_symbol, data=True)) if target_symbol in g else []
    outgoing = _edge_view(g.out_edges(target_symbol, data=True)) if target_symbol in g else []

    # "Before" = anywhere in target_symbol's own ancestry (every node with
    # a directed path reaching it) - the real call-chain sense of
    # REQUIRES_BEFORE, not just target_symbol's own tags or its immediate
    # callers.
    ancestor_tags: set[str] = set(active_tags)
    if target_symbol in g:
        import networkx as nx

        for ancestor in nx.ancestors(g, target_symbol):
            ancestor_tags.update(ctx.tag_matrix.get(ancestor, set()))

    unfulfilled: list[dict] = []
    for tag in active_tags:
        if tag not in ctx.metamodel.graph:
            continue
        for required_tag, edge_data in ctx.metamodel.graph[tag].items():
            if edge_data.get("relation") != TagRelation.REQUIRES_BEFORE:
                continue
            if required_tag not in ancestor_tags:
                unfulfilled.append({"tag": tag, "requires_before": required_tag})

    return {
        "symbol": target_symbol,
        "active_tags": active_tags,
        "incoming_edges": incoming,
        "outgoing_edges": outgoing,
        "unfulfilled_invariants": unfulfilled,
    }


@server.tool()
def find_symbols_by_tag(tag: str, repo_path: str | None = None) -> dict[str, Any]:
    """Return every symbol matrix `M` maps to `tag` (e.g. `#route_handler`,
    `#db_write`, `#auth_guard`, `#external_io`), with its file path, line
    range, and runtime invocation count (the sum of confirmed runtime
    invocations across every caller that reached it - 0 if it has never
    been observed at runtime, regardless of how many static call sites
    reference it).

    Args:
        tag: One of the metamodel's registered tags, `#`-prefixed.
        repo_path: Absolute path to the repository root. Defaults to the
            server's current working directory.
    """
    try:
        tag = validate_tag(tag)
    except SecurityError as exc:
        raise ToolError(str(exc)) from exc
    ctx = _repo_context(repo_path)
    known_tags = set(ctx.metamodel.tags())
    if tag not in known_tags:
        raise ToolError(f"unknown tag {tag!r} - registered tags are: {', '.join(sorted(known_tags))}")

    g = ctx.builder.graph
    matches = []
    for symbol, tags in ctx.tag_matrix.items():
        if tag not in tags:
            continue
        info = ctx.symbol_table.get(symbol)
        if info is None:
            continue
        invocation_count = sum(
            data.get("runtime_invocation_count", 0) for _, _, data in g.in_edges(symbol, data=True)
        ) if symbol in g else 0
        matches.append(
            {
                "symbol": symbol,
                "file": _relative_path(ctx.repo_root, info.file),
                "line_range": list(info.line_range),
                "language": info.language_id,
                "runtime_invocation_count": invocation_count,
            }
        )
    matches.sort(key=lambda m: m["symbol"])
    return {"tag": tag, "count": len(matches), "symbols": matches}


@server.tool()
def get_graph_status(repo_path: str | None = None) -> dict[str, Any]:
    """Return summary statistics for a repository's indexed graph: total
    symbols, total call edges, how many of those edges a runtime trace
    has confirmed (`CONFIRMED_RUNTIME`) vs. how many the static resolver
    never found at all until a trace revealed them (`RUNTIME_DISCOVERED`),
    and a per-tag symbol count.

    Args:
        repo_path: Absolute path to the repository root. Defaults to the
            server's current working directory.
    """
    ctx = _repo_context(repo_path)
    g = ctx.builder.graph

    confirmed_runtime_edges = 0
    runtime_discovered_edges = 0
    for _u, _v, data in g.edges(data=True):
        if data.get("confidence") == "CONFIRMED_RUNTIME":
            confirmed_runtime_edges += 1
        if data.get("provenance") == "RUNTIME_DISCOVERED":
            runtime_discovered_edges += 1

    tag_counts: dict[str, int] = {}
    for tags in ctx.tag_matrix.values():
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "repo_path": ctx.repo_root,
        "total_symbols": len(ctx.symbol_table),
        "total_edges": g.number_of_edges(),
        "confirmed_runtime_edges": confirmed_runtime_edges,
        "runtime_discovered_edges": runtime_discovered_edges,
        "tag_counts": dict(sorted(tag_counts.items())),
        "trace_files_ingested": len(ctx.runtime_state.get("trace_files", [])),
        "indexed_at": ctx.indexed_at,
    }


@server.tool()
def reindex_repo(repo_path: str | None = None) -> dict[str, Any]:
    """Drop the cached graph for a repository (if any) and rebuild it
    from source, re-merging `.prism/runtime_state.json` if present. Call
    this after editing source files or after a new `prism trace` run, since
    nothing here watches the filesystem automatically.

    Args:
        repo_path: Absolute path to the repository root. Defaults to the
            server's current working directory.
    """
    try:
        ctx = _cache.reindex(repo_path)
    except (RepoNotFoundError, SecurityError) as exc:
        raise ToolError(str(exc)) from exc
    return {
        "repo_path": ctx.repo_root,
        "total_symbols": len(ctx.symbol_table),
        "total_edges": ctx.builder.graph.number_of_edges(),
        "trace_files_ingested": len(ctx.runtime_state.get("trace_files", [])),
        "indexed_at": ctx.indexed_at,
    }


# --------------------------------------------------------------------- #
# v1.1+ Agent Surface: prism.slice / prism.explain
# --------------------------------------------------------------------- #
def _validate_repo_path_for_surface(repo_path: str) -> None:
    """-32001: a `..` path-traversal segment, or (when the server is
    pinned via `--repo`) a path outside that sandbox - reuses `GraphCache.
    canonical_path`'s own `SecurityError`, the same check the five
    original tools already depend on, rather than a second, possibly-
    drifting implementation.
    """
    normalized = repo_path.replace("\\", "/")
    if any(part == ".." for part in normalized.split("/")):
        raise MCPError(code=-32001, message=f"repo_path {repo_path!r} contains a '..' path-traversal segment")
    try:
        GraphCache.canonical_path(repo_path)
    except SecurityError as exc:
        raise MCPError(code=-32001, message=str(exc)) from exc


def _repo_context_for_surface(repo_path: str):
    _validate_repo_path_for_surface(repo_path)
    try:
        return _cache.get_or_index(repo_path)
    except (RepoNotFoundError, SecurityError) as exc:
        raise MCPError(code=-32001, message=str(exc)) from exc


def _resolve_seed_or_raise(repo_ctx, seed_symbol: str) -> None:
    """-32002, with a fuzzy-matched `candidates` list in `data` when one
    exists - `difflib.get_close_matches` against every indexed qualified
    name, the same "did you mean" a human would want typing a symbol name
    from memory."""
    if seed_symbol in repo_ctx.symbol_table:
        return
    all_names = [s.qualified_name for s in repo_ctx.symbol_table]
    candidates = difflib.get_close_matches(seed_symbol, all_names, n=5, cutoff=0.5)
    raise MCPError(
        code=-32002,
        message=f"symbol '{seed_symbol}' was not found in '{repo_ctx.repo_root}'",
        data={"candidates": candidates} if candidates else None,
    )


def _filter_by_language_tier(pkg, language_tier: str):
    """`language_tier != "auto"` restricts `<nodes>`/`<edges>` to symbols
    at that precision tier (`prism.language_tiers.precision_tier_for`) -
    the seed itself is always kept regardless (a caller filtering out
    their own seed's language would get a package with no seed at all,
    which is never useful)."""
    if language_tier == "auto":
        return pkg
    keep_ids = set()
    for node in pkg.nodes:
        if node.role == "seed":
            keep_ids.add(node.id)
            continue
        tier = precision_tier_for(node.language)
        digit = tier.value[-1] if tier is not None else "3"
        if digit == language_tier:
            keep_ids.add(node.id)
    filtered_nodes = [n for n in pkg.nodes if n.id in keep_ids]
    filtered_edges = [e for e in pkg.edges if e.from_node in keep_ids and e.to_node in keep_ids]
    return pkg.model_copy(update={"nodes": filtered_nodes, "edges": filtered_edges})


def _apply_detail_level(pkg, detail_level: str):
    """`prism.explain`'s own two reduced views - "summary" drops
    `<nodes>`/`<edges>` entirely (manifest/coverage/metadata only);
    "manifest" keeps every node but blanks its `body` (signature/
    features/contract stay). Applied at the `ContextPackage` level (not
    via `RenderOptions`) so both `format="xml"` and `format="json"` see
    the same reduced content, not just the XML rendering."""
    if detail_level == "summary":
        return pkg.model_copy(update={"nodes": [], "edges": []})
    if detail_level == "manifest":
        blanked = [n.model_copy(update={"body": ""}) for n in pkg.nodes]
        return pkg.model_copy(update={"nodes": blanked})
    return pkg


def _authorization_header(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    headers = ctx.headers
    if not headers:
        return None
    return headers.get("authorization") or headers.get("Authorization")


def _build_envelope_response(
    repo_path: str,
    seed_symbol: str,
    budget_tokens: int,
    language_tier: str,
    format: str,
    include_warnings: bool,
    api_key: str | None,
    ctx: Context | None,
    detail_level: str = "full",
) -> dict[str, Any]:
    if not (500 <= budget_tokens <= 128_000):
        raise MCPError(code=-32602, message=f"budget_tokens must be between 500 and 128000, got {budget_tokens}")
    if language_tier not in ("auto", "1", "2", "3"):
        raise MCPError(code=-32602, message=f"language_tier must be one of 'auto', '1', '2', '3' - got {language_tier!r}")
    if format not in ("xml", "json"):
        raise MCPError(code=-32602, message=f"format must be 'xml' or 'json' - got {format!r}")
    if detail_level not in ("summary", "manifest", "full"):
        raise MCPError(code=-32602, message=f"detail_level must be 'summary', 'manifest', or 'full' - got {detail_level!r}")

    enforce_auth(_authorization_header(ctx), api_key)

    repo_ctx = _repo_context_for_surface(repo_path)
    _resolve_seed_or_raise(repo_ctx, seed_symbol)

    seed_cost = count_tokens(_node_body(repo_ctx.builder, seed_symbol))
    if seed_cost > budget_tokens:
        raise MCPError(
            code=-32003,
            message=f"seed '{seed_symbol}' alone costs {seed_cost} tokens, exceeding budget_tokens={budget_tokens}",
        )

    pkg = build_context_package(repo_ctx.builder, seed_symbol, repo_ctx.repo_root, budget_tokens, contracts=repo_ctx.contracts)
    pkg = _filter_by_language_tier(pkg, language_tier)
    pkg = _apply_detail_level(pkg, detail_level)
    if not include_warnings:
        pkg = pkg.model_copy(update={"warnings": []})

    envelope = pkg.model_dump_json(indent=2) if format == "json" else render(pkg, RenderOptions())
    token_count = count_tokens(envelope)
    return {"envelope": envelope, "token_count": token_count, "truncated": token_count > budget_tokens}


@server.tool(name="prism.slice")
def prism_slice(
    repo_path: str,
    seed_symbol: str,
    budget_tokens: int = 4000,
    language_tier: str = "auto",
    format: str = "xml",
    include_warnings: bool = True,
    api_key: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Return a `<prism_context>` envelope (v1.1+ Agent Surface, `prism.
    surface`) packing `seed_symbol`'s causally-coupled context - both
    downstream dependencies and upstream blast-radius callers (`prism.
    packer.submodular_knapsack.pack_symbol_context`) - deterministically
    serialized (`prism.surface.renderer.render`) or as an equivalent JSON
    document (`format="json"`).

    Args:
        repo_path: Absolute path to the repository root. Rejected
            (`-32001`) if it contains a `..` segment or - when this
            server is pinned via `prism mcp --repo` - escapes that
            sandbox.
        seed_symbol: Fully qualified symbol name, exactly as indexed.
            Unknown names return `-32002` with fuzzy-matched candidates.
        budget_tokens: 500-128000. A seed whose own body alone exceeds
            this returns `-32003`.
        language_tier: "auto", or restrict `<nodes>`/`<edges>` to one
            precision tier ("1"/"2"/"3") - the seed is always kept.
        format: "xml" (the canonical envelope) or "json" (the same
            `ContextPackage`, JSON-serialized).
        include_warnings: When `False`, drops this package's own derived
            warnings before rendering - a genuine `BUDGET_OVERFLOW`
            (computed at render time) is never suppressed.
        api_key: Bearer credential, when `PRISM_MCP_API_KEYS` is
            configured - see `prism.mcp.auth`'s own module docstring for
            why this exists as a tool argument, not only a header.
    """
    return _build_envelope_response(repo_path, seed_symbol, budget_tokens, language_tier, format, include_warnings, api_key, ctx)


@server.tool(name="prism.explain")
def prism_explain(
    repo_path: str,
    seed_symbol: str,
    budget_tokens: int = 4000,
    language_tier: str = "auto",
    format: str = "xml",
    include_warnings: bool = True,
    detail_level: str = "full",
    api_key: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Same envelope as `prism.slice`, at a reduced level of detail:

    Args:
        detail_level: "full" (identical to `prism.slice`), "manifest"
            (every packed node listed, `body` blanked), or "summary"
            (`<nodes>`/`<edges>` omitted entirely - metadata/manifest/
            coverage only).
        (all other arguments: see `prism.slice`.)
    """
    return _build_envelope_response(
        repo_path, seed_symbol, budget_tokens, language_tier, format, include_warnings, api_key, ctx, detail_level=detail_level
    )


def run_server(transport: str = "stdio", repo_path: str | None = None) -> None:
    """Entry point for `prism mcp` (`src/prism/cli.py`). `repo_path`, if given,
    becomes the default `repo_path` for any tool call that omits it -
    via `PRISM_MCP_DEFAULT_REPO` (see `GraphCache.canonical_path`), not by
    changing this process's actual working directory.
    """
    if repo_path is not None:
        os.environ["PRISM_MCP_DEFAULT_REPO"] = os.path.abspath(repo_path)
    server.run(transport=transport)
