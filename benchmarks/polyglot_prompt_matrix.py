"""Polyglot-enterprise validation harness: Java and C# real-repository
benchmarking, the same shape as `multi_repo_eval.py`'s Python suite but for
the two compiled, enterprise-framework languages the parser/slicer/tagger
layers were just extended to support (`UniversalSlicer`, package/namespace-
aware symbol resolution, Spring/ASP.NET Core annotation grounding).

Targets:
  - Java:  spring-projects/spring-petclinic (Spring MVC controllers/services)
  - C#:    dotnet-architecture/eShopOnWeb (a compact, single-project ASP.NET
           Core reference app - eShop's own microservices split was judged
           too large/slow to shallow-clone and index reliably for a repeated
           benchmark run)

Every scenario target below was found by actually cloning and indexing each
repository and inspecting its real concrete graph and tag matrix (see the
comments next to each `QueryScenario`) - not guessed.

What's checked, per (repo, scenario, budget):
  1. every Java/C# code block SCE renders reparses cleanly via Tree-sitter
     (`benchmarks.validity.check_tree_sitter_syntax` - the same
     `root_node.has_error` check `tests/test_polyglot_enterprise.py`
     already uses, generalized to a real cloned repo's rendered Markdown);
  2. >=1 direct callee of the target is reachable within budget (the same
     coverage guarantee `multi_repo_eval.py` checks for Python);
  3. every symbol SCE's own Markdown output references resolves to a real
     node in the repository's `GlobalSymbolTable`/`G_C` - a package/
     namespace-aware hallucination guard, reusing `multi_repo_eval.py`'s
     own (already language-agnostic) checker;
  4. compression ratio and symbol-hallucination rate, reported per
     scenario/budget, not gated on a fixed threshold (a real third-party
     repo's achievable compression is a property of that repo's own call
     density, not a correctness property of SCE - matching
     `multi_repo_eval.py`'s own stance on its `expected_tag` diagnostic).

Usage:
    python -m benchmarks.polyglot_prompt_matrix --suite all --report benchmarks/polyglot_report.json
    python -m benchmarks.polyglot_prompt_matrix --repo spring-petclinic
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

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
from benchmarks.multi_repo_eval import (  # noqa: E402
    GraphMetrics,
    HallucinationCheckResult,
    _tag_observed_nearby,
    check_no_hallucinated_symbols,
    compute_graph_metrics,
)
from benchmarks.reporting import format_table, shorten  # noqa: E402
from benchmarks.run_benchmark import BenchmarkError, BenchmarkResult, run_single_benchmark_from_pipeline  # noqa: E402
from benchmarks.validity import CodeBlockValidity, check_tree_sitter_syntax  # noqa: E402

DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "clones"
DEFAULT_BUDGETS = (2000, 4000)
DEFAULT_REPORT_PATH = PROJECT_ROOT / "benchmarks" / "polyglot_report.json"

QUERY_TYPE_LABELS = {
    "A": "Root-Cause Analysis / Bug Trace",
    "B": "Cross-Cutting Architectural Invariant Audit",
    "C": "Feature Extension / Interface Conformance",
}


class PolyglotEvalError(Exception):
    """Raised when a repository or scenario can't be evaluated (e.g. a
    scenario's target symbol no longer exists because upstream restructured
    the code the scenario was written against)."""


@dataclasses.dataclass(frozen=True)
class RepoSpec:
    key: str
    url: str
    language_id: str  # LanguageID value; also the Tree-sitter fence-lang tag
    description: str


@dataclasses.dataclass(frozen=True)
class QueryScenario:
    scenario_id: str
    query_type: str  # "A" | "B" | "C"
    title: str
    description: str
    target: str
    expected_tag: str | None = None


REPOS: dict[str, RepoSpec] = {
    "spring-petclinic": RepoSpec(
        "spring-petclinic",
        "https://github.com/spring-projects/spring-petclinic.git",
        "java",
        "Spring MVC controllers/services/repositories, Spring Data JPA persistence",
    ),
    "eShopOnWeb": RepoSpec(
        "eShopOnWeb",
        "https://github.com/dotnet-architecture/eShopOnWeb.git",
        "csharp",
        "A compact, single-project ASP.NET Core MVC reference app (Clean Architecture layering)",
    ),
}

# Targets below were confirmed by actually cloning and indexing each
# repository (`build_pipeline`) and inspecting its real concrete graph and
# tag matrix - see benchmarks/README.md for the same methodology used to
# pick the Python multi_repo_eval.py targets.
SCENARIOS: dict[str, tuple[QueryScenario, ...]] = {
    "spring-petclinic": (
        QueryScenario(
            "bug_trace", "A",
            "Trace a duplicate-pet-name validation failure through pet creation",
            "A user reports a pet creation form silently rejecting valid names. Trace "
            "PetController.processCreationForm's validation chain (its own duplicate-name check, "
            "plus the framework calls it makes) to find every point that could reject the submission "
            "before a pet is actually saved.",
            "org.springframework.samples.petclinic.owner.PetController.processCreationForm",
        ),
        QueryScenario(
            "interface_conformance", "C",
            "Implement a new owner-search filter, following the existing pagination flow",
            "A new search filter (e.g. by city) must integrate with OwnerController's existing "
            "processFindForm -> findPaginatedForOwnersLastName -> addPaginationModel pipeline "
            "without breaking pagination for the existing last-name search.",
            "org.springframework.samples.petclinic.owner.OwnerController.processFindForm",
        ),
    ),
    "eShopOnWeb": (
        QueryScenario(
            "invariant_audit", "B",
            "Audit the authenticated-user endpoint for its auth invariant",
            "Audit exactly what UserController.GetCurrentUser enforces (or delegates to "
            "CreateUserInfo for) before returning a user's claims - to confirm the [Authorize] "
            "attribute alone is the enforcement point, not something the action body also re-checks.",
            "Microsoft.eShopWeb.Web.Controllers.UserController.GetCurrentUser",
            expected_tag="#auth_guard",
        ),
        QueryScenario(
            "bug_trace", "A",
            "Trace a password-change failure from the form post to the error surface",
            "A user reports their password-change form silently failing. Trace "
            "ManageController.ChangePassword's flow (the Identity call, its AddErrors fallback) to "
            "find every point that could fail before the user sees a confirmation.",
            "Microsoft.eShopWeb.Web.Controllers.ManageController.ChangePassword",
        ),
    ),
}


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
    syntax_blocks: list[CodeBlockValidity]
    checks: dict[str, bool]
    passed: bool


@dataclasses.dataclass
class RepoRunResult:
    repo: str
    url: str
    language_id: str
    description: str
    clone_path: str
    index_elapsed_seconds: float
    graph_metrics: GraphMetrics
    scenarios: list[ScenarioRunResult] = dataclasses.field(default_factory=list)


def run_scenario(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    repo_key: str,
    language_id: str,
    repo_path: str,
    scenario: QueryScenario,
    budget: int,
    k_hops: int,
) -> ScenarioRunResult:
    if scenario.target not in builder.symbol_table:
        raise PolyglotEvalError(
            f"scenario '{scenario.scenario_id}' target '{scenario.target}' was not found - "
            "the upstream repository may have restructured since this scenario was written"
        )

    try:
        benchmark = run_single_benchmark_from_pipeline(builder, tag_matrix, repo_path, scenario.target, budget, k_hops=k_hops)
    except BenchmarkError as exc:
        raise PolyglotEvalError(str(exc)) from exc

    # `run_single_benchmark_from_pipeline`'s own code_blocks_valid/_total is
    # Python-only (`check_python_syntax`, which skips every non-Python
    # fence) - a second, independent pack+render (the same pattern
    # multi_repo_eval.py already uses for its hallucination check) gets the
    # real Markdown text to run the *Tree-sitter*-based check against
    # instead, for this target language specifically.
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(scenario.target, builder, tag_matrix, distance_engine)
    markdown_text = render_markdown(pack_result, tag_matrix)
    hallucination = check_no_hallucinated_symbols(markdown_text, builder)
    syntax_blocks = check_tree_sitter_syntax(markdown_text, languages=frozenset({language_id}))
    invalid_blocks = [b for b in syntax_blocks if not b.valid]

    checks = {
        "syntax_valid": not invalid_blocks,
        "direct_callees_fully_covered": (
            benchmark.direct_callee_coverage.reached_nodes == benchmark.direct_callee_coverage.subgraph_node_count
        ),
        "no_hallucinated_symbols": hallucination.passed,
    }

    expected_tag_observed = (
        _tag_observed_nearby(builder, tag_matrix, scenario.target, scenario.expected_tag)
        if scenario.expected_tag
        else True
    )

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
        syntax_blocks=syntax_blocks,
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
        language_id=spec.language_id,
        description=spec.description,
        clone_path=str(repo_path),
        index_elapsed_seconds=round(index_elapsed, 4),
        graph_metrics=graph_metrics,
    )

    for scenario in SCENARIOS[repo_key]:
        for budget in budgets:
            print(f"  Running scenario '{scenario.scenario_id}' (budget={budget}) ...", file=sys.stderr)
            result.scenarios.append(
                run_scenario(builder, tag_matrix, repo_key, spec.language_id, str(repo_path), scenario, budget, k_hops)
            )

    return result


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #
def render_summary_table(results: list[RepoRunResult]) -> str:
    headers = ["Repo", "Scenario", "Type", "Budget", "Raw Tok", "SCE Tok", "Compression %", "Direct Callees", "Bad Syntax", "Hallucinations", "Result"]
    rows = []
    for repo_result in results:
        for s in repo_result.scenarios:
            dc = s.benchmark.direct_callee_coverage
            bad_syntax = sum(1 for b in s.syntax_blocks if not b.valid)
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
                    f"{bad_syntax}/{len(s.syntax_blocks)}",
                    str(len(s.hallucination.unknown_symbols)),
                    "PASS" if s.passed else "FAIL",
                ]
            )
    return format_table(headers, rows)


def render_repo_detail(result: RepoRunResult) -> str:
    gm = result.graph_metrics
    lines = [
        f"=== {result.repo} ({result.url}) [{result.language_id}] ===",
        f"  {result.description}",
        f"  Clone path:          {result.clone_path}",
        f"  Indexed in:          {result.index_elapsed_seconds}s",
        f"  Total symbols:       {gm.total_symbols}",
        f"  Total CALLS edges:   {gm.total_edges}",
        f"  Isolated nodes:      {gm.isolated_nodes} ({gm.isolated_node_ratio * 100:.1f}%)",
        f"  Max traversal depth: {gm.max_traversal_depth}",
    ]
    for s in result.scenarios:
        lines.append("")
        lines.append(f"  --- [{s.query_type}] {s.scenario_id} @ budget={s.budget}: {'PASS' if s.passed else 'FAIL'} ---")
        lines.append(f"  {QUERY_TYPE_LABELS[s.query_type]}: {s.title}")
        lines.append(f"  Target: {s.target}")
        for check, ok in s.checks.items():
            lines.append(f"    [{'x' if ok else ' '}] {check}")
        if s.expected_tag:
            observed = "observed" if s.expected_tag_observed else "NOT observed (naming-convention/tagger-coverage gap)"
            lines.append(f"    diagnostic: expected tag {s.expected_tag} {observed} within 2 hops of the target")
        if s.hallucination.unknown_symbols:
            lines.append(f"    UNKNOWN SYMBOLS: {', '.join(s.hallucination.unknown_symbols)}")
        for block in s.syntax_blocks:
            if not block.valid:
                lines.append(f"    SYNTAX ERROR: {block.symbol} ({block.resolution_label}): {block.error}")
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
        prog="python -m benchmarks.polyglot_prompt_matrix",
        description="Validate SCE's Java/C# support against real, cloned enterprise-framework repositories.",
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
        except (CloneError, PolyglotEvalError) as exc:
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
        print(f"\nWrote polyglot report to {args.report}")

    all_passed = all(s.passed for r in results for s in r.scenarios)
    return 0 if (not had_error and all_passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
