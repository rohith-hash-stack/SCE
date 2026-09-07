"""Offline, hermetic tests for the LLM accuracy validation harness: no
network access and no OpenAI API key required. Real LLM calls are replaced
with canned `CallResult`s; everything else - patching, sandbox execution,
hallucination detection, and the CLI's dry-run/error paths - runs for real
against the actual `benchmarks/fixtures/accuracy_repo` fixture and a real
`pytest` subprocess, exactly as the live harness does.
"""
from __future__ import annotations

import pytest

from benchmarks.openai_client import CallResult
from benchmarks.validate_llm_accuracy import (
    TASKS_BY_ID,
    apply_patch,
    build_arg_parser,
    dry_run_preview,
    find_hallucinated_calls,
    main,
    run_in_sandbox,
    run_task_variant,
    select_tasks,
    select_variants,
)

from prism.cli import build_pipeline


@pytest.fixture(scope="module")
def builder_and_tags():
    from benchmarks.validate_llm_accuracy import ACCURACY_REPO as REPO_PATH

    return build_pipeline(str(REPO_PATH))


def _fake_client(content: str) -> object:
    """A minimal stand-in for `LLMClient`: only `complete()` is ever called
    by `run_task_variant`, so nothing else needs to exist."""

    class _FakeClient:
        def complete(self, model, system, user, temperature):
            return CallResult(
                model=model,
                content=content,
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                cost_usd=0.0001,
                latency_seconds=0.01,
            )

    return _FakeClient()


# --------------------------------------------------------------------- #
# apply_patch
# --------------------------------------------------------------------- #
def test_apply_patch_reindents_flush_left_code():
    original = "class C:\n    def f(self):\n        return 1\n"
    patched = apply_patch(original, (2, 3), "def f(self):\n    return 2\n")
    assert patched == "class C:\n    def f(self):\n        return 2\n"


def test_apply_patch_handles_already_indented_code():
    original = "class C:\n    def f(self):\n        return 1\n"
    patched = apply_patch(original, (2, 3), "    def f(self):\n        return 2\n")
    assert patched == "class C:\n    def f(self):\n        return 2\n"


# --------------------------------------------------------------------- #
# find_hallucinated_calls
# --------------------------------------------------------------------- #
def test_find_hallucinated_calls_accepts_real_symbols(builder_and_tags):
    builder, _ = builder_and_tags
    code = "def checkout_order(self, token, order_id, items):\n    verify_session(token)\n    self.repo.save({})\n"
    assert find_hallucinated_calls(code, builder) == ()


def test_find_hallucinated_calls_flags_fabricated_method(builder_and_tags):
    builder, _ = builder_and_tags
    code = "def refund_transaction(self, order_id, amount):\n    return self.gateway.refund(order_id, amount)\n"
    assert "refund" in find_hallucinated_calls(code, builder)


def test_find_hallucinated_calls_ignores_builtins_and_common_stdlib_methods(builder_and_tags):
    builder, _ = builder_and_tags
    code = "def f(payload):\n    x = payload.get('amount')\n    return len(str(x))\n"
    assert find_hallucinated_calls(code, builder) == ()


# --------------------------------------------------------------------- #
# run_in_sandbox: real pytest execution against real correct/buggy patches
# --------------------------------------------------------------------- #
CORRECT_CHECKOUT_ORDER = """def checkout_order(self, token, order_id, items):
    verify_session(token)
    order = {"order_id": order_id, "items": items, "status": "PENDING"}
    order["status"] = "PAID"
    self.repo.save(order)
    return order
"""

BUGGY_CHECKOUT_ORDER_NO_AUTH = """def checkout_order(self, token, order_id, items):
    order = {"order_id": order_id, "items": items, "status": "PENDING"}
    order["status"] = "PAID"
    self.repo.save(order)
    return order
"""

CORRECT_REFUND_TRANSACTION = """def refund_transaction(self, order_id, amount):
    refund_id = self.gateway.reverse_charge(order_id, amount)
    order = {"order_id": order_id, "status": "REFUNDED", "refund_id": refund_id}
    self.repo.save(order)
    return order
"""

HALLUCINATED_REFUND_TRANSACTION = """def refund_transaction(self, order_id, amount):
    refund_id = self.gateway.refund(order_id, amount)
    order = {"order_id": order_id, "status": "REFUNDED", "refund_id": refund_id}
    self.repo.save(order)
    return order
"""

CORRECT_PROCESS_PAYLOAD = """def process_payload(payload):
    try:
        amount = _prepare_payload(payload)
        charge_customer(payload, amount)
        return {"status": "ok", "amount": amount}
    except PayloadValidationError:
        return {"error": "invalid payload"}
"""


def test_run_in_sandbox_passes_for_correct_checkout_order_patch(builder_and_tags):
    builder, _ = builder_and_tags
    task = TASKS_BY_ID["missing_invariant"]
    passed, returncode, _tail = run_in_sandbox(builder, task, CORRECT_CHECKOUT_ORDER)
    assert passed is True
    assert returncode == 0


def test_run_in_sandbox_fails_for_missing_auth_check(builder_and_tags):
    builder, _ = builder_and_tags
    task = TASKS_BY_ID["missing_invariant"]
    passed, returncode, _tail = run_in_sandbox(builder, task, BUGGY_CHECKOUT_ORDER_NO_AUTH)
    assert passed is False
    assert returncode != 0


