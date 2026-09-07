"""Offline, hermetic tests for the large-repo prompt-matrix harness: no
network access and no OpenAI API key required for the default suite. Real
LLM calls are replaced with canned `CallResult`s; everything else -
context building, the mechanical scoring helpers, and the CLI's fast-fail
paths - runs for real against `tests/fixtures/python_repo` (a small,
already-tracked fixture, so no cloning is needed for these).

A separate opt-in suite at the bottom actually indexes the real
django/django clone (reusing the cache at `.benchmarks/clones/django` if
present) to confirm the 33 real taxonomy archetypes' target symbols still
exist upstream - skipped unless `PRISM_LIVE_NETWORK_TESTS=1` is set, mirroring
`tests/test_clone_eval.py`'s real-network opt-in test.
"""
from __future__ import annotations

import os

import pytest

from benchmarks.large_repo_prompt_matrix import (
    REPOS,
    ArchetypeRunResult,
    _module_path_for_file,
    build_arg_parser,
    build_contexts,
    build_repo_index,
    main,
    measure_peak_memory_mb,
    read_original_signature,
    render_index_metrics,
    run_archetype,
    run_variant,
    select_archetypes,
    select_variants,
)
from benchmarks.openai_client import CallResult
from benchmarks.prompt_taxonomy import ARCHETYPES, ARCHETYPES_BY_ID, PromptArchetype
from benchmarks.prompt_taxonomy.spec import (
    build_user_prompt,
    check_negative_constraints,
    check_output_format,
    check_required_substrings,
    check_self_consistency,
    check_signature_preserved,
    extract_first_code_block,
    find_forbidden_tag_violations,
    find_hallucinated_calls,
    find_referenced_symbol_mentions,
    function_signature,
)

from prism.cli import build_pipeline

PYTHON_FIXTURE = "tests/fixtures/python_repo"


def _fake_client(content: str) -> object:
    class _FakeClient:
        def complete(self, model, system, user, temperature):
            return CallResult(
                model=model, content=content, prompt_tokens=100, completion_tokens=20,
                total_tokens=120, cost_usd=0.0001, latency_seconds=0.01,
            )

        def complete_conversation(self, model, messages, temperature=0.0, max_tokens=None):
            return CallResult(
                model=model, content=content, prompt_tokens=100, completion_tokens=20,
                total_tokens=120, cost_usd=0.0001, latency_seconds=0.01,
            )

    return _FakeClient()


def _sequenced_client(contents: list[str]) -> object:
    """A fake client that returns a different canned response each call, in
    order - for exercising multi-turn/self-consistency archetypes where a
    single fixed response wouldn't be representative."""
    calls = {"i": 0}

    class _SequencedClient:
        def complete(self, model, system, user, temperature):
            return self.complete_conversation(model, [], temperature)

        def complete_conversation(self, model, messages, temperature=0.0, max_tokens=None):
            content = contents[min(calls["i"], len(contents) - 1)]
            calls["i"] += 1
            return CallResult(
                model=model, content=content, prompt_tokens=80, completion_tokens=15,
                total_tokens=95, cost_usd=0.00005, latency_seconds=0.01,
            )

    return _SequencedClient()


@pytest.fixture(scope="module")
def builder_and_tags():
    return build_pipeline(PYTHON_FIXTURE)


@pytest.fixture(scope="module")
def repo_index(builder_and_tags):
    builder, _ = builder_and_tags
    return build_repo_index(builder)


# --------------------------------------------------------------------- #
# spec.py: prompt construction
# --------------------------------------------------------------------- #
def test_build_user_prompt_includes_context_and_task():
    archetype = PromptArchetype(archetype_id=1, slug="x", title="X", cluster="C", target="a.b.c", task_prompt="Do the thing.")
    prompt = build_user_prompt(archetype, "CONTEXT HERE")
    assert "CONTEXT HERE" in prompt
    assert "Do the thing." in prompt


def test_build_user_prompt_includes_numbered_few_shot_examples():
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C", target="a.b.c", task_prompt="Do it.",
        few_shot_examples=("demo one", "demo two"),
    )
    prompt = build_user_prompt(archetype, "CTX")
    assert "Demonstration 1:\ndemo one" in prompt
    assert "Demonstration 2:\ndemo two" in prompt


