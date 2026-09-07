"""The `PromptArchetype` data model shared by all 33 taxonomy entries in
`benchmarks/prompt_taxonomy/archetypes.py`, plus the prompt-construction and
mechanical-scoring helpers `large_repo_prompt_matrix.py` runs generically
against every archetype.

The 33 archetypes the caller asked for span two very different axes:
  - **content** (what the model is asked to do: explain, patch, classify,
    document, ...), and
  - **prompting technique** (zero/one/few-shot, chain-of-thought, ReAct,
    multi-turn chaining, self-consistency sampling, ...).

Rather than writing 33 bespoke scorers, every archetype is described
declaratively - target symbol, prompt text, optional system-prompt
override, optional few-shot demonstrations, optional follow-up turns for
multi-turn techniques, and a small set of *contract* fields (does it expect
code, what tags must it avoid, what format/substrings must the response
contain). A single generic pipeline in `large_repo_prompt_matrix.py` then
builds the right prompt shape and runs the right mechanical checks purely
from those fields - never an LLM judge.
"""
from __future__ import annotations

import ast
import builtins as _builtins_module
import re
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "You are a senior software engineer working in a large, real production codebase (Django). "
    "You will be given a context package describing part of the codebase - either full source "
    "files or a variable-resolution slice with interface contracts for less-relevant code - plus "
    "a task. Use ONLY the exact symbols, function/method names, and signatures that literally "
    "appear in the given context; never invent a helper, method, or class that is not shown. If "
    "the task asks for code, respond with a brief explanation followed by a single fenced Python "
    "code block containing ONLY the requested function/method/class - no surrounding boilerplate "
    "beyond what was asked for."
)


@dataclass(frozen=True)
class PromptArchetype:
    archetype_id: int  # 1-33, matches the caller's numbered taxonomy
    slug: str
    title: str
    cluster: str  # short grouping label, e.g. "Interaction style", "Prompting technique"
    # The symbol Prism queries / the raw dump's call-chain closure is scoped
    # around. Always a real, verified-to-exist qualified name in the target
    # repository (see archetypes.py's module docstring for how these were
    # confirmed against a real indexed django/django clone).
    target: str
    task_prompt: str
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Whether the model is expected to return a single fenced, parseable
    # Python code block. When True: a missing/unparseable code block is a
    # hard failure, and the syntax/hallucination/tag/signature checks below
    # all apply. When False (e.g. an explanation, an audit, an open-ended
    # design question): those checks are skipped entirely (reported as
    # "not applicable"), not silently marked as passed or failed.
    expects_code: bool = True
    # Demonstration blocks prepended to the user prompt, in order - empty
    # for zero-shot archetypes, one entry for one-shot, three for few-shot.
    few_shot_examples: tuple[str, ...] = ()
    # Additional user turns sent *after* the model's first reply, for
    # multi-turn techniques (iterative follow-up, prompt chaining,
    # conversational, reflective self-critique). The archetype is scored on
    # its FINAL turn's response; earlier turns are still recorded in the
    # report for transparency. Empty means single-turn.
    follow_up_prompts: tuple[str, ...] = ()
    # "json" (or "yaml") - the contract check tries to parse the response's
    # fenced block (or, failing that, the whole response) with the matching
    # parser.
    output_format: str | None = None
    # Simple case-insensitive substrings the code block's *source text*
    # must not contain (e.g. a banned raw-SQL escape hatch). Distinct from
    # forbidden_tags, which checks *called symbols'* semantic tags, not text.
    negative_constraints: tuple[str, ...] = ()
    # Metamodel tags (e.g. "#db_write") that no call in the generated code
    # may resolve to, via best-effort simple-name lookup against the
    # target repo's tag matrix (the same no-type-inference limitation
    # `find_hallucinated_calls` in validate_llm_accuracy.py already
    # documents - this is a heuristic guard, not a proof).
    forbidden_tags: tuple[str, ...] = ()
    # If True, the generated code's `def` line (name + parameter list) must
    # match the original target symbol's signature exactly - for negative/
    # constraint archetypes that explicitly forbid signature changes.
    preserve_signature: bool = False
    # Case-insensitive "at least one of these substrings must appear
    # somewhere in the response text" - e.g. a closed-ended prompt that
    # must answer YES or NO.
    required_any_substrings: tuple[str, ...] = ()
    # Case-insensitive "all of these substrings must appear" - e.g. a
    # balanced/bias-mitigating prompt that must actually discuss both
    # sides of a trade-off.
    required_all_substrings: tuple[str, ...] = ()
    # Regex patterns (searched case-insensitively) that must each match
    # somewhere in the response - for structural requirements a plain
    # substring can't express (e.g. "contains a question mark").
    required_regexes: tuple[str, ...] = ()
    # Like `required_regexes`, but "at least one must match" rather than
    # "all must match" - for a requirement satisfiable multiple ways (e.g.
    # a Socratic prompt is honestly interrogative whether it literally uses
    # "?" or phrases the question as "Can you clarify ..." - literal
    # question-mark punctuation is a real but incomplete proxy for "asked a
    # question", confirmed by a live gpt-4o-mini response that posed a
    # genuine clarifying question entirely in declarative sentences).
    required_any_regexes: tuple[str, ...] = ()
    # >1 for self-consistency sampling (archetype 14): the prompt is sent
    # this many times at a higher temperature and scored for whether a real
    # symbol name reaches consensus (mentioned in a majority of samples).
    sample_count: int = 1
    notes: str = ""


