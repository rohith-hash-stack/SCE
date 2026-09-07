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