# --------------------------------------------------------------------- #
# spec.py: extract_first_code_block
# --------------------------------------------------------------------- #
def test_extract_first_code_block_prefers_python():
    text = "prose\n```json\n{}\n```\n```python\ndef f(): pass\n```"
    assert extract_first_code_block(text) == "def f(): pass"


def test_extract_first_code_block_returns_none_when_absent():
    assert extract_first_code_block("no code here") is None


# --------------------------------------------------------------------- #
# spec.py: find_hallucinated_calls
# --------------------------------------------------------------------- #
def test_find_hallucinated_calls_accepts_known_and_builtin_names():
    known = frozenset({"charge", "verify"})
    code = "def f(x):\n    verify(x)\n    len(x)\n    logging.getLogger('x').debug('hi')\n"
    assert find_hallucinated_calls(code, known) == ()


def test_find_hallucinated_calls_flags_fabricated_name():
    known = frozenset({"charge"})
    code = "def f():\n    return totally_made_up_helper()\n"
    assert "totally_made_up_helper" in find_hallucinated_calls(code, known)


# --------------------------------------------------------------------- #
# spec.py: find_forbidden_tag_violations
# --------------------------------------------------------------------- #
def test_find_forbidden_tag_violations_flags_call_matching_forbidden_tag(builder_and_tags):
    _builder, tag_matrix = builder_and_tags
    simple_to_qualified = {"mark_paid": {"src.repositories.orders.OrderRepository.mark_paid"}}
    code = "def f(repo, order_id):\n    return repo.mark_paid(order_id)\n"
    violations = find_forbidden_tag_violations(code, ("#db_write",), tag_matrix, simple_to_qualified)
    assert violations == ("mark_paid",)


def test_find_forbidden_tag_violations_empty_when_no_forbidden_tags_declared(builder_and_tags):
    _builder, tag_matrix = builder_and_tags
    code = "def f(repo, order_id):\n    return repo.mark_paid(order_id)\n"
    assert find_forbidden_tag_violations(code, (), tag_matrix, {}) == ()


def test_find_forbidden_tag_violations_ignores_unrelated_tags(builder_and_tags):
    _builder, tag_matrix = builder_and_tags
    simple_to_qualified = {"charge": {"src.services.billing.PaymentProcessor.charge"}}
    code = "def f(gw):\n    return gw.charge(10)\n"
    assert find_forbidden_tag_violations(code, ("#db_write",), tag_matrix, simple_to_qualified) == ()


# --------------------------------------------------------------------- #
# spec.py: find_referenced_symbol_mentions (incl. the module-path fix)
# --------------------------------------------------------------------- #
def test_find_referenced_symbol_mentions_accepts_real_qualified_name():
    known = frozenset({"src.repositories.orders.OrderRepository.mark_paid"})
    text = "The write happens in src.repositories.orders.OrderRepository.mark_paid."
    mentioned, unknown = find_referenced_symbol_mentions(text, known, ("src.",))
    assert "src.repositories.orders.OrderRepository.mark_paid" in mentioned
    assert unknown == ()


def test_find_referenced_symbol_mentions_flags_fabricated_qualified_name():
    known = frozenset({"src.repositories.orders.OrderRepository.mark_paid"})
    text = "This is handled by src.repositories.orders.OrderRepository.finalize_and_archive."
    _mentioned, unknown = find_referenced_symbol_mentions(text, known, ("src.",))
    assert "src.repositories.orders.OrderRepository.finalize_and_archive" in unknown


def test_find_referenced_symbol_mentions_accepts_known_module_path():
    """A model legitimately talks about a *module*, not just a leaf symbol
    (e.g. "handled in src.repositories.orders") - confirmed as a real false
    positive from a live run against django/django (archetype 29's Prism
    variant referenced `django.contrib.auth.backends` on its own)."""
    known_qualified = frozenset({"src.repositories.orders.OrderRepository.mark_paid"})
    known_modules = frozenset({"src", "src.repositories", "src.repositories.orders"})
    text = "This logic lives in src.repositories.orders, not src.repositories.fake_module."
    _mentioned, unknown = find_referenced_symbol_mentions(text, known_qualified, ("src.",), known_modules)
    assert "src.repositories.orders" not in unknown
    assert "src.repositories.fake_module" in unknown


