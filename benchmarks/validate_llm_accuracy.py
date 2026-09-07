"""End-to-end LLM accuracy validation: does a real model produce
functionally correct, hallucination-free code when given Prism's sliced
context vs. a raw whole-file dump?

Three tasks with deterministic ground truth (verified by actually running
`pytest` against the model's patch in a sandboxed copy of the fixture
repo - never an LLM judge):

  1. The Missing Invariant Bug - `checkout_order()` writes to the database
     without the codebase's own auth guard. Ground truth: unauthenticated
     calls must raise `PermissionError`.
  2. Interface Conformance & Feature Extension - a new `refund_transaction`
     method must call the *real* gateway method (`reverse_charge`, not a
     hallucinated `refund`/`process_refund`) with the exact arguments.
  3. Cross-File Control Flow Debugging - `process_payload()` over-catches
     `except Exception`, silently swallowing an unrelated `RuntimeError`
     from two hops away. Ground truth: only the specific validation error
     should be caught; other errors must still propagate.

Usage:
    python -m benchmarks.validate_llm_accuracy --model gpt-4o-mini --report benchmarks/accuracy_report.json
    python -m benchmarks.validate_llm_accuracy --dry-run          # no API key needed
"""
from __future__ import annotations

import argparse
import ast
import builtins as _builtins_module
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
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

from prism.cli import build_pipeline  # noqa: E402
from prism.graph.concrete_builder import ConcreteGraphBuilder  # noqa: E402
from prism.graph.metamodel import SemanticMetamodel  # noqa: E402
from prism.serializers.markdown import render_markdown  # noqa: E402
from prism.slicer.distance import DistanceConfig, DistanceEngine  # noqa: E402
from prism.slicer.knapsack import ContextKnapsackPacker  # noqa: E402

from benchmarks.openai_client import LLMClient, OpenAIClientError  # noqa: E402
from benchmarks.raw_context import build_raw_context, dump_files  # noqa: E402
from benchmarks.reporting import format_table  # noqa: E402
from benchmarks.tasks import extract_first_code_block  # noqa: E402
from benchmarks.tokenizer import active_backend, count_tokens  # noqa: E402

ACCURACY_REPO = PROJECT_ROOT / "benchmarks" / "fixtures" / "accuracy_repo"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BUDGET = 2000
DEFAULT_REPORT_PATH = PROJECT_ROOT / "benchmarks" / "accuracy_report.json"
VARIANTS: tuple[str, ...] = ("raw", "prism")
PYTEST_TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = (
    "You are a senior software engineer fixing a real, running production codebase. "
    "You will be given a context package describing part of the codebase - either full source "
    "files or a variable-resolution slice with contracts for less-relevant code - plus a task. "
    "Use ONLY the exact symbols, function/method names, and signatures that literally appear in "
    "the given context; never invent a helper, method, or exception class that is not shown. "
    "Respond with a brief explanation, then a single fenced Python code block containing ONLY "
    "the complete function or method requested - no surrounding class definition, no other "
    "functions or methods, and no markdown outside that one code block."
)


class ValidationError(Exception):
    """User-facing setup failure (bad task target, etc.)."""


@dataclasses.dataclass(frozen=True)
class AccuracyTask:
    task_id: str
    title: str
    # The symbol Prism queries / the raw dump is scoped around.
    context_target: str
    # The symbol whose body actually gets replaced with the LLM's code in
    # the sandbox. Equal to context_target for Tasks 1 and 3 (fixing code
    # in place); different for Task 2 (querying the working `cancel_order`
    # template while patching the as-yet-unimplemented `refund_transaction`
    # placeholder next to it).
    patch_target: str
    task_description: str
    test_file: str
    # "call_chain" (default): raw baseline dumps context_target's call-chain
    # closure, same as benchmarks/run_benchmark.py. "whole_repo": dump every
    # fixture file instead - needed for Task 1, whose fix (the auth guard)
    # lives outside the buggy function's own call graph, which would
    # otherwise make it equally undiscoverable in a call-chain-scoped raw
    # dump (see benchmarks/tasks.py for the same situation on a different
    # fixture).
    raw_scope: str = "call_chain"


