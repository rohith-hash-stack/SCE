"""Validates SCE against a large-scale, real enterprise repository
(django/django - 42,282 symbols, 79,268 CALLS edges once indexed; see
`index_repo`'s docstring for the full metrics this harness measures) across
33 structured prompt archetypes covering the breadth of how a real
developer actually talks to an LLM coding assistant: informational
queries, instruction-following, zero/one/few-shot setups, chain-of-thought
and ReAct-style reasoning, negative constraints, multi-turn dialogue,
closed-ended fact checks, code generation, debugging, architecture
mapping, documentation, and more. See `benchmarks/prompt_taxonomy/` for the
full numbered list and the real target symbol each one queries.

For every archetype, both a raw whole-file-dump context (the target's
call-chain closure) and an SCE L0-L3 context package (3000-token budget by
default) are sent to a real OpenAI model, then scored entirely
mechanically - never an LLM judge:

  - **Token compression ratio**: `(1 - sce_tokens / raw_tokens) * 100`.
  - **Syntax validation**: `ast.parse()` on the extracted code block, for
    archetypes that expect one.
  - **Hallucination counters**: every called symbol (for code-producing
    archetypes) and every fully-qualified symbol *mentioned* in prose (for
    every archetype) is checked against the real repository's
    `GlobalSymbolTable`.
  - **Prompt contract adherence**: archetype-declared checks - output
    format (JSON/YAML), negative constraints, forbidden semantic tags
    (e.g. no `#db_write` call), signature preservation, required
    substrings/regexes, and self-consistency sample agreement.

Usage:
    # Run all 33 taxonomy prompts:
    python -m benchmarks.large_repo_prompt_matrix --repo django --prompts all \
        --report benchmarks/django_33_prompts.json

    # Run a specific archetype:
    python -m benchmarks.large_repo_prompt_matrix --repo django --prompt 17

    # No API key needed: build every context and print sizes/compression, zero API calls
    python -m benchmarks.large_repo_prompt_matrix --repo django --dry-run
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import resource
import sys
import textwrap
import time
from collections import Counter
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
from sce.graph.concrete_builder import ConcreteGraphBuilder  # noqa: E402
from sce.graph.metamodel import SemanticMetamodel  # noqa: E402
from sce.serializers.markdown import render_markdown  # noqa: E402
from sce.slicer.distance import DistanceConfig, DistanceEngine  # noqa: E402
from sce.slicer.knapsack import ContextKnapsackPacker  # noqa: E402

from benchmarks.clone_eval import CloneError, clone_repo  # noqa: E402
from benchmarks.openai_client import LLMClient, OpenAIClientError  # noqa: E402
from benchmarks.prompt_taxonomy import ARCHETYPES, ARCHETYPES_BY_ID, ARCHETYPES_BY_SLUG, PromptArchetype  # noqa: E402
from benchmarks.prompt_taxonomy.spec import (  # noqa: E402
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
from benchmarks.raw_context import RawContextError, build_raw_context  # noqa: E402
from benchmarks.reporting import format_table, shorten  # noqa: E402
from benchmarks.tokenizer import count_tokens  # noqa: E402

DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "clones"
DEFAULT_BUDGET = 3000
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.0
SELF_CONSISTENCY_TEMPERATURE = 0.7
DEFAULT_REPORT_PATH = PROJECT_ROOT / "benchmarks" / "django_33_prompts.json"
VARIANTS: tuple[str, ...] = ("raw", "sce")

REPOS: dict[str, str] = {
    "django": "https://github.com/django/django.git",
}


class PromptMatrixError(Exception):
    """User-facing setup failure (bad repo/archetype selection, missing target, ...)."""


# --------------------------------------------------------------------- #
# Section 1: large-repo indexing metrics
# --------------------------------------------------------------------- #
@dataclasses.dataclass
class IndexMetrics:
    repo_key: str
    repo_url: str
    clone_path: str
    total_symbols: int
    total_call_edges: int
    tag_distribution: dict[str, int]
    indexing_time_seconds: float
    peak_memory_mb: float


def measure_peak_memory_mb() -> float:
    """Peak resident-set size of this process so far, via `getrusage` - no
    extra dependency needed. `ru_maxrss` is KB on Linux, bytes on macOS;
    normalized here since indexing a 40k-symbol repo is by far the
    dominant memory consumer in a fresh CLI invocation, making "peak so
    far, measured right after indexing" a fair, cheap proxy for "peak
    memory indexing took".
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(raw / divisor, 1)