def test_find_referenced_symbol_mentions_ignores_names_outside_root_prefixes():
    known = frozenset({"src.repositories.orders.OrderRepository.mark_paid"})
    text = "This resembles some.other.package.Thing.method but that's unrelated."
    mentioned, unknown = find_referenced_symbol_mentions(text, known, ("src.",))
    assert mentioned == ()
    assert unknown == ()


# --------------------------------------------------------------------- #
# spec.py: signature preservation
# --------------------------------------------------------------------- #
def test_check_signature_preserved_true_for_matching_signature():
    original = "authenticate(self, request, username=None, password=None)"
    code = "def authenticate(self, request, username=None, password=None):\n    return None\n"
    assert check_signature_preserved(code, original) is True


def test_check_signature_preserved_false_when_params_change():
    original = "authenticate(self, request, username=None, password=None)"
    code = "def authenticate(self, request, username=None):\n    return None\n"
    assert check_signature_preserved(code, original) is False


def test_function_signature_normalizes_via_ast_unparse():
    import ast

    tree = ast.parse("def f(a,   b = 1 ):\n    pass\n")
    node = tree.body[0]
    assert function_signature(node) == "f(a, b=1)"


# --------------------------------------------------------------------- #
# spec.py: output format / negative constraints / required substrings
# --------------------------------------------------------------------- #
def test_check_output_format_json_valid():
    text = 'Here you go:\n```json\n{"a": 1}\n```'
    assert check_output_format(text, "json") is True


def test_check_output_format_json_invalid():
    text = "```json\n{not valid json\n```"
    assert check_output_format(text, "json") is False


def test_check_output_format_none_when_not_declared():
    assert check_output_format("anything", None) is None


def test_check_negative_constraints_detects_violation():
    violated = check_negative_constraints("cursor.execute('DROP TABLE x')", ("DROP TABLE",))
    assert violated == ("DROP TABLE",)


def test_check_negative_constraints_case_insensitive_and_clean():
    assert check_negative_constraints("SELECT 1", ("drop table",)) == ()


def test_check_required_substrings_any_all_and_regex():
    checks = check_required_substrings(
        "Strategy 1: cache. Strategy 2: batch. Does this work? yes.",
        required_any=("yes", "no"),
        required_all=("strategy 1", "strategy 2"),
        required_regexes=(r"\?",),
    )
    assert checks == {
        "required_any_substrings_present": True,
        "required_all_substrings_present": True,
        "required_regexes_matched": True,
    }


def test_check_required_substrings_all_fails_when_one_missing():
    checks = check_required_substrings("Strategy 1 only.", required_any=(), required_all=("strategy 1", "strategy 2"), required_regexes=())
    assert checks["required_all_substrings_present"] is False


# --------------------------------------------------------------------- #
# spec.py: self-consistency
# --------------------------------------------------------------------- #
def test_check_self_consistency_reaches_consensus_on_majority_mention():
    known = frozenset({"src.repositories.orders.OrderRepository.mark_paid"})
    samples = [
        "The sink is src.repositories.orders.OrderRepository.mark_paid.",
        "It's src.repositories.orders.OrderRepository.mark_paid, called last.",
        "Hard to say for sure.",
    ]
    reached, symbol = check_self_consistency(samples, known, ("src.",))
    assert reached is True
    assert symbol == "src.repositories.orders.OrderRepository.mark_paid"


def test_check_self_consistency_no_consensus_when_samples_disagree():
    known = frozenset({"src.repositories.orders.OrderRepository.mark_paid", "src.services.billing.PaymentProcessor.charge"})
    samples = [
        "It's src.repositories.orders.OrderRepository.mark_paid.",
        "It's src.services.billing.PaymentProcessor.charge.",
        "Not sure honestly.",
    ]
    reached, _symbol = check_self_consistency(samples, known, ("src.",))
    assert reached is False


