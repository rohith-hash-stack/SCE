"""Offline, hermetic tests for the polyglot 33-prompt matrix harness: no
network access and no OpenAI API key required for the default suite. Real
LLM calls are replaced with canned `CallResult`s; the language-specific
mechanical checkers (`check_code_syntax`, `find_hallucinated_calls_generic`)
are exercised directly against small hand-written snippets per language, and
the `run_variant`/`run_archetype` orchestration is exercised end-to-end
against `tests/fixtures/python_repo` (already tracked, no cloning needed)
treated as the "python" leg.

A separate opt-in suite at the bottom confirms all six real REPOS targets
still resolve against their real, already-cloned repositories - skipped
unless `PRISM_LIVE_NETWORK_TESTS=1` is set, mirroring
`tests/test_large_repo_prompt_matrix.py`'s and `tests/test_clone_eval.py`'s
real-network opt-in tests.
"""
from __future__ import annotations

import os

import pytest

from benchmarks.openai_client import CallResult
from benchmarks.polyglot_33_matrix import (
    REPOS,
    build_arg_parser,
    build_contexts,
    build_repo_index,
    check_code_syntax,
    find_hallucinated_calls_generic,
    main,
    run_archetype,
    run_variant,
    select_archetype_ids,
    select_variants,
)
from benchmarks.prompt_taxonomy.polyglot_prompts import PROMPT_TEMPLATES, PROMPT_TEMPLATES_BY_ID
from benchmarks.prompt_taxonomy.spec import PromptArchetype

from prism.cli import build_pipeline

PYTHON_FIXTURE = "tests/fixtures/python_repo"


def _fake_client(content: str) -> object:
    class _FakeClient:
        def complete(self, model, system, user, temperature):
            return CallResult(model=model, content=content, prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.0001, latency_seconds=0.01)

        def complete_conversation(self, model, messages, temperature=0.0, max_tokens=None):
            return CallResult(model=model, content=content, prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.0001, latency_seconds=0.01)

    return _FakeClient()


@pytest.fixture(scope="module")
def builder_and_tags():
    return build_pipeline(PYTHON_FIXTURE)


@pytest.fixture(scope="module")
def repo_index(builder_and_tags):
    builder, _ = builder_and_tags
    return build_repo_index(builder)


# --------------------------------------------------------------------- #
# prompt_taxonomy/polyglot_prompts.py: the 33 templates
# --------------------------------------------------------------------- #
def test_exactly_33_templates_with_unique_ids_and_slugs():
    assert len(PROMPT_TEMPLATES) == 33
    assert {t.archetype_id for t in PROMPT_TEMPLATES} == set(range(1, 34))
    assert len({t.slug for t in PROMPT_TEMPLATES}) == 33


def test_every_template_task_prompt_contains_target_placeholder():
    for template in PROMPT_TEMPLATES:
        assert "{target}" in template.task_prompt_template


def test_instantiate_fills_target_language_and_repo_description():
    template = PROMPT_TEMPLATES_BY_ID[1]
    archetype = template.instantiate("pkg.Foo.bar", "go", "a Go web framework")
    assert isinstance(archetype, PromptArchetype)
    assert archetype.target == "pkg.Foo.bar"
    assert "pkg.Foo.bar" in archetype.task_prompt
    assert "go" in archetype.system_prompt
    assert "a Go web framework" in archetype.system_prompt
    assert archetype.preserve_signature is False


def test_self_consistency_template_samples_more_than_once():
    template = PROMPT_TEMPLATES_BY_ID[14]
    assert template.sample_count > 1


def test_multi_turn_templates_have_follow_up_prompts():
    for archetype_id in (20, 21):
        assert PROMPT_TEMPLATES_BY_ID[archetype_id].follow_up_prompts


def test_instruction_system_template_uses_strict_system_prompt():
    template = PROMPT_TEMPLATES_BY_ID[11]
    archetype = template.instantiate("a.b.c", "python", "a codebase")
    assert "ZERO" in archetype.system_prompt


# --------------------------------------------------------------------- #
# REPOS registry
# --------------------------------------------------------------------- #
def test_repos_registry_covers_all_six_languages():
    languages = {spec.language_id for spec in REPOS.values()}
    assert languages == {"python", "typescript", "javascript", "go", "java", "csharp"}


def test_repos_registry_targets_are_nonempty_qualified_names():
    for spec in REPOS.values():
        assert spec.target
        assert "." in spec.target