def index_repo(repo_key: str, cache_dir: Path, force_clone: bool) -> tuple[ConcreteGraphBuilder, dict[str, set[str]], IndexMetrics]:
    if repo_key not in REPOS:
        raise PromptMatrixError(f"unknown repo '{repo_key}' (available: {', '.join(REPOS)})")
    url = REPOS[repo_key]
    try:
        repo_path = clone_repo(url, cache_dir=cache_dir, force=force_clone)
    except CloneError as exc:
        raise PromptMatrixError(str(exc)) from exc

    print(f"Indexing {repo_key} ({repo_path}) - this is a large repository, expect a few minutes ...", file=sys.stderr)
    start = time.perf_counter()
    builder, tag_matrix = build_pipeline(str(repo_path))
    elapsed = time.perf_counter() - start
    peak_mb = measure_peak_memory_mb()

    tag_counts: Counter[str] = Counter()
    for tags in tag_matrix.values():
        tag_counts.update(tags)

    metrics = IndexMetrics(
        repo_key=repo_key,
        repo_url=url,
        clone_path=str(repo_path),
        total_symbols=len(builder.symbol_table),
        total_call_edges=builder.graph.number_of_edges(),
        tag_distribution=dict(sorted(tag_counts.items(), key=lambda kv: -kv[1])),
        indexing_time_seconds=round(elapsed, 3),
        peak_memory_mb=peak_mb,
    )
    print(
        f"  {metrics.total_symbols} symbols, {metrics.total_call_edges} CALLS edges, "
        f"indexed in {metrics.indexing_time_seconds}s, peak RSS {metrics.peak_memory_mb} MB",
        file=sys.stderr,
    )
    return builder, tag_matrix, metrics


def render_index_metrics(m: IndexMetrics) -> str:
    lines = [
        f"=== Index metrics: {m.repo_key} ({m.repo_url}) ===",
        f"  Clone path:            {m.clone_path}",
        f"  Total symbols (G_C):   {m.total_symbols}",
        f"  Total CALLS edges:     {m.total_call_edges}",
        f"  Indexing time:         {m.indexing_time_seconds}s",
        f"  Peak memory (RSS):     {m.peak_memory_mb} MB",
        "  Tag distribution (M):",
    ]
    if m.tag_distribution:
        for tag, count in m.tag_distribution.items():
            lines.append(f"    {tag}: {count}")
    else:
        lines.append("    (no tags fired)")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# Repo-derived lookup tables the mechanical checks need
# --------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class RepoIndex:
    known_qualified_names: frozenset[str]
    known_simple_names: frozenset[str]
    known_module_paths: frozenset[str]
    simple_name_to_qualified: dict[str, set[str]]
    root_prefixes: tuple[str, ...]