# --------------------------------------------------------------------- #
# archetypes.py: taxonomy sanity
# --------------------------------------------------------------------- #
def test_all_33_archetypes_present_with_unique_ids_and_slugs():
    assert len(ARCHETYPES) == 33
    assert set(ARCHETYPES_BY_ID) == set(range(1, 34))
    assert len({a.slug for a in ARCHETYPES}) == 33


def test_every_archetype_has_a_non_empty_target_and_task_prompt():
    for a in ARCHETYPES:
        assert a.target.strip()
        assert a.task_prompt.strip()
        assert a.target.startswith("django.")


def test_self_consistency_archetype_samples_more_than_once():
    self_consistency = [a for a in ARCHETYPES if a.sample_count > 1]
    assert len(self_consistency) == 1
    assert self_consistency[0].slug == "self_consistency"


def test_multi_turn_archetypes_have_follow_up_prompts():
    multi_turn_slugs = {"iterative_follow_up", "prompt_chaining", "conversational", "reflective"}
    for a in ARCHETYPES:
        if a.slug in multi_turn_slugs:
            assert a.follow_up_prompts, f"{a.slug} should declare follow_up_prompts"


# --------------------------------------------------------------------- #
# large_repo_prompt_matrix.py: metrics + repo index helpers
# --------------------------------------------------------------------- #
def test_measure_peak_memory_mb_is_positive():
    assert measure_peak_memory_mb() > 0


def test_module_path_for_file_strips_extension_and_converts_separators():
    assert _module_path_for_file("/repo/src/services/billing.py", "/repo") == "src.services.billing"


def test_module_path_for_file_handles_init_py():
    assert _module_path_for_file("/repo/src/services/__init__.py", "/repo") == "src.services"


def test_build_repo_index_covers_known_symbols_and_module_paths(repo_index):
    assert "src.repositories.orders.OrderRepository.mark_paid" in repo_index.known_qualified_names
    assert "mark_paid" in repo_index.known_simple_names
    assert "src.repositories.orders.OrderRepository.mark_paid" in repo_index.simple_name_to_qualified["mark_paid"]
    assert "src.repositories.orders" in repo_index.known_module_paths
    assert "src." in repo_index.root_prefixes


def test_render_index_metrics_includes_tag_distribution():
    from benchmarks.large_repo_prompt_matrix import IndexMetrics

    metrics = IndexMetrics(
        repo_key="test", repo_url="https://example.invalid/test.git", clone_path="/tmp/x",
        total_symbols=42, total_call_edges=7, tag_distribution={"#db_write": 3}, indexing_time_seconds=1.23, peak_memory_mb=99.0,
    )
    rendered = render_index_metrics(metrics)
    assert "42" in rendered
    assert "#db_write: 3" in rendered
    assert "1.23s" in rendered


# --------------------------------------------------------------------- #
# large_repo_prompt_matrix.py: context building
# --------------------------------------------------------------------- #
def test_build_contexts_returns_nonempty_raw_and_prism_text(builder_and_tags):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.controllers.checkout.CheckoutController.process_checkout", task_prompt="Do it.",
    )
    raw_text, prism_text = build_contexts(builder, tag_matrix, archetype, budget=1000)
    assert "process_checkout" in raw_text
    assert "process_checkout" in prism_text


def test_read_original_signature(builder_and_tags):
    builder, _tag_matrix = builder_and_tags
    signature = read_original_signature(builder, "src.repositories.orders.OrderRepository.mark_paid")
    assert signature.startswith("mark_paid(")


# --------------------------------------------------------------------- #
# large_repo_prompt_matrix.py: run_variant / run_archetype (canned responses)
# --------------------------------------------------------------------- #
def test_run_variant_passes_for_clean_code_response(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Add a docstring.",
        expects_code=True,
    )
    client = _fake_client("```python\ndef charge(self, amount):\n    return self._gateway.charge(amount)\n```")
    result = run_variant(builder, tag_matrix, repo_index, archetype, "prism", "CTX", "gpt-4o-mini", client, 0.0)
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
    result = run_variant(builder, tag_matrix, repo_index, archetype, "raw", "CTX", "gpt-4o-mini", client, 0.0)
    assert "_totally_invented_helper" in result.hallucinated_calls
    assert result.passed is False


