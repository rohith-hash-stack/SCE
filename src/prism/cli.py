"""Prism command-line interface: `prism index`, `prism query`."""
from __future__ import annotations

import json
import os

import click

from prism.analysis.hybrid_engine import DEFAULT_RUNTIME_BIAS, HybridFlowEngine
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.hierarchy import compute_hierarchical_profile
from prism.graph.metamodel import SemanticMetamodel
from prism.graph.symbol_table import GlobalSymbolTable
from prism.language_tiers import TIER_1_ONLY_LANGUAGES
from prism.parser.tree_sitter_loader import EXTENSION_LANGUAGE_MAP
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.runtime.index_cache import load_pipeline_from_cache, save_pipeline_to_cache
from prism.runtime.reconciler import (
    GraphReconciler,
    heal_and_apply_runtime_state,
    ingest_otel_file,
    load_runtime_state,
    load_trace_file,
    merge_result_into_state,
    new_trace_path,
    runtime_state_path,
    save_runtime_state,
    write_trace_file,
)
from prism.runtime.trace_ingester import merge_env_traces
from prism.runtime.trace_validator import StaleTraceError
from prism.runtime.tracer import run_traced_pytest
from prism.serializers.json_debug import render_json_debug
from prism.serializers.markdown import render_markdown
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.blueprint import mine_sibling_blueprint
from prism.slicer.knapsack import QUERY_TYPE_BUG_LOCALIZATION, QUERY_TYPE_GENERAL, ContextKnapsackPacker
from prism.tagger.engine import TaggingEngine

IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", "site-packages", ".prism_cache",
}


#: `--language-tier` values (Issues #1/#2/#3): "permissive" (default)
#: indexes every language Prism supports at whatever precision it
#: actually has; "tier1-only" restricts indexing to Tier 1 (Semantic &
#: Instance Precision) languages only - currently just Python - for a
#: caller that wants to guarantee every symbol in the resulting context
#: carries Rule A-D/instance-binding-grade resolution, with no
#: lower-precision (CST-only) language mixed in.
LANGUAGE_TIER_PERMISSIVE = "permissive"
LANGUAGE_TIER_TIER1_ONLY = "tier1-only"


def _resolve_legacy_go_bare_seed(builder: ConcreteGraphBuilder, symbol: str) -> str | None:
    """Item 16 (second post-implementation audit): backward-compatible
    seed resolution for a bare Go method name (`"JSON"`) against Issue
    B1's receiver-qualified registration (`"Context.JSON"`) - a qualified
    name always contains at least one `.`, so a bare `symbol` here can
    only ever have meant the old flat naming. Reuses
    `ConcreteGraphBuilder._go_method_registry` (Item 3), which already
    indexes every Go method by its own simple name for exactly this kind
    of "is there exactly one match" lookup. Returns `None` (never
    guesses) for a qualified-looking symbol, zero matches, or - unlike
    Item 3 Stage 2's own tentative-call fallback, which still binds a
    lower-confidence edge in that case - more than one match, since
    silently picking one of several same-named receiver methods for a
    query's own *seed* (not a mid-graph call edge) would be misleading
    about which symbol the returned context is actually for.
    """
    if "." in symbol:
        return None
    candidates = builder._go_method_registry().get(symbol)
    if candidates is None or len(candidates) != 1:
        return None
    return candidates[0]


def discover_files(repo_root: str, language_tier: str = LANGUAGE_TIER_PERMISSIVE) -> list[str]:
    allowed_extensions = (
        {ext for ext, lang in EXTENSION_LANGUAGE_MAP.items() if lang in TIER_1_ONLY_LANGUAGES}
        if language_tier == LANGUAGE_TIER_TIER1_ONLY
        else EXTENSION_LANGUAGE_MAP
    )
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            if any(filename.endswith(ext) for ext in allowed_extensions):
                files.append(os.path.join(dirpath, filename))
    return sorted(files)