def _module_path_for_file(file_path: str, repo_root: str) -> str:
    import os

    rel = os.path.relpath(file_path, repo_root)
    if rel.endswith("__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[: -len(".py")]
    return rel.replace(os.sep, ".")


def build_repo_index(builder: ConcreteGraphBuilder) -> RepoIndex:
    qualified = frozenset(builder.symbol_table.all_qualified_names())
    simple_to_qualified: dict[str, set[str]] = {}
    for qname in qualified:
        simple_to_qualified.setdefault(qname.rsplit(".", 1)[-1], set()).add(qname)
    root_prefixes = tuple(sorted({f"{qname.split('.', 1)[0]}." for qname in qualified}))

    # A model legitimately talks about *modules*, not just the leaf
    # functions/classes/methods GlobalSymbolTable indexes (e.g.
    # "django.contrib.auth.backends" on its own, referring to the whole
    # file) - confirmed as a real, non-hallucinated false positive from a
    # live gpt-4o-mini run (archetype 29's SCE variant). Every real
    # module's dotted path, plus every real package prefix along the way,
    # counts as known for the mention-hallucination check.
    module_paths: set[str] = set()
    for symbol in builder.symbol_table:
        module = _module_path_for_file(symbol.file, builder.repo_root)
        parts = module.split(".")
        for i in range(1, len(parts) + 1):
            module_paths.add(".".join(parts[:i]))

    return RepoIndex(
        known_qualified_names=qualified,
        known_simple_names=frozenset(simple_to_qualified),
        known_module_paths=frozenset(module_paths),
        simple_name_to_qualified=simple_to_qualified,
        root_prefixes=root_prefixes,
    )


# --------------------------------------------------------------------- #
# Section 2: context building (SCE package vs. raw whole-file dump)
# --------------------------------------------------------------------- #
def build_contexts(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], archetype: PromptArchetype, budget: int, lambda_weight: float = 0.7) -> tuple[str, str]:
    if archetype.target not in builder.symbol_table:
        raise PromptMatrixError(f"archetype '{archetype.slug}' target '{archetype.target}' was not found in the indexed repository")

    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(archetype.target, builder, tag_matrix, distance_engine)
    sce_text = render_markdown(pack_result, tag_matrix)

    try:
        raw_text = build_raw_context(builder, archetype.target).text
    except RawContextError as exc:
        raise PromptMatrixError(str(exc)) from exc

    return raw_text, sce_text


def read_original_signature(builder: ConcreteGraphBuilder, qualified_name: str) -> str:
    """`name(arg list)` for `qualified_name`'s real, original definition -
    the ground truth `check_signature_preserved` compares a response's
    code against for negative/constraint archetypes."""
    symbol = builder.symbol_table.get(qualified_name)
    parsed = builder.parsed_file(symbol.file)
    source = parsed.source.decode("utf-8", errors="replace")
    lines = source.splitlines()
    start, end = symbol.line_range
    snippet = textwrap.dedent("\n".join(lines[start - 1 : end]))
    tree = ast.parse(snippet)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return function_signature(node)
    return ""


# --------------------------------------------------------------------- #
# Section 3: orchestration + mechanical scoring
# --------------------------------------------------------------------- #
@dataclasses.dataclass
class TurnResult:
    prompt: str
    response: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    latency_seconds: float


@dataclasses.dataclass
class VariantRunResult:
    variant: str
    context_tokens: int
    turns: list[TurnResult]
    extracted_code: str | None
    syntax_valid: bool | None
    syntax_error: str | None
    hallucinated_calls: tuple[str, ...]
    referenced_symbol_mentions: tuple[str, ...]
    hallucinated_symbol_mentions: tuple[str, ...]
    forbidden_tag_violations: tuple[str, ...]
    signature_preserved: bool | None
    consensus_reached: bool | None
    consensus_symbol: str | None
    contract_checks: dict[str, bool]
    passed: bool


@dataclasses.dataclass
class ArchetypeRunResult:
    archetype_id: int
    slug: str
    title: str
    cluster: str
    target: str
    model: str
    raw_tokens: int
    sce_tokens: int
    compression_pct: float
    raw: VariantRunResult
    sce: VariantRunResult