TASKS: tuple[AccuracyTask, ...] = (
    AccuracyTask(
        task_id="missing_invariant",
        title="Task 1: The Missing Invariant Bug (Security/Correctness)",
        context_target="app.orders.OrderService.checkout_order",
        patch_target="app.orders.OrderService.checkout_order",
        task_description=(
            "Function `checkout_order()` performs a database mutation but is missing an "
            "authorization check. Identify the vulnerability and provide the patched function."
        ),
        test_file="tests/test_task1_checkout_order.py",
        raw_scope="whole_repo",
    ),
    AccuracyTask(
        task_id="interface_conformance",
        title="Task 2: Interface Conformance & Feature Extension (No Hallucinations)",
        context_target="app.orders.OrderService.cancel_order",
        patch_target="app.orders.OrderService.refund_transaction",
        task_description=(
            "Implement a new method `refund_transaction(order_id, amount)` that coordinates with "
            "the payment client and updates order status, following the same dependency-usage "
            "pattern as `cancel_order` shown above."
        ),
        test_file="tests/test_task2_refund_transaction.py",
        raw_scope="call_chain",
    ),
    AccuracyTask(
        task_id="cross_file_control_flow",
        title="Task 3: Cross-File Control Flow Debugging",
        context_target="app.payloads.process_payload",
        patch_target="app.payloads.process_payload",
        task_description=(
            "When calling `process_payload()`, a payment-gateway outage under a specific edge "
            "condition gets silently reported to the caller as an 'invalid payload' error, masking "
            "a real infrastructure failure behind a validation-error message. Fix the exception "
            "handling so only genuine payload validation failures are reported this way, while "
            "other errors propagate normally."
        ),
        test_file="tests/test_task3_process_payload.py",
        raw_scope="call_chain",
    ),
)
TASKS_BY_ID: dict[str, AccuracyTask] = {t.task_id: t for t in TASKS}


# --------------------------------------------------------------------- #
# Context building (raw whole-file dump vs. Prism L0-L3 package)
# --------------------------------------------------------------------- #
def _whole_repo_files(builder: ConcreteGraphBuilder) -> tuple[str, ...]:
    return tuple(sorted({symbol.file for symbol in builder.symbol_table}))


def build_contexts(
    builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], task: AccuracyTask, budget: int, lambda_weight: float = 0.7
) -> tuple[str, str]:
    """Returns (raw_text, prism_text) for a task's context_target."""
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(task.context_target, builder, tag_matrix, distance_engine)
    prism_text = render_markdown(pack_result, tag_matrix)

    if task.raw_scope == "whole_repo":
        raw_files = _whole_repo_files(builder)
    else:
        raw_files = build_raw_context(builder, task.context_target).files
    raw_text = dump_files(builder, raw_files)

    return raw_text, prism_text


def read_original_snippet(builder: ConcreteGraphBuilder, qualified_name: str) -> str:
    symbol = builder.symbol_table.get(qualified_name)
    parsed = builder.parsed_file(symbol.file)
    source = parsed.source.decode("utf-8", errors="replace")
    lines = source.splitlines()
    start, end = symbol.line_range
    raw = "\n".join(lines[start - 1 : end])
    return textwrap.dedent(raw)


def build_user_prompt(task: AccuracyTask, context_text: str, original_snippet: str) -> str:
    patch_name = task.patch_target.rsplit(".", 1)[-1]
    return (
        f"CONTEXT PACKAGE:\n{context_text}\n\n"
        f"TASK:\n{task.task_description}\n\n"
        f"Current implementation of `{task.patch_target}`:\n```python\n{original_snippet}\n```\n\n"
        f"Respond with the corrected/new `{patch_name}` method only."
    )


