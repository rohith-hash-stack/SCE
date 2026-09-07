"""Live OpenAI evaluation: runs Prism's two code-task benchmarks against a
real model, once per context variant (`raw` whole-file dump, `prism` sliced
context), and scores each response with a deterministic AST verifier -
never another LLM - so results are reproducible and free to re-check.

Usage:
    python -m benchmarks.live_eval --model "gpt-4o-mini" --tasks all --report benchmarks/live_report.json
    python -m benchmarks.live_eval --dry-run          # build prompts, print sizes, no API calls / no key needed
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_prism_importable() -> None:
    try:
        import prism  # noqa: F401
    except ImportError:
        src_path = str(PROJECT_ROOT / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


_ensure_prism_importable()

from benchmarks.openai_client import LLMClient, OpenAIClientError  # noqa: E402
from benchmarks.reporting import format_table  # noqa: E402
from benchmarks.tasks import ALL_TASKS, Task, TaskContext, TaskSetupError, build_task_context  # noqa: E402
from benchmarks.tokenizer import active_backend, count_tokens  # noqa: E402

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BUDGET = 2000
DEFAULT_REPORT_PATH = PROJECT_ROOT / "benchmarks" / "live_report.json"
VARIANTS: tuple[str, ...] = ("raw", "prism")


@dataclasses.dataclass
class LiveRunResult:
    task_id: str
    task_title: str
    variant: str
    model: str
    passed: bool
    checks: dict[str, bool]
    notes: list[str]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None
    latency_seconds: float
    response_text: str
    extracted_code: str | None


def run_task_variant(task: Task, ctx: TaskContext, variant: str, client: LLMClient, model: str, temperature: float) -> LiveRunResult:
    context_text = ctx.context_for(variant)
    user_prompt = task.build_prompt(context_text)
    call = client.complete(model=model, system=task.system_prompt, user=user_prompt, temperature=temperature)
    verifier_result = task.verify(call.content, ctx.known_symbols_for(variant))
    return LiveRunResult(
        task_id=task.task_id,
        task_title=task.title,
        variant=variant,
        model=model,
        passed=verifier_result.passed,
        checks=verifier_result.checks,
        notes=verifier_result.notes,
        prompt_tokens=call.prompt_tokens,
        completion_tokens=call.completion_tokens,
        total_tokens=call.total_tokens,
        cost_usd=call.cost_usd,
        latency_seconds=call.latency_seconds,
        response_text=call.content,
        extracted_code=verifier_result.extracted_code,
    )


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #
def render_summary_table(results: list[LiveRunResult]) -> str:
    headers = ["Task", "Variant", "Model", "Result", "Prompt Tok", "Compl Tok", "Cost (USD)", "Latency (s)"]
    rows = []
    for r in results:
        cost = f"${r.cost_usd:.5f}" if r.cost_usd is not None else "n/a"
        rows.append(
            [
                r.task_id,
                r.variant,
                r.model,
                "PASS" if r.passed else "FAIL",
                str(r.prompt_tokens),
                str(r.completion_tokens),
                cost,
                f"{r.latency_seconds:.2f}",
            ]
        )
    return format_table(headers, rows)


def render_cost_summary(results: list[LiveRunResult]) -> str:
    if not results:
        return "(no runs)"
    lines = []
    passed = sum(1 for r in results if r.passed)
    lines.append(f"Overall: {passed}/{len(results)} passed ({passed / len(results) * 100:.1f}%)")

    for variant in VARIANTS:
        variant_results = [r for r in results if r.variant == variant]
        if not variant_results:
            continue
        v_passed = sum(1 for r in variant_results if r.passed)
        avg_prompt = sum(r.prompt_tokens for r in variant_results) / len(variant_results)
        lines.append(
            f"  [{variant}] {v_passed}/{len(variant_results)} passed "
            f"({v_passed / len(variant_results) * 100:.1f}%), avg prompt tokens: {avg_prompt:.0f}"
        )

    known_costs = [r.cost_usd for r in results if r.cost_usd is not None]
    unpriced_models = sorted({r.model for r in results if r.cost_usd is None})
    total_prompt = sum(r.prompt_tokens for r in results)
    total_completion = sum(r.completion_tokens for r in results)
    lines.append(f"Total tokens: {total_prompt} prompt + {total_completion} completion = {total_prompt + total_completion}")
    if known_costs:
        lines.append(f"Total estimated cost: ${sum(known_costs):.5f}")
    if unpriced_models:
        lines.append(f"  (cost unknown for model(s) not in the pricing table: {', '.join(unpriced_models)} - see --price-in/--price-out)")

    for r in results:
        if not r.passed:
            lines.append(f"  FAIL [{r.task_id}/{r.variant}]: {'; '.join(r.notes) or 'unspecified'}")
    return "\n".join(lines)


def dry_run_preview(tasks: list[Task], budget: int) -> str:
    lines = [f"(dry run - no API calls made; tokenizer backend: {active_backend()})", ""]
    for task in tasks:
        try:
            ctx = build_task_context(task, budget=budget)
        except TaskSetupError as exc:
            lines.append(f"=== {task.task_id}: {task.title} === ERROR: {exc}")
            continue
        lines.append(f"=== {task.task_id}: {task.title} ===")
        for variant in VARIANTS:
            context_text = ctx.context_for(variant)
            prompt = task.build_prompt(context_text)
            known = sorted(ctx.known_symbols_for(variant))
            lines.append(
                f"  [{variant}] context={count_tokens(context_text)} tok, full prompt={count_tokens(prompt)} tok, "
                f"known symbols={known}"
            )
        lines.append("")
    return "\n".join(lines)


def write_report(results: list[LiveRunResult], path: str) -> None:
    payload = {"result_count": len(results), "results": [dataclasses.asdict(r) for r in results]}
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.live_eval",
        description="Run live OpenAI evaluations of Prism-sliced vs. whole-file-dump context on code tasks.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model to use (default: {DEFAULT_MODEL}).")
    parser.add_argument(
        "--tasks", default="all",
        help=f"Comma-separated task ids to run, or 'all' (available: {', '.join(ALL_TASKS)}).",
    )
    parser.add_argument(
        "--variants", default=",".join(VARIANTS),
        help=f"Comma-separated context variants to run (available: {', '.join(VARIANTS)}).",
    )
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"Prism token budget (default: {DEFAULT_BUDGET}).")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (default: 0.0).")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help=f"Write full results as JSON here (default: {DEFAULT_REPORT_PATH}). Pass '' to skip.")
    parser.add_argument("--price-in", type=float, default=None, dest="price_in", help="Override input price (USD per 1M tokens) for cost estimation.")
    parser.add_argument("--price-out", type=float, default=None, dest="price_out", help="Override output price (USD per 1M tokens) for cost estimation.")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts/contexts and print sizes without calling the API (no key required).")
    return parser


def _select_tasks(spec: str) -> list[Task]:
    if spec == "all":
        return list(ALL_TASKS.values())
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [task_id for task_id in ids if task_id not in ALL_TASKS]
    if unknown:
        raise ValueError(f"unknown task id(s): {', '.join(unknown)} (available: {', '.join(ALL_TASKS)})")
    return [ALL_TASKS[task_id] for task_id in ids]


def _select_variants(spec: str) -> list[str]:
    variants = [v.strip() for v in spec.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variant(s): {', '.join(unknown)} (available: {', '.join(VARIANTS)})")
    return variants


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        tasks = _select_tasks(args.tasks)
        variants = _select_variants(args.variants)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(dry_run_preview(tasks, args.budget))
        return 0

    try:
        client = LLMClient(price_in_override=args.price_in, price_out_override=args.price_out)
    except OpenAIClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    results: list[LiveRunResult] = []
    for task in tasks:
        try:
            ctx = build_task_context(task, budget=args.budget)
        except TaskSetupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for variant in variants:
            print(f"Running {task.task_id} [{variant}] with {args.model}...", file=sys.stderr)
            try:
                results.append(run_task_variant(task, ctx, variant, client, args.model, args.temperature))
            except OpenAIClientError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    print(render_summary_table(results))
    print()
    print(render_cost_summary(results))

    if args.report:
        write_report(results, args.report)
        print(f"\nWrote live-eval report to {args.report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
