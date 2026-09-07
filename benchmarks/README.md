# SCE Evaluation Harness

Quantifies the Semantic Context Engine's packed context against a naive
"whole-file dump" baseline across three dimensions:

1. **Token reduction** - exact token counts (`tiktoken`'s `cl100k_base`
   encoding when reachable, otherwise a deterministic regex-based fallback -
   see `tokenizer.py`) for the raw-file-dump baseline vs. the SCE L0-L3
   Markdown package, at one or more token budgets.
2. **Call-graph & invariant-tag coverage** - builds a ground-truth k-hop
   subgraph around the target (`coverage.py`) and measures what fraction of
   its nodes, edges, and architectural invariant tags (`#auth_guard`,
   `#db_write`, ...) survive into the packed context.
3. **Syntactic validity** - every Python code block SCE renders is
   extracted straight from the Markdown output and checked with
   `ast.parse()` (`validity.py`), so AST-stripping regressions (e.g. an
   empty loop body missing a `pass`) get caught immediately.

## Running it

```bash
# Single target, one or more budgets
python -m benchmarks.run_benchmark --target "src.controllers.checkout.CheckoutController.process_checkout" --budget 2000 4000

# Against a specific repo
python -m benchmarks.run_benchmark --repo path/to/repo --target some.qualified.symbol --budget 4000

# The built-in suite: the HLD's own worked-example fixture, plus the
# synthetic multi-file stress repo below
python -m benchmarks.run_benchmark --suite
```

Prints an ASCII summary table plus a per-target detail section to stdout,
and writes full raw metrics to `benchmarks/results.json` (override with
`--output`, or pass `--output ""` to skip the file).

## Fixtures