def test_run_in_sandbox_passes_for_correct_refund_transaction(builder_and_tags):
    builder, _ = builder_and_tags
    task = TASKS_BY_ID["interface_conformance"]
    passed, _returncode, _tail = run_in_sandbox(builder, task, CORRECT_REFUND_TRANSACTION)
    assert passed is True


def test_run_in_sandbox_fails_for_hallucinated_gateway_method(builder_and_tags):
    """The hallucinated `.refund(...)` call doesn't exist on the gateway
    test double, so it raises AttributeError at runtime - a real, functional
    hallucination check via execution, independent of the static AST one."""
    builder, _ = builder_and_tags
    task = TASKS_BY_ID["interface_conformance"]
    passed, _returncode, tail = run_in_sandbox(builder, task, HALLUCINATED_REFUND_TRANSACTION)
    assert passed is False
    assert "AttributeError" in tail


def test_run_in_sandbox_passes_for_correct_process_payload_fix(builder_and_tags):
    builder, _ = builder_and_tags
    task = TASKS_BY_ID["cross_file_control_flow"]
    passed, _returncode, _tail = run_in_sandbox(builder, task, CORRECT_PROCESS_PAYLOAD)
    assert passed is True


# --------------------------------------------------------------------- #
# run_task_variant: end-to-end with a canned LLM response (no network)
# --------------------------------------------------------------------- #
def test_run_task_variant_passes_for_correct_canned_response(builder_and_tags):
    builder, tag_matrix = builder_and_tags
    task = TASKS_BY_ID["missing_invariant"]
    client = _fake_client(f"Here is the fix:\n```python\n{CORRECT_CHECKOUT_ORDER}```")
    result = run_task_variant(builder, task, "prism", "irrelevant context text", "gpt-4o-mini", client, 0.0)
    assert result.syntax_valid is True
    assert result.pytest_passed is True
    assert result.hallucinated_calls == ()
    assert result.passed is True


def test_run_task_variant_fails_and_flags_hallucination_for_fabricated_method(builder_and_tags):
    builder, tag_matrix = builder_and_tags
    task = TASKS_BY_ID["interface_conformance"]
    client = _fake_client(f"```python\n{HALLUCINATED_REFUND_TRANSACTION}```")
    result = run_task_variant(builder, task, "raw", "irrelevant context text", "gpt-4o-mini", client, 0.0)
    assert result.syntax_valid is True
    assert "refund" in result.hallucinated_calls
    assert result.passed is False


def test_run_task_variant_reports_syntax_error_without_running_pytest(builder_and_tags):
    builder, tag_matrix = builder_and_tags
    task = TASKS_BY_ID["missing_invariant"]
    client = _fake_client("```python\ndef checkout_order(self, token\n    pass\n```")
    result = run_task_variant(builder, task, "prism", "irrelevant context text", "gpt-4o-mini", client, 0.0)
    assert result.syntax_valid is False
    assert result.syntax_error is not None
    assert result.pytest_passed is False
    assert result.passed is False


def test_run_task_variant_handles_missing_code_block(builder_and_tags):
    builder, tag_matrix = builder_and_tags
    task = TASKS_BY_ID["missing_invariant"]
    client = _fake_client("I refuse to write code.")
    result = run_task_variant(builder, task, "prism", "irrelevant context text", "gpt-4o-mini", client, 0.0)
    assert result.extracted_code is None
    assert result.syntax_valid is False
    assert result.passed is False


# --------------------------------------------------------------------- #
# select_tasks / select_variants
# --------------------------------------------------------------------- #
def test_select_tasks_all_returns_every_task():
    assert {t.task_id for t in select_tasks("all")} == set(TASKS_BY_ID)


def test_select_tasks_filters_by_id():
    tasks = select_tasks("missing_invariant,cross_file_control_flow")
    assert [t.task_id for t in tasks] == ["missing_invariant", "cross_file_control_flow"]


def test_select_tasks_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown task id"):
        select_tasks("not_a_real_task")


def test_select_variants_rejects_unknown_variant():
    with pytest.raises(ValueError, match="unknown variant"):
        select_variants("not_a_real_variant")


def test_select_variants_accepts_single_variant():
    assert select_variants("prism") == ["prism"]


# --------------------------------------------------------------------- #
# CLI: dry-run and error paths (no network/API key needed)
# --------------------------------------------------------------------- #
def test_dry_run_preview_reports_both_variants_for_every_task():
    output = dry_run_preview(list(TASKS_BY_ID.values()), budget=2000)
    for task_id in TASKS_BY_ID:
        assert task_id in output
    assert "[raw]" in output
    assert "[prism]" in output


def test_main_dry_run_exits_zero(capsys):
    exit_code = main(["--dry-run", "--tasks", "all"])
    assert exit_code == 0
    captured = capsys.readouterr()
    for task_id in TASKS_BY_ID:
        assert task_id in captured.out


def test_main_unknown_task_exits_nonzero(capsys):
    exit_code = main(["--tasks", "nonexistent", "--dry-run"])
    assert exit_code == 1


def test_main_missing_api_key_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    exit_code = main(["--tasks", "missing_invariant"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY is not set" in captured.err


def test_build_arg_parser_defaults():
    args = build_arg_parser().parse_args([])
    assert args.model == "gpt-4o-mini"
    assert args.tasks == "all"
    assert args.budget == 2000
    assert args.temperature == 0.0