# --------------------------------------------------------------------- #
# check_code_syntax: python (ast) + tree-sitter languages
# --------------------------------------------------------------------- #
def test_check_code_syntax_python_valid():
    valid, err = check_code_syntax("def f(x):\n    return x + 1\n", "python")
    assert valid is True
    assert err is None


def test_check_code_syntax_python_invalid():
    valid, err = check_code_syntax("def f(x)\n    return x\n", "python")
    assert valid is False
    assert err is not None


def test_check_code_syntax_go_valid():
    valid, _ = check_code_syntax("func handle(c *Context) {\n\tc.JSON(200, nil)\n}", "go")
    assert valid is True


def test_check_code_syntax_go_invalid():
    valid, err = check_code_syntax("func broken( {", "go")
    assert valid is False
    assert err is not None


def test_check_code_syntax_typescript_valid_uses_class_wrap_fallback():
    # A bare method body (no enclosing class) is only valid wrapped - the
    # same fallback benchmarks/validity.py's own checker relies on.
    valid, _ = check_code_syntax("use(path: string) {\n  compose([])\n}", "typescript")
    assert valid is True


def test_check_code_syntax_java_valid():
    valid, _ = check_code_syntax("public String greet(String name) {\n    return name;\n}", "java")
    assert valid is True


def test_check_code_syntax_csharp_valid():
    valid, _ = check_code_syntax("public IActionResult Foo() {\n    return Ok();\n}", "csharp")
    assert valid is True


def test_check_code_syntax_unsupported_language_is_not_applicable():
    valid, err = check_code_syntax("whatever", "brainfuck")
    assert valid is True
    assert err is None


# --------------------------------------------------------------------- #
# find_hallucinated_calls_generic: regex-based, non-Python languages
# --------------------------------------------------------------------- #
def test_find_hallucinated_calls_generic_go_ignores_own_declaration_name():
    known = frozenset({"redirectTrailingSlash", "JSON"})
    code = "func handle(c *Context) {\n\tc.JSON(200, nil)\n\tredirectTrailingSlash(c)\n}"
    assert find_hallucinated_calls_generic(code, "go", known) == ()


def test_find_hallucinated_calls_generic_go_flags_fabricated_call():
    known = frozenset({"redirectTrailingSlash"})
    code = "func handle(c *Context) {\n\ttotallyFakeHelper(c)\n}"
    result = find_hallucinated_calls_generic(code, "go", known)
    assert "totallyFakeHelper" in result
    assert "handle" not in result  # the function's own name is a declaration, not a call


def test_find_hallucinated_calls_generic_typescript_ignores_declared_function():
    known = frozenset({"compose", "basePath"})
    code = "function use(path: string) {\n  compose([])\n  basePath(path)\n}"
    assert find_hallucinated_calls_generic(code, "typescript", known) == ()


def test_find_hallucinated_calls_generic_java_ignores_declared_method():
    known = frozenset({"isDuplicatePetNameViolation"})
    code = "public String processCreationForm(Owner owner) {\n    return isDuplicatePetNameViolation(owner);\n}"
    assert find_hallucinated_calls_generic(code, "java", known) == ()


def test_find_hallucinated_calls_generic_csharp_ignores_declared_method():
    known = frozenset({"CreateUserInfo"})
    code = "public IActionResult GetCurrentUser() {\n    return CreateUserInfo();\n}"
    assert find_hallucinated_calls_generic(code, "csharp", known) == ()


def test_find_hallucinated_calls_generic_dispatches_python_to_ast_checker():
    known = frozenset({"charge"})
    code = "def f(x):\n    charge(x)\n    len(x)\n"
    assert find_hallucinated_calls_generic(code, "python", known) == ()


# --------------------------------------------------------------------- #
# run_variant / run_archetype end-to-end (canned responses, python fixture)
# --------------------------------------------------------------------- #
def test_run_variant_passes_for_clean_python_response(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Add a docstring.", expects_code=True,
    )
    client = _fake_client("```python\ndef charge(self, amount):\n    return self._gateway.charge(amount)\n```")
    result = run_variant(repo_index, tag_matrix, "python", archetype, "prism", "CTX", "gpt-4o-mini", client, 0.0)
    assert result.syntax_valid is True
    assert result.hallucinated_calls == ()
    assert result.passed is True