# --------------------------------------------------------------------- #
# Sandbox execution (section 3: syntax check, execution sandbox, pytest)
# --------------------------------------------------------------------- #
def apply_patch(original_source: str, line_range: tuple[int, int], llm_code: str) -> str:
    """Replace `line_range` (1-indexed, inclusive) in `original_source` with
    `llm_code`, re-indented to match the original block's indentation -
    the LLM is asked for just a method body and usually returns it either
    flush-left or already indented; this handles both.
    """
    lines = original_source.splitlines()
    start, end = line_range
    indent_match = re.match(r"[ \t]*", lines[start - 1])
    indent = indent_match.group(0) if indent_match else ""
    dedented = textwrap.dedent(llm_code).strip("\n")
    reindented = textwrap.indent(dedented, indent)
    new_lines = lines[: start - 1] + [reindented] + lines[end:]
    return "\n".join(new_lines) + "\n"


def run_in_sandbox(builder: ConcreteGraphBuilder, task: AccuracyTask, llm_code: str) -> tuple[bool, int | None, str]:
    """Copies the fixture repo to a temp dir, patches `task.patch_target`'s
    body with `llm_code`, and runs the task's ground-truth pytest file
    against the patched copy via `subprocess.run`. Returns
    (all_tests_passed, pytest_returncode, tail_of_pytest_output).
    """
    symbol = builder.symbol_table.get(task.patch_target)
    fixture_root = Path(builder.repo_root)

    with tempfile.TemporaryDirectory(prefix="prism_accuracy_") as tmp:
        sandbox_root = Path(tmp) / "repo"
        shutil.copytree(fixture_root, sandbox_root)

        rel_path = os.path.relpath(symbol.file, fixture_root)
        sandbox_file = sandbox_root / rel_path
        original_source = sandbox_file.read_text(encoding="utf-8")
        patched_source = apply_patch(original_source, symbol.line_range, llm_code)
        sandbox_file.write_text(patched_source, encoding="utf-8")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", task.test_file, "-q"],
                cwd=str(sandbox_root),
                capture_output=True,
                text=True,
                timeout=PYTEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, None, f"pytest timed out after {PYTEST_TIMEOUT_SECONDS}s"

        output = (result.stdout + "\n" + result.stderr).strip()
        tail = "\n".join(output.splitlines()[-40:])
        return result.returncode == 0, result.returncode, tail


# --------------------------------------------------------------------- #
# Hallucination counter (section 3: static AST inspection vs. GlobalSymbolTable)
# --------------------------------------------------------------------- #
_BUILTIN_NAMES = frozenset(dir(_builtins_module))
# Common built-in container/string methods AST-level call-name inspection
# can't distinguish from a hallucinated project-level method without type
# inference (which this checker deliberately doesn't do, matching Prism's own
# "no dynamic execution" design principle). Kept short and genuinely
# common - anything not in this set, not a builtin, and not a real
# GlobalSymbolTable symbol is still flagged.
_COMMON_STDLIB_METHOD_NAMES = frozenset(
    {
        "get", "keys", "values", "items", "append", "extend", "pop", "update",
        "join", "split", "strip", "lower", "upper", "replace", "format",
        "startswith", "endswith", "encode", "decode", "copy", "sort", "index",
    }
)


def find_hallucinated_calls(code: str, builder: ConcreteGraphBuilder) -> tuple[str, ...]:
    """Every call in `code` whose simple name is neither a real symbol in
    the repository's `GlobalSymbolTable`, a Python builtin, nor a common
    built-in container/string method - i.e. it does not exist in the
    codebase, per section 3's literal check.

    "Real symbol" deliberately means any kind `GlobalSymbolTable` indexes -
    function, method, class, or attribute (module/class/instance-level
    assignments like `_iterable_class = ModelIterable`, indexed by
    `ConcreteGraphBuilder`'s Pass 1) - not just callables. A live run
    against django/django found real, correct code calling attribute-bound
    references (e.g. `self._iterable_class(self)`) that a kind-filtered
    check would have flagged as fabricated.
    """
    tree = ast.parse(code)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)

    known_simple_names = {qname.rsplit(".", 1)[-1] for qname in builder.symbol_table.all_qualified_names()}
    unknown = sorted(
        name
        for name in called
        if name not in known_simple_names and name not in _BUILTIN_NAMES and name not in _COMMON_STDLIB_METHOD_NAMES
    )
    return tuple(unknown)