def build_pipeline(
    repo_path: str, language_tier: str = LANGUAGE_TIER_PERMISSIVE, use_cache: bool = True
) -> tuple[ConcreteGraphBuilder, dict[str, set[str]]]:
    """Run Stages 1-3 (parse, link, tag) end to end for one repository.

    Item 12 (second post-implementation audit): when `use_cache` (default
    True), a whole-repository content-hash fingerprint match against
    `.prism/cache/index.db` (`prism.runtime.index_cache`) skips parsing
    and linking entirely and rehydrates the exact same `(builder,
    tag_matrix)` shape from the last cached run - see that module's own
    docstring for why this is a whole-repo fingerprint, not per-file
    incremental linking. Any cache miss, corruption, or `use_cache=False`
    falls straight through to the full Stage 1-3 run below, and a fresh
    result is always cached afterward for next time.
    """
    repo_root = os.path.abspath(repo_path)
    files = discover_files(repo_root, language_tier)

    if use_cache:
        cached = load_pipeline_from_cache(repo_root, files, language_tier)
        if cached is not None:
            return cached

    symbol_table = GlobalSymbolTable()
    builder = ConcreteGraphBuilder(repo_root, symbol_table)
    builder.pass1_collect_definitions(files)
    builder.pass2_resolve_calls(files)
    tag_matrix = TaggingEngine().tag_graph(builder)

    if use_cache:
        save_pipeline_to_cache(repo_root, files, language_tier, builder, tag_matrix)

    return builder, tag_matrix


@click.group()
@click.version_option(package_name="prism-context")
def main() -> None:
    """Prism: deterministic, offline repo context extraction."""


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--debug-json", is_flag=True, help="Emit the full dual-layer graph as JSON instead of a summary.")
@click.option(
    "--language-tier", "language_tier",
    type=click.Choice([LANGUAGE_TIER_PERMISSIVE, LANGUAGE_TIER_TIER1_ONLY]),
    default=LANGUAGE_TIER_PERMISSIVE, show_default=True,
    help="'permissive' indexes every supported language; 'tier1-only' restricts indexing to Tier 1 "
    "(Semantic & Instance Precision) languages only - see prism.language_tiers.",
)
def index(repo_path: str, debug_json: bool, language_tier: str) -> None:
    """Parse REPO_PATH and summarize the dual-layer semantic graph."""
    builder, tag_matrix = build_pipeline(repo_path, language_tier)

    if debug_json:
        click.echo(render_json_debug(builder, tag_matrix))
        return

    tag_counts: dict[str, int] = {}
    for tags in tag_matrix.values():
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    click.echo(f"Repository: {os.path.abspath(repo_path)}")
    click.echo(f"Symbols indexed: {len(builder.symbol_table)}")
    click.echo(f"CALLS edges: {builder.graph.number_of_edges()}")
    if builder._go_receiver_call_sites_total > 0:
        # Item 3: only shown when the repo actually has Go receiver-shaped
        # call sites to report on - silent (not a misleading "1.0") for
        # a repo with no Go at all.
        click.echo(f"Go call resolution ratio: {builder.go_call_resolution_ratio:.2%}")
    click.echo("Tag distribution:")
    if not tag_counts:
        click.echo("  (none)")
    for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        click.echo(f"  {tag}: {count}")
    if builder.index_errors:
        # Item 4 (second post-implementation audit): a file an error
        # boundary caught and skipped (INDEX_ERROR_SKIPPED) during
        # indexing - visible here rather than only in whatever `logging`
        # configuration (if any) the caller happens to have.
        click.echo(f"Skipped files (indexing errors): {len(builder.index_errors)}")
        for entry in builder.index_errors:
            click.echo(f"  {entry['file']} [{entry['stage']}] {entry['category']}: {entry['message']}")


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.argument("symbol")
@click.option("--budget", type=int, default=4000, show_default=True, help="Token budget for the packed context.")
@click.option(
    "--lambda-weight", "lambda_weight", type=float, default=0.7, show_default=True,
    help="Structural (G_C) vs. semantic (G_T) distance balance.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the packed context as JSON instead of Markdown.")