# --------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------- #
def build_user_prompt(archetype: PromptArchetype, context_text: str) -> str:
    demo_block = ""
    if archetype.few_shot_examples:
        numbered = "\n\n".join(
            f"Demonstration {i}:\n{example}" for i, example in enumerate(archetype.few_shot_examples, start=1)
        )
        demo_block = f"{numbered}\n\n"
    return f"CONTEXT PACKAGE:\n{context_text}\n\n{demo_block}TASK:\n{archetype.task_prompt}"


# --------------------------------------------------------------------- #
# Mechanical scoring helpers (no LLM judge anywhere)
# --------------------------------------------------------------------- #
_CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)\n```", re.DOTALL)
_PREFERRED_FENCE_LANGS = {"python", "py", "python3", ""}
_JSON_FENCE_LANGS = {"json"}
_YAML_FENCE_LANGS = {"yaml", "yml"}


def extract_first_code_block(text: str, preferred_langs: frozenset[str] = frozenset(_PREFERRED_FENCE_LANGS)) -> str | None:
    matches = _CODE_FENCE_RE.findall(text)
    if not matches:
        return None
    for lang, code in matches:
        if lang.lower() in preferred_langs:
            return code
    return matches[0][1]


def _collect_call_simple_names(code: str) -> set[str]:
    tree = ast.parse(code)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


_BUILTIN_NAMES = frozenset(dir(_builtins_module))
_COMMON_STDLIB_METHOD_NAMES = frozenset(
    {
        "get", "keys", "values", "items", "append", "extend", "pop", "update",
        "join", "split", "strip", "lower", "upper", "replace", "format",
        "startswith", "endswith", "encode", "decode", "copy", "sort", "index",
        "add", "remove", "discard", "count", "find",
        # logging's module-level API and hmac/secrets' constant-time
        # comparison helper - confirmed, real false positives from a live
        # gpt-4o-mini run against django/django (archetypes 5 and 17 both
        # legitimately reached for these): common enough, and specific
        # enough in name, to allowlist the same way container/string
        # methods already are above.
        "getLogger", "debug", "info", "warning", "warn", "error", "exception", "critical",
        "compare_digest",
    }
)


def find_hallucinated_calls(code: str, known_simple_names: frozenset[str]) -> tuple[str, ...]:
    """Every call in `code` whose simple name is neither a real symbol's
    simple name in the target repo, a Python builtin, nor a common built-in
    container/string method - mirrors `validate_llm_accuracy.py`'s checker.

    `known_simple_names` (built by `RepoIndex` from
    `GlobalSymbolTable.all_qualified_names()`) is kind-agnostic by
    construction: it includes attribute-kind symbols (module/class/instance
    assignments, e.g. `_iterable_class = ModelIterable`) alongside
    functions/methods/classes, so a call to one of those isn't mistaken for
    a hallucination.
    """
    called = _collect_call_simple_names(code)
    unknown = sorted(
        name for name in called if name not in known_simple_names and name not in _BUILTIN_NAMES and name not in _COMMON_STDLIB_METHOD_NAMES
    )
    return tuple(unknown)


def find_forbidden_tag_violations(
    code: str, forbidden_tags: tuple[str, ...], tag_matrix: dict[str, set[str]], simple_name_to_qualified: dict[str, set[str]]
) -> tuple[str, ...]:
    """Best-effort: for every call in `code`, resolve its simple name to
    every real symbol sharing that name (no type inference is available -
    see `find_hallucinated_calls`'s docstring), and flag it if ANY of those
    candidates carries one of `forbidden_tags`. A heuristic guard against
    the model quietly reintroducing a forbidden operation (e.g. a
    `#db_write`) under a plausible call name, not a proof of absence.
    """
    if not forbidden_tags:
        return ()
    try:
        called = _collect_call_simple_names(code)
    except SyntaxError:
        return ()
    violations: set[str] = set()
    for name in called:
        for qname in simple_name_to_qualified.get(name, ()):
            tags = tag_matrix.get(qname, set())
            if any(tag in tags for tag in forbidden_tags):
                violations.add(name)
    return tuple(sorted(violations))


_QUALIFIED_NAME_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*){2,}\b")


def find_referenced_symbol_mentions(
    text: str,
    known_qualified_names: frozenset[str],
    root_prefixes: tuple[str, ...],
    known_module_paths: frozenset[str] = frozenset(),
    known_simple_names: frozenset[str] = frozenset(),
) -> tuple[str, tuple[str, ...]]:
    """Scans free-form prose (not just a code block) for dotted,
    qualified-name-shaped tokens rooted in the target repo's own package(s)
    (e.g. `django.contrib.auth.tokens.PasswordResetTokenGenerator`) and
    checks each one against the repo's real `GlobalSymbolTable`, or (since
    a model legitimately talks about *modules*, not just leaf symbols - e.g.
    `django.contrib.auth.backends` on its own) against `known_module_paths`
    too. Gives every archetype - even the purely explanatory, non-code ones
    - a real, mechanical hallucination check: a model that writes out a
    fully qualified path is claiming that path is real, so a fabricated
    one is caught the same way a fabricated method *call* is elsewhere.

    A third, narrower fallback covers two real patterns a purely
    file-path-based qualified name can't represent directly:

      - an *instance-attribute chain*, like `django.db.router.db_for_write`
        - real, correct django usage (`router` is a module-level
        `ConnectionRouter()` instance; `db_for_write` a real method on that
        class) that doesn't match any single indexed qualified name, since
        `GlobalSymbolTable` tracks one attribute-assignment hop at a time,
        not chained instance attribute access;
      - a *public re-export*, like `django.urls.get_resolver` - real,
        idiomatic django usage (the function is defined in
        `django.urls.base` but re-exported from the `django.urls` package's
        `__init__.py`, which is how it's actually imported in practice) that
        doesn't match its own *defining* module's qualified name, since
        `GlobalSymbolTable` indexes by defining file, not by every place a
        symbol gets re-exported.

    Both are accepted the same way: when the mention's prefix (everything
    before the last `.`) is itself a real qualified symbol *or* a real
    module path, and the trailing segment is some real symbol's simple name
    anywhere in the repo - the same no-type-inference, simple-name-only
    heuristic `find_hallucinated_calls` already uses for calls, extended
    here to prose mentions of the same shape.

    Returns (mentioned, unknown) - all qualified-looking mentions found,
    and the subset that is neither a real symbol, a real module path, nor a
    recognized attribute-chain/re-export mention.
    """
    mentioned = {m.group(0) for m in _QUALIFIED_NAME_RE.finditer(text) if m.group(0).startswith(root_prefixes)}

    def _is_known(name: str) -> bool:
        if name in known_qualified_names or name in known_module_paths:
            return True
        prefix, _, suffix = name.rpartition(".")
        if not prefix or not suffix or suffix not in known_simple_names:
            return False
        return prefix in known_qualified_names or prefix in known_module_paths

    unknown = tuple(sorted(name for name in mentioned if not _is_known(name)))
    return tuple(sorted(mentioned)), unknown


def function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """`name(arg list)`, normalized via `ast.unparse` so formatting/whitespace
    differences between the original source and the model's response don't
    cause a false mismatch."""
    return f"{node.name}({ast.unparse(node.args)})"


def check_signature_preserved(code: str, original_signature: str) -> bool:
    """True iff `code`'s first top-level function/method has the exact same
    name and parameter list as `original_signature` (from
    `function_signature` on the real, original symbol) - an exact,
    normalization-tolerant comparison, not a fuzzy one.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return function_signature(node) == original_signature.strip()
    return False