def test_run_variant_fails_on_hallucinated_call(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Add a docstring.", expects_code=True,
    )
    client = _fake_client("```python\ndef charge(self, amount):\n    return self._totally_invented_helper(amount)\n```")
    result = run_variant(repo_index, tag_matrix, "python", archetype, "raw", "CTX", "gpt-4o-mini", client, 0.0)
    assert "_totally_invented_helper" in result.hallucinated_calls
    assert result.passed is False


def test_run_variant_enforces_forbidden_tags(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.repositories.orders.OrderRepository.find_open_orders",
        task_prompt="Add logging, but never write to the database.",
        expects_code=True, forbidden_tags=("#db_write",),
    )
    client = _fake_client("```python\ndef find_open_orders(self):\n    self.mark_paid(1)\n    return []\n```")
    result = run_variant(repo_index, tag_matrix, "python", archetype, "raw", "CTX", "gpt-4o-mini", client, 0.0)
    assert result.forbidden_tag_violations == ("mark_paid",)
    assert result.passed is False


def test_run_variant_skips_code_checks_when_expects_code_false(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Explain it.", expects_code=False,
    )
    client = _fake_client("This method charges the customer via the gateway.")
    result = run_variant(repo_index, tag_matrix, "python", archetype, "prism", "CTX", "gpt-4o-mini", client, 0.0)
    assert result.syntax_valid is None
    assert "syntax_valid" not in result.contract_checks
    assert result.passed is True


def test_run_archetype_computes_compression_and_both_variants(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Explain it.", expects_code=False,
    )
    client = _fake_client("This charges the customer.")
    result = run_archetype(builder, tag_matrix, repo_index, "python-fixture", "python", archetype, ["raw", "prism"], 1000, "gpt-4o-mini", client, 0.0)
    assert result.raw is not None
    assert result.prism is not None
    assert result.raw_tokens > 0
    assert result.prism_tokens > 0


def test_build_contexts_returns_nonempty_raw_and_prism_text(builder_and_tags):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(archetype_id=1, slug="x", title="X", cluster="C", target="src.services.billing.PaymentProcessor.charge", task_prompt="x")
    raw_text, prism_text = build_contexts(builder, tag_matrix, archetype, 2000)
    assert raw_text
    assert prism_text


# --------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------- #
def test_select_archetype_ids_all_returns_every_id():
    assert select_archetype_ids("all") == list(range(1, 34))


def test_select_archetype_ids_by_numeric_id_and_slug():
    assert select_archetype_ids("5,informational-query") == [5, 1]


def test_select_archetype_ids_rejects_unknown():
    with pytest.raises(ValueError):
        select_archetype_ids("not-a-real-slug")


def test_select_variants_rejects_unknown():
    with pytest.raises(ValueError):
        select_variants("raw,not-a-variant")


def test_build_arg_parser_requires_repo_or_run_all(capsys):
    parser = build_arg_parser()
    args = parser.parse_args(["--repo", "gin", "--dry-run"])
    assert args.repo == "gin"
    assert args.dry_run is True


def test_main_rejects_unknown_repo_choice():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--repo", "not-a-real-repo"])


def test_main_requires_repo_or_run_all(capsys):
    with pytest.raises(SystemExit):
        main([])


def test_main_unknown_prompt_exits_nonzero_without_indexing(capsys):
    exit_code = main(["--repo", "gin", "--prompts", "999", "--dry-run"])
    assert exit_code == 1
    assert "unknown prompt" in capsys.readouterr().err


# --------------------------------------------------------------------- #
# Opt-in: real network, all six real repositories (PRISM_LIVE_NETWORK_TESTS=1)
# --------------------------------------------------------------------- #
pytestmark_live = pytest.mark.skipif(
    os.environ.get("PRISM_LIVE_NETWORK_TESTS") != "1",
    reason="set PRISM_LIVE_NETWORK_TESTS=1 to run the real multi-repo clone/index suite",
)


@pytestmark_live
@pytest.mark.parametrize("repo_key", sorted(REPOS))
def test_repo_target_resolves_against_real_clone(repo_key):
    from pathlib import Path

    from benchmarks.polyglot_33_matrix import DEFAULT_CACHE_DIR, index_repo

    builder, tag_matrix, metrics = index_repo(repo_key, Path(DEFAULT_CACHE_DIR), force_clone=False)
    spec = REPOS[repo_key]
    assert spec.target in builder.symbol_table
    assert metrics.total_symbols > 0


@pytestmark_live
def test_dry_run_cli_against_real_gin(capsys):
    exit_code = main(["--repo", "gin", "--dry-run", "--prompts", "1,17"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "compression=" in out
