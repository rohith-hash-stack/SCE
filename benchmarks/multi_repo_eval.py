"""Multi-repository, multi-query validation harness.

Validates SCE against three real, architecturally distinct Python
codebases - not just hand-built fixtures - across three realistic
developer query types, before any client wrapper (e.g. an MCP server) gets
built on top of it:

  - encode/httpx        - transport layers, async/sync duality, I/O-heavy
  - pallets/flask        - WSGI architecture, request contexts, routing
  - marshmallow-code/marshmallow - data schemas, deep class inheritance

Per repository, per query scenario (Root-Cause Analysis, Architectural
Invariant Audit, Feature Extension), per token budget, this asserts:

  1. every Python code block SCE renders parses cleanly (`ast.parse`);
  2. reports graph connectivity/density (symbols, edges, isolated-node
     ratio, approximate traversal depth) - descriptive, not a pass/fail
     gate, since that's a property of the target repo, not of SCE;
  3. >=50% token compression vs. the whole-file-dump baseline, with 100%
     of direct callees still reachable in the packed context;
  4. every symbol SCE's own output references resolves to a real node in
     the repository's `GlobalSymbolTable` (or concrete graph, for a
     legitimately external/unresolved call) - a static regression guard
     against the renderer ever fabricating a name, since nothing in this
     pipeline is LLM-generated.

Usage:
    python -m benchmarks.multi_repo_eval --suite all --report benchmarks/multi_repo_report.json
    python -m benchmarks.multi_repo_eval --repo httpx
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import time
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_sce_importable() -> None:
    try:
        import sce  # noqa: F401
    except ImportError:
        src_path = str(PROJECT_ROOT / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


_ensure_sce_importable()

from sce.cli import build_pipeline  # noqa: E402
from sce.graph.concrete_builder import ConcreteGraphBuilder  # noqa: E402
from sce.graph.metamodel import SemanticMetamodel  # noqa: E402
from sce.serializers.markdown import render_markdown  # noqa: E402
from sce.slicer.distance import DistanceConfig, DistanceEngine  # noqa: E402
from sce.slicer.knapsack import ContextKnapsackPacker  # noqa: E402

from benchmarks.clone_eval import CloneError, clone_repo  # noqa: E402
from benchmarks.reporting import format_table, shorten  # noqa: E402
from benchmarks.run_benchmark import BenchmarkError, BenchmarkResult, run_single_benchmark_from_pipeline  # noqa: E402

DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "clones"
DEFAULT_BUDGETS = (2000, 4000)
DEFAULT_REPORT_PATH = PROJECT_ROOT / "benchmarks" / "multi_repo_report.json"

QUERY_TYPE_LABELS = {
    "A": "Root-Cause Analysis / Bug Trace",
    "B": "Cross-Cutting Architectural Invariant Audit",
    "C": "Feature Extension / Interface Conformance",
}


class MultiRepoEvalError(Exception):
    """Raised when a repository or scenario can't be evaluated (e.g. a
    scenario's target symbol no longer exists because upstream restructured
    the code the scenario was written against)."""


@dataclasses.dataclass(frozen=True)
class RepoSpec:
    key: str
    url: str
    description: str


@dataclasses.dataclass(frozen=True)
class QueryScenario:
    scenario_id: str
    query_type: str  # "A" | "B" | "C"
    title: str
    description: str
    target: str
    # A tag we'd expect to see fire on/near the target, purely for
    # diagnostic reporting - NOT a pass/fail gate. Whether a specific
    # heuristic tag fires is a property of the target codebase's naming
    # conventions (see benchmarks/README.md for a concrete example of this
    # not firing on httpx), not a correctness property of SCE itself.
    expected_tag: str | None = None


REPOS: dict[str, RepoSpec] = {
    "httpx": RepoSpec(
        "httpx",
        "https://github.com/encode/httpx.git",
        "Transport layers, async/sync duality, heavy external I/O",
    ),
    "flask": RepoSpec(
        "flask",
        "https://github.com/pallets/flask.git",
        "WSGI architecture, request contexts, routing decorators",
    ),
    "marshmallow": RepoSpec(
        "marshmallow",
        "https://github.com/marshmallow-code/marshmallow.git",
        "Data schemas, deep class inheritance, field mutations",
    ),
}

# Targets below were found by actually cloning and indexing each repository
# and inspecting its real concrete graph (see benchmarks/README.md) - not
# guessed. Module paths carry a `src.` prefix for flask/marshmallow because
# both repos use a src/ layout; httpx does not.
SCENARIOS: dict[str, tuple[QueryScenario, ...]] = {
    "httpx": (
        QueryScenario(
            "bug_trace", "A",
            "Trace a hung/failed request from the public API down to the transport call",
            "A user reports requests occasionally hanging past the configured timeout. Trace "
            "Client.send's dispatch chain (auth handling -> redirect handling -> the single-request "
            "send) to find where a timeout or retry decision would actually need to be made.",
            "httpx._client.Client.send",
        ),
        QueryScenario(
            "invariant_audit", "B",
            "Audit the default transport's network I/O boundary",
            "Identify the exact point where HTTPTransport hands a request to the underlying "
            "connection pool, to audit what guarantees (timeouts, retries, connection limits) are "
            "or are not enforced immediately before the real network I/O happens.",
            "httpx._transports.default.HTTPTransport.handle_request",
            expected_tag="#external_io",
        ),
        QueryScenario(
            "interface_conformance", "C",
            "Implement a new async Transport, following ASGITransport as the reference conformance example",
            "A new custom AsyncBaseTransport subclass (e.g. for a alternative backend or protocol) must "
            "implement handle_async_request with the exact signature and return type the base class "
            "declares. ASGITransport is the existing implementation to follow as a template.",
            "httpx._transports.asgi.ASGITransport.handle_async_request",
        ),
    ),
    "flask": (
        QueryScenario(
            "bug_trace", "A",
            "Trace a 500 error from WSGI entry to the failing hook",
            "An endpoint intermittently 500s. Trace full_dispatch_request's fan-out (preprocessing "
            "hooks, the view dispatch itself, user-exception handling, response finalization) to find "
            "every point that could raise before a response is actually produced.",
            "src.flask.app.Flask.full_dispatch_request",
        ),
        QueryScenario(
            "invariant_audit", "B",
            "Audit route registration for a missing auth invariant",
            "Every endpoint gets registered through add_url_rule. Audit whether route registration "
            "itself enforces (or even can enforce) an authentication invariant, versus leaving it "
            "entirely to application-level view code.",
            "src.flask.sansio.scaffold.Scaffold.add_url_rule",
            expected_tag="#auth_guard",
        ),
        QueryScenario(
            "interface_conformance", "C",
            "Implement a new pluggable View without breaking dispatch",
            "A new class-based View (or MethodView) must implement dispatch_request with a signature "
            "the base View.dispatch_request contract, and Flask's own View.as_view machinery, expect - "
            "get it wrong and routing breaks silently at request time, not at import time.",
            "src.flask.views.View.dispatch_request",
        ),
    ),
    "marshmallow": (
        QueryScenario(
            "bug_trace", "A",
            "Trace a validation error from Schema.load to the failing field",
            "A client reports a confusing validation error on Schema.load. Trace the deserialization "
            "pipeline (field validators, schema validators, error accumulation) to find exactly where "
            "a ValidationError gets raised and collected.",
            "src.marshmallow.schema.Schema.load",
        ),
        QueryScenario(
            "invariant_audit", "B",
            "Audit state mutation across the Field class hierarchy",
            "Dozens of Field subclasses (String, Number, Nested, List, ...) inherit _bind_to_schema. "
            "Audit exactly what instance state it mutates, since every subclass depends on that "
            "contract holding regardless of how deep the inheritance chain is.",
            "src.marshmallow.fields.Field._bind_to_schema",
            expected_tag="#state_mutation",
        ),
        QueryScenario(
            "interface_conformance", "C",
            "Implement a new custom Field without breaking (de)serialization",
            "A new custom Field subclass must implement _serialize/_deserialize with the exact "
            "signature the base Field class and every existing subclass (String, Number, Nested, ...) "
            "already conform to.",
            "src.marshmallow.fields.Field._deserialize",
        ),
    ),
}


# --------------------------------------------------------------------- #
# Graph connectivity/density metrics (section 3.2 - descriptive, reported
# for every repo, not gated on).
# --------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class GraphMetrics:
    total_symbols: int
    total_edges: int
    isolated_nodes: int
    isolated_node_ratio: float
    max_traversal_depth: int


def compute_graph_metrics(builder: ConcreteGraphBuilder) -> GraphMetrics:
    g = builder.graph
    total_nodes = g.number_of_nodes()
    total_edges = g.number_of_edges()
    isolated = sum(1 for n in g.nodes if g.degree(n) == 0)
    isolated_ratio = round(isolated / total_nodes, 4) if total_nodes else 0.0

    max_depth = 0
    if total_nodes:
        undirected = g.to_undirected()
        largest_component = max(nx.connected_components(undirected), key=len)
        subgraph = undirected.subgraph(largest_component)
        # Approximate the component's diameter via double-BFS: pick an
        # arbitrary start, find the farthest node from it, then find the
        # farthest node from *that* - a standard O(V+E) diameter estimate,
        # exact on trees and a safe lower bound in general (a real
        # all-pairs computation is unnecessary here and far more costly on
        # a graph with thousands of nodes).
        start = next(iter(largest_component))
        first_pass = nx.single_source_shortest_path_length(subgraph, start)
        far_node = max(first_pass, key=first_pass.get)
        second_pass = nx.single_source_shortest_path_length(subgraph, far_node)
        max_depth = max(second_pass.values()) if second_pass else 0

    return GraphMetrics(
        total_symbols=len(builder.symbol_table),
        total_edges=total_edges,
        isolated_nodes=isolated,
        isolated_node_ratio=isolated_ratio,
        max_traversal_depth=max_depth,
    )


# --------------------------------------------------------------------- #
# Symbol hallucination check (section 3.4)
# --------------------------------------------------------------------- #
_HEADING_RE = re.compile(r"^###\s+(?:\[TARGET\]\s+)?(?P<symbol>\S+)\s+\(", re.MULTILINE)
_CALLS_LINE_RE = re.compile(r"^#\s*Calls:\s*(?P<names>.+)$", re.MULTILINE)
_ARCH_PATH_RE = re.compile(r"(?:calls|requires)\s+──►\s+(?:\[[^\]]*\]\s+)?(?P<symbol>\S+)")


@dataclasses.dataclass(frozen=True)
class HallucinationCheckResult:
    referenced_symbols: tuple[str, ...]
    unknown_symbols: tuple[str, ...]
    passed: bool


def check_no_hallucinated_symbols(markdown_text: str, builder: ConcreteGraphBuilder) -> HallucinationCheckResult:
    """Every dotted symbol SCE's own Markdown references - in a heading, a
    "Calls:" contract line, or an architectural-path arrow - must resolve
    to a real node: either a fully indexed symbol in `GlobalSymbolTable`,
    or a real (if externally-unresolved) node the linker actually recorded
    in `G_C` (e.g. `requests.post`). Nothing in this pipeline is
    LLM-generated, so a name that is neither is a genuine renderer bug, not
    a "hallucination" in the LLM sense - this is a static regression guard
    against exactly that.
    """
    referenced: set[str] = set()
    for match in _HEADING_RE.finditer(markdown_text):
        referenced.add(match.group("symbol"))
    for match in _CALLS_LINE_RE.finditer(markdown_text):
        for name in match.group("names").split(","):
            name = name.strip()
            if name:
                referenced.add(name)
    for match in _ARCH_PATH_RE.finditer(markdown_text):
        referenced.add(match.group("symbol"))

    unknown = tuple(
        sorted(symbol for symbol in referenced if symbol not in builder.symbol_table and symbol not in builder.graph)
    )
    return HallucinationCheckResult(referenced_symbols=tuple(sorted(referenced)), unknown_symbols=unknown, passed=not unknown)


# --------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------- #
@dataclasses.dataclass
class ScenarioRunResult:
    repo: str
    scenario_id: str
    query_type: str
    title: str
    target: str
    budget: int
    expected_tag: str | None
    expected_tag_observed: bool
    benchmark: BenchmarkResult
    hallucination: HallucinationCheckResult
    checks: dict[str, bool]
    passed: bool


@dataclasses.dataclass
class RepoRunResult:
    repo: str
    url: str
    description: str
    clone_path: str
    index_elapsed_seconds: float
    graph_metrics: GraphMetrics
    scenarios: list[ScenarioRunResult] = dataclasses.field(default_factory=list)


def _tag_observed_nearby(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], target: str, tag: str, k: int = 2) -> bool:
    """Diagnostic only: does `tag` fire on the target itself or anything
    within `k` call-graph hops of it? Used to honestly report tagger
    coverage gaps (naming-convention mismatches on real third-party code),
    never to gate pass/fail.
    """
    if tag in tag_matrix.get(target, set()):
        return True
    if target not in builder.graph:
        return False
    lengths = nx.single_source_shortest_path_length(builder.graph, target, cutoff=k)
    return any(tag in tag_matrix.get(node, set()) for node in lengths)


def run_scenario(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    repo_key: str,
    repo_path: str,
    scenario: QueryScenario,
    budget: int,
    k_hops: int,
) -> ScenarioRunResult:
    if scenario.target not in builder.symbol_table:
        raise MultiRepoEvalError(
            f"scenario '{scenario.scenario_id}' target '{scenario.target}' was not found - "
            "the upstream repository may have restructured since this scenario was written"
        )

    try:
        benchmark = run_single_benchmark_from_pipeline(builder, tag_matrix, repo_path, scenario.target, budget, k_hops=k_hops)
    except BenchmarkError as exc:
        raise MultiRepoEvalError(str(exc)) from exc

    # A second, independent pack+render purely to get the actual Markdown
    # text for the hallucination check - run_single_benchmark_from_pipeline
    # intentionally only returns aggregate metrics, not the raw document.
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(scenario.target, builder, tag_matrix, distance_engine)
    markdown_text = render_markdown(pack_result, tag_matrix)
    hallucination = check_no_hallucinated_symbols(markdown_text, builder)

    expected_tag_observed = (
        _tag_observed_nearby(builder, tag_matrix, scenario.target, scenario.expected_tag)
        if scenario.expected_tag
        else True
    )

    checks = {
        "syntax_valid": benchmark.code_blocks_valid == benchmark.code_blocks_total,
        "compression_ge_50pct": benchmark.compression_pct >= 50.0,
        "direct_callees_fully_covered": (
            benchmark.direct_callee_coverage.reached_nodes == benchmark.direct_callee_coverage.subgraph_node_count
        ),
        "no_hallucinated_symbols": hallucination.passed,
    }

    return ScenarioRunResult(
        repo=repo_key,
        scenario_id=scenario.scenario_id,
        query_type=scenario.query_type,
        title=scenario.title,
        target=scenario.target,
        budget=budget,
        expected_tag=scenario.expected_tag,
        expected_tag_observed=expected_tag_observed,
        benchmark=benchmark,
        hallucination=hallucination,
        checks=checks,
        passed=all(checks.values()),
    )


def run_repo(
    repo_key: str,
    budgets: list[int],
    cache_dir: Path,
    force_clone: bool,
    k_hops: int,
) -> RepoRunResult:
    spec = REPOS[repo_key]
    repo_path = clone_repo(spec.url, cache_dir=cache_dir, force=force_clone)

    print(f"Indexing {repo_key} ({repo_path}) ...", file=sys.stderr)
    start = time.perf_counter()
    builder, tag_matrix = build_pipeline(str(repo_path))
    index_elapsed = time.perf_counter() - start
    graph_metrics = compute_graph_metrics(builder)
    print(
        f"  {graph_metrics.total_symbols} symbols, {graph_metrics.total_edges} CALLS edges, "
        f"indexed in {index_elapsed:.2f}s (no crash)",
        file=sys.stderr,
    )

    result = RepoRunResult(
        repo=repo_key,
        url=spec.url,
        description=spec.description,
        clone_path=str(repo_path),
        index_elapsed_seconds=round(index_elapsed, 4),
        graph_metrics=graph_metrics,
    )

    for scenario in SCENARIOS[repo_key]:
        for budget in budgets:
            print(f"  Running scenario '{scenario.scenario_id}' (budget={budget}) ...", file=sys.stderr)
            result.scenarios.append(run_scenario(builder, tag_matrix, repo_key, str(repo_path), scenario, budget, k_hops))

    return result


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #
def render_summary_table(results: list[RepoRunResult]) -> str:
    headers = ["Repo", "Scenario", "Type", "Budget", "Raw Tok", "SCE Tok", "Compression %", "Direct Callees", "Hallucinations", "Result"]
    rows = []
    for repo_result in results:
        for s in repo_result.scenarios:
            dc = s.benchmark.direct_callee_coverage
            rows.append(
                [
                    repo_result.repo,
                    s.scenario_id,
                    s.query_type,
                    str(s.budget),
                    str(s.benchmark.raw_tokens),
                    str(s.benchmark.sce_tokens),
                    f"{s.benchmark.compression_pct:.1f}%",
                    f"{dc.reached_nodes}/{dc.subgraph_node_count}",
                    str(len(s.hallucination.unknown_symbols)),
                    "PASS" if s.passed else "FAIL",
                ]
            )
    return format_table(headers, rows)


def render_repo_detail(result: RepoRunResult) -> str:
    gm = result.graph_metrics
    lines = [
        f"=== {result.repo} ({result.url}) ===",
        f"  {result.description}",
        f"  Clone path:          {result.clone_path}",
        f"  Indexed in:          {result.index_elapsed_seconds}s",
        f"  Total symbols:       {gm.total_symbols}",
        f"  Total CALLS edges:   {gm.total_edges}",
        f"  Isolated nodes:      {gm.isolated_nodes} ({gm.isolated_node_ratio * 100:.1f}%)",
        f"  Max traversal depth: {gm.max_traversal_depth} (approx. diameter of the largest weakly-connected component)",
    ]
    for s in result.scenarios:
        lines.append("")
        lines.append(f"  --- [{s.query_type}] {s.scenario_id} @ budget={s.budget}: {'PASS' if s.passed else 'FAIL'} ---")
        lines.append(f"  {QUERY_TYPE_LABELS[s.query_type]}: {s.title}")
        lines.append(f"  Target: {s.target}")
        for check, ok in s.checks.items():
            lines.append(f"    [{'x' if ok else ' '}] {check}")
        if s.expected_tag:
            observed = "observed" if s.expected_tag_observed else "NOT observed (naming-convention/tagger-coverage gap - see benchmarks/README.md)"
            lines.append(f"    diagnostic: expected tag {s.expected_tag} {observed} within 2 hops of the target")
        if s.hallucination.unknown_symbols:
            lines.append(f"    UNKNOWN SYMBOLS: {', '.join(s.hallucination.unknown_symbols)}")
    return "\n".join(lines)


def _to_json_payload(results: list[RepoRunResult]) -> dict:
    return {"repo_count": len(results), "repos": [dataclasses.asdict(r) for r in results]}


def write_report(results: list[RepoRunResult], path: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(_to_json_payload(results), indent=2))


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.multi_repo_eval",
        description="Validate SCE against multiple real, architecturally distinct repositories and query scenarios.",
    )
    parser.add_argument("--suite", choices=["all"], default=None, help="Run every repository in the built-in suite.")
    parser.add_argument("--repo", choices=sorted(REPOS), default=None, help="Run a single repository by key.")
    parser.add_argument("--budget", type=int, nargs="+", default=list(DEFAULT_BUDGETS), help=f"Token budgets to evaluate (default: {list(DEFAULT_BUDGETS)}).")
    parser.add_argument("--k-hops", type=int, default=3, dest="k_hops", help="Ground-truth subgraph hop radius (default: 3).")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help=f"Clone cache directory (default: {DEFAULT_CACHE_DIR}).")
    parser.add_argument("--force-clone", action="store_true", help="Re-clone even if a cached copy already exists.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help=f"Write full results as JSON here (default: {DEFAULT_REPORT_PATH}). Pass '' to skip.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.suite and not args.repo:
        parser.error("pass --suite all or --repo NAME")

    repo_keys = list(REPOS) if args.suite else [args.repo]

    results: list[RepoRunResult] = []
    had_error = False
    for key in repo_keys:
        try:
            results.append(run_repo(key, args.budget, Path(args.cache_dir), args.force_clone, args.k_hops))
        except (CloneError, MultiRepoEvalError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            had_error = True

    if not results:
        return 1

    print(render_summary_table(results))
    for repo_result in results:
        print()
        print(render_repo_detail(repo_result))

    if args.report:
        write_report(results, args.report)
        print(f"\nWrote multi-repo report to {args.report}")

    all_passed = all(s.passed for r in results for s in r.scenarios)
    return 0 if (not had_error and all_passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