# --------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------- #
@dataclasses.dataclass
class TaskRunResult:
    task_id: str
    title: str
    variant: str
    model: str
    context_tokens: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    latency_seconds: float
    extracted_code: str | None
    syntax_valid: bool
    syntax_error: str | None
    pytest_passed: bool
    pytest_returncode: int | None
    pytest_output_tail: str
    hallucinated_calls: tuple[str, ...]
    passed: bool


def run_task_variant(
    builder: ConcreteGraphBuilder,
    task: AccuracyTask,
    variant: str,
    context_text: str,
    model: str,
    client: LLMClient,
    temperature: float,
) -> TaskRunResult:
    original_snippet = read_original_snippet(builder, task.patch_target)
    user_prompt = build_user_prompt(task, context_text, original_snippet)
    call = client.complete(model=model, system=SYSTEM_PROMPT, user=user_prompt, temperature=temperature)

    code = extract_first_code_block(call.content)
    syntax_valid = False
    syntax_error: str | None = None
    pytest_passed = False
    pytest_returncode: int | None = None
    pytest_tail = ""
    hallucinated: tuple[str, ...] = ()

    if code is None:
        syntax_error = "no fenced code block found in the model's response"
    else:
        try:
            ast.parse(code)
            syntax_valid = True
        except SyntaxError as exc:
            syntax_error = f"{exc.msg} (line {exc.lineno})"

        if syntax_valid:
            hallucinated = find_hallucinated_calls(code, builder)
            pytest_passed, pytest_returncode, pytest_tail = run_in_sandbox(builder, task, code)

    passed = syntax_valid and pytest_passed and not hallucinated

    return TaskRunResult(
        task_id=task.task_id,
        title=task.title,
        variant=variant,
        model=model,
        context_tokens=count_tokens(context_text),
        prompt_tokens=call.prompt_tokens,
        completion_tokens=call.completion_tokens,
        cost_usd=call.cost_usd,
        latency_seconds=call.latency_seconds,
        extracted_code=code,
        syntax_valid=syntax_valid,
        syntax_error=syntax_error,
        pytest_passed=pytest_passed,
        pytest_returncode=pytest_returncode,
        pytest_output_tail=pytest_tail,
        hallucinated_calls=hallucinated,
        passed=passed,
    )


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #
def render_summary_table(results: list[TaskRunResult]) -> str:
    headers = ["Task", "Variant", "Syntax", "Pytest", "Hallucinations", "Result", "Prompt Tok", "Cost"]
    rows = []
    for r in results:
        cost = f"${r.cost_usd:.5f}" if r.cost_usd is not None else "n/a"
        rows.append(
            [
                r.task_id,
                r.variant,
                "OK" if r.syntax_valid else "FAIL",
                "PASS" if r.pytest_passed else "FAIL",
                str(len(r.hallucinated_calls)),
                "PASS" if r.passed else "FAIL",
                str(r.prompt_tokens),
                cost,
            ]
        )
    return format_table(headers, rows)


def render_detail(results: list[TaskRunResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"--- {r.task_id} [{r.variant}] - {'PASS' if r.passed else 'FAIL'} ---")
        lines.append(f"  {r.title}")
        lines.append(
            f"  syntax_valid={r.syntax_valid}  pytest_passed={r.pytest_passed}  "
            f"hallucinated_calls={list(r.hallucinated_calls)}"
        )
        if r.syntax_error:
            lines.append(f"  syntax error: {r.syntax_error}")
        if r.syntax_valid and not r.pytest_passed:
            lines.append("  pytest output (tail):")
            for line in r.pytest_output_tail.splitlines():
                lines.append(f"    {line}")
        lines.append("")
    return "\n".join(lines)


def render_cost_summary(results: list[TaskRunResult]) -> str:
    if not results:
        return "(no runs)"
    passed = sum(1 for r in results if r.passed)
    lines = [f"Overall: {passed}/{len(results)} passed ({passed / len(results) * 100:.1f}%)"]
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
    if known_costs:
        lines.append(f"Total estimated cost: ${sum(known_costs):.5f}")
    return "\n".join(lines)


def write_report(results: list[TaskRunResult], path: str) -> None:
    payload = {"result_count": len(results), "results": [dataclasses.asdict(r) for r in results]}
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))


