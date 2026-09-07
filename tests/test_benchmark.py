"""Regression tests for the evaluation harness in `benchmarks/`.

These lock in the two headline guarantees the benchmark exists to measure:
compression actually beats a naive whole-file dump by a wide margin, and
every Python code block SCE renders stays syntactically valid at every
resolution level.
"""
from __future__ import annotations

import ast

import pytest

from benchmarks.run_benchmark import (
    DEFAULT_PYTHON_FIXTURE,
    STRESS_FIXTURE,
    BenchmarkError,
    run_single_benchmark,
)
from benchmarks.validity import extract_code_blocks

STANDARD_TARGET = "src.controllers.checkout.CheckoutController.process_checkout"
STRESS_TARGET = "app.controllers.orders.OrderController.process_order"


# The stress fixture (benchmarks/fixtures/stress_repo) is the "standard
# fixture" this specific threshold is checked against: it is sized like a
# real module (multiple methods per file, module docstrings, logging,
# admin/ancillary helpers alongside the hot path), which is what makes a
# whole-file dump wasteful in the first place. The tiny hand-written
# tests/fixtures/python_repo used throughout the rest of the suite is
# almost entirely "signal" with no filler, so it does not exercise the
# compression story meaningfully - it's still benchmarked below for
# structural-coverage/validity purposes, just without this specific ratio
# gate.
@pytest.mark.parametrize("budget", [2000, 4000])
def test_compression_ratio_meets_threshold_without_dropping_direct_callees(budget):
    result = run_single_benchmark(STRESS_FIXTURE, STRESS_TARGET, budget=budget)

    assert result.compression_pct >= 50.0, (
        f"expected >=50% compression vs. the whole-file-dump baseline, got {result.compression_pct}% "
        f"(raw={result.raw_tokens} tokens, sce={result.sce_tokens} tokens)"
    )

    # "Without dropping direct callee contracts": every function/method the
    # target directly calls must survive into the packed context (at some
    # resolution level, L0-L3) - compression must never come at the cost of
    # losing the target's own immediate call chain.
    dc = result.direct_callee_coverage
    assert dc.reached_nodes == dc.subgraph_node_count, (
        f"{dc.subgraph_node_count - dc.reached_nodes} direct callee(s) were dropped from the packed context "
        f"(reached {dc.reached_nodes}/{dc.subgraph_node_count})"
    )
    assert dc.subgraph_node_count > 0, "expected the stress fixture's target to have direct, resolvable callees"


@pytest.mark.parametrize("budget", [2000, 4000])
def test_packed_context_respects_budget_under_a_real_tokenizer(budget):
    """Regression test for a real bug caught via `clone_eval.py` against a
    live clone of encode/starlette: the knapsack's internal word-count
    budget accounting didn't include the Markdown wrapping (heading +
    fences) added around each packed item, so its own running total
    silently diverged from the size of the document it was actually
    building - badly enough, once compounded across 100+ small packed
    items on a real densely-connected repo, that a 4000-token budget
    rendered a document measuring over 13,000 tokens against a real
    tokenizer (3.3x over). Fixed in `sce.slicer.knapsack` by costing each
    item's wrapping overhead, recalibrating the word-to-token ratio against
    measured samples, and packing only up to a safety-margined fraction of
    the nominal budget. `result.sce_tokens` here is computed with the same
    tokenizer (tiktoken, or its fallback) used everywhere else in this
    harness - not the packer's own internal estimate - so this is a
    genuine, independent check.
    """
    for repo, target in ((DEFAULT_PYTHON_FIXTURE, STANDARD_TARGET), (STRESS_FIXTURE, STRESS_TARGET)):
        result = run_single_benchmark(repo, target, budget=budget)
        assert result.sce_tokens <= budget, (
            f"packed context for {target} measured {result.sce_tokens} tokens against a {budget}-token budget"
        )


def test_standard_fixture_also_runs_and_preserves_direct_callees():
    """The existing fixture repo is part of the required benchmark surface
    too (HLD's own worked example) - it just isn't large enough to be a
    meaningful compression-ratio gate. It must still run cleanly and never
    drop a direct callee.
    """
    result = run_single_benchmark(DEFAULT_PYTHON_FIXTURE, STANDARD_TARGET, budget=2000)
    dc = result.direct_callee_coverage
    assert dc.reached_nodes == dc.subgraph_node_count
    assert result.compression_pct >= 0.0


@pytest.mark.parametrize(
    ("repo", "target"),
    [
        (STRESS_FIXTURE, STRESS_TARGET),
        (DEFAULT_PYTHON_FIXTURE, STANDARD_TARGET),
    ],
)
def test_all_generated_code_blocks_parse_cleanly(repo, target):
    result = run_single_benchmark(repo, target, budget=4000)

    assert result.code_blocks_total > 0, "expected at least one rendered code block"
    assert result.invalid_code_blocks == [], (
        f"{len(result.invalid_code_blocks)} code block(s) failed ast.parse(): "
        f"{[(b.symbol, b.resolution_label, b.error) for b in result.invalid_code_blocks]}"
    )
    assert result.code_blocks_valid == result.code_blocks_total


def test_l0_and_l1_blocks_specifically_are_syntactically_valid():
    """Directly targets the scenario the HLD's compressor is most likely to
    regress on: L0 raw slices (indentation) and L1 skeletons (AST stripping
    leaving an empty suite where a `pass` is required)."""
    result = run_single_benchmark(STRESS_FIXTURE, STRESS_TARGET, budget=4000)

    from sce.cli import build_pipeline
    from sce.graph.metamodel import SemanticMetamodel
    from sce.serializers.markdown import render_markdown
    from sce.slicer.distance import DistanceConfig, DistanceEngine
    from sce.slicer.knapsack import ContextKnapsackPacker

    builder, tag_matrix = build_pipeline(str(STRESS_FIXTURE))
    distance_engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=4000).pack(STRESS_TARGET, builder, tag_matrix, distance_engine)
    markdown_text = render_markdown(pack_result, tag_matrix)

    blocks = extract_code_blocks(markdown_text)
    l0_or_l1 = [b for b in blocks if b.resolution in (0, 1) and b.language == "python"]
    assert l0_or_l1, "expected at least one L0/L1 Python block in the packed context"

    # Re-parse each one directly from the markdown, independent of the
    # harness's own bookkeeping, as a second, black-box confirmation.
    import re

    for block in l0_or_l1:
        pattern = re.compile(
            r"^###\s+(?:\[TARGET\]\s+)?" + re.escape(block.symbol) + r"\s+\([^)]+\)\s*\n```python\n(.*?)\n```",
            re.DOTALL | re.MULTILINE,
        )
        match = pattern.search(markdown_text)
        assert match is not None
        ast.parse(match.group(1))  # raises SyntaxError on failure


def test_unknown_target_raises_benchmark_error():
    with pytest.raises(BenchmarkError):
        run_single_benchmark(DEFAULT_PYTHON_FIXTURE, "does.not.exist", budget=2000)