def _run_turns(
    archetype: PromptArchetype, context_text: str, model: str, client: LLMClient, temperature: float
) -> tuple[list[TurnResult], str, list[str]]:
    """Runs the archetype's conversation (single-turn, multi-turn, or
    independent self-consistency samples) and returns (turns, scored_text,
    raw_samples). `scored_text` is the final turn's response for a normal
    or multi-turn archetype, or every sample joined together for a
    self-consistency one (so hallucination checks cover all samples, not
    just the last).
    """
    turns: list[TurnResult] = []

    if archetype.sample_count > 1:
        samples: list[str] = []
        sample_temperature = max(temperature, SELF_CONSISTENCY_TEMPERATURE)
        for _ in range(archetype.sample_count):
            user_prompt = build_user_prompt(archetype, context_text)
            call = client.complete(model=model, system=archetype.system_prompt, user=user_prompt, temperature=sample_temperature)
            turns.append(
                TurnResult(
                    prompt=user_prompt,
                    response=call.content,
                    prompt_tokens=call.prompt_tokens,
                    completion_tokens=call.completion_tokens,
                    cost_usd=call.cost_usd,
                    latency_seconds=call.latency_seconds,
                )
            )
            samples.append(call.content)
        return turns, "\n\n".join(samples), samples

    user_prompt = build_user_prompt(archetype, context_text)
    messages = [{"role": "system", "content": archetype.system_prompt}, {"role": "user", "content": user_prompt}]
    call = client.complete_conversation(model=model, messages=messages, temperature=temperature)
    turns.append(
        TurnResult(
            prompt=user_prompt,
            response=call.content,
            prompt_tokens=call.prompt_tokens,
            completion_tokens=call.completion_tokens,
            cost_usd=call.cost_usd,
            latency_seconds=call.latency_seconds,
        )
    )
    for follow_up in archetype.follow_up_prompts:
        messages.append({"role": "assistant", "content": call.content})
        messages.append({"role": "user", "content": follow_up})
        call = client.complete_conversation(model=model, messages=messages, temperature=temperature)
        turns.append(
            TurnResult(
                prompt=follow_up,
                response=call.content,
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
                cost_usd=call.cost_usd,
                latency_seconds=call.latency_seconds,
            )
        )
    return turns, turns[-1].response, [turns[-1].response]


def run_variant(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    repo_index: RepoIndex,
    archetype: PromptArchetype,
    variant: str,
    context_text: str,
    model: str,
    client: LLMClient,
    temperature: float,
) -> VariantRunResult:
    turns, scored_text, samples = _run_turns(archetype, context_text, model, client, temperature)

    extracted_code: str | None = None
    syntax_valid: bool | None = None
    syntax_error: str | None = None
    hallucinated_calls: tuple[str, ...] = ()
    forbidden_tag_violations: tuple[str, ...] = ()
    signature_preserved: bool | None = None

    if archetype.expects_code:
        extracted_code = extract_first_code_block(scored_text)
        if extracted_code is None:
            syntax_valid = False
            syntax_error = "no fenced code block found in the model's response"
        else:
            try:
                ast.parse(extracted_code)
                syntax_valid = True
            except SyntaxError as exc:
                syntax_valid = False
                syntax_error = f"{exc.msg} (line {exc.lineno})"
            if syntax_valid:
                hallucinated_calls = find_hallucinated_calls(extracted_code, repo_index.known_simple_names)
                forbidden_tag_violations = find_forbidden_tag_violations(
                    extracted_code, archetype.forbidden_tags, tag_matrix, repo_index.simple_name_to_qualified
                )
                if archetype.preserve_signature:
                    original_signature = read_original_signature(builder, archetype.target)
                    signature_preserved = check_signature_preserved(extracted_code, original_signature)

    referenced, unknown_refs = find_referenced_symbol_mentions(
        scored_text, repo_index.known_qualified_names, repo_index.root_prefixes, repo_index.known_module_paths, repo_index.known_simple_names
    )

    consensus_reached: bool | None = None
    consensus_symbol: str | None = None
    if archetype.sample_count > 1:
        consensus_reached, consensus_symbol = check_self_consistency(samples, repo_index.known_qualified_names, repo_index.root_prefixes)

    contract_checks: dict[str, bool] = {"no_hallucinated_symbol_mentions": not unknown_refs}
    if archetype.expects_code:
        contract_checks["syntax_valid"] = bool(syntax_valid)
        contract_checks["no_hallucinated_calls"] = not hallucinated_calls
    fmt_ok = check_output_format(scored_text, archetype.output_format)
    if fmt_ok is not None:
        contract_checks["output_format_valid"] = fmt_ok
    if archetype.negative_constraints:
        violated = check_negative_constraints(extracted_code or scored_text, archetype.negative_constraints)
        contract_checks["negative_constraints_respected"] = not violated
    if archetype.forbidden_tags:
        contract_checks["no_forbidden_tag_calls"] = not forbidden_tag_violations
    if archetype.preserve_signature:
        contract_checks["signature_preserved"] = bool(signature_preserved)
    if archetype.sample_count > 1:
        contract_checks["self_consistency_reached"] = bool(consensus_reached)
    contract_checks.update(
        check_required_substrings(
            scored_text, archetype.required_any_substrings, archetype.required_all_substrings,
            archetype.required_regexes, archetype.required_any_regexes,
        )
    )

    return VariantRunResult(
        variant=variant,
        context_tokens=count_tokens(context_text),
        turns=turns,
        extracted_code=extracted_code,
        syntax_valid=syntax_valid,
        syntax_error=syntax_error,
        hallucinated_calls=hallucinated_calls,
        referenced_symbol_mentions=referenced,
        hallucinated_symbol_mentions=unknown_refs,
        forbidden_tag_violations=forbidden_tag_violations,
        signature_preserved=signature_preserved,
        consensus_reached=consensus_reached,
        consensus_symbol=consensus_symbol,
        contract_checks=contract_checks,
        passed=all(contract_checks.values()),
    )


