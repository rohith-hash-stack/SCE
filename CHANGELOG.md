# Changelog

This file starts tracking from the post-audit remediation batch below - it
is not a retroactive reconstruction of this project's full commit
history (see `git log` for that), only a forward record from here on.

## Unreleased

Remediation of gaps found by a post-implementation verification audit of
the `feat/benchmark-optimizations` branch (Category A/B/C, referred to
below by their issue codes).

### Benchmark Calibration Baseline Break

Issue B1 changed Go method registration from a bare simple name
(`kind="function"`, name `JSON`) to receiver-qualified
(`kind="method"`, name `<Module>.<ReceiverType>.<JSON>`) - a real,
necessary, deliberate fix (Go structs were previously never registered
as classes at all, and every Go method registered as an unassociated
top-level function - see the earlier "Fixed" entry for Issue B1 above).
This is **not** backward compatible with anything - a saved query, a
script, a benchmark task definition - that named a Go seed by its old,
bare, unqualified name. `benchmarks/comparison_tasks.py`'s own four Gin
seed symbols (`routergroup.Use`, `context.MustBindWith`,
`context.GetInt64`, `context.Set`) needed exactly this update at the
time (see that commit's own message), which is what surfaced the
breaking-change risk in the first place for anyone else's saved queries.

Item 16 (second post-implementation audit) adds a compatibility path
for exactly this case: `prism query <repo> JSON` (a bare, unqualified
seed name) against a Go codebase where exactly one receiver method
named `JSON` exists resolves to it automatically, with a visible
warning (`seed 'JSON' resolved to receiver method 'Context.JSON'.
Update seed to fully-qualified name.`) rather than failing outright -
see `prism.cli._resolve_legacy_go_bare_seed`. An ambiguous bare name
(more than one same-named receiver method anywhere in the repo) is
never guessed, even for this compatibility path - the caller must use
the fully-qualified name in that case.

### Fixed

- **Issue A1** - the regex-based fallback tokenizer (used only when
  `tiktoken`'s real `cl100k_base` encoding can't be loaded) now fails
  *closed* instead of open: snake_case/camelCase identifiers are split
  into subword pieces, multi-digit numbers estimate multiple tokens, and
  a +8% ceiling safety multiplier is applied - so a budget-admission
  decision made on fallback counts can no longer under-count real token
  usage relative to what the earlier flat "one `\w+` run = one token"
  version did.
- **Issue A3** - `PackResult` now exposes `seed_cost`/`budget_exceeded`/
  `truncation_occurred` so a caller (CLI `--json` output, the Markdown
  serializer, the MCP server) can see explicitly when the seed's own
  mandatory L0 render alone exceeds the requested budget, instead of
  having to infer it from `allocated_tokens > budget`.
- **Issue B1, a pre-existing bug this surfaced** - Go `type_declaration`
  nodes have no `name` field of their own (it lives on the nested
  `type_spec` child); the `definitions` query's `@def.class` capture
  previously bound to the outer node, so `_register_definition` always
  hit its `name_node is None` guard and returned early - **every Go
  struct was silently never registered as a class at all**, for any
  Go repository, at any point before this fix. Moving the capture to
  `type_spec` fixes it; confirmed directly against gin-gonic/gin
  (0 -> 428 registered Go methods, 0 -> 121 resolved `CALLS` edges into
  `*.Context.*` methods).
- **Issue C2** - three recursive tree-walking code paths
  (`compress_python`'s AST skeletonizers, `iter_scoped_nodes`,
  `_mro_ancestors`) previously raised an uncaught `RecursionError` on
  adversarially deep-but-valid input, confirmed directly (a
  several-hundred-deep `not`-expression chain, a ~600-deep nested Go
  `if` block, and a ~600-class linear inheritance chain respectively).
  All three now degrade gracefully (a raw-slice fallback, or an
  explicit traversal-depth cap) instead of crashing the whole
  `index`/`query` run on one adversarial file - see
  `tests/test_recursion_safeguards.py`.

### Documented

- **Issue A2** - `tests/fixtures/tokens/README.md` documents how to
  populate an offline `TIKTOKEN_CACHE_DIR` cache for `cl100k_base` so
  `tests/test_bpe_vs_fallback.py`'s real-BPE-vs-fallback comparison can
  run without network access.
- **Issue A4** - `ContextKnapsackPacker._swap_refine`'s docstring now
  states its real worst-case complexity (`O(W * I log I)` scan/sort work
  plus at most `W` renders, `W = MAX_SWAP_ATTEMPTS = 20`; `K = MAX_SWAPS
  = 5` bounds only *successful* substitutions, not attempts) and exactly
  which class of greedy-knapsack starvation it repairs vs. which it
  structurally cannot (multi-item swaps, candidates beyond the closest
  W, resolution-tier changes to already-packed items).
- **Issue A5** - inheritance-graph resolution's language scope is now
  stated precisely next to `TRAVERSABLE_RELATIONS` in
  `src/prism/graph/concrete_builder.py`: Python gets an MRO-approximating
  EXTENDS walk (`_mro_ancestors`), JS/TS/TSX get the same walk over their
  own EXTENDS/IMPLEMENTS edges (prototype-chain resolution, not literal
  C3 MRO, though single-chain in practice), and Go/Java/C# build no
  EXTENDS/IMPLEMENTS edges at all - Go because it has no class syntax
  (only struct embedding, not modeled as a graph relation), Java/C#
  because it is genuinely unimplemented despite being straightforward
  single-inheritance languages.
- **Issue B2** - `README.md` and `docs/design_formalism.md` replace the
  earlier linear "Precision Tier 1/2/3" framing with an explicit
  Language Capability Matrix (parser frontend, symbol registration,
  re-export resolution, call resolution, inheritance traversal,
  compression fidelity) across Python/TypeScript-JavaScript/Go, since the
  linear framing implied a single uniform degradation axis that doesn't
  match reality (e.g. Go's Compression Fidelity is identical to JS/TS's,
  and Go now has real - if narrower than Python's - call resolution via
  Issue B1, while still having zero inheritance-traversal edges of any
  kind).
- **Issue C1** - explicit comments at two previously-undocumented
  invariant sites: `SELF_TOKEN_TEXT[LanguageID.GO] = set()` in
  `src/prism/parser/lang_config.py` (why it's empty, and what resolves
  a Go method call instead), and the seed's unconditional L0 admission
  in `src/prism/slicer/knapsack.py` (already added alongside Issue A3).

### Added

- **Issue B1** - Go `method_declaration` receiver clauses are now parsed
  and registered receiver-qualified (`kind="method"`, name
  `<Module>.<ReceiverType>.<MethodName>`) instead of as bare top-level
  functions - see `src/prism/graph/concrete_builder.py` and
  `src/prism/parser/queries.py`. Go receiver/parameter type-signature
  instance binding is added (`_bind_go_typed_parameters`) so
  `c.JSON(...)`-shaped calls actually resolve to the correctly-named
  method - registration alone would not have been enough on its own.
- **Issue C2** - `SECURITY.md` documents Prism's offline execution
  boundary, opt-in OpenTelemetry trace ingestion, parser recursion-depth
  safeguards, and vulnerability-reporting contact.

### Recalibrated

- `benchmarks/comparison_tasks.py`'s four Gin seed symbols that named
  the *old*, incorrectly-flattened Go method names (`routergroup.Use`,
  `context.MustBindWith`, `context.GetInt64`, `context.Set`) are updated
  to their real, receiver-qualified names now that Issue B1's fix means
  the old names no longer exist in the symbol table - confirmed via a
  dry-run of the full 33-task suite (all four previously fell back to
  0 packed tokens; all four now resolve real content).
- `tests/test_runtime_tracer.py`'s tight-budget priority test raised its
  budget fixture from 100 to 150 tokens - Issue A1's fail-closed
  tokenizer counts run higher, so the old threshold no longer left room
  for exactly one extra candidate; re-measured directly, not guessed.