- `tests/fixtures/python_repo` - the small repo mirroring the HLD's own
  checkout/billing/auth worked example. Almost entirely "signal" (every
  line is on the target's call chain), so it's a poor showcase for
  compression specifically, but it's still run for coverage/validity.
- `benchmarks/fixtures/stress_repo` - a synthetic, realistically-sized
  module: 9 files, multiple classes with several methods each (only some
  of which are on the benchmarked call path), a barrel `app/__init__.py`
  re-export consumed by `main.py`, module docstrings, logging, and enough
  ancillary/admin methods per class that a whole-file dump carries a lot of
  weight the target's own call chain never touches. All 8 tags the
  deterministic tagger can assign appear somewhere in it. This is the
  fixture the `>=50%` compression regression test in
  `tests/test_benchmark.py` is checked against.

## A note on the barrel re-export

`app/__init__.py` re-exports `OrderController` for external consumers
(`main.py` uses `from app import OrderController`). SCE's import resolution
follows a file's own `import`/`from...import` statements textually; it does
not chase a re-export back to where a symbol was *originally* defined. So
`main.bootstrap`'s call to the barrel-imported `OrderController(...)`
resolves to an external/unknown node rather than
`app.controllers.orders.OrderController` - a known, documented limitation
(see `sce.graph.concrete_builder`'s module docstring). It's deliberately
kept off the benchmarked target's own call chain here so it demonstrates
the pattern without skewing this harness's coverage numbers.

---

## Live evaluation (`live_eval.py`)

Runs two code tasks against a real OpenAI model, once with a whole-file-dump
context and once with an SCE-sliced context, and scores each response with
a deterministic **AST verifier** - never another LLM - so results are
reproducible and free to re-check:

- **Bug Localization & Invariant Fix**: the model is shown a function with a
  real security defect (a `#db_write` path with no `#auth_guard` before it)
  and asked to fix it. The verifier checks the patch parses, actually calls
  the real auth guard, and doesn't invent a plausible-sounding one.
- **Feature Extension / Interface Call**: the model implements a new method
  that must call specific real dependencies (a refund + an event publish).
  The verifier checks it calls the exact real method names shown in the
  context, not hallucinated ones.

Both tasks run against `benchmarks/fixtures/task_repo`, a small dedicated
fixture (separate from `stress_repo`) built so the fix/feature is
unambiguous and the "real symbol" vocabulary is small and fully known.

```bash
# Needs OPENAI_API_KEY in the environment or a .env file (python-dotenv)
python -m benchmarks.live_eval --model "gpt-4o-mini" --tasks all --report benchmarks/live_report.json

# No key needed: build every prompt/context and print sizes, make zero API calls
python -m benchmarks.live_eval --dry-run
```

Reports prompt/completion tokens, estimated cost (see the pricing table in
`openai_client.py` - verify against OpenAI's current pricing page before
trusting it for anything but an order-of-magnitude estimate; override with
`--price-in`/`--price-out` for a model not in the table), and latency per
call, plus an overall and per-variant pass rate.

**On the bug-localization task's raw baseline**: the buggy function never
calls the auth guard (that's the bug), so a raw dump scoped to only the
target's own call chain wouldn't include it either - making the fix
undiscoverable for both variants and the comparison meaningless. That
task's raw baseline dumps the whole fixture instead; SCE's baseline still
uses its normal sliced package, which surfaces the guard via the
`#db_write REQUIRES_BEFORE #auth_guard` architectural-path annotation
regardless of the literal call graph. See `benchmarks/tasks.py`.

## Real-repository evaluation (`clone_eval.py`)

Clones a real, public Git repository and runs SCE's actual pipeline against
it, to prove the cross-file linker survives real-world Python (decorators,
type hints, relative imports, complex `__init__.py` barrels) rather than
only the hand-built fixtures above.

```bash
python -m benchmarks.clone_eval --repo https://github.com/encode/starlette.git \
    --target "starlette.applications.Starlette.__call__" --budget 4000

# No --target: auto-selects a #route_handler-tagged symbol, or the
# highest-call-graph-degree function/method if none is tagged
python -m benchmarks.clone_eval --repo https://github.com/pallets/flask.git

# Also ask a real model to explain the architecture from both contexts
python -m benchmarks.clone_eval --repo https://github.com/encode/starlette.git --live
```

Clones with `git clone --depth 1` into `.benchmarks/clones/<repo_name>`
(cached across runs; pass `--force-clone` to re-clone) and reports the same
compression/coverage/validity metrics as `run_benchmark.py`, reusing that
module's `run_single_benchmark_from_pipeline` directly. `--live` sends both
the raw and SCE contexts to OpenAI for an architectural explanation of the
target - printed side by side, not auto-graded (unlike `live_eval.py`'s
tasks, "did it reason correctly" isn't a cheap, trustworthy static check).

**Validated against a real clone**: `encode/starlette` indexes cleanly in
~2-3s (1711 symbols, 2005 `CALLS` edges) with zero pipeline crashes, and
every one of the 250+ Python code blocks SCE renders from it parses with
`ast.parse()` - including `async def` methods, PEP 604 union types
(`X | None`), and keyword-only parameters. The default test suite doesn't
repeat this clone (it would make CI depend on network access); set
`SCE_LIVE_NETWORK_TESTS=1` to run `tests/test_clone_eval.py`'s opt-in
real-network smoke test.

## Multi-repository, multi-query validation (`multi_repo_eval.py`)

Before building a client wrapper (an MCP server, an IDE extension, ...) on
top of SCE, this validates it against three real, architecturally distinct
codebases - not just hand-built fixtures - across three realistic query
types:

- **encode/httpx** - transport layers, async/sync duality, heavy I/O
- **pallets/flask** - WSGI architecture, request contexts, routing
- **marshmallow-code/marshmallow** - data schemas, deep class inheritance

For each repo, three scenarios (found by actually cloning and indexing
each one and inspecting its real concrete graph, not guessed):

- **Root-Cause Analysis / Bug Trace** (Query A) - a deep vertical call
  chain from a public entrypoint down toward where a failure would
  actually surface (`httpx.Client.send`, `Flask.full_dispatch_request`,
  `marshmallow.Schema.load`).
- **Cross-Cutting Architectural Invariant Audit** (Query B) - a
  sensitive boundary method, checked against the metamodel/tag matrix
  (`HTTPTransport.handle_request`'s network I/O boundary, Flask's route
  registration and the auth invariant, `Field._bind_to_schema`'s state
  mutation across the entire Field hierarchy).
- **Feature Extension / Interface Conformance** (Query C) - an interface
  contract a new implementation must match exactly (`BaseTransport`'s
  async contract via `ASGITransport` as the reference, `View.dispatch_request`,
  `Field._serialize`/`_deserialize`).

```bash
python -m benchmarks.multi_repo_eval --suite all --report benchmarks/multi_repo_report.json
python -m benchmarks.multi_repo_eval --repo httpx
```

For every (repo, scenario, budget) combination this asserts: every L0-L3
Python code block parses cleanly; >=50% compression vs. the whole-file-dump
baseline with 100% of direct callees still reachable; and zero
"hallucinated" symbols (anything SCE's own Markdown references that isn't a
real node in `GlobalSymbolTable`/`G_C` - a static regression guard, since
nothing in this pipeline is LLM-generated). Graph connectivity/density
(total symbols, edges, isolated-node ratio, approximate traversal depth) is
reported per repo, not gated on - it describes the target codebase, not
SCE's correctness. Exits non-zero if any scenario fails.

**Result across all 3 repos x 3 scenarios x 2 budgets (18 runs): 18/18
pass.** Compression ranged 59.8%-98.7%, zero hallucinated symbols anywhere.

**Two real engine bugs were caught and fixed by this validation, not
hidden**:

1. **D_hybrid unfairly penalized untagged direct callees.** The metamodel
   treated "this node has no tag at all" identically to "these two tags are
   confirmed to be maximally different" (`MAX_TAG_DISTANCE`). Against
   httpx's real call graph, this let a same-tagged-but-4-hops-away sibling
   method outrank a genuine untagged 1-hop callee, dropping that callee
   from a tight budget. Fixed in `sce.graph.metamodel` by giving "untagged"
   its own, smaller `UNTAGGED_TAG_DISTANCE` (no signal, not a confirmed
   gap) - see `tests/test_distance_metric.py`.
2. **`self.method()` resolution doesn't know about inheritance.** When a
   subclass method calls `self.some_method()` and `some_method` is only
   defined on a *parent* class (httpx's `Client` calling `BaseClient`-only
   methods like `_build_request_auth`), the linker resolves it against the
   subclass's own name instead, producing a plausible-looking but
   nonexistent qualified name recorded as an external/unresolved node. This
   is a known, currently unfixed limitation - real, but out of scope for
   this harness to fix - documented here rather than papered over; it
   inflates the isolated/unresolved node counts `compute_graph_metrics`
   reports for any codebase with non-trivial inheritance (all three of
   these real repos included).

**On tagger coverage gaps**: `#external_io` never fires inside httpx's own
source, because its default transport calls `self._pool.handle_request(...)`
(a `httpcore`-level method) rather than one of the generic REST-verb names
(`.get`/`.post`/`.request`/...) the deterministic tagger looks for.
Similarly `#auth_guard` never fires in Flask's own source, since Flask is
routing infrastructure - enforcing auth is left entirely to application
code, which is architecturally correct, not a bug. Each scenario reports
whether its `expected_tag` diagnostic was actually observed near the
target, without treating a miss as a failure: a heuristic tag not firing on
an unfamiliar naming convention is an honest finding about tagger coverage,
not a defect in compression, coverage, or hallucination-freedom.

## Downstream LLM accuracy validation (`validate_llm_accuracy.py`)

Every harness above proves SCE's *own* output is well-formed - it never
proves a real model actually writes correct code from it. This is the one
that closes that gap: it sends real tasks to a real OpenAI model under both
a raw whole-file-dump context and an SCE L0-L3 context, then scores each
response by **actually running it**, never by asking another LLM to judge
it.

Three tasks, each with deterministic, pytest-verifiable ground truth,
against a dedicated fixture (`benchmarks/fixtures/accuracy_repo`):

1. **The Missing Invariant Bug** (security/correctness) -
   `OrderService.checkout_order` performs a `#db_write` with no auth check.
   Ground truth: unauthenticated calls must raise `PermissionError`
   (`app.exceptions.Forbidden`, the codebase's one real auth-failure type,
   subclasses it).
2. **Interface Conformance & Feature Extension** (no hallucinations) - a new
   `refund_transaction(order_id, amount)` method must coordinate with the
   payment gateway and persist the resulting order state. Ground truth:
   it must call the gateway's one real method, `reverse_charge` - not a
   plausible-sounding but nonexistent `refund`/`process_refund`.
3. **Cross-File Control Flow Debugging** - `process_payload()` over-catches
   `except Exception`, silently reporting an unrelated `RuntimeError` (a
   simulated payment-gateway outage) as an "invalid payload" error. Ground
   truth: only the specific `PayloadValidationError`, defined two hops away
   in `app/exceptions.py`, may be caught this way; other errors must still
   propagate.

```bash
# Needs OPENAI_API_KEY in the environment or a .env file (python-dotenv)
python -m benchmarks.validate_llm_accuracy --model gpt-4o-mini --report benchmarks/accuracy_report.json

# No key needed: build every prompt/context and print sizes, make zero API calls
python -m benchmarks.validate_llm_accuracy --dry-run
```

**Scoring is entirely mechanical - no LLM judge anywhere:**

- **Syntax check**: `ast.parse()` on the code block extracted from the
  model's Markdown response.
- **Execution sandbox**: the response's code is spliced into a temp copy of
  the fixture repo at the target symbol's exact `line_range` (from SCE's
  own symbol table), re-indented to match the surrounding block, then the
  task's pytest file is run against it via `subprocess.run`. This is a real
  `pytest` process on real (copied) files, not a mock.
- **Hallucination counter**: every call/method name the response's AST
  invokes is checked against `GlobalSymbolTable`'s real qualified names
  (plus Python builtins and a short common-stdlib-method allowlist, since
  static AST inspection can't do type inference); anything left over is
  flagged as invented. `RecordingGateway`, Task 2's test double, backs this
  with a second, independent check: it only implements the two real gateway
  methods, so a hallucinated call raises a real `AttributeError` at
  execution time too.

**Fixture design decisions worth knowing before extending it:**

- **Task 1 uses `raw_scope="whole_repo"`.** `checkout_order` (the buggy
  code) never calls the auth guard - that's the bug - so a raw dump scoped
  to just its own call-chain closure would never include `app/auth.py`
  either, making the fix equally undiscoverable for both variants (the same
  situation as `live_eval.py`'s bug-localization task; see `benchmarks/tasks.py`).
- **Task 2 queries one symbol but patches another.** SCE's context is built
  around the working `cancel_order` method (a template that already
  exercises the gateway + repository pattern); the model's code actually
  replaces the placeholder `refund_transaction` next to it. Querying the
  placeholder itself would surface nothing useful, since it has no call-graph
  connections yet.
- **`OrderService.__init__` constructs its own `PaymentGateway`** (rather
  than accepting one as a pass-through parameter) so the
  `cancel_order -> PaymentGateway.reverse_charge` edge is statically
  resolvable by SCE's `InstanceTypeMap`; tests substitute
  `service.gateway = RecordingGateway()` post-construction for
  observability instead.
- **`app/repository.py` imports `sqlalchemy`** purely to trigger SCE's
  `#db_write` tag on `.commit()` - unlike every earlier fixture in this
  repo, this one's code is actually *executed* by pytest, which is why
  `sqlalchemy` is now a real `dev` extra in `pyproject.toml`.

**Live result against `gpt-4o-mini`, temperature 0: 6/6 passed (100%)**
across all 3 tasks x 2 variants, after fixing the real engine bug below.

**One real engine bug was found and fixed by this validation.** The first
live run scored 5/6: the `missing_invariant` / `sce` variant failed with a
runtime `NameError: name 'app' is not defined`. The model had written
`app.auth.verify_session(token)` verbatim - it had copied the fully
qualified dotted name straight out of SCE's Markdown "Architectural Path"
section, which named `verify_session` only as a bare `requires` annotation
with no accompanying code block, and treated that internal qualified name
as if it were literal, callable Python. Root-caused by rendering the actual
SCE package sent to the model and confirming `verify_session` never got its
own contract block - only symbols reachable via the seed's *call graph* were
being packed as real content; `requires` targets (found via the metamodel's
tag-relation graph, a different structure) were previously only ever
mentioned by name in prose. Fixed in `sce.slicer.knapsack.ContextKnapsackPacker.pack()`
by force-packing a real L2 contract for every `requires` target before the
normal distance-ranked candidate loop runs, so the model always sees an
actual signature and import path to act on instead of a bare dotted name.
Verified by re-rendering the Markdown (confirmed `verify_session` now gets
its own "Contract - L2" block), re-running the single failing task/variant
live (passed), then the full 3x2 suite (6/6, 100%). Checked for regressions
across every other harness in this repo - `run_benchmark.py --suite`,
`multi_repo_eval.py --suite all` (still 18/18 against real clones,
unchanged numbers), `live_eval.py --tasks all` (still 4/4) - and the full
`pytest` suite: no regressions, only a small, expected compression-ratio
dip from the extra packed content.

Hermetic regression tests (`tests/test_validate_llm_accuracy.py`) cover
patching, sandbox execution against real correct/buggy/hallucinated
snippets, hallucination detection, and the CLI's dry-run/error paths with
canned `CallResult`s standing in for the real model - no network access or
API key required.

## Large-repo prompt-archetype matrix (`large_repo_prompt_matrix.py`)

Everything above validates SCE against small hand-built fixtures or a
handful of mid-sized real repos. This one asks a different question: how
does SCE actually behave, at scale, against a genuine large enterprise
codebase, across the full breadth of how developers actually talk to an
LLM coding assistant - not just "fix this bug" but zero/few-shot setups,
chain-of-thought and ReAct-style reasoning, negative constraints,
multi-turn dialogue, closed-ended fact checks, documentation generation,
and more.

**Target repository: `django/django`.** Shallow-cloned (`--depth 1`) and
cached at `.benchmarks/clones/django`. Indexed metrics from a real run:

```
Total symbols (G_C):   42,282
Total CALLS edges:     79,268
Indexing time:         ~155-162s
Peak memory (RSS):     ~1.8 GB
Tag distribution (M):  #state_mutation: 1595   #auth_guard: 60
                        #db_write: 7            #external_io: 3
```

**33 structured prompt archetypes** (`benchmarks/prompt_taxonomy/`), each
declaratively specified as a `PromptArchetype` - a real, verified target
symbol in django, a task prompt, and a small set of *contract* fields
(expects code? forbidden tags? output format? required substrings? few-shot
demonstrations? follow-up turns for multi-turn techniques? self-consistency
sample count?). One generic pipeline in `large_repo_prompt_matrix.py` builds
the right prompt shape and runs the right mechanical checks purely from
those fields, rather than 33 bespoke scorers. The full numbered list -
informational query, instruction-following, creative/generative,
analytical reasoning, rewrite, classification, fact-check, zero/one/few-shot,
system/persona prompting, chain-of-thought, self-consistency,
tree-of-thoughts, ReAct, negative constraints, output formatting,
bias-mitigation, iterative follow-up, prompt chaining, meta-prompting,
conversational, open/closed-ended, hypotheticals, Socratic questioning,
self-reflection, code generation, debugging, architecture mapping,
documentation, and data extraction - lives in
`benchmarks/prompt_taxonomy/archetypes.py`, with the real django target and
exact task prompt for each.

```bash
# Needs OPENAI_API_KEY in the environment or a .env file (python-dotenv)
python -m benchmarks.large_repo_prompt_matrix --repo django --prompts all \
    --report benchmarks/django_33_prompts.json

# A single archetype by numeric id:
python -m benchmarks.large_repo_prompt_matrix --repo django --prompt 17

# No key needed: index the repo and build every context, print sizes/compression, zero API calls
python -m benchmarks.large_repo_prompt_matrix --repo django --dry-run
```

**Scoring is entirely mechanical, matching the spec's four checks - no LLM
judge anywhere:**

- **Token compression ratio**: `(1 - sce_tokens / raw_tokens) * 100`,
  computed per archetype from the same `ContextKnapsackPacker`
  (3000-token budget by default) vs. `build_raw_context`'s call-chain-closure
  dump this whole benchmark suite uses everywhere else.
- **Syntax validation**: `ast.parse()` on the extracted code block, for the
  ~15 archetypes that actually expect one (`expects_code=True`); archetypes
  that expect prose (an explanation, an audit, an open design question)
  skip this check entirely rather than trivially "passing" it.
- **Hallucination counters, two independent layers**: every *called*
  symbol in a code-producing archetype's response, checked against
  django's real `GlobalSymbolTable` (mirroring `validate_llm_accuracy.py`'s
  checker); and, uniquely for this harness, every fully-qualified,
  dotted-path-shaped symbol *mentioned in prose* - in ANY archetype, code or
  not - checked the same way. A model that writes out
  `django.contrib.auth.tokens.PasswordResetTokenGenerator` in an
  explanation is claiming that path is real, so a fabricated one is caught
  exactly like a fabricated call.
- **Prompt contract adherence**: archetype-declared checks run generically
  from the archetype's own fields - output format (JSON/YAML actually
  parses), negative constraints (a banned substring didn't appear),
  forbidden semantic tags (no call resolves to e.g. `#db_write`), exact
  signature preservation (via `ast.unparse`-normalized comparison),
  required substrings/regexes (closed-ended YES/NO, balanced-viewpoint
  coverage, Socratic question marks, ToT's "Strategy 1/2/3" structure),
  and self-consistency consensus (does a real symbol get mentioned in a
  majority of `sample_count` independent samples).

**Live result against `gpt-4o-mini`, temperature 0 (33 archetypes x 2
variants = 66 runs): 58/66 passed (87.9%)**, average compression 28.7%,
total cost $0.098.

**Two real, generic false-positive bugs in the mechanical checkers were
found and fixed by this live run** (not django-specific hacks - both
improve every harness that reuses `benchmarks/prompt_taxonomy/spec.py`):

1. **A too-narrow stdlib allowlist flagged completely ordinary Python as
   "hallucinated".** `logging.getLogger(...)`/`.debug(...)` (archetype 17,
   asked to add exactly this) and `hmac`/`secrets`'s `compare_digest`
   (archetype 5, refactoring a constant-time comparison - the single most
   likely real function a correct answer would reach for) aren't in
   django's own symbol table and aren't container/string methods, so the
   existing narrow allowlist missed them. Fixed by widening
   `_COMMON_STDLIB_METHOD_NAMES` in `spec.py` with these specific,
   confirmed-real names - not a blanket allowance, the same
   "kept short and genuinely common" principle the allowlist already
   documented.
2. **The prose hallucination check didn't know modules are real too.** A
   model correctly referenced the module `django.contrib.auth.backends` on
   its own (not a specific class/function inside it) while explaining
   where a new backend class belongs; since `GlobalSymbolTable` only
   indexes `def`/`class` symbols, not module paths, this real reference
   was flagged unknown. Fixed by having `build_repo_index` also compute
   every real module's dotted path (and each package prefix along the
   way) from the indexed files, and `find_referenced_symbol_mentions`
   accept either.

**A third, unrelated real engine bug was found and fixed while first
indexing django**: `sce.slicer.compressor.compress_python` crashed with an
unhandled `SyntaxError` on any file using a PEP 695 `type` alias statement
(Python 3.12+ grammar - a real example is
`tests/auth_tests/test_auth_backends.py`), because it re-parses a symbol's
*whole file* with the stdlib `ast` module at L1-L3 (L0 already worked,
since raw slicing doesn't need to parse anything) even though tree-sitter's
more tolerant grammar had already indexed that same file just fine at the
graph-building stage. Fixed by catching the `SyntaxError` and degrading to
the same raw-slice fallback already used a few lines down for "definition
couldn't be relocated" - one line of defense extended to cover the parse
step itself, not just the post-parse lookup. Verified with the full
`pytest` suite (no regressions) before re-running against django.

**Findings surfaced, not hidden, by this validation - real, stable, and
deliberately not "fixed" by loosening a check:**

- **Metaprogrammed/attribute-based real code isn't in `GlobalSymbolTable`,
  by design** (it only indexes `def`/`class` nodes). Three confirmed real
  examples from this exact run: `QuerySet._fetch_all`'s own body calling
  `self._iterable_class(self)` (an instance attribute, not a method -
  archetype 32, which merely asked to add a docstring to the *existing*
  body); `BaseHandler`'s `self._middleware_chain(request)` (same pattern -
  archetype 16); and `router.db_for_write(...)`, whose real implementation
  in `django/db/utils.py` is `db_for_write = _router_func("db_for_write")`
  - a dynamically bound class attribute, never a literal `def` (archetype
  1). All three are genuine, correct Django API usage that the checker
  can't distinguish from a hallucination without attribute-level type
  inference - the same documented limitation `find_hallucinated_calls`
  already carries for its call-based check, just also true of the
  mention-based one, and now demonstrated concretely rather than
  hypothetically.
- **Archetype 29 (code-generation against a "fictional external API") is
  structurally guaranteed to trip the hallucination counter**, since any
  correct implementation necessarily invents a helper name for the
  fictional integration the task asks for - that's the task, not a defect.
- **The closed-ended archetype (25, "what line number...") structurally
  favors the raw baseline.** Asked to name an exact source line, the raw
  variant (the literal, uncompressed file) could and did cite one; the SCE
  variant, given a compressed package that doesn't preserve original line
  numbers for less-central content, honestly answered it couldn't
  determine one rather than fabricating a line number - a genuine,
  worth-knowing trade-off of compression for this specific fact-shape, not
  a bug in either the model or the checker.

Hermetic regression tests (`tests/test_large_repo_prompt_matrix.py`) run
against `tests/fixtures/python_repo` (fast, no cloning) and cover every
mechanical checker, context building, and `run_variant`/`run_archetype`
end-to-end with canned/sequenced responses standing in for the real model -
including multi-turn and self-consistency archetypes. A separate opt-in
suite (`SCE_LIVE_NETWORK_TESTS=1`) re-indexes the real django clone to
confirm all 33 archetype targets still resolve upstream and the CLI's
dry-run path works end-to-end - both passed as of this validation.
