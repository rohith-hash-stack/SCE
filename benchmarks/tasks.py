"""Live-eval task definitions and their automated AST verifiers.

Each `Task` pins an "anchor" symbol in `benchmarks/fixtures/task_repo` that
SCE (and the raw-dump baseline) build a context package around, plus a
prompt asking the model to produce a function/method that must call real,
specific dependency symbols. The verifier is a static AST check - it never
asks another LLM to grade the answer - so scoring is deterministic and
free: did the patch parse, did it call the dependency it needed to, and did
it avoid inventing a plausible-looking method name that doesn't exist in
the context it was actually given.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
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
from sce.graph.metamodel import SemanticMetamodel  # noqa: E402
from sce.serializers.markdown import render_markdown  # noqa: E402
from sce.slicer.distance import DistanceConfig, DistanceEngine  # noqa: E402
from sce.slicer.knapsack import ContextKnapsackPacker  # noqa: E402

from benchmarks.raw_context import build_raw_context, dump_files  # noqa: E402

TASK_REPO = PROJECT_ROOT / "benchmarks" / "fixtures" / "task_repo"


class TaskSetupError(Exception):
    """Raised when a task's anchor symbol can't be found/packed."""


# --------------------------------------------------------------------- #
# Verifier
# --------------------------------------------------------------------- #
_CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)\n```", re.DOTALL)
_PREFERRED_FENCE_LANGS = {"python", "py", "python3", ""}


def extract_first_code_block(text: str) -> str | None:
    """Pull the most likely Python snippet out of a freeform LLM response:
    prefer a fenced block explicitly tagged (or untagged) as Python, else
    fall back to the first fenced block of any kind.
    """
    matches = _CODE_FENCE_RE.findall(text)
    if not matches:
        return None
    for lang, code in matches:
        if lang.lower() in _PREFERRED_FENCE_LANGS:
            return code
    return matches[0][1]


def _collect_call_simple_names(tree: ast.AST) -> set[str]:
    """Every call in `tree`, reduced to its trailing identifier
    (`self.billing.refund(...)` -> `refund`, `require_auth(...)` ->
    `require_auth`) - the granularity that matters for detecting a
    hallucinated method/function name.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@dataclass(frozen=True)
class VerifierResult:
    passed: bool
    checks: dict[str, bool]
    notes: list[str]
    extracted_code: str | None


def check_calls_use_real_symbols(
    response_text: str,
    required_exact_calls: frozenset[str],
    known_real_simple_names: frozenset[str],
    suspicious_keyword_hints: tuple[str, ...],
) -> VerifierResult:
    """The shared verifier for both tasks:

      1. a fenced code block must be present,
      2. it must parse (`ast.parse`),
      3. every call in `required_exact_calls` must actually be invoked, and
      4. no call whose name merely *resembles* one of the required
         capabilities (matched by `suspicious_keyword_hints`) may be a name
         absent from `known_real_simple_names` - that's a fabricated
         helper standing in for the real one.
    """
    code = extract_first_code_block(response_text)
    if code is None:
        return VerifierResult(
            passed=False,
            checks={"has_code_block": False, "parses": False, "invokes_required_calls": False, "no_hallucinated_dependency_calls": False},
            notes=["no fenced code block found in the model's response"],
            extracted_code=None,
        )

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return VerifierResult(
            passed=False,
            checks={"has_code_block": True, "parses": False, "invokes_required_calls": False, "no_hallucinated_dependency_calls": False},
            notes=[f"SyntaxError: {exc.msg} (line {exc.lineno})"],
            extracted_code=code,
        )

    called_names = _collect_call_simple_names(tree)
    missing_required = required_exact_calls - called_names
    hallucinated = {
        name
        for name in called_names
        if name not in known_real_simple_names and any(hint in name.lower() for hint in suspicious_keyword_hints)
    }

    checks = {
        "has_code_block": True,
        "parses": True,
        "invokes_required_calls": not missing_required,
        "no_hallucinated_dependency_calls": not hallucinated,
    }
    notes = []
    if missing_required:
        notes.append(f"missing required call(s): {sorted(missing_required)}")
    if hallucinated:
        notes.append(f"hallucinated/fabricated call(s) not present in the given context: {sorted(hallucinated)}")

    return VerifierResult(passed=all(checks.values()), checks=checks, notes=notes, extracted_code=code)