def check_output_format(text: str, output_format: str | None) -> bool | None:
    if output_format is None:
        return None
    import json

    fence_langs = _JSON_FENCE_LANGS if output_format == "json" else _YAML_FENCE_LANGS
    block = extract_first_code_block(text, preferred_langs=fence_langs) or extract_first_code_block(text)
    candidate = block if block is not None else text
    if output_format == "json":
        try:
            json.loads(candidate)
            return True
        except (json.JSONDecodeError, ValueError):
            return False
    if output_format == "yaml":
        try:
            import yaml

            yaml.safe_load(candidate)
            return True
        except Exception:
            return False
    return None


def check_negative_constraints(code_or_text: str, negative_constraints: tuple[str, ...]) -> tuple[str, ...]:
    lowered = code_or_text.lower()
    return tuple(c for c in negative_constraints if c.lower() in lowered)


def check_required_substrings(
    text: str,
    required_any: tuple[str, ...],
    required_all: tuple[str, ...],
    required_regexes: tuple[str, ...],
    required_any_regexes: tuple[str, ...] = (),
) -> dict[str, bool]:
    lowered = text.lower()
    checks: dict[str, bool] = {}
    if required_any:
        checks["required_any_substrings_present"] = any(s.lower() in lowered for s in required_any)
    if required_all:
        checks["required_all_substrings_present"] = all(s.lower() in lowered for s in required_all)
    if required_regexes:
        checks["required_regexes_matched"] = all(re.search(p, text, re.IGNORECASE) for p in required_regexes)
    if required_any_regexes:
        checks["required_any_regex_matched"] = any(re.search(p, text, re.IGNORECASE) for p in required_any_regexes)
    return checks


def check_self_consistency(samples: list[str], known_qualified_names: frozenset[str], root_prefixes: tuple[str, ...]) -> tuple[bool, str | None]:
    """Consensus check for self-consistency sampling: does any single real
    symbol get mentioned (as a fully qualified name) in a majority of the
    sampled responses? Purely mechanical (counting), never a judge call on
    whether the *reasoning* was good.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for sample in samples:
        mentioned, _unknown = find_referenced_symbol_mentions(sample, known_qualified_names, root_prefixes)
        counts.update(set(mentioned) & known_qualified_names)
    if not counts:
        return False, None
    symbol, count = counts.most_common(1)[0]
    majority = (len(samples) // 2) + 1
    return count >= majority, symbol