def run_archetype(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    repo_index: RepoIndex,
    archetype: PromptArchetype,
    variants: list[str],
    budget: int,
    model: str,
    client: LLMClient,
    temperature: float,
) -> ArchetypeRunResult:
    raw_text, sce_text = build_contexts(builder, tag_matrix, archetype, budget)
    raw_tokens = count_tokens(raw_text)
    sce_tokens = count_tokens(sce_text)
    compression_pct = round((1 - sce_tokens / raw_tokens) * 100, 1) if raw_tokens else 0.0

    context_by_variant = {"raw": raw_text, "sce": sce_text}
    results: dict[str, VariantRunResult] = {}
    for variant in variants:
        print(f"  [{archetype.archetype_id:02d}] {archetype.slug} [{variant}] ...", file=sys.stderr)
        results[variant] = run_variant(builder, tag_matrix, repo_index, archetype, variant, context_by_variant[variant], model, client, temperature)

    return ArchetypeRunResult(
        archetype_id=archetype.archetype_id,
        slug=archetype.slug,
        title=archetype.title,
        cluster=archetype.cluster,
        target=archetype.target,
        model=model,
        raw_tokens=raw_tokens,
        sce_tokens=sce_tokens,
        compression_pct=compression_pct,
        raw=results.get("raw"),
        sce=results.get("sce"),
    )


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #
def render_summary_table(results: list[ArchetypeRunResult]) -> str:
    headers = ["ID", "Archetype", "Target", "Compression %", "Variant", "Syntax", "Halluc", "Contract", "Result"]
    rows = []
    for r in results:
        for variant_result in (r.raw, r.sce):
            if variant_result is None:
                continue
            syntax_cell = "n/a" if variant_result.syntax_valid is None else ("OK" if variant_result.syntax_valid else "FAIL")
            halluc_count = len(variant_result.hallucinated_calls) + len(variant_result.hallucinated_symbol_mentions)
            failed_checks = sum(1 for ok in variant_result.contract_checks.values() if not ok)
            rows.append(
                [
                    str(r.archetype_id),
                    r.slug,
                    shorten(r.target, 40),
                    f"{r.compression_pct:.1f}%",
                    variant_result.variant,
                    syntax_cell,
                    str(halluc_count),
                    f"{len(variant_result.contract_checks) - failed_checks}/{len(variant_result.contract_checks)}",
                    "PASS" if variant_result.passed else "FAIL",
                ]
            )
    return format_table(headers, rows)


