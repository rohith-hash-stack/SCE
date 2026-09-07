"""Polyglot 33-prompt evaluation matrix: the same 33-archetype taxonomy
`large_repo_prompt_matrix.py` runs against django/Python alone, generalized
across all six languages SCE's parser/tagger/slicer layers support, each
against a real, cloned, architecturally distinct open-source repository:

  - Python:      django/django                (Model.save)
  - TypeScript:  honojs/hono                   (Hono.route -> compose)
  - JavaScript:  expressjs/express             (response finalization chain)
  - Go:          gin-gonic/gin                 ((*Engine).handleHTTPRequest)
  - Java:        spring-projects/spring-petclinic (PetController.processCreationForm)
  - C#:          dotnet-architecture/eShopOnWeb (UserController.GetCurrentUser)

See `REPOS` below for the exact target symbol per language and how each was
confirmed (by actually cloning and indexing the repo and inspecting its
real concrete graph - never guessed; the same methodology
`benchmarks/README.md` documents for every other multi-repo harness here).

For every (repo, archetype) pair, both a raw whole-file-dump context (the
target's call-chain closure) and an SCE L0-L3 context package are sent to a
real OpenAI model, then scored entirely mechanically - never an LLM judge:

  - **Token compression**: `(1 - sce_tokens / raw_tokens) * 100`.
  - **Syntax validity**: Python responses via `ast.parse`; every other
    language via a real Tree-sitter reparse (`sce.parser.tree_sitter_loader`),
    reusing the exact same class-wrap fallback heuristic
    `benchmarks/validity.py` already validates SCE's OWN rendered code
    blocks with.
  - **Hallucination detection**: every called symbol's simple name checked
    against the repository's real `GlobalSymbolTable` - `ast`-based for
    Python (`benchmarks/prompt_taxonomy/spec.py`'s existing checker), and a
    regex-based call-name extractor for the other five languages (no
    Tree-sitter AST walk of arbitrary LLM-generated snippets is attempted;
    this is the same "no type inference, simple-name-only" heuristic guard
    spec.py's own Python checker already documents, just without a real
    parser backing the extraction step). Every archetype - code-producing
    or not - additionally has its prose scanned for fully-qualified,
    dotted-path-shaped symbol mentions via spec.py's already
    language-agnostic `find_referenced_symbol_mentions`.
  - **Contract adherence**: archetype-declared checks (output format,
    negative constraints, forbidden semantic tags, required
    substrings/regexes, self-consistency) - all of `spec.py`'s checkers are
    already pure text/regex operations with no Python-specific parsing, so
    they apply completely unchanged across all six languages.

Usage:
    # A single language repository, all 33 prompts:
    python -m benchmarks.polyglot_33_matrix --repo django --report benchmarks/polyglot_33_results.json

    # The full six-language suite:
    python -m benchmarks.polyglot_33_matrix --run-all --report benchmarks/polyglot_33_results.json

    # A single archetype by id:
    python -m benchmarks.polyglot_33_matrix --repo gin --prompt 17

    # No API key needed: index every repo, build every context, print
    # sizes/compression/SCE's-own-hallucination-freedom, zero API calls:
    python -m benchmarks.polyglot_33_matrix --run-all --dry-run
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import re
import resource
import sys
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

from sce.graph.concrete_builder import ConcreteGraphBuilder  # noqa: E402
from sce.graph.metamodel import SemanticMetamodel  # noqa: E402
from sce.cli import build_pipeline  # noqa: E402
from sce.serializers.markdown import render_markdown  # noqa: E402
from sce.slicer.distance import DistanceConfig, DistanceEngine  # noqa: E402
from sce.slicer.knapsack import ContextKnapsackPacker  # noqa: E402

from benchmarks.clone_eval import CloneError, clone_repo  # noqa: E402
from benchmarks.multi_repo_eval import check_no_hallucinated_symbols  # noqa: E402
from benchmarks.openai_client import LLMClient, OpenAIClientError  # noqa: E402
from benchmarks.prompt_taxonomy.polyglot_prompts import PROMPT_TEMPLATES, PROMPT_TEMPLATES_BY_ID, PROMPT_TEMPLATES_BY_SLUG  # noqa: E402
from benchmarks.prompt_taxonomy.spec import (  # noqa: E402
    build_user_prompt,
    check_negative_constraints,
    check_output_format,
    check_required_substrings,
    check_self_consistency,
    extract_first_code_block,
    find_forbidden_tag_violations,
    find_hallucinated_calls as find_hallucinated_calls_python,
    find_referenced_symbol_mentions,
)
from benchmarks.prompt_taxonomy.spec import PromptArchetype  # noqa: E402
from benchmarks.raw_context import RawContextError, build_raw_context  # noqa: E402
from benchmarks.reporting import format_table  # noqa: E402
from benchmarks.tokenizer import count_tokens  # noqa: E402
from benchmarks.validity import (  # noqa: E402
    TREE_SITTER_CHECKED_LANGUAGES,
    _tree_sitter_reparses_cleanly,
    check_python_syntax,
    check_tree_sitter_syntax,
)

DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "clones"
DEFAULT_BUDGET = 3000
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.0
SELF_CONSISTENCY_TEMPERATURE = 0.7
DEFAULT_REPORT_PATH = PROJECT_ROOT / "benchmarks" / "polyglot_33_results.json"
VARIANTS: tuple[str, ...] = ("raw", "sce")


class PolyglotMatrixError(Exception):
    """User-facing setup failure (bad repo/archetype selection, missing
    target, clone/index failure, ...)."""


# --------------------------------------------------------------------- #
# Section 1: representative targets per language (see module docstring -
# every target below was confirmed by actually cloning and indexing the
# repository and inspecting its real concrete graph, not guessed).
# --------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class RepoSpec:
    key: str
    url: str
    language_id: str  # sce.parser.tree_sitter_loader.LanguageID value
    target: str
    description: str
    notes: str = ""


REPOS: dict[str, RepoSpec] = {
    "django": RepoSpec(
        "django", "https://github.com/django/django.git", "python",
        "django.db.models.base.Model.save",
        "Python - the ORM's model persistence entrypoint",
    ),
    "hono": RepoSpec(
        "hono", "https://github.com/honojs/hono.git", "typescript",
        "src.hono-base.Hono.route",
        "TypeScript - router registration feeding into the middleware compose() pipeline",
        notes=(
            "Hono.fetch/.use are class-FIELD arrow functions assigned in the constructor "
            "(`this.use = (...) => {...}`), not `method_definition` nodes, so SCE's current "
            "TS/JS symbol collector (which only indexes function_declaration/method_definition, "
            "unlike its Python counterpart's separate attribute-assignment pass) does not index "
            "them as callable symbols - Hono.route is the closest real, indexed, richly-connected "
            "alternative (it calls basePath and compose directly)."
        ),
    ),
    "express": RepoSpec(
        "express", "https://github.com/expressjs/express.git", "javascript",
        "lib.response.onfinish",
        "JavaScript - the response finalization handler on the request/response lifecycle",
        notes=(
            "app.handle/Router.process_params are legacy CommonJS prototype-method assignments "
            "(`app.handle = function handle(req, res, callback) {...}`) - a real, confirmed gap in "
            "SCE's current JS symbol collector, which only indexes function_declaration/"
            "method_definition nodes. Every such assignment in express's own lib/ indexes with "
            "ZERO resolvable call edges under the current pipeline; lib.response.onfinish is the "
            "one real function in express's own source with any outgoing CALLS edges at all "
            "(-> onaborted, -> onerror) at time of writing. Reported here rather than silently "
            "worked around - the same 'found and documented, not hidden' policy every other real "
            "engine limitation in benchmarks/README.md follows."
        ),
    ),
    "gin": RepoSpec(
        "gin", "https://github.com/gin-gonic/gin.git", "go",
        "gin.handleHTTPRequest",
        "Go - the Engine's core HTTP request dispatch",
    ),
    "spring-petclinic": RepoSpec(
        "spring-petclinic", "https://github.com/spring-projects/spring-petclinic.git", "java",
        "org.springframework.samples.petclinic.owner.PetController.processCreationForm",
        "Java - Spring MVC form-submission controller with duplicate-name validation",
    ),
    "eShopOnWeb": RepoSpec(
        "eShopOnWeb", "https://github.com/dotnet-architecture/eShopOnWeb.git", "csharp",
        "Microsoft.eShopWeb.Web.Controllers.UserController.GetCurrentUser",
        "C# - an ASP.NET Core [Authorize]-gated endpoint (#auth_guard + #route_handler)",
        notes=(
            "OrderService.CreateOrderAsync (a real, indexed symbol) resolves with ZERO outgoing "
            "call edges under the current pipeline - its body calls only through constructor-"
            "injected interface-typed fields (_orderRepository, _uriComposer, ...), which SCE's "
            "no-type-inference InstanceTypeMap cannot statically resolve to a concrete "
            "implementation. UserController.GetCurrentUser is the nearby real, richly-connected, "
            "auth-tagged alternative actually used here."
        ),
    ),
}


# --------------------------------------------------------------------- #
# Indexing metrics (section 4: "Indexing capacity")
# --------------------------------------------------------------------- #
@dataclasses.dataclass
class IndexMetrics:
    repo_key: str
    repo_url: str
    language_id: str
    clone_path: str
    total_symbols: int
    total_call_edges: int
    tag_distribution: dict[str, int]
    indexing_time_seconds: float
    peak_memory_mb: float


def measure_peak_memory_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(raw / divisor, 1)


def index_repo(repo_key: str, cache_dir: Path, force_clone: bool) -> tuple[ConcreteGraphBuilder, dict[str, set[str]], IndexMetrics]:
    if repo_key not in REPOS:
        raise PolyglotMatrixError(f"unknown repo '{repo_key}' (available: {', '.join(REPOS)})")
    spec = REPOS[repo_key]
    try:
        repo_path = clone_repo(spec.url, cache_dir=cache_dir, force=force_clone)
    except CloneError as exc:
        raise PolyglotMatrixError(str(exc)) from exc

    print(f"Indexing {repo_key} [{spec.language_id}] ({repo_path}) ...", file=sys.stderr)
    start = time.perf_counter()
    builder, tag_matrix = build_pipeline(str(repo_path))
    elapsed = time.perf_counter() - start
    peak_mb = measure_peak_memory_mb()

    tag_counts: Counter[str] = Counter()
    for tags in tag_matrix.values():
        tag_counts.update(tags)

    metrics = IndexMetrics(
        repo_key=repo_key,
        repo_url=spec.url,
        language_id=spec.language_id,
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
    if spec.target not in builder.symbol_table:
        raise PolyglotMatrixError(
            f"'{repo_key}' target '{spec.target}' was not found in the indexed repository - "
            "upstream may have restructured since this target was confirmed"
        )
    return builder, tag_matrix, metrics


def render_index_metrics(m: IndexMetrics) -> str:
    lines = [
        f"=== Index metrics: {m.repo_key} [{m.language_id}] ({m.repo_url}) ===",
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
    notes = REPOS[m.repo_key].notes
    if notes:
        lines.append(f"  Note: {notes}")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# Repo-derived lookup tables the mechanical checks need (language-agnostic:
# `GlobalSymbolTable.all_qualified_names()` works identically regardless of
# source language).
# --------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class RepoIndex:
    known_qualified_names: frozenset[str]
    known_simple_names: frozenset[str]
    simple_name_to_qualified: dict[str, set[str]]
    root_prefixes: tuple[str, ...]


def build_repo_index(builder: ConcreteGraphBuilder) -> RepoIndex:
    qualified = frozenset(builder.symbol_table.all_qualified_names())
    simple_to_qualified: dict[str, set[str]] = {}
    for qname in qualified:
        simple_to_qualified.setdefault(qname.rsplit(".", 1)[-1], set()).add(qname)
    root_prefixes = tuple(sorted({f"{qname.split('.', 1)[0]}." for qname in qualified}))
    return RepoIndex(
        known_qualified_names=qualified,
        known_simple_names=frozenset(simple_to_qualified),
        simple_name_to_qualified=simple_to_qualified,
        root_prefixes=root_prefixes,
    )


# --------------------------------------------------------------------- #
# Section 2: context building (SCE package vs. raw whole-file dump) -
# already fully language-agnostic (ContextKnapsackPacker/build_raw_context
# operate purely on the concrete graph/symbol table, never on Python-
# specific AST), so this is identical to large_repo_prompt_matrix.py.
# --------------------------------------------------------------------- #
def build_contexts(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], archetype: PromptArchetype, budget: int, lambda_weight: float = 0.7) -> tuple[str, str]:
    if archetype.target not in builder.symbol_table:
        raise PolyglotMatrixError(f"archetype '{archetype.slug}' target '{archetype.target}' was not found in the indexed repository")

    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(archetype.target, builder, tag_matrix, distance_engine)
    sce_text = render_markdown(pack_result, tag_matrix)

    try:
        raw_text = build_raw_context(builder, archetype.target).text
    except RawContextError as exc:
        raise PolyglotMatrixError(str(exc)) from exc

    return raw_text, sce_text


# --------------------------------------------------------------------- #
# Section 3: language-specific mechanical evaluator
# --------------------------------------------------------------------- #
def check_code_syntax(code: str, language_id: str) -> tuple[bool, str | None]:
    """Python -> `ast.parse`; every other supported language -> a real
    Tree-sitter reparse (`root_node` ERROR/MISSING node count), reusing
    `benchmarks/validity.py`'s exact class-wrap fallback heuristic - a
    rendered snippet is either a free-standing declaration or a class
    member, and either reparsing unwrapped or wrapped in a throwaway class
    body cleanly counts as valid.
    """
    if language_id == "python":
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as exc:
            return False, f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})"
    if language_id in TREE_SITTER_CHECKED_LANGUAGES:
        return _tree_sitter_reparses_cleanly(language_id, code)
    return True, None  # no checker available for this language - not applicable


# Keywords/control-flow tokens that look like a call-name match
# (`identifier(`) but never name a callable symbol - excluded so the
# regex-based extractor below doesn't flag ordinary control flow as a
# "hallucinated call". Deliberately small and additive per language, the
# same "kept short and genuinely common" principle spec.py's own
# `_COMMON_STDLIB_METHOD_NAMES` documents.
_NON_CALL_KEYWORDS_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "typescript": frozenset({"if", "for", "while", "switch", "catch", "function", "return", "typeof", "instanceof", "new", "await", "yield", "async"}),
    "javascript": frozenset({"if", "for", "while", "switch", "catch", "function", "return", "typeof", "instanceof", "new", "await", "yield", "async"}),
    "tsx": frozenset({"if", "for", "while", "switch", "catch", "function", "return", "typeof", "instanceof", "new", "await", "yield", "async"}),
    "go": frozenset({"if", "for", "switch", "select", "return", "go", "defer", "range", "make", "new", "len", "cap", "append", "copy", "delete", "panic", "recover", "print", "println", "close", "int", "string", "bool", "float64", "int64", "int32", "byte", "rune", "error"}),
    "java": frozenset({"if", "for", "while", "switch", "catch", "return", "new", "instanceof", "synchronized", "throw", "throws", "super", "this"}),
    "csharp": frozenset({"if", "for", "foreach", "while", "switch", "catch", "return", "new", "typeof", "nameof", "await", "using", "lock", "throw", "base", "this"}),
}

# Small, curated per-language builtin/framework-primitive allowlist -
# mirrors spec.py's `_COMMON_STDLIB_METHOD_NAMES` philosophy (specific,
# confirmed-common names, not a blanket allowance) so ordinary language/
# runtime idioms don't get flagged as fabricated repository symbols.
_COMMON_BUILTIN_NAMES_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "typescript": frozenset({"console", "log", "error", "warn", "info", "debug", "Promise", "resolve", "reject", "then", "catch", "finally", "Array", "from", "isArray", "Object", "keys", "values", "entries", "assign", "freeze", "Map", "Set", "has", "get", "set", "delete", "push", "pop", "slice", "splice", "map", "filter", "reduce", "forEach", "find", "includes", "join", "split", "trim", "toString", "JSON", "parse", "stringify", "fetch", "setTimeout", "setInterval", "parseInt", "parseFloat"}),
    "javascript": frozenset({"console", "log", "error", "warn", "info", "debug", "Promise", "resolve", "reject", "then", "catch", "finally", "Array", "from", "isArray", "Object", "keys", "values", "entries", "assign", "freeze", "Map", "Set", "has", "get", "set", "delete", "push", "pop", "slice", "splice", "map", "filter", "reduce", "forEach", "find", "includes", "join", "split", "trim", "toString", "JSON", "parse", "stringify", "fetch", "setTimeout", "setInterval", "require", "module", "exports", "parseInt", "parseFloat"}),
    "tsx": frozenset({"console", "log", "Promise", "Array", "Object", "Map", "Set", "JSON", "parse", "stringify"}),
    "go": frozenset({"fmt", "Println", "Printf", "Sprintf", "Errorf", "errors", "New", "strings", "Join", "Split", "Contains", "HasPrefix", "HasSuffix", "TrimSpace", "context", "Background", "sync", "Lock", "Unlock", "Wait", "time", "Now", "Since"}),
    "java": frozenset({"System", "out", "println", "print", "String", "valueOf", "format", "Integer", "parseInt", "List", "of", "Map", "Optional", "empty", "isPresent", "orElse", "orElseThrow", "Objects", "requireNonNull", "equals", "Arrays", "asList", "Collections", "emptyList", "stream", "collect", "toList", "Exception", "getMessage"}),
    "csharp": frozenset({"Console", "WriteLine", "Write", "string", "Format", "Convert", "ToString", "ToInt32", "List", "Add", "Dictionary", "ContainsKey", "Task", "FromResult", "CompletedTask", "Enumerable", "Where", "Select", "ToList", "ToListAsync", "FirstOrDefault", "FirstOrDefaultAsync", "Exception", "Message"}),
}

_CALL_NAME_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")

# `identifier(` also matches a function/method DECLARATION, not just a call
# site (`func handle(c *Context) {` reads identically to a call to the
# regex above) - these per-language patterns extract just the declared
# name(s) in a snippet so they can be excluded from the "called" set below.
# Best-effort, same spirit as the rest of this heuristic checker: it won't
# catch every declaration shape (e.g. a bare arrow-function class field),
# but it removes the single most damaging false positive - the model's own
# generated function being flagged as a hallucinated call to itself.
_DECLARATION_NAME_RE_BY_LANGUAGE: dict[str, tuple[re.Pattern, ...]] = {
    "go": (re.compile(r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),),
    "typescript": (
        re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$]\w*)\s*\("),
        re.compile(r"\bclass\s+([A-Za-z_$]\w*)"),
        re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$]\w*)\s*="),
    ),
    "javascript": (
        re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$]\w*)\s*\("),
        re.compile(r"\bclass\s+([A-Za-z_$]\w*)"),
        re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$]\w*)\s*="),
    ),
    "tsx": (
        re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$]\w*)\s*\("),
        re.compile(r"\bclass\s+([A-Za-z_$]\w*)"),
        re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$]\w*)\s*="),
    ),
    "java": (
        re.compile(r"\b(?:public|private|protected|static|final|abstract|synchronized)\s+[\w<>\[\],\s]+?\s([A-Za-z_]\w*)\s*\("),
        re.compile(r"\bclass\s+([A-Za-z_]\w*)"),
    ),
    "csharp": (
        re.compile(r"\b(?:public|private|protected|internal|static|virtual|override|async|sealed)\s+[\w<>\[\],\s?]+?\s([A-Za-z_]\w*)\s*\("),
        re.compile(r"\bclass\s+([A-Za-z_]\w*)"),
    ),
}


def _declared_names(code: str, language_id: str) -> set[str]:
    declared: set[str] = set()
    for pattern in _DECLARATION_NAME_RE_BY_LANGUAGE.get(language_id, ()):
        declared.update(m.group(1) for m in pattern.finditer(code))
    return declared


def find_hallucinated_calls_generic(code: str, language_id: str, known_simple_names: frozenset[str]) -> tuple[str, ...]:
    """Regex-based stand-in for `spec.py`'s `ast`-based
    `find_hallucinated_calls`, for the five languages that don't have a
    stdlib AST module available: every `identifier(` -shaped token in
    `code`, minus this language's control-flow keywords and a small
    curated builtin allowlist, checked against the real repository's
    simple symbol names. A heuristic guard (no parser backs the
    extraction step, so a string literal or comment containing
    `foo(` -shaped text can produce a false positive) - reported as a
    diagnostic count, not treated as an infallible proof either way,
    exactly like spec.py's own Python checker already documents itself as.
    """
    if language_id == "python":
        return find_hallucinated_calls_python(code, known_simple_names)
    keywords = _NON_CALL_KEYWORDS_BY_LANGUAGE.get(language_id, frozenset())
    builtins = _COMMON_BUILTIN_NAMES_BY_LANGUAGE.get(language_id, frozenset())
    declared = _declared_names(code, language_id)
    called = {m.group(1) for m in _CALL_NAME_RE.finditer(code)} - declared
    unknown = sorted(name for name in called if name not in known_simple_names and name not in keywords and name not in builtins)
    return tuple(unknown)


# --------------------------------------------------------------------- #
# Orchestration + mechanical scoring
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
    repo: str
    language_id: str
    target: str
    model: str
    raw_tokens: int
    sce_tokens: int
    compression_pct: float
    raw: VariantRunResult
    sce: VariantRunResult


def _run_turns(archetype: PromptArchetype, context_text: str, model: str, client: LLMClient, temperature: float) -> tuple[list[TurnResult], str, list[str]]:
    turns: list[TurnResult] = []

    if archetype.sample_count > 1:
        samples: list[str] = []
        sample_temperature = max(temperature, SELF_CONSISTENCY_TEMPERATURE)
        for _ in range(archetype.sample_count):
            user_prompt = build_user_prompt(archetype, context_text)
            call = client.complete(model=model, system=archetype.system_prompt, user=user_prompt, temperature=sample_temperature)
            turns.append(TurnResult(user_prompt, call.content, call.prompt_tokens, call.completion_tokens, call.cost_usd, call.latency_seconds))
            samples.append(call.content)
        return turns, "\n\n".join(samples), samples

    user_prompt = build_user_prompt(archetype, context_text)
    messages = [{"role": "system", "content": archetype.system_prompt}, {"role": "user", "content": user_prompt}]
    call = client.complete_conversation(model=model, messages=messages, temperature=temperature)
    turns.append(TurnResult(user_prompt, call.content, call.prompt_tokens, call.completion_tokens, call.cost_usd, call.latency_seconds))
    for follow_up in archetype.follow_up_prompts:
        messages.append({"role": "assistant", "content": call.content})
        messages.append({"role": "user", "content": follow_up})
        call = client.complete_conversation(model=model, messages=messages, temperature=temperature)
        turns.append(TurnResult(follow_up, call.content, call.prompt_tokens, call.completion_tokens, call.cost_usd, call.latency_seconds))
    return turns, turns[-1].response, [turns[-1].response]


def run_variant(
    repo_index: RepoIndex,
    tag_matrix: dict[str, set[str]],
    language_id: str,
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

    if archetype.expects_code:
        extracted_code = extract_first_code_block(scored_text)
        if extracted_code is None:
            syntax_valid = False
            syntax_error = "no fenced code block found in the model's response"
        else:
            syntax_valid, syntax_error = check_code_syntax(extracted_code, language_id)
            if syntax_valid:
                hallucinated_calls = find_hallucinated_calls_generic(extracted_code, language_id, repo_index.known_simple_names)
                forbidden_tag_violations = find_forbidden_tag_violations(
                    extracted_code, archetype.forbidden_tags, tag_matrix, repo_index.simple_name_to_qualified
                )

    referenced, unknown_refs = find_referenced_symbol_mentions(
        scored_text, repo_index.known_qualified_names, repo_index.root_prefixes, frozenset(), repo_index.known_simple_names
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
        consensus_reached=consensus_reached,
        consensus_symbol=consensus_symbol,
        contract_checks=contract_checks,
        passed=all(contract_checks.values()),
    )


def run_archetype(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    repo_index: RepoIndex,
    repo_key: str,
    language_id: str,
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
        print(f"  [{repo_key}] [{archetype.archetype_id:02d}] {archetype.slug} [{variant}] ...", file=sys.stderr)
        results[variant] = run_variant(repo_index, tag_matrix, language_id, archetype, variant, context_by_variant[variant], model, client, temperature)

    return ArchetypeRunResult(
        archetype_id=archetype.archetype_id,
        slug=archetype.slug,
        title=archetype.title,
        cluster=archetype.cluster,
        repo=repo_key,
        language_id=language_id,
        target=archetype.target,
        model=model,
        raw_tokens=raw_tokens,
        sce_tokens=sce_tokens,
        compression_pct=compression_pct,
        raw=results.get("raw"),
        sce=results.get("sce"),
    )


@dataclasses.dataclass
class RepoRunResult:
    repo: str
    metrics: IndexMetrics
    archetypes: list[ArchetypeRunResult] = dataclasses.field(default_factory=list)


def run_repo(
    repo_key: str,
    archetype_ids: list[int],
    variants: list[str],
    budget: int,
    model: str,
    client: LLMClient | None,
    temperature: float,
    cache_dir: Path,
    force_clone: bool,
    dry_run: bool,
) -> tuple[RepoRunResult, str | None]:
    """Returns (result, dry_run_preview_text). `dry_run_preview_text` is
    non-None only when `dry_run` is True, in which case `result.archetypes`
    is left empty (no API calls made)."""
    spec = REPOS[repo_key]
    builder, tag_matrix, metrics = index_repo(repo_key, cache_dir, force_clone)
    result = RepoRunResult(repo=repo_key, metrics=metrics)

    templates = [PROMPT_TEMPLATES_BY_ID[i] for i in archetype_ids]

    if dry_run:
        lines = [render_index_metrics(metrics), "", "(dry run - no API calls made)", ""]
        for template in templates:
            archetype = template.instantiate(spec.target, spec.language_id, spec.description)
            try:
                raw_text, sce_text = build_contexts(builder, tag_matrix, archetype, budget)
            except PolyglotMatrixError as exc:
                lines.append(f"=== [{archetype.archetype_id:02d}] {archetype.slug} === ERROR: {exc}")
                continue
            raw_tokens = count_tokens(raw_text)
            sce_tokens = count_tokens(sce_text)
            compression = round((1 - sce_tokens / raw_tokens) * 100, 1) if raw_tokens else 0.0
            hallucination = check_no_hallucinated_symbols(sce_text, builder)
            blocks = (
                check_python_syntax(sce_text)
                if spec.language_id == "python"
                else check_tree_sitter_syntax(sce_text, languages=frozenset({spec.language_id}))
            )
            total = len(blocks)
            invalid = sum(1 for b in blocks if not b.valid)
            lines.append(f"=== [{archetype.archetype_id:02d}] {archetype.title} ({archetype.slug}) ===")
            lines.append(f"  target={archetype.target}")
            lines.append(
                f"  raw={raw_tokens} tok  sce={sce_tokens} tok  compression={compression:.1f}%  "
                f"sce_syntax_valid={total - invalid}/{total}  sce_hallucinations={len(hallucination.unknown_symbols)}"
            )
        return result, "\n".join(lines)

    assert client is not None
    repo_index = build_repo_index(builder)
    for template in templates:
        archetype = template.instantiate(spec.target, spec.language_id, spec.description)
        try:
            result.archetypes.append(
                run_archetype(builder, tag_matrix, repo_index, repo_key, spec.language_id, archetype, variants, budget, model, client, temperature)
            )
        except (PolyglotMatrixError, OpenAIClientError) as exc:
            raise PolyglotMatrixError(f"[{repo_key}] archetype '{archetype.slug}' failed: {exc}") from exc

    return result, None


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class LanguageSummary:
    repo: str
    language_id: str
    total_symbols: int
    total_edges: int
    indexing_time_seconds: float
    peak_memory_mb: float
    avg_compression_pct: float
    syntax_validity_pct: float
    zero_hallucination_pct: float
    contract_accuracy_pct: float
    prompt_pass_rate_pct: float


def summarize_repo(result: RepoRunResult) -> LanguageSummary:
    variant_results = [v for a in result.archetypes for v in (a.raw, a.sce) if v is not None]
    n = len(variant_results) or 1

    syntax_checked = [v for v in variant_results if v.syntax_valid is not None]
    syntax_ok = sum(1 for v in syntax_checked if v.syntax_valid)
    syntax_pct = round(syntax_ok / len(syntax_checked) * 100, 1) if syntax_checked else 100.0

    zero_halluc = sum(1 for v in variant_results if not v.hallucinated_calls and not v.hallucinated_symbol_mentions)
    zero_halluc_pct = round(zero_halluc / n * 100, 1)

    total_checks = sum(len(v.contract_checks) for v in variant_results)
    passed_checks = sum(sum(1 for ok in v.contract_checks.values() if ok) for v in variant_results)
    accuracy_pct = round(passed_checks / total_checks * 100, 1) if total_checks else 100.0

    passed = sum(1 for v in variant_results if v.passed)
    pass_rate_pct = round(passed / n * 100, 1)

    avg_compression = round(sum(a.compression_pct for a in result.archetypes) / len(result.archetypes), 1) if result.archetypes else 0.0

    m = result.metrics
    return LanguageSummary(
        repo=result.repo,
        language_id=m.language_id,
        total_symbols=m.total_symbols,
        total_edges=m.total_call_edges,
        indexing_time_seconds=m.indexing_time_seconds,
        peak_memory_mb=m.peak_memory_mb,
        avg_compression_pct=avg_compression,
        syntax_validity_pct=syntax_pct,
        zero_hallucination_pct=zero_halluc_pct,
        contract_accuracy_pct=accuracy_pct,
        prompt_pass_rate_pct=pass_rate_pct,
    )


def render_comparative_summary(summaries: list[LanguageSummary]) -> str:
    headers = ["Repo", "Lang", "Symbols", "Edges", "Index (s)", "Peak MB", "Avg Compression %", "Syntax Valid %", "Zero-Halluc %", "Response Accuracy %", "Prompt Pass Rate %"]
    rows = [
        [
            s.repo, s.language_id, str(s.total_symbols), str(s.total_edges), f"{s.indexing_time_seconds:.1f}", f"{s.peak_memory_mb:.1f}",
            f"{s.avg_compression_pct:.1f}", f"{s.syntax_validity_pct:.1f}", f"{s.zero_hallucination_pct:.1f}",
            f"{s.contract_accuracy_pct:.1f}", f"{s.prompt_pass_rate_pct:.1f}",
        ]
        for s in summaries
    ]
    return format_table(headers, rows)


def render_summary_table(results: list[ArchetypeRunResult]) -> str:
    headers = ["Repo", "ID", "Archetype", "Compression %", "Variant", "Syntax", "Halluc", "Contract", "Result"]
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
                    r.repo, str(r.archetype_id), r.slug, f"{r.compression_pct:.1f}%", variant_result.variant,
                    syntax_cell, str(halluc_count),
                    f"{len(variant_result.contract_checks) - failed_checks}/{len(variant_result.contract_checks)}",
                    "PASS" if variant_result.passed else "FAIL",
                ]
            )
    return format_table(headers, rows)


def render_detail(results: list[ArchetypeRunResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"=== [{r.repo}] [{r.archetype_id:02d}] {r.title} ({r.slug}) - {r.cluster} ===")
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
    total_cost = sum(t.cost_usd for r in results for v in (r.raw, r.sce) if v is not None for t in v.turns if t.cost_usd is not None)
    lines.append(f"Total estimated cost: ${total_cost:.5f}")
    return "\n".join(lines)


def write_report(repo_results: list[RepoRunResult], summaries: list[LanguageSummary], path: str) -> None:
    payload = {
        "repo_count": len(repo_results),
        "language_summary": [dataclasses.asdict(s) for s in summaries],
        "repos": [
            {
                "repo": r.repo,
                "index_metrics": dataclasses.asdict(r.metrics),
                "archetype_count": len(r.archetypes),
                "results": [dataclasses.asdict(a) for a in r.archetypes],
            }
            for r in repo_results
        ],
    }
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.polyglot_33_matrix",
        description="Run the 33-prompt archetype taxonomy against real repositories across all six languages SCE supports.",
    )
    parser.add_argument("--repo", choices=sorted(REPOS), default=None, help="Run a single repository by key.")
    parser.add_argument("--run-all", action="store_true", help="Run every repository in the built-in six-language suite.")
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
    parser.add_argument("--dry-run", action="store_true", help="Index the repo(s) and build every context, printing sizes, without calling the API.")
    return parser


def select_archetype_ids(spec: str) -> list[int]:
    if spec == "all":
        return [t.archetype_id for t in PROMPT_TEMPLATES]
    tokens = [s.strip() for s in spec.split(",") if s.strip()]
    selected: list[int] = []
    unknown: list[str] = []
    for token in tokens:
        if token.isdigit() and int(token) in PROMPT_TEMPLATES_BY_ID:
            selected.append(int(token))
        elif token in PROMPT_TEMPLATES_BY_SLUG:
            selected.append(PROMPT_TEMPLATES_BY_SLUG[token].archetype_id)
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

    if not args.run_all and not args.repo:
        parser.error("pass --run-all or --repo NAME")
    repo_keys = list(REPOS) if args.run_all else [args.repo]

    prompts_spec = str(args.prompt) if args.prompt is not None else args.prompts
    try:
        archetype_ids = select_archetype_ids(prompts_spec)
        variants = select_variants(args.variants)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    client: LLMClient | None = None
    if not args.dry_run:
        try:
            client = LLMClient(price_in_override=args.price_in, price_out_override=args.price_out)
        except OpenAIClientError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    repo_results: list[RepoRunResult] = []
    had_error = False
    for key in repo_keys:
        try:
            result, dry_run_text = run_repo(
                key, archetype_ids, variants, args.budget, args.model, client, args.temperature,
                Path(args.cache_dir), args.force_clone, args.dry_run,
            )
        except PolyglotMatrixError as exc:
            print(f"error: {exc}", file=sys.stderr)
            had_error = True
            continue
        repo_results.append(result)
        if dry_run_text is not None:
            print()
            print(dry_run_text)

    if not repo_results:
        return 1
    if args.dry_run:
        return 0 if not had_error else 1

    all_archetype_results = [a for r in repo_results for a in r.archetypes]
    summaries = [summarize_repo(r) for r in repo_results]

    print()
    for r in repo_results:
        print(render_index_metrics(r.metrics))
        print()
    print(render_comparative_summary(summaries))
    print()
    print(render_summary_table(all_archetype_results))
    print()
    print(render_detail(all_archetype_results))
    print(render_cost_summary(all_archetype_results))

    if args.report:
        write_report(repo_results, summaries, args.report)
        print(f"\nWrote report to {args.report}")

    all_passed = all(v.passed for a in all_archetype_results for v in (a.raw, a.sce) if v is not None)
    return 0 if (not had_error and all_passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