# --------------------------------------------------------------------- #
# Task + context package
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    repo_path: Path
    anchor_target: str
    system_prompt: str
    user_prompt_template: str
    required_calls: frozenset[str]
    suspicious_keyword_hints: tuple[str, ...]
    broken_snippet: str | None = None
    # "call_chain" (default): the raw baseline dumps the same call-chain
    # closure benchmarks/raw_context.py uses elsewhere. "whole_repo": dump
    # every file in the fixture instead - needed when the fix requires a
    # symbol that the (buggy) code never actually calls, so it would
    # otherwise be invisible to a call-chain-scoped baseline too (see
    # bug_localization_task below).
    raw_scope: str = "call_chain"

    def build_prompt(self, context_text: str) -> str:
        return self.user_prompt_template.format(context=context_text, broken_snippet=self.broken_snippet or "")

    def verify(self, response_text: str, known_real_simple_names: frozenset[str]) -> VerifierResult:
        return check_calls_use_real_symbols(
            response_text, self.required_calls, known_real_simple_names, self.suspicious_keyword_hints
        )


@dataclass(frozen=True)
class TaskContext:
    task: Task
    raw_text: str
    sce_text: str
    known_symbols_raw: frozenset[str]
    known_symbols_sce: frozenset[str]

    def context_for(self, variant: str) -> str:
        if variant == "raw":
            return self.raw_text
        if variant == "sce":
            return self.sce_text
        raise ValueError(f"unknown context variant: {variant!r} (expected 'raw' or 'sce')")

    def known_symbols_for(self, variant: str) -> frozenset[str]:
        if variant == "raw":
            return self.known_symbols_raw
        if variant == "sce":
            return self.known_symbols_sce
        raise ValueError(f"unknown context variant: {variant!r} (expected 'raw' or 'sce')")


def _whole_repo_files(builder) -> tuple[str, ...]:
    return tuple(sorted({symbol.file for symbol in builder.symbol_table}))


def _known_simple_names_for_files(builder, files: set[str]) -> frozenset[str]:
    """Every symbol's simple name whose home file is fully dumped - the raw
    baseline's "vocabulary": since the whole file is verbatim in the
    context, every def/class it contains is trivially visible to the model.
    """
    return frozenset(symbol.qualified_name.rsplit(".", 1)[-1] for symbol in builder.symbol_table if symbol.file in files)


def _known_simple_names_mentioned_in(builder, text: str) -> frozenset[str]:
    """Every real qualified symbol whose *fully-qualified* name literally
    appears in `text`, reduced to its simple name. Works for SCE's rendered
    Markdown specifically, since it always spells out fully-qualified
    dotted names (in contract headings and architectural-path lines alike)
    - unlike a raw source file, which never contains a symbol's synthetic
    dotted path as literal text. This is the actual "vocabulary" a model
    reading the SCE package could draw on without inventing anything: it
    covers both fully-rendered contract blocks and symbols that only
    appear as an architectural-path annotation (e.g. "requires ->
    [#auth_guard] app.auth.require_auth", which names a dependency without
    giving it its own code block).
    """
    return frozenset(
        qname.rsplit(".", 1)[-1] for qname in builder.symbol_table.all_qualified_names() if qname in text
    )


def build_task_context(task: Task, budget: int, lambda_weight: float = 0.7) -> TaskContext:
    """Build both the raw-dump and SCE-sliced context packages for a task's
    anchor symbol, plus the set of "real" simple names visible in each -
    the ground truth the verifier checks generated calls against.
    """
    builder, tag_matrix = build_pipeline(str(task.repo_path))
    if task.anchor_target not in builder.symbol_table:
        raise TaskSetupError(f"anchor target '{task.anchor_target}' was not found in {task.repo_path}")

    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(task.anchor_target, builder, tag_matrix, distance_engine)
    sce_text = render_markdown(pack_result, tag_matrix)
    known_symbols_sce = _known_simple_names_mentioned_in(builder, sce_text)

    if task.raw_scope == "whole_repo":
        raw_files = _whole_repo_files(builder)
    else:
        raw_files = build_raw_context(builder, task.anchor_target).files
    raw_text = dump_files(builder, raw_files)
    known_symbols_raw = _known_simple_names_for_files(builder, set(raw_files))

    return TaskContext(
        task=task,
        raw_text=raw_text,
        sce_text=sce_text,
        known_symbols_raw=known_symbols_raw,
        known_symbols_sce=known_symbols_sce,
    )