def test_run_variant_skips_code_checks_when_expects_code_false(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Explain it.", expects_code=False,
    )
    client = _fake_client("This method charges the customer via the gateway.")
    result = run_variant(builder, tag_matrix, repo_index, archetype, "prism", "CTX", "gpt-4o-mini", client, 0.0)
    assert result.syntax_valid is None
    assert "syntax_valid" not in result.contract_checks
    assert result.passed is True


def test_run_variant_enforces_forbidden_tags(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.repositories.orders.OrderRepository.find_open_orders",
        task_prompt="Add logging, but never write to the database.",
        expects_code=True, forbidden_tags=("#db_write",),
    )
    client = _fake_client("```python\ndef find_open_orders(self):\n    self.mark_paid(1)\n    return []\n```")
    result = run_variant(builder, tag_matrix, repo_index, archetype, "raw", "CTX", "gpt-4o-mini", client, 0.0)
    assert result.forbidden_tag_violations == ("mark_paid",)
    assert result.contract_checks["no_forbidden_tag_calls"] is False
    assert result.passed is False


def test_run_variant_enforces_signature_preservation(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.repositories.orders.OrderRepository.mark_paid", task_prompt="Add logging only.",
        expects_code=True, preserve_signature=True,
    )
    original_signature = read_original_signature(builder, archetype.target)
    good_client = _fake_client(f"```python\ndef {original_signature}:\n    return None\n```")
    good_result = run_variant(builder, tag_matrix, repo_index, archetype, "raw", "CTX", "gpt-4o-mini", good_client, 0.0)
    assert good_result.signature_preserved is True

    bad_client = _fake_client("```python\ndef mark_paid(self, order_id, extra_flag):\n    return None\n```")
    bad_result = run_variant(builder, tag_matrix, repo_index, archetype, "raw", "CTX", "gpt-4o-mini", bad_client, 0.0)
    assert bad_result.signature_preserved is False
    assert bad_result.passed is False


def test_run_variant_multi_turn_scores_final_turn(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Propose a patch.",
        follow_up_prompts=("Now refine it for the edge case.",), expects_code=True,
    )
    client = _sequenced_client(
        [
            "```python\ndef charge(self, amount):\n    return self._gateway.charge(amount)\n```",
            "```python\ndef charge(self, amount):\n    if amount <= 0:\n        raise ValueError('bad amount')\n    return self._gateway.charge(amount)\n```",
        ]
    )
    result = run_variant(builder, tag_matrix, repo_index, archetype, "prism", "CTX", "gpt-4o-mini", client, 0.0)
    assert len(result.turns) == 2
    assert "bad amount" in result.extracted_code
    assert result.passed is True


def test_run_variant_self_consistency_reaches_consensus(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge",
        task_prompt="Which method actually performs the charge?", expects_code=False, sample_count=3,
    )
    client = _sequenced_client(
        [
            "It's src.services.billing.PaymentProcessor.charge.",
            "The answer is src.services.billing.PaymentProcessor.charge.",
            "Not entirely sure, maybe somewhere else.",
        ]
    )
    result = run_variant(builder, tag_matrix, repo_index, archetype, "prism", "CTX", "gpt-4o-mini", client, 0.0)
    assert len(result.turns) == 3
    assert result.consensus_reached is True
    assert result.consensus_symbol == "src.services.billing.PaymentProcessor.charge"
    assert result.passed is True


def test_run_archetype_computes_compression_and_both_variants(builder_and_tags, repo_index):
    builder, tag_matrix = builder_and_tags
    archetype = PromptArchetype(
        archetype_id=1, slug="x", title="X", cluster="C",
        target="src.services.billing.PaymentProcessor.charge", task_prompt="Explain it.", expects_code=False,
    )
    client = _fake_client("This charges the customer.")
    result: ArchetypeRunResult = run_archetype(builder, tag_matrix, repo_index, archetype, ["raw", "prism"], 1000, "gpt-4o-mini", client, 0.0)
    assert result.raw is not None
    assert result.prism is not None
    assert result.raw_tokens > 0
    assert result.prism_tokens > 0


