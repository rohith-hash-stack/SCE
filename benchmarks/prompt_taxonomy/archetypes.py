"""The 33 prompt archetypes, numbered exactly as specified: a structured
taxonomy of developer/user interaction styles, each pointed at a real
target symbol in `django/django` and confirmed to exist there.

Targets and their real-repo token footprints were confirmed by actually
cloning and indexing `django/django` (42,282 symbols, 79,268 CALLS edges;
see `large_repo_prompt_matrix.py`'s module docstring for the full indexing
report) and inspecting the real concrete graph and raw call-chain closures
- not guessed. Every target below resolves to a real, non-isolated
function/method/class in that graph.

One honest tagger-coverage note, parallel to the ones already documented in
`benchmarks/README.md` for httpx/flask/marshmallow: `#route_handler` never
fires anywhere in django's own source (Django's URL routing is table-driven
via `urls.py`, not decorator-based route registration the way Flask's
`@app.route` is), so archetype 31 (which the spec frames around a
`#route_handler` -> `#db_write` path) uses `BaseHandler.get_response` - the
actual WSGI/ASGI inbound entrypoint - as its starting point instead, noted
explicitly in that archetype's own task prompt rather than papered over.
"""
from __future__ import annotations

from benchmarks.prompt_taxonomy.spec import PromptArchetype

# --------------------------------------------------------------------- #
# Few-shot demonstration blocks (archetypes 9 and 10). Generic, hand-built
# illustrations of "a stated contract -> a conforming implementation" -
# deliberately not lifted from django's own source, so the demonstration
# teaches the *pattern* (validate/derive an input before using it) without
# asserting anything about django internals that the SCE/raw context itself
# is responsible for supplying accurately.
# --------------------------------------------------------------------- #
_ONE_SHOT_EXAMPLE = """Contract: a mixin method that must call the object's existing accessor \
method to get its required inputs before making an access decision, rather than reading \
private state directly.
Implementation:
```python
def has_any_permission(self, user, obj=None):
    required = self.get_permission_required()
    return any(user.has_perm(perm, obj) for perm in required)
```"""

_FEW_SHOT_EXAMPLE_1 = """Contract: a backend method that looks up a user by a natural key, then \
defers the actual credential check to the same password-verification helper every other \
backend method in this class uses.
Implementation:
```python
def authenticate_by_username(self, request, username=None, password=None, **kwargs):
    user = UserModel._default_manager.get(username=username)
    if user.check_password(password) and self.user_can_authenticate(user):
        return user
    return None
```"""

_FEW_SHOT_EXAMPLE_2 = """Contract: a backend method that must call the same \
`user_can_authenticate` gate every other successful-lookup path in this class calls, even on \
an alternate lookup field.
Implementation:
```python
def authenticate_by_phone(self, request, phone_number=None, password=None, **kwargs):
    user = UserModel._default_manager.get(phone_number=phone_number)
    if not user.check_password(password):
        return None
    if not self.user_can_authenticate(user):
        return None
    return user
```"""

_FEW_SHOT_EXAMPLE_3 = """Contract: a backend method that swallows the lookup's "not found" \
exception the same way every other lookup-based method in this class does, rather than letting \
it propagate.
Implementation:
```python
def authenticate_by_token(self, request, token=None, **kwargs):
    try:
        user = UserModel._default_manager.get(auth_token=token)
    except UserModel.DoesNotExist:
        return None
    return user if self.user_can_authenticate(user) else None
```"""

_SECURITY_AUDITOR_SYSTEM_PROMPT = (
    "You are a senior Django security auditor. Adhere strictly to invariant guards: never remove "
    "or weaken an existing authentication or permission check. Use ONLY the exact symbols and "
    "signatures shown in the given context. If you find a genuine issue, patch it while preserving "
    "every existing check; if you find none, say so explicitly rather than inventing one."
)

_PERFORMANCE_PERSONA_SYSTEM_PROMPT = (
    "You are an infrastructure engineer whose sole focus is production query performance. You "
    "care about database round-trips, N+1 query patterns, and query-plan cost above all else. Use "
    "ONLY the exact symbols and signatures shown in the given context."
)

