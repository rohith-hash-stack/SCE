"""Offline, hermetic tests for the live-eval harness: no network access and
no OpenAI API key required. Everything that doesn't need a real model call
- task context construction, the AST verifiers, and the CLI's dry-run and
error paths - is covered here.
"""
from __future__ import annotations

import pytest

from benchmarks.live_eval import _select_tasks, _select_variants, dry_run_preview, main
from benchmarks.tasks import ALL_TASKS, build_task_context, check_calls_use_real_symbols, extract_first_code_block


# --------------------------------------------------------------------- #
# extract_first_code_block
# --------------------------------------------------------------------- #
def test_extract_first_code_block_prefers_python_tagged():
    text = "some prose\n```python\nprint('hi')\n```\nmore prose"
    assert extract_first_code_block(text) == "print('hi')"


def test_extract_first_code_block_accepts_untagged_fence():
    text = "```\nx = 1\n```"
    assert extract_first_code_block(text) == "x = 1"


def test_extract_first_code_block_prefers_python_over_other_language():
    text = "```json\n{}\n```\n```python\ndef f(): pass\n```"
    assert extract_first_code_block(text) == "def f(): pass"


def test_extract_first_code_block_returns_none_when_absent():
    assert extract_first_code_block("no fenced code here") is None


# --------------------------------------------------------------------- #
# check_calls_use_real_symbols
# --------------------------------------------------------------------- #
GOOD_RESPONSE = """Diagnosis: missing auth check.

```python
def process_order(self, token, order_id, items):
    require_auth(token)
    self.repo.save({"order_id": order_id})
```"""

HALLUCINATED_RESPONSE = """```python
def process_order(self, token, order_id, items):
    verify_permissions(token)
    self.repo.save({"order_id": order_id})
```"""

MISSING_CALL_RESPONSE = """```python
def process_order(self, token, order_id, items):
    self.repo.save({"order_id": order_id})
```"""

NO_FENCE_RESPONSE = "def process_order(self): pass"

SYNTAX_ERROR_RESPONSE = """```python
def process_order(self, token)
    require_auth(token)
```"""


@pytest.mark.parametrize(
    ("response", "expected_passed"),
    [
        (GOOD_RESPONSE, True),
        (HALLUCINATED_RESPONSE, False),
        (MISSING_CALL_RESPONSE, False),
        (NO_FENCE_RESPONSE, False),
        (SYNTAX_ERROR_RESPONSE, False),
    ],
)
def test_check_calls_use_real_symbols(response, expected_passed):
    result = check_calls_use_real_symbols(
        response,
        required_exact_calls=frozenset({"require_auth"}),
        known_real_simple_names=frozenset({"require_auth", "save"}),
        suspicious_keyword_hints=("auth", "verify", "permission", "guard"),
    )
    assert result.passed is expected_passed


def test_hallucination_check_flags_only_suspicious_names():
    """A call that doesn't resemble any required capability shouldn't be
    flagged just for being unrecognized - only names matching the
    suspicious-keyword hints count as a fabricated stand-in."""
    response = """```python
def f(x):
    require_auth(x)
    some_totally_unrelated_helper(x)
```"""
    result = check_calls_use_real_symbols(
        response,
        required_exact_calls=frozenset({"require_auth"}),
        known_real_simple_names=frozenset({"require_auth"}),
        suspicious_keyword_hints=("auth", "verify", "permission", "guard"),
    )
    assert result.passed is True
    assert result.checks["no_hallucinated_dependency_calls"] is True


# --------------------------------------------------------------------- #
# Task context construction
# --------------------------------------------------------------------- #
def test_bug_localization_context_surfaces_auth_guard_despite_broken_call_graph():
    """The whole point of the task: process_order never calls require_auth
    (that's the bug), so it must still be discoverable in both context
    variants, or the task would be unfair/impossible."""
    task = ALL_TASKS["bug_localization"]
    ctx = build_task_context(task, budget=2000)
    assert "require_auth" in ctx.known_symbols_sce
    assert "require_auth" in ctx.known_symbols_raw
    assert "app.auth.require_auth" in ctx.sce_text


def test_feature_extension_context_includes_all_required_dependencies():
    task = ALL_TASKS["feature_extension"]
    ctx = build_task_context(task, budget=2000)
    for name in ("require_auth", "refund", "publish"):
        assert name in ctx.known_symbols_sce
        assert name in ctx.known_symbols_raw


def test_sce_context_is_smaller_than_raw_context_for_both_tasks():
    from benchmarks.tokenizer import count_tokens

    for task in ALL_TASKS.values():
        ctx = build_task_context(task, budget=2000)
        assert count_tokens(ctx.sce_text) < count_tokens(ctx.raw_text)


# --------------------------------------------------------------------- #
# CLI: dry-run and argument validation (no network/API key needed)
# --------------------------------------------------------------------- #
def test_dry_run_preview_reports_both_variants_for_every_task():
    output = dry_run_preview(list(ALL_TASKS.values()), budget=2000)
    for task in ALL_TASKS.values():
        assert task.task_id in output
    assert "[raw]" in output
    assert "[sce]" in output


def test_select_tasks_all_returns_every_task():
    assert set(t.task_id for t in _select_tasks("all")) == set(ALL_TASKS)


def test_select_tasks_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown task id"):
        _select_tasks("not_a_real_task")


def test_select_variants_rejects_unknown_variant():
    with pytest.raises(ValueError, match="unknown variant"):
        _select_variants("not_a_real_variant")


def test_main_dry_run_exits_zero(capsys):
    exit_code = main(["--dry-run", "--tasks", "all"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "bug_localization" in captured.out
    assert "feature_extension" in captured.out


def test_main_unknown_task_exits_nonzero(capsys):
    exit_code = main(["--tasks", "nonexistent", "--dry-run"])
    assert exit_code == 1


def test_main_missing_api_key_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    exit_code = main(["--tasks", "bug_localization"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY is not set" in captured.err
