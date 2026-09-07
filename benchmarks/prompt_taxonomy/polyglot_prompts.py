"""33 language-agnostic prompt-archetype *templates*, each a single
`{target}`-parameterized task applied uniformly to one real, verified
entrypoint symbol per language repository - the polyglot counterpart to
`archetypes.py`'s 33 django-specific, individually-targeted archetypes.

`archetypes.py` picks a different real django symbol for each of its 33
entries; this module instead follows `benchmarks/polyglot_33_matrix.py`'s
own repo table (one representative, richly-connected entrypoint per
language - `Model.save` for Python, `Hono.route` for TypeScript,
`handleHTTPRequest` for Go, ...) and reuses the exact same 33-item taxonomy
against every one of them, so the SAME prompt shape can be compared across
six architecturally distinct, real codebases in six different languages.

Every template's `instantiate()` fills `{target}` (and, for the system
prompt, `{language}`/`{repo_description}`) into a real `PromptArchetype` -
the identical dataclass `benchmarks/prompt_taxonomy/spec.py` already
defines and `large_repo_prompt_matrix.py` already knows how to build
prompts from and mechanically score - so nothing about prompt construction
or contract-checking needs to be reinvented per language. What DOES change
per language is what "checking the code" means (Python `ast.parse` vs. a
Tree-sitter reparse for the other five, and a regex-based call-name
extractor standing in for `ast`-based hallucination detection where a
Python AST isn't available) - that generalization lives in
`polyglot_33_matrix.py`, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

from benchmarks.prompt_taxonomy.spec import PromptArchetype

DEFAULT_SYSTEM_PROMPT_TEMPLATE = (
    "You are a senior software engineer working in a real, production {language} codebase "
    "({repo_description}). You will be given a context package describing part of the codebase - "
    "either full source files or a variable-resolution slice with interface contracts for "
    "less-relevant code - plus a task. Use ONLY the exact symbols, function/method names, and "
    "signatures that literally appear in the given context; never invent a helper, method, or class "
    "that is not shown. If the task asks for code, respond with a brief explanation followed by a "
    "single fenced code block (tagged with the correct language) containing ONLY the requested "
    "function/method/class - no surrounding boilerplate beyond what was asked for."
)

# Overrides DEFAULT_SYSTEM_PROMPT_TEMPLATE for archetype 11 (Instruction/System
# persona) - the archetype's whole point is a stricter system-level persona,
# not the default one every other archetype shares.
STRICT_COMPLIANCE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a strict compliance-focused senior engineer reviewing changes to a real, production "
    "{language} codebase ({repo_description}). You must introduce ZERO architectural invariant "
    "violations under any circumstance. If a requested change would violate an invariant visible in "
    "the given context (e.g. a #db_write path with no upstream #auth_guard), refuse that unsafe "
    "portion explicitly, explain exactly which invariant it would violate, and provide the safe "
    "alternative instead. Use ONLY the exact symbols and signatures shown in the context; never "
    "invent one."
)


@dataclass(frozen=True)
class PolyglotPromptTemplate:
    archetype_id: int  # 1-33, matches the caller's numbered taxonomy
    slug: str
    title: str
    cluster: str
    task_prompt_template: str  # contains a literal "{target}" placeholder
    system_prompt_template: str = DEFAULT_SYSTEM_PROMPT_TEMPLATE
    expects_code: bool = True
    few_shot_examples: tuple[str, ...] = ()
    follow_up_prompts: tuple[str, ...] = ()
    output_format: str | None = None
    negative_constraints: tuple[str, ...] = ()
    forbidden_tags: tuple[str, ...] = ()
    required_any_substrings: tuple[str, ...] = ()
    required_all_substrings: tuple[str, ...] = ()
    required_regexes: tuple[str, ...] = ()
    required_any_regexes: tuple[str, ...] = ()
    sample_count: int = 1
    notes: str = ""

    def instantiate(self, target: str, language: str, repo_description: str) -> PromptArchetype:
        """Fill this template's `{target}`/`{language}`/`{repo_description}`
        placeholders and return a real `PromptArchetype` - the same
        dataclass `large_repo_prompt_matrix.py`'s django harness uses, so
        `benchmarks.prompt_taxonomy.spec`'s prompt-construction and
        mechanical-scoring helpers apply completely unchanged.
        """
        return PromptArchetype(
            archetype_id=self.archetype_id,
            slug=self.slug,
            title=self.title,
            cluster=self.cluster,
            target=target,
            task_prompt=self.task_prompt_template.format(target=target),
            system_prompt=self.system_prompt_template.format(language=language, repo_description=repo_description),
            expects_code=self.expects_code,
            few_shot_examples=self.few_shot_examples,
            follow_up_prompts=self.follow_up_prompts,
            output_format=self.output_format,
            negative_constraints=self.negative_constraints,
            forbidden_tags=self.forbidden_tags,
            preserve_signature=False,  # not checked in the polyglot harness - see polyglot_33_matrix.py
            required_any_substrings=self.required_any_substrings,
            required_all_substrings=self.required_all_substrings,
            required_regexes=self.required_regexes,
            required_any_regexes=self.required_any_regexes,
            sample_count=self.sample_count,
            notes=self.notes,
        )


# --------------------------------------------------------------------- #
# The 33 templates
# --------------------------------------------------------------------- #
PROMPT_TEMPLATES: tuple[PolyglotPromptTemplate, ...] = (
    PolyglotPromptTemplate(
        1, "informational-query", "Informational / Query", "Content: informational",
        "What methods reachable from `{target}` are responsible for state persistence or I/O? "
        "List each one with a one-sentence justification, based only on the given context.",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        2, "task-instruction", "Task-Specific / Instruction", "Content: instruction-following",
        "Implement a method extension conforming to `{target}`'s interface contract, without "
        "altering `{target}`'s own existing signature or behavior.",
    ),
    PolyglotPromptTemplate(
        3, "creative-generative", "Creative / Generative", "Content: creative",
        "Propose an asynchronous event dispatcher that fits the current module architecture around "
        "`{target}`, and sketch its core implementation.",
    ),
    PolyglotPromptTemplate(
        4, "analytical-reasoning", "Analytical / Reasoning", "Content: analytical",
        "Analyze error propagation and exception/panic bubbling starting from `{target}`: what can "
        "fail, and where does the failure actually surface to a caller?",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        5, "transformation-rewrite", "Transformation / Rewrite", "Content: rewrite",
        "Refactor `{target}` to use strict typings / early guard clauses instead of nested "
        "conditionals, without altering its public signature.",
    ),
    PolyglotPromptTemplate(
        6, "classification-labeling", "Classification / Labeling", "Content: classification",
        "Categorize every immediate callee of `{target}` shown in the context into exactly one of: "
        "READ, WRITE, or COMPUTE. Respond as a bulleted list, one callee per line.",
        expects_code=False,
        required_any_regexes=(r"^\s*[-*]\s", r"^\s*\d+[.)]\s"),
    ),
    PolyglotPromptTemplate(
        7, "retrieval-fact-check", "Retrieval / Fact-Check", "Content: fact-check",
        "Does `{target}` perform an external I/O operation or a database write, under any code "
        "branch shown in the context? Answer precisely, citing the specific call that does (or "
        "confirming none does).",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        8, "zero-shot", "Zero-Shot", "Prompting technique: shot count",
        "Without any reference example, implement a thin validation wrapper around `{target}` that "
        "rejects a null/empty/invalid input before delegating to the real implementation.",
    ),
    PolyglotPromptTemplate(
        9, "one-shot", "One-Shot", "Prompting technique: shot count",
        "Following the one reference example above of how an existing caller invokes `{target}`, "
        "implement a second, equally well-formed caller for a different realistic input.",
        few_shot_examples=(
            "Reference: an existing, well-formed call site invokes the target with fully valid "
            "arguments and immediately checks its result before continuing - it never assumes success.",
        ),
    ),
    PolyglotPromptTemplate(
        10, "few-shot", "Few-Shot", "Prompting technique: shot count",
        "Following the three reference examples above (three different existing call-site patterns "
        "around `{target}`), implement one more caller that combines the safety habits all three "
        "demonstrate.",
        few_shot_examples=(
            "Example 1: a caller validates its input before invoking the target.",
            "Example 2: a caller wraps the invocation in error handling and logs failures.",
            "Example 3: a caller checks the target's return value before using it downstream.",
        ),
    ),
    PolyglotPromptTemplate(
        11, "instruction-system-persona", "Instruction / System", "Prompting technique: system persona",
        "Implement a safe wrapper for `{target}` that preserves every architectural invariant "
        "visible in the given context. Any invariant violation is unacceptable.",
        system_prompt_template=STRICT_COMPLIANCE_SYSTEM_PROMPT_TEMPLATE,
    ),
    PolyglotPromptTemplate(
        12, "role-persona", "Role / Persona", "Prompting technique: role framing",
        "As an infrastructure engineer auditing latency bottlenecks, evaluate `{target}`'s call "
        "chain for its single highest-risk latency source, and propose the one change with the "
        "greatest expected impact.",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        13, "chain-of-thought", "Chain-of-Thought (CoT)", "Prompting technique: reasoning style",
        "Step by step, trace from `{target}` (the public entrypoint) down to its underlying sinks. "
        "Number each hop explicitly (1, 2, 3, ...) before giving your final answer.",
        expects_code=False,
        required_any_regexes=(r"^\s*1[.)]\s", r"\bstep\s*1\b"),
    ),
    PolyglotPromptTemplate(
        14, "self-consistency", "Self-Consistency", "Prompting technique: sampling",
        "State the single fully-qualified symbol most likely to be the ultimate sink reached from "
        "`{target}`'s call chain, and nothing else beyond a one-sentence justification.",
        expects_code=False,
        sample_count=5,
    ),
    PolyglotPromptTemplate(
        15, "tree-of-thoughts", "Tree-of-Thoughts (ToT)", "Prompting technique: reasoning style",
        "Explore three distinct architectural patterns for caching the result of `{target}`, "
        "labeled 'Strategy 1', 'Strategy 2', and 'Strategy 3', then state which one you recommend "
        "and why.",
        expects_code=False,
        required_regexes=(r"strategy\s*1", r"strategy\s*2", r"strategy\s*3"),
    ),
    PolyglotPromptTemplate(
        16, "react", "ReAct", "Prompting technique: reasoning style",
        "Using an alternating Reason+Act trace (a 'Thought:' line followed by an 'Action:' line, "
        "repeated as needed), determine which module you would need to inspect next after "
        "`{target}`, consulting only the interface contracts shown in the context.",
        expects_code=False,
        required_regexes=(r"thought:", r"action:"),
    ),
    PolyglotPromptTemplate(
        17, "negative-constraint", "Negative / Constraint", "Content: constrained",
        "Add structured logging immediately before and after `{target}` executes. You are STRICTLY "
        "FORBIDDEN from invoking any `#db_write`-tagged sink anywhere in the code you add.",
        forbidden_tags=("#db_write",),
    ),
    PolyglotPromptTemplate(
        18, "output-formatting", "Output Formatting", "Content: structured output",
        "Emit `{target}`'s own interface contract (its name, parameter names, and return type, "
        "exactly as shown in the context) as a single JSON object with keys 'name', 'parameters', "
        "and 'returns'. Output ONLY that JSON object - no surrounding prose, no code fence language "
        "other than json.",
        expects_code=False,
        output_format="json",
    ),
    PolyglotPromptTemplate(
        19, "bias-mitigating-balanced", "Bias-Mitigating / Balanced", "Content: balanced analysis",
        "Analyze both the performance strengths and the design weaknesses of `{target}`'s current "
        "implementation, giving genuinely comparable attention to each side rather than favoring one.",
        expects_code=False,
        required_all_substrings=("strength", "weakness"),
    ),
    PolyglotPromptTemplate(
        20, "iterative-follow-up", "Iterative / Follow-Up", "Prompting technique: multi-turn",
        "Identify the single boundary condition inside `{target}` most likely to cause an "
        "off-by-one, null/nil-dereference, or unhandled-empty-input error.",
        expects_code=False,
        follow_up_prompts=(
            "Now patch exactly that boundary condition. Show only the corrected function, as a "
            "single fenced code block.",
        ),
    ),
    PolyglotPromptTemplate(
        21, "prompt-chaining", "Prompt Chaining", "Prompting technique: multi-turn",
        "Audit `{target}`'s architecture and list every downstream sink it can reach, in order of "
        "call-graph distance.",
        expects_code=False,
        follow_up_prompts=(
            "Using only the sinks you just listed, generate a patch that adds a guard clause "
            "immediately before the riskiest one. Respond with a single fenced code block.",
        ),
    ),
    PolyglotPromptTemplate(
        22, "meta-prompt", "Meta-Prompt", "Prompting technique: meta",
        "Critique the following retrieval prompt for querying `{target}` using Prism's own "
        "architectural tag grammar (e.g. #db_write, #auth_guard, #external_io, #state_mutation), "
        "then rewrite it to be more precise: \"Tell me about {target}.\"",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        23, "conversational", "Conversational", "Content: conversational",
        "I'm new to this codebase - can you walk me through, in plain conversational terms, what "
        "actually happens during a single call to `{target}`?",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        24, "open-ended", "Open-Ended", "Content: open-ended",
        "Propose an architecture for decoupling `{target}` into a standalone microservice or "
        "background worker, discussing the real trade-offs of doing so.",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        25, "closed-ended", "Closed-Ended", "Content: closed-ended",
        "Strict yes/no: does `{target}` ever return without performing any mutating side effect, on "
        "at least one branch? Answer with exactly one word, YES or NO, followed by one sentence of "
        "justification.",
        expects_code=False,
        required_any_substrings=("yes", "no"),
    ),
    PolyglotPromptTemplate(
        26, "hypothetical-counterfactual", "Hypothetical / Counterfactual", "Content: counterfactual",
        "Hypothetically, if whatever `#auth_guard` is enforced somewhere upstream of `{target}` were "
        "removed entirely, what specific invariant would be violated, and what is the worst-case "
        "consequence?",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        27, "socratic", "Socratic", "Prompting technique: Socratic",
        "Before writing any implementation that touches `{target}`, ask me explicit clarifying "
        "questions you would need answered first, each ending with a question mark ('?').",
        expects_code=False,
        required_any_regexes=(r"\?", r"can you clarify", r"what is the expected", r"how should"),
    ),
    PolyglotPromptTemplate(
        28, "reflective", "Reflective", "Prompting technique: self-critique",
        "Here is a proposed one-line patch to `{target}`: `# TODO: patch`. Critique it against "
        "`{target}`'s real interface contract, and identify at least one concrete edge case it "
        "fails to handle.",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        29, "code-generation", "Code-Generation", "Content: code generation",
        "Generate a complete new function whose parameter and return types match `{target}`'s "
        "direct callees exactly, implementing a retry wrapper around one of those callees.",
    ),
    PolyglotPromptTemplate(
        30, "debugging-explanation", "Debugging / Explanation", "Content: debugging",
        "Explain the most likely root cause of a simulated concurrency or transaction-rollback "
        "failure occurring inside `{target}`, reasoning only from what the given context actually "
        "shows.",
        expects_code=False,
    ),
    PolyglotPromptTemplate(
        31, "architecture-design", "Architecture / Design", "Content: architecture",
        "Map the complete path from the nearest inbound route-handler / entrypoint down to the "
        "outbound persistence sink reachable from `{target}`, as an ordered, numbered list of hops.",
        expects_code=False,
        required_any_regexes=(r"^\s*1[.)]\s", r"\bhop\s*1\b"),
    ),
    PolyglotPromptTemplate(
        32, "documentation", "Documentation", "Content: documentation",
        "Generate idiomatic inline documentation for `{target}` in the target language's own native "
        "doc-comment style (docstring / TSDoc / JSDoc / GoDoc / Javadoc / XML doc, as appropriate), "
        "covering its parameters, return value, and any errors/exceptions it raises or propagates.",
        expects_code=False,
        required_any_regexes=(r'"""', r"/\*\*", r"///", r"^\s*#\s"),
    ),
    PolyglotPromptTemplate(
        33, "data-analysis", "Data / Analysis", "Content: data extraction",
        "Extract and tabulate, as a Markdown table, the parameter names, exception/error types, and "
        "return types across every direct callee of `{target}` shown in the context.",
        expects_code=False,
        required_any_regexes=(r"\|.+\|.+\|",),
    ),
)

PROMPT_TEMPLATES_BY_ID: dict[int, PolyglotPromptTemplate] = {t.archetype_id: t for t in PROMPT_TEMPLATES}
PROMPT_TEMPLATES_BY_SLUG: dict[str, PolyglotPromptTemplate] = {t.slug: t for t in PROMPT_TEMPLATES}

assert len(PROMPT_TEMPLATES) == 33, f"expected exactly 33 prompt templates, found {len(PROMPT_TEMPLATES)}"
assert list(PROMPT_TEMPLATES_BY_ID) == list(range(1, 34)), "archetype ids must be exactly 1..33, no gaps/dupes"

__all__ = [
    "DEFAULT_SYSTEM_PROMPT_TEMPLATE",
    "STRICT_COMPLIANCE_SYSTEM_PROMPT_TEMPLATE",
    "PolyglotPromptTemplate",
    "PROMPT_TEMPLATES",
    "PROMPT_TEMPLATES_BY_ID",
    "PROMPT_TEMPLATES_BY_SLUG",
]