@click.option("-o", "--output", type=click.Path(dir_okay=False), help="Write the context package to a file instead of stdout.")
@click.option(
    "--trace-file", "trace_files", multiple=True, type=click.Path(exists=True, dir_okay=False),
    help="Path to a Prism JSON trace export or a raw OpenTelemetry JSON export (may be given more than once, "
    "one per environment, to merge several - see prism.runtime.trace_ingester).",
)
@click.option(
    "--runtime-bias", "runtime_bias", type=float, default=DEFAULT_RUNTIME_BIAS, show_default=True,
    help="Blend of runtime execution evidence into edge weighting, in [0.0, 1.0]. 0.0 forces purely static "
    "traversal while retaining contract/telemetry rendering; only takes effect when --trace-file is given.",
)
@click.option(
    "--strict-trace-validation", "strict_trace_validation", is_flag=True,
    help="Reject (raise) instead of warn-and-skip when an ingested trace's git commit or file SHA-256 "
    "fingerprints don't match the current repository state.",
)
@click.option(
    "--query-type", "query_type", type=click.Choice([QUERY_TYPE_GENERAL, QUERY_TYPE_BUG_LOCALIZATION]),
    default=QUERY_TYPE_GENERAL, show_default=True,
    help="Query intent hint. 'bug_localization' documents/reinforces Fallibility-Based Knapsack Pruning "
    "(infallible-leaf dependencies are always pruned to a compact signature regardless of this flag, "
    "since packing is always budget-constrained - see prism.slicer.knapsack).",
)
@click.option(
    "--language-tier", "language_tier",
    type=click.Choice([LANGUAGE_TIER_PERMISSIVE, LANGUAGE_TIER_TIER1_ONLY]),
    default=LANGUAGE_TIER_PERMISSIVE, show_default=True,
    help="'permissive' indexes every supported language; 'tier1-only' restricts indexing to Tier 1 "
    "(Semantic & Instance Precision) languages only - see prism.language_tiers.",
)
def query(
    repo_path: str, symbol: str, budget: int, lambda_weight: float, as_json: bool, output: str | None,
    trace_files: tuple[str, ...], runtime_bias: float, strict_trace_validation: bool, query_type: str,
    language_tier: str,
) -> None:
    """Extract a variable-resolution context package for SYMBOL (a fully
    qualified name, e.g. `src.controllers.checkout.process_checkout`)."""
    builder, tag_matrix = build_pipeline(repo_path, language_tier)

    if symbol not in builder.symbol_table:
        # Item 16 (second post-implementation audit) - Benchmark
        # Calibration Baseline Break: Issue B1 changed Go method
        # registration from a bare simple name (`JSON`) to receiver-
        # qualified (`Context.JSON`) - a real, deliberate, necessary fix
        # (see CHANGELOG.md's own "Benchmark Calibration Baseline Break"
        # entry), but it silently breaks any caller (a saved query, a
        # script, a benchmark task definition written against the old
        # naming) still passing the old bare name. A bare `symbol` (no
        # `.` in it - a qualified name always has at least one) that
        # matches exactly one Go receiver method's own simple name
        # resolves to it, with a visible deprecation warning, rather
        # than failing outright.
        resolved = _resolve_legacy_go_bare_seed(builder, symbol)
        if resolved is not None:
            click.echo(
                f"warning: seed '{symbol}' resolved to receiver method '{resolved}'. "
                "Update seed to fully-qualified name.",
                err=True,
            )
            symbol = resolved
        else:
            click.echo(f"error: symbol '{symbol}' not found in {repo_path}", err=True)
            click.echo("hint: run `prism index REPO_PATH --debug-json` to list known symbols.", err=True)
            raise SystemExit(1)
    if not 0.0 <= runtime_bias <= 1.0:
        click.echo(f"error: --runtime-bias must be between 0.0 and 1.0, got {runtime_bias}", err=True)
        raise SystemExit(1)

    repo_root = os.path.abspath(repo_path)
    contracts = compute_or_load_contracts(builder, repo_root)

    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    # Reserve room for the hierarchical-intent-profile + idiomatic-
    # blueprint sections rendered around the packed items (see
    # ContextKnapsackPacker.DEFAULT_RESERVED_OVERHEAD_TOKENS) - both are
    # always included in the Markdown path below, so the *rendered
    # document* actually respects `--budget`, not just the packed-items
    # portion of it.
    packer = ContextKnapsackPacker(
        token_budget=budget, reserved_overhead_tokens=ContextKnapsackPacker.DEFAULT_RESERVED_OVERHEAD_TOKENS
    )
    result = packer.pack(symbol, builder, tag_matrix, distance_engine, contracts=contracts, query_type=query_type)

    runtime_overlay = None
    hybrid_flow_result = None
    if trace_files:
        try:
            runtime_overlay = merge_env_traces(list(trace_files), repo_root, strict=strict_trace_validation)
        except StaleTraceError as exc:
            click.echo(f"error: {exc}", err=True)
            raise SystemExit(1) from exc
        for rejection in runtime_overlay.rejected:
            click.echo(f"warning: {rejection}", err=True)
        all_counts: dict[tuple[str, str], int] = {}
        all_errors: dict[tuple[str, str], int] = {}
        all_breakdown: dict[tuple[str, str], dict[str, int]] = {}
        for env, counts in runtime_overlay.per_env_edge_counts.items():
            for edge, count in counts.items():
                all_counts[edge] = all_counts.get(edge, 0) + count
                all_breakdown.setdefault(edge, {})[env] = count
        for env, errors in runtime_overlay.per_env_edge_errors.items():
            for edge, count in errors.items():
                all_errors[edge] = all_errors.get(edge, 0) + count
        builder.apply_runtime_overlay(all_counts, all_errors, all_breakdown)
        if runtime_overlay.per_env_run_counts:
            hybrid_flow_result = HybridFlowEngine(runtime_overlay, alpha=runtime_bias).analyze_from_seed(
                builder, contracts, symbol
            )

    if as_json:
        payload = {
            "seed": result.seed,
            "budget": result.budget,
            "allocated_tokens": result.allocated_tokens,
            "preserved_semantics": result.preserved_semantics,
            "budget_exceeded": result.budget_exceeded,
            "seed_cost": result.seed_cost,
            "items": [
                {"symbol": i.symbol, "resolution": i.resolution, "language": i.language_id, "content": i.content}
                for i in result.items
            ],
        }
        text = json.dumps(payload, indent=2)
    else:
        hierarchy = compute_hierarchical_profile(builder, contracts, repo_root)
        blueprint = mine_sibling_blueprint(builder, symbol)
        text = render_markdown(
            result, tag_matrix, contracts=contracts, graph=builder.graph, hierarchy=hierarchy,
            runtime_overlay=runtime_overlay, hybrid_flow_result=hybrid_flow_result, blueprint=blueprint,
        )

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Wrote context package to {output}")
    else:
        click.echo(text)