def render_detail(results: list[ArchetypeRunResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"=== [{r.archetype_id:02d}] {r.title} ({r.slug}) - {r.cluster} ===")
        lines.append(f"  Target: {r.target}")
        lines.append(f"  Raw tokens: {r.raw_tokens}  SCE tokens: {r.sce_tokens}  Compression: {r.compression_pct:.1f}%")
        for variant_result in (r.raw, r.sce):
            if variant_result is None:
                continue
            lines.append(f"  --- [{variant_result.variant}] {'PASS' if variant_result.passed else 'FAIL'} ---")
            for check, ok in variant_result.contract_checks.items():
                lines.append(f"    [{'x' if ok else ' '}] {check}")
            if variant_result.syntax_error:
                lines.append(f"    syntax error: {variant_result.syntax_error}")
            if variant_result.hallucinated_calls:
                lines.append(f"    hallucinated calls: {list(variant_result.hallucinated_calls)}")
            if variant_result.hallucinated_symbol_mentions:
                lines.append(f"    hallucinated symbol mentions: {list(variant_result.hallucinated_symbol_mentions)}")
            if variant_result.forbidden_tag_violations:
                lines.append(f"    forbidden-tag violations: {list(variant_result.forbidden_tag_violations)}")
            if variant_result.consensus_symbol is not None:
                lines.append(f"    self-consistency: reached={variant_result.consensus_reached} symbol={variant_result.consensus_symbol}")
        lines.append("")
    return "\n".join(lines)


def render_cost_summary(results: list[ArchetypeRunResult]) -> str:
    all_variants = [v for r in results for v in (r.raw, r.sce) if v is not None]
    if not all_variants:
        return "(no runs)"
    passed = sum(1 for v in all_variants if v.passed)
    lines = [f"Overall: {passed}/{len(all_variants)} passed ({passed / len(all_variants) * 100:.1f}%)"]
    for variant in VARIANTS:
        vr = [v for v in all_variants if v.variant == variant]
        if not vr:
            continue
        vp = sum(1 for v in vr if v.passed)
        lines.append(f"  [{variant}] {vp}/{len(vr)} passed ({vp / len(vr) * 100:.1f}%)")
    avg_compression = sum(r.compression_pct for r in results) / len(results)
    lines.append(f"Average token compression ratio (SCE vs. raw): {avg_compression:.1f}%")
    total_cost = sum(t.cost_usd for r in results for v in (r.raw, r.sce) if v is not None for t in v.turns if t.cost_usd is not None)
    lines.append(f"Total estimated cost: ${total_cost:.5f}")
    return "\n".join(lines)


def write_report(results: list[ArchetypeRunResult], metrics: IndexMetrics, path: str) -> None:
    payload = {
        "index_metrics": dataclasses.asdict(metrics),
        "archetype_count": len(results),
        "results": [dataclasses.asdict(r) for r in results],
    }
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))


