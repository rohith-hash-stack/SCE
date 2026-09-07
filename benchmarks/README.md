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
