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