@main.command(
    context_settings={"ignore_unknown_options": True},
    help=(
        "Record a runtime trace and reconcile it into the static graph.\n\n"
        "\b\n"
        "  prism trace --repo . -- pytest tests/test_orders.py\n"
        "  prism trace --repo . --ingest-otel ./traces/otel_export.json"
    ),
)
@click.option("--repo", "repo_path", default=".", type=click.Path(exists=True, file_okay=False), help="Repository root (default: current directory).")
@click.option(
    "--ingest-otel", "otel_path", type=click.Path(exists=True, dir_okay=False), default=None,
    help="Ingest an OpenTelemetry JSON export instead of running a traced command.",
)
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def trace(repo_path: str, otel_path: str | None, command: tuple[str, ...]) -> None:
    repo_root = os.path.abspath(repo_path)

    if otel_path:
        events = ingest_otel_file(otel_path)
        trace_path = new_trace_path(repo_root)
        write_trace_file(trace_path, events)
        click.echo(f"Ingested {len(events)} event(s) from {otel_path}")
    else:
        if not command:
            click.echo("error: no command given - expected e.g. `prism trace --repo . -- pytest tests/`", err=True)
            raise SystemExit(1)
        cmd = list(command)
        if cmd[0] != "pytest":
            click.echo(f"error: only a `pytest` command is currently supported for live tracing, got {cmd[0]!r}", err=True)
            raise SystemExit(1)
        trace_path = new_trace_path(repo_root)
        exit_code = run_traced_pytest(repo_root, trace_path, cmd[1:])
        events = load_trace_file(trace_path) if trace_path.exists() else []
        if exit_code != 0:
            click.echo(f"warning: traced command exited with status {exit_code} - the trace may be incomplete", err=True)

    builder, tag_matrix = build_pipeline(repo_root)
    result = GraphReconciler(builder, tag_matrix).reconcile(events)

    state = load_runtime_state(repo_root)
    state = merge_result_into_state(state, result, trace_path.name)
    save_runtime_state(repo_root, state)

    click.echo(f"Trace written to {trace_path}")
    click.echo(f"Runtime events processed: {len(events)}")
    click.echo(f"  Confirmed edges (this run):          {len(result.confirmed_edges)}")
    click.echo(f"  Newly discovered edges (this run):   {len(result.discovered_edges)}")
    click.echo(f"  Sink-tagged symbols (this run):      {len(result.sink_symbols)}")
    click.echo(f"  Unresolved events (this run):        {len(result.unresolved_events)}")
    if result.orphan_reasons:
        for reason, count in sorted(result.orphan_reasons.items()):
            click.echo(f"    {reason}: {count}")
    click.echo(f"  RuntimeTrust (this run):             {result.trust_score:.0%}")
    if result.trust_warning:
        click.echo(f"  warning: {result.trust_warning}", err=True)
    if result.dynamic_tagged_symbols:
        click.echo(f"  Symbols newly tagged #dynamic:       {len(result.dynamic_tagged_symbols)}")
    if result.non_observed_edges:
        click.echo(f"  Static edges flagged unobserved (this run): {len(result.non_observed_edges)}")
    if result.fuzzy_matched_edges:
        click.echo(f"  Fuzzy-anchor-matched edges (this run):      {len(result.fuzzy_matched_edges)}")
        click.echo(f"  Orphan resolution ratio (this run):         {result.orphan_resolution_ratio:.0%}")
    click.echo(f"Runtime state saved to {runtime_state_path(repo_root)}")