ARCHETYPES: tuple[PromptArchetype, ...] = (
    PromptArchetype(
        archetype_id=1,
        slug="informational_query",
        title="Informational / Query",
        cluster="Content & Purpose",
        target="django.db.models.base.Model.save",
        task_prompt=(
            "What methods reachable from `django.db.models.base.Model.save` are responsible for "
            "flushing state to the database? List them by fully qualified name and explain each "
            "one's role in one sentence."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=2,
        slug="task_specific_instruction",
        title="Task-Specific / Instruction",
        cluster="Content & Purpose",
        target="django.db.models.query.QuerySet.delete",
        task_prompt=(
            "Implement a new `QuerySet.soft_delete()` method that marks every matched row inactive "
            "(set an `is_active` field to `False`) instead of removing it, while still conforming "
            "to the same transactional/collector contract `QuerySet.delete()` uses. Return only the "
            "new `soft_delete` method."
        ),
        expects_code=True,
    ),
    PromptArchetype(
        archetype_id=3,
        slug="creative_generative",
        title="Creative / Generative",
        cluster="Content & Purpose",
        target="django.dispatch.dispatcher.Signal.send",
        task_prompt=(
            "Propose and sketch an asynchronous, event-driven dispatcher for Django's signals "
            "system that fits the existing `Signal.send`/`send_robust` design shown above, rather "
            "than replacing it outright. Prose plus an illustrative fragment is fine."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=4,
        slug="analytical_reasoning",
        title="Analytical / Reasoning",
        cluster="Content & Purpose",
        target="django.core.handlers.base.BaseHandler._get_response",
        task_prompt=(
            "Analyze how an exception raised inside a view propagates through "
            "`BaseHandler._get_response` and any middleware wrapping it. Where, specifically, does "
            "exception bubbling stop being silent?"
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=5,
        slug="transformation_rewrite",
        title="Transformation / Rewrite",
        cluster="Content & Purpose",
        target="django.utils.crypto.constant_time_compare",
        task_prompt=(
            "Refactor `constant_time_compare` to add strict type hints (parameters and return "
            "type) and an early return for the empty-string case, without changing its comparison "
            "semantics."
        ),
        expects_code=True,
    ),
    PromptArchetype(
        archetype_id=6,
        slug="classification_labeling",
        title="Classification / Labeling",
        cluster="Content & Purpose",
        target="django.contrib.auth.tokens.PasswordResetTokenGenerator.make_token",
        task_prompt=(
            "Categorize every method on `PasswordResetTokenGenerator` shown in the context into "
            "exactly one of: read (returns data, no side effect), compute (pure derivation, e.g. "
            "hashing), or write (mutates instance/external state). Present the result as a short "
            "list of `method_name: category`."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=7,
        slug="retrieval_fact_check",
        title="Retrieval / Fact-Check",
        cluster="Content & Purpose",
        target="django.contrib.auth.authenticate",
        task_prompt=(
            "Does `django.contrib.auth.authenticate` perform a database write under any code "
            "branch shown in the context? Answer YES or NO, then justify your answer by naming the "
            "exact call (if any) that would perform it."
        ),
        expects_code=False,
        required_any_substrings=("yes", "no"),
    ),
    PromptArchetype(
        archetype_id=8,
        slug="zero_shot",
        title="Zero-Shot",
        cluster="Prompting Setup",
        target="django.contrib.sessions.backends.base.SessionBase.cycle_key",
        task_prompt=(
            "Implement `SessionBase.flush_and_cycle()`, a new method that discards the current "
            "session data and immediately establishes a fresh session key in one call, reusing the "
            "existing `flush`/`cycle_key` methods rather than reimplementing their logic."
        ),
        expects_code=True,
        notes="Zero demonstrations - direct task execution, the baseline every other prompting-setup archetype is compared against.",
    ),
    PromptArchetype(
        archetype_id=9,
        slug="one_shot",
        title="One-Shot",
        cluster="Prompting Setup",
        target="django.contrib.auth.mixins.PermissionRequiredMixin.has_permission",
        task_prompt=(
            "Following the demonstration above, implement "
            "`PermissionRequiredMixin.has_any_permission(self, user, obj=None)`, which grants "
            "access if the user has AT LEAST ONE of the permissions `get_permission_required()` "
            "returns, instead of requiring all of them."
        ),
        expects_code=True,
        few_shot_examples=(_ONE_SHOT_EXAMPLE,),
    ),
    PromptArchetype(
        archetype_id=10,
        slug="few_shot",
        title="Few-Shot",
        cluster="Prompting Setup",
        target="django.contrib.auth.backends.ModelBackend.authenticate",
        task_prompt=(
            "Following the three demonstrations above, implement "
            "`ModelBackend.authenticate_by_email(self, request, email=None, password=None, "
            "**kwargs)`, an alternate entrypoint that looks a user up by email instead of the "
            "configured USERNAME_FIELD, then defers to the same password-check contract the "
            "demonstrations use."
        ),
        expects_code=True,
        few_shot_examples=(_FEW_SHOT_EXAMPLE_1, _FEW_SHOT_EXAMPLE_2, _FEW_SHOT_EXAMPLE_3),
    ),
    PromptArchetype(
        archetype_id=11,
        slug="instruction_system",
        title="Instruction / System",
        cluster="Prompting Setup",
        target="django.contrib.auth.backends.ModelBackend.authenticate",
        system_prompt=_SECURITY_AUDITOR_SYSTEM_PROMPT,
        task_prompt=(
            "Audit `ModelBackend.authenticate` for any missing invariant (e.g. a path that returns "
            "a user object without validating their password or active status). If you find one, "
            "patch it while preserving every existing check; if you find none, say so explicitly "
            "instead of inventing an issue."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=12,
        slug="role_persona",
        title="Role / Persona",
        cluster="Prompting Setup",
        target="django.db.models.query.QuerySet.filter",
        system_prompt=_PERFORMANCE_PERSONA_SYSTEM_PROMPT,
        task_prompt=(
            "From this performance-first perspective, propose one concrete change (with a short "
            "illustrative implementation) that would reduce redundant work in repeated calls to "
            "`QuerySet.filter()` in a hot request path."
        ),
        expects_code=True,
    ),
    PromptArchetype(
        archetype_id=13,
        slug="chain_of_thought",
        title="Chain-of-Thought (CoT)",
        cluster="Reasoning Technique",
        target="django.db.models.base.Model.save",
        task_prompt=(
            "Think step-by-step through the call chain starting at `Model.save()` and ending at "
            "the point a SQL statement is actually sent to the database connection. Number each "
            "step and name the exact method it passes through."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=14,
        slug="self_consistency",
        title="Self-Consistency",
        cluster="Reasoning Technique",
        target="django.db.models.base.Model.save",
        task_prompt=(
            "Which single method in the call chain from `Model.save()` is the one that actually "
            "executes the SQL statement against the database connection (the final 'sink')? Name "
            "its fully qualified symbol."
        ),
        expects_code=False,
        sample_count=3,
        notes="Sampled 3x at a higher temperature; scored on whether a real symbol reaches consensus across samples, not on any single answer.",
    ),
    PromptArchetype(
        archetype_id=15,
        slug="tree_of_thoughts",
        title="Tree-of-Thoughts (ToT)",
        cluster="Reasoning Technique",
        target="django.db.models.query.QuerySet.filter",
        task_prompt=(
            "Explore three distinct architectural strategies for adding a caching layer in front "
            "of `QuerySet.filter()`. Present them as 'Strategy 1', 'Strategy 2', 'Strategy 3', each "
            "with a one-paragraph trade-off assessment, then recommend one."
        ),
        expects_code=False,
        required_all_substrings=("strategy 1", "strategy 2", "strategy 3"),
    ),
    PromptArchetype(
        archetype_id=16,
        slug="react",
        title="ReAct",
        cluster="Reasoning Technique",
        target="django.core.handlers.base.BaseHandler.get_response",
        task_prompt=(
            "Using a Reason+Act loop (alternate a 'Reason:' line explaining what you need to know "
            "next and an 'Act:' line naming the exact symbol from the context you'd inspect next), "
            "determine what you would need to inspect to fully trace what happens after "
            "`BaseHandler.get_response` returns. Only name symbols that actually appear in the "
            "context above."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=17,
        slug="negative_constraint",
        title="Negative / Constraint",
        cluster="Prompt Engineering Constraint",
        target="django.contrib.auth.authenticate",
        task_prompt=(
            "Add a single `logging.debug(...)` call at the start of `authenticate` to record that "
            "an authentication attempt occurred. You MUST NOT modify the function's signature, and "
            "MUST NOT invoke any operation that performs a database write."
        ),
        expects_code=True,
        forbidden_tags=("#db_write",),
        preserve_signature=True,
    ),
    PromptArchetype(
        archetype_id=18,
        slug="output_formatting",
        title="Output Formatting",
        cluster="Prompt Engineering Constraint",
        target="django.contrib.auth.tokens.PasswordResetTokenGenerator.make_token",
        task_prompt=(
            "Describe `PasswordResetTokenGenerator.make_token`'s signature as a strict JSON object "
            "(and nothing else - no prose, no markdown outside the JSON itself) with exactly the "
            "keys `qualified_name`, `parameters` (list of parameter names), and `returns` (a short "
            "type description)."
        ),
        expects_code=False,
        output_format="json",
    ),
    PromptArchetype(
        archetype_id=19,
        slug="bias_mitigating_balanced",
        title="Bias-Mitigating / Balanced",
        cluster="Prompt Engineering Constraint",
        target="django.db.models.query.QuerySet.filter",
        task_prompt=(
            "Evaluate the trade-offs between using raw SQL execution and relying on "
            "`QuerySet.filter()`'s ORM query synthesis for a complex, performance-sensitive query. "
            "Give a genuinely balanced assessment covering both approaches' advantages, not just "
            "one side."
        ),
        expects_code=False,
        required_all_substrings=("raw sql", "orm"),
    ),
    PromptArchetype(
        archetype_id=20,
        slug="iterative_follow_up",
        title="Iterative / Follow-Up",
        cluster="Dialogue Structure",
        target="django.db.models.query.QuerySet.delete",
        task_prompt=(
            "Implement `QuerySet.soft_delete()`, following the same collector/transactional "
            "pattern `QuerySet.delete()` uses, but setting an `is_active` field to False instead of "
            "removing rows."
        ),
        follow_up_prompts=(
            "Refine your implementation to handle the edge case where a row has already been "
            "soft-deleted: calling `soft_delete()` again on an already-inactive subset must be a "
            "safe no-op, not an error and not a duplicate signal/log.",
        ),
        expects_code=True,
        notes="Multi-turn: query 1 retrieves context and produces a first pass; query 2 refines an edge condition. Scored on the final (refined) turn.",
    ),
    PromptArchetype(
        archetype_id=21,
        slug="prompt_chaining",
        title="Prompt Chaining",
        cluster="Dialogue Structure",
        target="django.contrib.auth.mixins.PermissionRequiredMixin.dispatch",
        task_prompt=(
            "Audit `PermissionRequiredMixin.dispatch` for any point where a check could be "
            "bypassed by a subclass overriding a single method incorrectly."
        ),
        follow_up_prompts=(
            "Now, using the specific issue you just identified, write the actual code patch as a "
            "single fenced Python code block containing only the corrected method.",
        ),
        expects_code=True,
        notes="An architectural-audit prompt piped into a code-patch-generation prompt. Scored on the final (patch) turn.",
    ),
    PromptArchetype(
        archetype_id=22,
        slug="meta_prompt",
        title="Meta-Prompt",
        cluster="Dialogue Structure",
        target="django.contrib.auth.authenticate",
        task_prompt=(
            'Analyze the following prompt intended for querying Django\'s authentication pipeline: '
            '"Tell me about authenticate." Critique its vagueness, then rewrite it as a precise, '
            "unambiguous prompt that makes good use of SCE's tag grammar (e.g. referencing specific "
            "tags like #auth_guard or #db_write, and the exact symbol shown in the context above) "
            "to get a useful, scoped answer."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=23,
        slug="conversational",
        title="Conversational",
        cluster="Dialogue Structure",
        target="django.core.handlers.base.BaseHandler.get_response",
        task_prompt=(
            "Hey, quick question - when a request comes in, what's actually responsible for "
            "turning it into a response? Keep it conversational, a couple of sentences is fine."
        ),
        follow_up_prompts=(
            "Follow-up: does that same path run even for requests that end up raising an "
            "exception, or does something else take over?",
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=24,
        slug="open_ended",
        title="Open-Ended",
        cluster="Task Type",
        target="django.contrib.sessions.backends.base.SessionBase._get_session",
        task_prompt=(
            "How could Django's session backend be adapted to work over Redis pub/sub instead of "
            "the request/response-cycle model `SessionBase` currently assumes? Discuss freely."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=25,
        slug="closed_ended",
        title="Closed-Ended",
        cluster="Task Type",
        target="django.contrib.sessions.backends.base.SessionBase.cycle_key",
        task_prompt=(
            "Does `SessionBase.cycle_key()` generate a new session hash? Answer strictly YES or "
            "NO, then give the line number (within the snippet shown) where the key is actually "
            "regenerated."
        ),
        expects_code=False,
        required_any_substrings=("yes", "no"),
        required_regexes=(r"line\D{0,10}\d+",),
    ),
    PromptArchetype(
        archetype_id=26,
        slug="hypothetical_counterfactual",
        title="Hypothetical / Counterfactual",
        cluster="Task Type",
        target="django.contrib.auth.mixins.PermissionRequiredMixin.dispatch",
        task_prompt=(
            "Hypothetically, if the permission check inside `PermissionRequiredMixin.dispatch` "
            "were removed entirely, what vulnerable call paths would open up for any view using "
            "this mixin? Be specific about what an attacker could then reach."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=27,
        slug="socratic",
        title="Socratic",
        cluster="Dialogue Structure",
        target="django.db.transaction.atomic",
        task_prompt=(
            "Before writing any code, ask at least one clarifying architectural question about how "
            "`transaction.atomic()` should behave when used as a decorator versus a context "
            "manager, and about nested atomic blocks - then, once you've asked it, answer it "
            "yourself using only what's shown in the context above."
        ),
        expects_code=False,
        required_regexes=(r"\?",),
    ),
    PromptArchetype(
        archetype_id=28,
        slug="reflective",
        title="Reflective",
        cluster="Dialogue Structure",
        target="django.contrib.auth.tokens.PasswordResetTokenGenerator.check_token",
        task_prompt=(
            "Propose a patch to `PasswordResetTokenGenerator.check_token` that also logs (via "
            "`logging`) every failed token check for audit purposes."
        ),
        follow_up_prompts=(
            "Now critique your own patch against the interface contract shown in the context: does "
            "it still return exactly what every existing caller of `check_token` expects, and does "
            "it avoid leaking sensitive token material into the log? Call out anything you'd "
            "change.",
        ),
        expects_code=False,
        notes="Scored on the final (self-critique) turn, which is prose, not code.",
    ),
    PromptArchetype(
        archetype_id=29,
        slug="code_generation",
        title="Code-Generation",
        cluster="Task Type",
        target="django.contrib.auth.backends.BaseBackend.authenticate",
        task_prompt=(
            "Generate a complete, minimal custom authentication backend class (subclassing the "
            "base backend contract shown above) that authenticates a user against a fictional "
            "external API keyed by an `api_token` field, implementing at least `authenticate` and "
            "`get_user` with the exact signatures the base contract declares."
        ),
        expects_code=True,
    ),
    PromptArchetype(
        archetype_id=30,
        slug="debugging_explanation",
        title="Debugging / Explanation",
        cluster="Task Type",
        target="django.db.transaction.atomic",
        task_prompt=(
            "A test intermittently deadlocks inside a `transaction.atomic()` block when two "
            "requests both update overlapping rows. Explain, based on what's shown in the context, "
            "the most likely mechanism causing this deadlock - trace the actual code path "
            "involved rather than just saying 'it's a database issue'."
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=31,
        slug="architecture_design",
        title="Architecture / Design",
        cluster="Task Type",
        target="django.core.handlers.base.BaseHandler.get_response",
        task_prompt=(
            "Map out the complete inbound-to-outbound path from the request entering "
            "`BaseHandler.get_response` to the point a database write could occur downstream. Name "
            "every intermediate symbol on that path that's visible in the context above. (Note: "
            "this codebase's own request routing has no `#route_handler`-tagged decorator the way "
            "some web frameworks do, so `get_response` - the actual WSGI/ASGI inbound entrypoint - "
            "is used as the starting point instead.)"
        ),
        expects_code=False,
    ),
    PromptArchetype(
        archetype_id=32,
        slug="documentation",
        title="Documentation",
        cluster="Task Type",
        target="django.db.models.query.QuerySet._fetch_all",
        task_prompt=(
            "Add a complete Google-style docstring (Args, Returns, and any side effects) to "
            "`QuerySet._fetch_all`, suitable for Sphinx's napoleon extension, without changing its "
            "behavior. Return the full method including the new docstring."
        ),
        expects_code=True,
    ),
    PromptArchetype(
        archetype_id=33,
        slug="data_analysis",
        title="Data / Analysis",
        cluster="Task Type",
        target="django.template.backends.base.BaseEngine",
        task_prompt=(
            "Extract and tabulate, as a Markdown table, every parameter name declared on "
            "`BaseEngine.__init__` and every exception class explicitly raised anywhere in the "
            "methods shown in the context above (one row per parameter or exception, with a `kind` "
            "column of `parameter` or `exception`)."
        ),
        expects_code=False,
        required_regexes=(r"\|.*\|",),
    ),
)

ARCHETYPES_BY_ID: dict[int, PromptArchetype] = {a.archetype_id: a for a in ARCHETYPES}
ARCHETYPES_BY_SLUG: dict[str, PromptArchetype] = {a.slug: a for a in ARCHETYPES}

assert len(ARCHETYPES) == 33
assert set(ARCHETYPES_BY_ID) == set(range(1, 34))