def dry_run_preview(tasks: list[AccuracyTask], budget: int) -> str:
    builder, tag_matrix = build_pipeline(str(ACCURACY_REPO))
    lines = [f"(dry run - no API calls made; tokenizer backend: {active_backend()})", ""]
    for task in tasks:
        if task.context_target not in builder.symbol_table or task.patch_target not in builder.symbol_table:
            lines.append(f"=== {task.task_id} === ERROR: target(s) not found in fixture")
            continue
        raw_text, prism_text = build_contexts(builder, tag_matrix, task, budget)
        lines.append(f"=== {task.task_id}: {task.title} ===")
        lines.append(f"  [raw] context={count_tokens(raw_text)} tok")
        lines.append(f"  [prism] context={count_tokens(prism_text)} tok")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.validate_llm_accuracy",
        description="Validate downstream LLM coding accuracy: Prism-sliced vs. raw-dump context, scored by real pytest execution.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model to use (default: {DEFAULT_MODEL}).")
    parser.add_argument(
        "--tasks", default="all", help=f"Comma-separated task ids to run, or 'all' (available: {', '.join(TASKS_BY_ID)})."
    )
    parser.add_argument(
        "--variants", default=",".join(VARIANTS), help=f"Comma-separated context variants (available: {', '.join(VARIANTS)})."
    )
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"Prism token budget (default: {DEFAULT_BUDGET}).")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (default: 0.0).")
    parser.add_argument(
        "--report", default=str(DEFAULT_REPORT_PATH), help=f"Write full results as JSON here (default: {DEFAULT_REPORT_PATH}). Pass '' to skip."
    )
    parser.add_argument("--price-in", type=float, default=None, dest="price_in", help="Override input price (USD per 1M tokens).")
    parser.add_argument("--price-out", type=float, default=None, dest="price_out", help="Override output price (USD per 1M tokens).")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts/contexts and print sizes without calling the API.")
    return parser


def select_tasks(spec: str) -> list[AccuracyTask]:
    if spec == "all":
        return list(TASKS)
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [task_id for task_id in ids if task_id not in TASKS_BY_ID]
    if unknown:
        raise ValueError(f"unknown task id(s): {', '.join(unknown)} (available: {', '.join(TASKS_BY_ID)})")
    return [TASKS_BY_ID[task_id] for task_id in ids]


def select_variants(spec: str) -> list[str]:
    variants = [v.strip() for v in spec.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variant(s): {', '.join(unknown)} (available: {', '.join(VARIANTS)})")
    return variants


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        tasks = select_tasks(args.tasks)
        variants = select_variants(args.variants)
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

    builder, tag_matrix = build_pipeline(str(ACCURACY_REPO))

    results: list[TaskRunResult] = []
    for task in tasks:
        if task.context_target not in builder.symbol_table or task.patch_target not in builder.symbol_table:
            print(f"error: task '{task.task_id}' target(s) not found in the fixture repo", file=sys.stderr)
            return 1
        raw_text, prism_text = build_contexts(builder, tag_matrix, task, args.budget)
        context_by_variant = {"raw": raw_text, "prism": prism_text}
        for variant in variants:
            print(f"Running {task.task_id} [{variant}] with {args.model} ...", file=sys.stderr)
            try:
                results.append(
                    run_task_variant(builder, task, variant, context_by_variant[variant], args.model, client, args.temperature)
                )
            except OpenAIClientError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    print(render_summary_table(results))
    print()
    print(render_detail(results))
    print(render_cost_summary(results))

    if args.report:
        write_report(results, args.report)
        print(f"\nWrote accuracy report to {args.report}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
