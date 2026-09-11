"""v1.1+ Empirical Benchmarking Harness: `--mode=smoke` - a fast,
network-free, synthetic-fixture exercise of the full retrieval ->
serialize -> parse -> diagnostics pipeline across all 5 engines. Real
enough (a real `build_pipeline` over real Python source, the real
engines, the real renderer/parser, the real metric formulas) to catch a
broken wiring between any two of those modules without needing a pinned
corpus clone or a paid LLM call - the whole thing runs in-process
against a handful of temp files.

**Not a substitute for the real pilot** (`--mode=pilot`): this fixture
is deliberately tiny and its "ground truth" is this module's own
synthetic annotation, not a real human one. It exists to catch
integration breakage fast (a defect that would otherwise only surface
hours into a real corpus run), not to measure retrieval quality -
`--mode=pilot`/`--mode=eval` against a real pinned corpus is what that
requires.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

from prism.surface.parser import parse_context
from prism.surface.renderer import RenderOptions, render

from benchmarks.engines.base import selected_symbols
from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.metrics.fcc import compute_corpus_feature_stats
from benchmarks.reporting.report_generator import EvaluationRun, TaskRunRecord, write_json_results, write_markdown_report

#: A tiny, self-contained fixture exercising the three shapes the
#: diagnostic metrics actually care about: a causal chain
#: (`calculate_tax` -> `invoice_generator` -> `batch_invoicer`), a
#: return-binding blast radius (`invoice_generator`/`invoice_generator_v2`
#: both bind `calculate_tax`'s return value), and a real feature-bearing
#: sink (`notify`'s `requests.post` call) so FCC has something non-zero
#: to compute over.
SMOKE_REPO_SOURCE = (
    "import requests\n\n\n"
    "def calculate_tax(amount):\n"
    "    return amount * 0.2\n\n\n"
    "def invoice_generator(amount):\n"
    "    total = calculate_tax(amount)\n"
    "    return total\n\n\n"
    "def invoice_generator_v2(amount):\n"
    "    total = calculate_tax(amount)\n"
    "    return total\n\n\n"
    "def batch_invoicer(amounts):\n"
    "    results = []\n"
    "    for a in amounts:\n"
    "        results.append(invoice_generator(a))\n"
    "    return results\n\n\n"
    "def notify(amount):\n"
    "    requests.post('http://example.com', json={'amount': amount})\n\n\n"
    "def unrelated_helper(x):\n"
    "    return x + 1\n"
)

SMOKE_TASK_ID = "smoke_001"
SMOKE_SEED_SYMBOL = "svc.calculate_tax"
SMOKE_BUDGET = 2000


def _smoke_task() -> EvaluationTask:
    """A synthetic, self-annotated task - `repo="django"` is a schema-
    required label only (this fixture never touches a real corpus);
    `cohen_kappa=1.0` reflects that `annotation_a`/`annotation_b` are
    the same object here, not a real double-blind measurement."""
    ann = GroundTruthAnnotation(
        annotator_id="smoke",
        pipeline_symbols=["svc.calculate_tax", "svc.invoice_generator", "svc.batch_invoicer"],
        critical_callers={"svc.invoice_generator", "svc.invoice_generator_v2"},
        expected_solution="synthetic smoke fixture - not a real human annotation",
    )
    return EvaluationTask(
        task_id=SMOKE_TASK_ID,
        repo="django",
        pinned_commit="smoke-fixture",
        seed_symbol=SMOKE_SEED_SYMBOL,
        task_type="chain",
        prompt="(smoke fixture - no LLM call made)",
        annotation_a=ann,
        annotation_b=ann,
        adjudicated=ann,
        cohen_kappa=1.0,
    )


def run_smoke_test(output_dir: str = "reports/") -> EvaluationRun:
    """Runs every engine once against `SMOKE_REPO_SOURCE`, asserting (via
    plain `assert` - a smoke test's own failure mode is "raise loudly
    and stop", not "record a soft failure and continue") every P0
    acceptance criterion: error-free retrieval, deterministic rendering,
    lossless parse/render idempotency, honored token budgeting, and
    NaN/zero-division-free diagnostics. Writes `smoke_results.{json,md}`
    to `output_dir` via the same (untouched) `report_generator` module a
    real eval run uses.
    """
    from benchmarks.runner import _build_engines, compute_diagnostics  # local: avoids a runner<->smoke import cycle

    task = _smoke_task()
    run = EvaluationRun()

    with tempfile.TemporaryDirectory(prefix="prism_smoke_") as tmp:
        tmp_path = Path(tmp)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / "svc.py").write_text(SMOKE_REPO_SOURCE)

        oracle_packages_path = tmp_path / "oracle_packages.json"
        oracle_packages_path.write_text(json.dumps({SMOKE_TASK_ID: task.adjudicated.pipeline_symbols}))

        from prism.cli import build_pipeline

        builder, _tag_matrix = build_pipeline(str(repo_path))
        feature_stats = compute_corpus_feature_stats(builder)

        engines = _build_engines(str(oracle_packages_path), task.task_id)
        assert len(engines) == 5, f"expected all 5 engines (4 baselines/Prism + Oracle), got {len(engines)}"

        render_options = RenderOptions(include_timestamp=False, include_run_id=False)

        # Oracle is always first in `_build_engines`'s own return order,
        # so by the time any other engine's turn comes around its
        # selected-symbol set is already here for `fpr_oracle`.
        oracle_selected: set[str] | None = None

        for engine in engines:
            engine.index(str(repo_path))
            pkg = engine.retrieve(SMOKE_SEED_SYMBOL, SMOKE_BUDGET)
            assert pkg.nodes, f"{engine.name}: retrieved an empty context package for a real seed symbol"

            # Token budgeting: the envelope must honor the budget it was
            # asked for, and Prism's own knapsack must actually respect
            # it (the baselines make no such packing guarantee).
            assert pkg.budget.tokens == SMOKE_BUDGET, f"{engine.name}: budget.tokens {pkg.budget.tokens} != requested {SMOKE_BUDGET}"
            if engine.name == "prism_v11":
                packed_cost = sum(n.cost for n in pkg.nodes)
                assert packed_cost <= SMOKE_BUDGET, f"{engine.name}: packed cost {packed_cost} exceeds budget {SMOKE_BUDGET}"

            # Determinism: identical (pkg, options) -> byte-identical XML.
            rendered_a = render(pkg, render_options)
            rendered_b = render(pkg, render_options)
            assert rendered_a == rendered_b, f"{engine.name}: render() is not deterministic for identical input"

            # Lossless roundtrip: parse(render(pkg)) re-rendered is
            # byte-identical to the original render (the direct pkg
            # comparison isn't safe here since a real-sized package may
            # legitimately trip BUDGET_OVERFLOW, which render() injects
            # into its *output* without mutating the input pkg - so the
            # correct invariant is idempotency of the render/parse cycle,
            # not equality against the pre-render pkg).
            parsed = parse_context(rendered_a)
            rerendered = render(parsed, render_options)
            assert rerendered == rendered_a, f"{engine.name}: parse_context(render(pkg)) is not a lossless roundtrip"

            candidate_symbols = selected_symbols(pkg)
            if engine.name == "oracle":
                oracle_selected = candidate_symbols

            diagnostics = compute_diagnostics(pkg, task, feature_stats, oracle_selected=oracle_selected)
            for metric_name, value in diagnostics.items():
                if value is None:
                    continue  # a structurally-absent dimension (e.g. fcc for a non-coordinate-space engine), not a failure
                assert not math.isnan(value), f"{engine.name}.{metric_name} is NaN"
                assert math.isfinite(value), f"{engine.name}.{metric_name} is not finite ({value})"
            ground_truth = (
                set(task.adjudicated.pipeline_symbols) | task.adjudicated.critical_callers | {task.seed_symbol}
            )
            run.records.append(
                TaskRunRecord(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    repo=task.repo,
                    engine_name=engine.name,
                    budget_tokens=SMOKE_BUDGET,
                    tsr_scores=[],  # smoke mode never calls a paid LLM
                    diagnostics=diagnostics,
                    selected_symbols=sorted(candidate_symbols),
                    ground_truth_symbols=sorted(ground_truth),
                )
            )

    out = Path(output_dir)
    write_json_results(run, out / "smoke_results.json")
    write_markdown_report(run, out / "smoke_results.md")
    return run


def run_smoke(output_dir: str = "reports/") -> int:
    """CLI entry point for `--mode=smoke` - returns a process exit code
    rather than raising, so `runner.main` can report a clean failure
    instead of a raw traceback."""
    try:
        run = run_smoke_test(output_dir)
    except AssertionError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    engines_exercised = sorted({r.engine_name for r in run.records})
    print(f"smoke OK: {len(engines_exercised)} engines exercised without error ({', '.join(engines_exercised)})")
    print(f"reports written to {Path(output_dir) / 'smoke_results.json'} / smoke_results.md")
    return 0