@main.command()
@click.option("--repo", "repo_path", default=".", type=click.Path(exists=True, file_okay=False), help="Repository root (default: current directory).")
def status(repo_path: str) -> None:
    """Show combined static + runtime-confirmed graph status."""
    repo_root = os.path.abspath(repo_path)
    builder, tag_matrix = build_pipeline(repo_root)
    state = load_runtime_state(repo_root)

    if state.get("trace_files"):
        reconciliation = heal_and_apply_runtime_state(builder, tag_matrix, state, repo_root)
        if reconciliation.renamed:
            click.echo(f"Self-healed {len(reconciliation.renamed)} renamed symbol(s) in runtime state:")
            for old_name, new_name in sorted(reconciliation.renamed.items()):
                click.echo(f"  {old_name} -> {new_name}")
        if reconciliation.ambiguous:
            click.echo(f"{len(reconciliation.ambiguous)} vanished symbol(s) have ambiguous rename candidates (left unresolved):")
            for old_name, candidates in sorted(reconciliation.ambiguous.items()):
                click.echo(f"  {old_name} -> {candidates}")

    click.echo(f"Repository: {repo_root}")
    click.echo(f"Static symbols:               {len(builder.symbol_table)}")
    click.echo(f"Static edges:                 {builder.graph.number_of_edges()}")
    click.echo(f"Confirmed runtime edges:      {len(state['confirmed_edges'])}")
    click.echo(f"Dynamically discovered edges: {len(state['discovered_edges'])}")
    click.echo(f"Sink-tagged symbols (runtime):{len(state['sink_symbols']):>2}")
    click.echo(f"Unresolved runtime events:    {state['unresolved_event_count']}")
    if state.get("orphan_reasons"):
        for reason, count in sorted(state["orphan_reasons"].items()):
            click.echo(f"  {reason}: {count}")
    click.echo(f"RuntimeTrust (latest run):    {state.get('trust_score', 1.0):.0%}")
    if state.get("unobserved_edges"):
        click.echo(f"Edges flagged unobserved:     {len(state['unobserved_edges'])}")
    if state.get("fuzzy_matched_edges"):
        click.echo(f"Fuzzy-anchor-matched edges:   {len(state['fuzzy_matched_edges'])}")
    click.echo(f"Trace files ingested:         {len(state['trace_files'])}")
    if state["last_updated"]:
        click.echo(f"Last updated:                 {state['last_updated']}")
    else:
        click.echo("No runtime trace has been recorded yet - run `prism trace --repo . -- pytest ...` first.")


@main.command(name="mcp")
@click.option(
    "--transport", type=click.Choice(["stdio", "sse", "streamable-http"]), default="stdio", show_default=True,
    help="MCP transport protocol. 'stdio' is what Claude Desktop/Cursor expect.",
)
@click.option(
    "--repo", "repo_path", type=click.Path(exists=True, file_okay=False), default=None,
    help="Default repository root for a tool call that omits its own repo_path (defaults to this process's cwd).",
)
def mcp_command(transport: str, repo_path: str | None) -> None:
    """Run Prism as a Model Context Protocol server (see docs/mcp_setup.md)."""
    try:
        from prism.mcp.server import run_server
    except ImportError as exc:
        # `mcp[cli]` is a core dependency (pyproject.toml) - a normal `pip
        # install prism-context` or `uvx --from ...` already
        # pulls it in. This only fires for an environment deliberately
        # installed without it (e.g. `pip install --no-deps`), so the fix
        # is a plain reinstall, not an extras flag.
        click.echo(
            "error: the 'mcp' package is required for this command but isn't importable - "
            "reinstall with `pip install prism-context` (or `pip install 'mcp[cli]>=2.0,<3.0'` directly)",
            err=True,
        )
        raise SystemExit(1) from exc
    run_server(transport=transport, repo_path=repo_path)


if __name__ == "__main__":
    main()