# --------------------------------------------------------------------- #
# CLI: select_archetypes / select_variants / arg parsing
# --------------------------------------------------------------------- #
def test_select_archetypes_all_returns_every_archetype():
    assert {a.archetype_id for a in select_archetypes("all")} == set(range(1, 34))


def test_select_archetypes_by_numeric_id():
    assert [a.archetype_id for a in select_archetypes("17")] == [17]


def test_select_archetypes_by_slug():
    assert [a.archetype_id for a in select_archetypes("negative_constraint")] == [17]


def test_select_archetypes_mixed_ids_and_slugs():
    ids = {a.archetype_id for a in select_archetypes("1,negative_constraint,33")}
    assert ids == {1, 17, 33}


def test_select_archetypes_rejects_unknown():
    with pytest.raises(ValueError, match="unknown prompt id/slug"):
        select_archetypes("not_a_real_one")


def test_select_variants_rejects_unknown():
    with pytest.raises(ValueError, match="unknown variant"):
        select_variants("not_a_real_variant")


def test_build_arg_parser_defaults():
    args = build_arg_parser().parse_args([])
    assert args.repo == "django"
    assert args.prompts == "all"
    assert args.prompt is None
    assert args.budget == 3000
    assert args.model == "gpt-4o-mini"


def test_build_arg_parser_prompt_alias():
    args = build_arg_parser().parse_args(["--prompt", "17"])
    assert args.prompt == 17


# --------------------------------------------------------------------- #
# CLI: main() fast-fail paths (must not touch the network or index anything)
# --------------------------------------------------------------------- #
def test_main_unknown_prompt_exits_nonzero_without_indexing(capsys):
    exit_code = main(["--repo", "django", "--prompts", "not_a_real_prompt", "--dry-run"])
    assert exit_code == 1
    assert "unknown prompt id" in capsys.readouterr().err


def test_main_unknown_variant_exits_nonzero(capsys):
    exit_code = main(["--repo", "django", "--variants", "not_a_real_variant", "--dry-run"])
    assert exit_code == 1
    assert "unknown variant" in capsys.readouterr().err


def test_main_missing_api_key_exits_cleanly_before_indexing(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    exit_code = main(["--repo", "django", "--prompts", "1"])
    assert exit_code == 1
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_main_rejects_unknown_repo_choice():
    with pytest.raises(SystemExit):
        main(["--repo", "not_a_configured_repo", "--dry-run"])


def test_repos_registry_has_django():
    assert "django" in REPOS
    assert REPOS["django"].endswith("django/django.git")


# --------------------------------------------------------------------- #
# Opt-in: real django/django clone + index (reuses the cache if present)
# --------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("PRISM_LIVE_NETWORK_TESTS") != "1",
    reason="set PRISM_LIVE_NETWORK_TESTS=1 to index the real django/django clone (large, network-dependent)",
)
def test_all_33_archetype_targets_resolve_against_real_django():
    from benchmarks.large_repo_prompt_matrix import DEFAULT_CACHE_DIR, index_repo

    builder, tag_matrix, metrics = index_repo("django", DEFAULT_CACHE_DIR, force_clone=False)
    assert metrics.total_symbols > 10_000
    for archetype in ARCHETYPES:
        assert archetype.target in builder.symbol_table, f"archetype {archetype.archetype_id} target missing: {archetype.target}"
        assert archetype.target in builder.graph, f"archetype {archetype.archetype_id} target is isolated: {archetype.target}"


@pytest.mark.skipif(
    os.environ.get("PRISM_LIVE_NETWORK_TESTS") != "1",
    reason="set PRISM_LIVE_NETWORK_TESTS=1 to index the real django/django clone (large, network-dependent)",
)
def test_dry_run_cli_against_real_django(capsys):
    exit_code = main(["--repo", "django", "--prompts", "1,17,29", "--dry-run"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "informational_query" in output
    assert "negative_constraint" in output
    assert "code_generation" in output