def dry_run_preview(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], metrics: IndexMetrics, archetypes: list[PromptArchetype], budget: int) -> str:
    lines = [render_index_metrics(metrics), "", "(dry run - no API calls made)", ""]
    for archetype in archetypes:
        try:
            raw_text, sce_text = build_contexts(builder, tag_matrix, archetype, budget)
        except PromptMatrixError as exc:
            lines.append(f"=== [{archetype.archetype_id:02d}] {archetype.slug} === ERROR: {exc}")
            continue
        raw_tokens = count_tokens(raw_text)
        sce_tokens = count_tokens(sce_text)
        compression = round((1 - sce_tokens / raw_tokens) * 100, 1) if raw_tokens else 0.0
        lines.append(f"=== [{archetype.archetype_id:02d}] {archetype.title} ({archetype.slug}) ===")
        lines.append(f"  target={archetype.target}")
        lines.append(f"  raw={raw_tokens} tok  sce={sce_tokens} tok  compression={compression:.1f}%")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.large_repo_prompt_matrix",
        description="Validate SCE against a large real repository across 33 structured prompt archetypes.",
    )
    parser.add_argument("--repo", default="django", choices=sorted(REPOS), help="Repository key to evaluate (default: django).")
    parser.add_argument("--prompts", default="all", help="Comma-separated archetype ids and/or slugs, or 'all' (default: all).")
    parser.add_argument("--prompt", type=int, default=None, help="Convenience alias for --prompts with a single numeric id, e.g. --prompt 17.")
    parser.add_argument("--variants", default=",".join(VARIANTS), help=f"Comma-separated context variants (available: {', '.join(VARIANTS)}).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model to use (default: {DEFAULT_MODEL}).")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"SCE token budget (default: {DEFAULT_BUDGET}).")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help=f"Sampling temperature (default: {DEFAULT_TEMPERATURE}).")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help=f"Clone cache directory (default: {DEFAULT_CACHE_DIR}).")
    parser.add_argument("--force-clone", action="store_true", help="Re-clone even if a cached copy already exists.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help=f"Write full results as JSON here (default: {DEFAULT_REPORT_PATH}). Pass '' to skip.")
    parser.add_argument("--price-in", type=float, default=None, dest="price_in", help="Override input price (USD per 1M tokens).")
    parser.add_argument("--price-out", type=float, default=None, dest="price_out", help="Override output price (USD per 1M tokens).")
    parser.add_argument("--dry-run", action="store_true", help="Index the repo and build every context, printing sizes, without calling the API.")
    return parser


def select_archetypes(spec: str) -> list[PromptArchetype]:
    if spec == "all":
        return list(ARCHETYPES)
    tokens = [s.strip() for s in spec.split(",") if s.strip()]
    selected: list[PromptArchetype] = []
    unknown: list[str] = []
    for token in tokens:
        if token.isdigit() and int(token) in ARCHETYPES_BY_ID:
            selected.append(ARCHETYPES_BY_ID[int(token)])
        elif token in ARCHETYPES_BY_SLUG:
            selected.append(ARCHETYPES_BY_SLUG[token])
        else:
            unknown.append(token)
    if unknown:
        raise ValueError(f"unknown prompt id/slug(s): {', '.join(unknown)}")
    return selected


def select_variants(spec: str) -> list[str]:
    variants = [v.strip() for v in spec.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variant(s): {', '.join(unknown)} (available: {', '.join(VARIANTS)})")
    return variants


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    prompts_spec = str(args.prompt) if args.prompt is not None else args.prompts
    try:
        archetypes = select_archetypes(prompts_spec)
        variants = select_variants(args.variants)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Fail fast on a bad argument or a missing API key *before* paying for a
    # multi-minute index of a large repository - only --dry-run gets to
    # skip the client construction, since it never calls the API at all.
    client: LLMClient | None = None
    if not args.dry_run:
        try:
            client = LLMClient(price_in_override=args.price_in, price_out_override=args.price_out)
        except OpenAIClientError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    try:
        builder, tag_matrix, metrics = index_repo(args.repo, Path(args.cache_dir), args.force_clone)
    except PromptMatrixError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(dry_run_preview(builder, tag_matrix, metrics, archetypes, args.budget))
        return 0

    repo_index = build_repo_index(builder)

    results: list[ArchetypeRunResult] = []
    for archetype in archetypes:
        try:
            results.append(run_archetype(builder, tag_matrix, repo_index, archetype, variants, args.budget, args.model, client, args.temperature))
        except (PromptMatrixError, OpenAIClientError) as exc:
            print(f"error: archetype '{archetype.slug}' failed: {exc}", file=sys.stderr)
            return 1

    print()
    print(render_index_metrics(metrics))
    print()
    print(render_summary_table(results))
    print()
    print(render_detail(results))
    print(render_cost_summary(results))

    if args.report:
        write_report(results, metrics, args.report)
        print(f"\nWrote report to {args.report}")

    all_passed = all(v.passed for r in results for v in (r.raw, r.sce) if v is not None)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
