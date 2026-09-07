"""SCE evaluation harness: quantify variable-resolution context packing
against a naive whole-file-dump baseline.

Usage:
    python -m benchmarks.run_benchmark --target "src.controllers.checkout.CheckoutController.process_checkout" --budget 2000 4000
    python -m benchmarks.run_benchmark --suite
    python -m benchmarks.run_benchmark --repo path/to/repo --target some.qualified.symbol --budget 4000 --output benchmarks/results.json

See the module docstring in `benchmarks/__init__.py` for the three
evaluation dimensions this measures.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_sce_importable() -> None:
    """Make `import sce` work even without an editable install, as long as
    this script is run from a checkout that still has `src/sce` in place.
    """
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

from benchmarks.coverage import CoverageError, CoverageResult, compute_coverage, ground_truth_subgraph  # noqa: E402
from benchmarks.raw_context import RawContextError, build_raw_context  # noqa: E402
from benchmarks.reporting import format_table, shorten  # noqa: E402
from benchmarks.tokenizer import active_backend, count_tokens  # noqa: E402
from benchmarks.validity import CodeBlockValidity, check_python_syntax  # noqa: E402

DEFAULT_PYTHON_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "python_repo"
STRESS_FIXTURE = PROJECT_ROOT / "benchmarks" / "fixtures" / "stress_repo"
DEFAULT_RESULTS_PATH = PROJECT_ROOT / "benchmarks" / "results.json"

DEFAULT_SUITE: tuple[tuple[Path, str], ...] = (
    (DEFAULT_PYTHON_FIXTURE, "src.controllers.checkout.CheckoutController.process_checkout"),
    (STRESS_FIXTURE, "app.controllers.orders.OrderController.process_order"),
)


class BenchmarkError(Exception):
    """A user-facing benchmark failure (bad target, bad repo, etc.), as
    opposed to an internal bug - callers should print `str(exc)` and exit
    non-zero rather than showing a traceback.
    """


@dataclasses.dataclass
class BenchmarkResult:
    target: str
    repo_path: str
    budget: int
    tokenizer_backend: str
    raw_tokens: int
    sce_tokens: int
    compression_pct: float
    k_hops: int
    coverage: CoverageResult
    direct_callee_coverage: CoverageResult
    reported_preserved_semantics: float
    code_blocks_total: int
    code_blocks_valid: int
    invalid_code_blocks: list[CodeBlockValidity]
    packed_symbols: tuple[str, ...]
    raw_files: tuple[str, ...]
    elapsed_seconds: float


def _callable_only(g_sub: nx.DiGraph, builder) -> nx.DiGraph:
    """Restrict a ground-truth subgraph to nodes SCE could ever render as
    their own L0-L3 contract: known internal functions/methods. Excludes
    classes (constructor calls) and external/unresolved symbols, neither of
    which the knapsack packer treats as a candidate (see
    `ContextKnapsackPacker.pack`'s candidate filter).
    """
    callable_nodes = {
        node
        for node in g_sub.nodes
        if (symbol := builder.symbol_table.get(node)) is not None and symbol.kind in ("function", "method")
    }
    return g_sub.subgraph(callable_nodes).copy()


def run_single_benchmark(
    repo_path: str | Path,
    target: str,
    budget: int,
    k_hops: int = 3,
    lambda_weight: float = 0.7,
) -> BenchmarkResult:
    """Run the full SCE pipeline for one (repo, target, budget) combination
    and compute every benchmark metric against it.

    Raises `BenchmarkError` for expected, user-facing failures (unknown
    repo path, unknown target symbol, a target with no resolvable call
    chain). Anything else (a real bug in the pipeline) propagates as-is.
    """
    repo_path = str(repo_path)
    builder, tag_matrix = build_pipeline(repo_path)
    return run_single_benchmark_from_pipeline(
        builder, tag_matrix, repo_path, target, budget, k_hops=k_hops, lambda_weight=lambda_weight
    )


def run_single_benchmark_from_pipeline(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    repo_path: str,
    target: str,
    budget: int,
    k_hops: int = 3,
    lambda_weight: float = 0.7,
) -> BenchmarkResult:
    """Same as `run_single_benchmark`, but against an already-built pipeline.

    Splitting this out lets a caller that needs the builder/tag_matrix for
    other reasons too (e.g. `clone_eval.py` auto-selecting a target from the
    tag matrix before benchmarking it) share a single parse pass instead of
    re-parsing a potentially large real-world repository twice.
    """
    start = time.perf_counter()

    if target not in builder.symbol_table:
        raise BenchmarkError(
            f"target symbol '{target}' was not found in '{repo_path}'. "
            f"Run `sce index {repo_path} --debug-json` to list known symbols."
        )

    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    packer = ContextKnapsackPacker(token_budget=budget)
    try:
        pack_result = packer.pack(target, builder, tag_matrix, distance_engine)
    except ValueError as exc:
        raise BenchmarkError(str(exc)) from exc

    sce_markdown = render_markdown(pack_result, tag_matrix)

    try:
        raw_result = build_raw_context(builder, target)
    except RawContextError as exc:
        raise BenchmarkError(str(exc)) from exc

    raw_tokens = count_tokens(raw_result.text)
    sce_tokens = count_tokens(sce_markdown)
    compression_pct = round((1.0 - (sce_tokens / raw_tokens)) * 100.0, 2) if raw_tokens else 0.0

    packed_symbols = {item.symbol for item in pack_result.items}

    try:
        g_sub = ground_truth_subgraph(builder.graph, target, k=k_hops)
        coverage = compute_coverage(g_sub, tag_matrix, packed_symbols, k_hops)
        # Always also check the strict k=1 "direct callee" guarantee,
        # independent of whatever k the caller asked for more broadly.
        # Scoped to function/method nodes only: a class construction edge
        # (e.g. `Order(order_id, items)`) has no standalone L0-L3 contract
        # of its own by design (see ContextKnapsackPacker's candidate
        # filter) - its shape is already visible in the caller's own code
        # and any raise/attribute info surfaces via the calling function's
        # contract, so it can't be "dropped" in the sense this guarantee
        # cares about.
        g_direct = ground_truth_subgraph(builder.graph, target, k=1)
        g_direct_callable = _callable_only(g_direct, builder)
        direct_callee_coverage = compute_coverage(g_direct_callable, tag_matrix, packed_symbols, 1)
    except CoverageError as exc:
        raise BenchmarkError(str(exc)) from exc

    code_blocks = check_python_syntax(sce_markdown)
    invalid_blocks = [block for block in code_blocks if not block.valid]

    elapsed = time.perf_counter() - start

    return BenchmarkResult(
        target=target,
        repo_path=repo_path,
        budget=budget,
        tokenizer_backend=active_backend(),
        raw_tokens=raw_tokens,
        sce_tokens=sce_tokens,
        compression_pct=compression_pct,
        k_hops=k_hops,
        coverage=coverage,
        direct_callee_coverage=direct_callee_coverage,
        reported_preserved_semantics=pack_result.preserved_semantics,
        code_blocks_total=len(code_blocks),
        code_blocks_valid=len(code_blocks) - len(invalid_blocks),
        invalid_code_blocks=invalid_blocks,
        packed_symbols=tuple(sorted(packed_symbols)),
        raw_files=tuple(raw_result.files),
        elapsed_seconds=round(elapsed, 4),
    )


# --------------------------------------------------------------------- #
# Reporting: ASCII summary table + per-result detail
# --------------------------------------------------------------------- #
def render_summary_table(results: list[BenchmarkResult]) -> str:
    headers = ["Target Symbol", "Budget", "Raw Tokens", "SCE Tokens", "Compression %", "Reached Nodes", "Invariant Coverage"]
    rows = []
    for r in results:
        tag_total = sum(r.coverage.tag_totals.values())
        tag_preserved = sum(r.coverage.tag_preserved.values())
        rows.append(
            [
                shorten(r.target),
                str(r.budget),
                str(r.raw_tokens),
                str(r.sce_tokens),
                f"{r.compression_pct:.1f}%",
                f"{r.coverage.reached_nodes}/{r.coverage.subgraph_node_count} ({r.coverage.reached_node_ratio * 100:.0f}%)",
                f"{tag_preserved}/{tag_total} ({r.coverage.invariant_coverage_ratio * 100:.0f}%)",
            ]
        )
    return format_table(headers, rows)


def render_detail_section(result: BenchmarkResult) -> str:
    lines = [
        f"Target:              {result.target}",
        f"Repository:          {result.repo_path}",
        f"Budget:               {result.budget} tokens (tokenizer: {result.tokenizer_backend})",
        f"Ground truth (k={result.k_hops}):  {result.coverage.subgraph_node_count} nodes, {result.coverage.subgraph_edge_count} edges",
        f"  reached nodes:      {result.coverage.reached_nodes}/{result.coverage.subgraph_node_count} ({result.coverage.reached_node_ratio * 100:.1f}%)",
        f"  preserved edges:    {result.coverage.preserved_edges}/{result.coverage.subgraph_edge_count} ({result.coverage.preserved_edge_ratio * 100:.1f}%)",
        f"  invariant tags:     {result.coverage.tag_preserved} / {result.coverage.tag_totals}",
    ]
    if result.coverage.missing_invariant_tags:
        lines.append(f"  MISSING tags:       {', '.join(result.coverage.missing_invariant_tags)}")
    lines.append(
        f"Direct callees (k=1): {result.direct_callee_coverage.reached_nodes}/"
        f"{result.direct_callee_coverage.subgraph_node_count} reached "
        f"({result.direct_callee_coverage.reached_node_ratio * 100:.1f}%)"
    )
    lines.append(f"Reported preserved semantics (SCE's own knapsack metric): {result.reported_preserved_semantics}%")
    lines.append(f"Syntactic validity:   {result.code_blocks_valid}/{result.code_blocks_total} Python code blocks parse cleanly")
    for block in result.invalid_code_blocks:
        lines.append(f"  INVALID: {block.symbol} ({block.resolution_label}): {block.error}")
    lines.append(f"Elapsed:              {result.elapsed_seconds}s")
    return "\n".join(lines)


def _to_json_payload(results: list[BenchmarkResult]) -> dict:
    return {
        "tokenizer_backend": active_backend(),
        "result_count": len(results),
        "results": [dataclasses.asdict(r) for r in results],
    }


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.run_benchmark",
        description="Benchmark SCE's variable-resolution context packing against a whole-file-dump baseline.",
    )
    parser.add_argument("--repo", default=None, help=f"Repository to benchmark (default: bundled fixture at {DEFAULT_PYTHON_FIXTURE}).")
    parser.add_argument("--target", default=None, help="Fully qualified target symbol (required unless --suite is given).")
    parser.add_argument("--budget", type=int, nargs="+", default=[2000, 4000], help="One or more token budgets to evaluate (default: 2000 4000).")
    parser.add_argument("--k-hops", type=int, default=3, dest="k_hops", help="Ground-truth subgraph hop radius (default: 3).")
    parser.add_argument("--lambda-weight", type=float, default=0.7, dest="lambda_weight", help="D_hybrid structural/semantic balance (default: 0.7).")
    parser.add_argument(
        "--suite", action="store_true",
        help="Ignore --repo/--target and run the built-in suite (standard fixture + synthetic stress repo).",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_RESULTS_PATH),
        help=f"Write raw metrics as JSON to this path (default: {DEFAULT_RESULTS_PATH}). Pass '' to skip.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.suite:
        jobs = [(str(repo), target) for repo, target in DEFAULT_SUITE]
    else:
        if not args.target:
            parser.error("--target is required unless --suite is given")
        jobs = [(args.repo or str(DEFAULT_PYTHON_FIXTURE), args.target)]

    results: list[BenchmarkResult] = []
    try:
        for repo, target in jobs:
            for budget in args.budget:
                results.append(
                    run_single_benchmark(repo, target, budget, k_hops=args.k_hops, lambda_weight=args.lambda_weight)
                )
    except BenchmarkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(render_summary_table(results))
    for result in results:
        print()
        print(render_detail_section(result))

    if args.output:
        output_path = Path(args.output)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(_to_json_payload(results), indent=2))
        except OSError as exc:
            print(f"warning: could not write results to '{output_path}': {exc}", file=sys.stderr)
        else:
            print(f"\nWrote raw metrics to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