# --------------------------------------------------------------------- #
# Task 1: Bug Localization & Invariant Fix
# --------------------------------------------------------------------- #
BROKEN_PROCESS_ORDER = '''def process_order(self, token, order_id, items):
    order = {"order_id": order_id, "items": items, "status": "PENDING"}
    order["status"] = "PAID"
    self.repo.save(order)
    return order'''

_BUG_LOCALIZATION_SYSTEM_PROMPT = (
    "You are a senior software engineer performing a security-focused code review. "
    "You will be given a context package describing part of a codebase - either full source "
    "files or a variable-resolution slice with interface contracts for less-relevant code - "
    "plus a task. Use ONLY the symbols, function names, and signatures that literally appear "
    "in the given context; never invent a helper function, method, or symbol that is not "
    "shown. Respond with a one-sentence diagnosis, then a single fenced Python code block "
    "containing just the corrected function."
)

_BUG_LOCALIZATION_USER_TEMPLATE = """CONTEXT PACKAGE:
{context}

TASK:
The function `process_order` has a security defect confirmed in a production incident: it \
writes a new order to the database (a call that reaches `OrderRepository.save`) without any \
authentication check beforehand, even though this codebase's own architectural rules require \
an auth guard before any database write.

Current (buggy) implementation of `app.orders.OrderService.process_order`:
```python
{broken_snippet}
```

Identify the architectural issue in one sentence, then provide the corrected implementation \
of `process_order` as a single Python code block. Call the exact real authentication helper \
shown in the context above - do not invent a new one or guess at its name."""


def bug_localization_task() -> Task:
    return Task(
        task_id="bug_localization",
        title="Bug Localization & Invariant Fix",
        repo_path=TASK_REPO,
        anchor_target="app.orders.OrderService.process_order",
        system_prompt=_BUG_LOCALIZATION_SYSTEM_PROMPT,
        user_prompt_template=_BUG_LOCALIZATION_USER_TEMPLATE,
        required_calls=frozenset({"require_auth"}),
        suspicious_keyword_hints=("auth", "verify", "permission", "guard", "authoriz"),
        broken_snippet=BROKEN_PROCESS_ORDER,
        # process_order (the buggy code) never calls require_auth - that's
        # the bug. A call-chain-scoped raw dump would therefore never
        # include app/auth.py either, making the fix undiscoverable for
        # *both* variants. SCE still surfaces it via the metamodel's
        # #db_write REQUIRES_BEFORE #auth_guard architectural-path
        # annotation regardless of the literal call graph; the raw
        # baseline needs the whole repo to have an equally fair shot.
        raw_scope="whole_repo",
    )


# --------------------------------------------------------------------- #
# Task 2: Feature Extension / Interface Call
# --------------------------------------------------------------------- #
_FEATURE_EXTENSION_SYSTEM_PROMPT = (
    "You are a senior software engineer implementing a new feature in an existing codebase. "
    "You will be given a context package - either full source files or a variable-resolution "
    "slice with interface contracts for less-relevant code - plus a task. Use ONLY the exact "
    "symbols, method names, and signatures that literally appear in the given context; never "
    "invent a method name or guess at a signature that isn't shown. Respond with a single "
    "fenced Python code block containing just the requested method."
)

_FEATURE_EXTENSION_USER_TEMPLATE = """CONTEXT PACKAGE:
{context}

TASK:
Implement a new method `cancel_and_refund(self, token, order_id, reason, amount)` on \
`OrderService`, following the same dependency-usage patterns as `process_return` shown above. \
It must:
  1. Authenticate the request the same way `process_return` does.
  2. Issue a refund through the billing service.
  3. Publish an event (topic "order.cancelled") announcing the cancellation through the event bus.

Return only the new method as a single Python code block. Use only the exact dependency \
methods and signatures shown in the context above - do not invent new ones or guess at \
signatures."""


def feature_extension_task() -> Task:
    return Task(
        task_id="feature_extension",
        title="Feature Extension / Interface Call",
        repo_path=TASK_REPO,
        anchor_target="app.orders.OrderService.process_return",
        system_prompt=_FEATURE_EXTENSION_SYSTEM_PROMPT,
        user_prompt_template=_FEATURE_EXTENSION_USER_TEMPLATE,
        required_calls=frozenset({"require_auth", "refund", "publish"}),
        suspicious_keyword_hints=("refund", "pay", "publish", "emit", "event", "bill"),
    )


ALL_TASKS: dict[str, Task] = {task.task_id: task for task in (bug_localization_task(), feature_extension_task())}
