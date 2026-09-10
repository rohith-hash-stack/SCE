# Prism

Prism parses an uncompiled, cloned Git repository and constructs a **Dual-Layer
Coupled Semantic Graph**: a concrete call graph (`G_C`) of real functions and
methods, coupled through a bipartite tag matrix (`M`) to a small semantic
metamodel (`G_T`) of architectural/behavioral tags (`#auth_guard`,
`#db_write`, `#payment_charge`, ...).

On top of that graph, Prism runs a **Semantic Knowledge Engine**: an
absorbing Markov chain over the call graph that deterministically derives,
per symbol, which effect boundaries (network, storage, UI, assertions,
process lifecycle, or none at all) a call eventually crosses and with what
probability - then classifies every symbol, module, execution pipeline, and
the repository as a whole into a small set of architectural archetypes from
that distribution.

Given a target symbol and a token budget, Prism walks a **hybrid
structural+semantic distance** outward from the target and packs a
**variable-resolution context package** (L0 full source, L1 control-flow
skeleton, L2 interface contract, L3 one-line alias) that fits the budget
while maximizing preserved architectural meaning - annotated with behavioral
contracts (purity, effects, thrown exceptions) and the archetype/subsystem/
pipeline classification above.

Everything is deterministic, static, and 100% offline: no vector databases,
no embeddings, no network calls, no dynamic code execution, no LLM call in
the extraction pipeline itself. Parsing is done with
[tree-sitter](https://tree-sitter.github.io/tree-sitter/) grammars (Python,
JavaScript, TypeScript/TSX, Go, Java, C#); Python's native `ast` module
drives some of the L0-L3 compression transforms.

### Language Capability Matrix

Language support is not uniform, and it is not a single linear ranking
either - a "Tier 1/2/3" framing this project used earlier implied one
monotonic degradation axis, which doesn't hold up: Go gets real
package/directive-level resolution Python doesn't need, and (as of the
B1 fix below) real receiver/parameter-typed call resolution, while still
having zero inheritance-relation edges of any kind. The precise,
per-feature picture (`src/prism/language_tiers.py`, `src/prism/graph/
concrete_builder.py`, `src/prism/slicer/compressor.py` are the source of
truth):

| Feature | Python | TypeScript / JavaScript | Go |
|---|---|---|---|
| **Parser frontend** | Tree-sitter CST (structure) + native `ast` module (L0-L3 compression transforms) | Tree-sitter CST only | Tree-sitter CST only |
| **Symbol registration** | Functions, classes, methods (class-body ancestor walk) | Functions, classes, methods, interfaces (`kind="interface"`, distinct from `"class"` - Item 17) | Functions, structs (`kind="class"`), receiver methods - receiver-qualified (`Type.Method`) as of Issue B1; previously registered as bare top-level functions with zero receiver association |
| **Re-export resolution** | Recursive `ExportRegistry` walk (depth <=5, cycle-safe): relative imports, package `__init__.py` barrels, static `__all__` whitelist with a `PARTIAL_EXPORT_MAP` fallback flag for dynamic `__all__` | Same `ExportRegistry`: `export {x} from`/`export * from` re-export chasing | Package-level import resolution only - Go has no re-export syntax to chase (exported-ness is a capitalization convention, not a statement), so `ExportRegistry` doesn't apply here, not merely unimplemented |
| **Call resolution** | Full Rules A-D: constructor-call instance binding (`x = Foo()`), `self.<attr>` binding, lexical reference-chain resolution | Rules B/C/D lexical reference-chain resolution only - no instance binding of any kind (no `x = new Foo()`-shaped tracking) | Receiver/parameter/short-var-decl type-signature binding (`c.Method()`/`r := &Router{}`) plus a codebase-unique-receiver fallback (exactly one struct defines the method - tentative, lower-weight edge) when the type can't be tracked locally; measured 49.75% receiver-call resolution on gin-gonic/gin - real progress, short of full resolution, since arbitrary struct-*field* type tracking is out of scope |
| **Inheritance traversal** | EXTENDS-walk approximating C3 MRO (exact for single and ordinary multiple inheritance) + OVERRIDES detection | Same EXTENDS/IMPLEMENTS walk over class-heritage clauses (JS/TS class chains are single-parent in practice, so this is prototype-chain resolution, not literal multi-parent C3) | `EMBEDS` edges from anonymous struct embedding (Go's real composition mechanism - it has no EXTENDS/IMPLEMENTS syntax at all) with BFS depth-based method promotion honoring Go's real shadowing rules (nearest-depth wins; a same-depth collision is ambiguous and never guessed) |
| **Compression fidelity** | Native-`ast`-driven 4-tier L0-L3: L1 preserves real call arguments (`ArgPreservingSkeletonizer`), with a Data-Flow Centrality floor pinning high-centrality nodes at L1 regardless of distance | `UniversalSlicer` CST byte-range L0/L1/L2/L3 - L1 is generic statement pruning, no argument-preservation distinction and no centrality floor | Same `UniversalSlicer` CST byte-range pipeline as JS/TS - identical fidelity tier, not lower |

`src/prism/language_tiers.py` still derives a coarse `PrecisionTier`
label per language from this same matrix (useful as a quick filter, not
a substitute for the table above): `--language-tier tier1-only` on
`prism index`/`prism query` restricts indexing to languages with full
instance-binding call resolution (currently Python only) instead of the
default `permissive` behavior (index every supported language at
whatever precision it actually has, per the table).

## Install

```bash
pip install -e ".[dev]"
```

Or, with no local install at all:

```bash
uvx --from git+https://github.com/<owner>/<repo>.git prism --help
```

See `docs/mcp_setup.md` for wiring Prism into Claude Desktop/Cursor/VS Code
as a zero-install MCP server.

## CLI

```bash
# Summarize the dual-layer graph built for a repository
prism index /path/to/repo

# Extract a variable-resolution context package for a target symbol
prism query /path/to/repo src.controllers.checkout.process_checkout --budget 4000

# Record a runtime trace (pytest or an OpenTelemetry export) and reconcile
# it into the static graph - confirms which statically-possible call paths
# actually execute
prism trace --repo . -- pytest tests/
prism status --repo .

# Run Prism as a Model Context Protocol server for an agent/IDE integration
prism mcp --transport stdio --repo .
```

## Architecture

See [`docs/design_formalism.md`](docs/design_formalism.md) for the full
mathematical formalism and pipeline description. In short:

1. **Stage 1/2 - Parsing & Linking** (`prism.parser`, `prism.graph`): tree-sitter
   extracts definitions from every supported source file; a two-pass linker
   resolves imports, local instance bindings, and call targets into a
   concrete graph `G_C`.
2. **Stage 3 - Tagging & Contracts** (`prism.tagger`, `prism.graph.contracts`):
   deterministic AST predicates (import boundaries, call sinks, decorators)
   populate the bipartite matrix `M` between concrete symbols and the tag
   vocabulary `G_T` (`prism.graph.metamodel`); a separate AST pass extracts
   each symbol's behavioral contract - signature, purity, effects, thrown
   exceptions, cyclomatic complexity.
3. **Stage 4 - Semantic Knowledge Engine** (`prism.analysis.flow_engine`,
   `prism.graph.symbol_archetype`/`subsystems`/`flows`/`repository_profile`):
   an absorbing Markov chain over the call graph (SCC-contracted, truncated
   power series, no dense inversion) derives each symbol's downstream effect
   distribution, upstream entry-point reachability, relative graph depth, and
   choke centrality; four classification layers turn that into a symbol
   archetype, a per-module (subsystem) contract, a per-entry-point execution
   pipeline contract, and one repository-wide archetype - all cached to
   `.prism/` so the (offline, zero-network) sink taxonomy in
   `prism.taxonomy` and the Markov solve only run once per unchanged index.
4. **Stage 5 - Slicing** (`prism.slicer`): a hybrid dual-graph distance ranks
   every node relative to the seed symbol; an AST compressor renders each
   node at its allocated resolution; a greedy knapsack packs the result into
   the token budget.
5. **Serialization** (`prism.serializers`): the packed context is rendered as
   structured Markdown - target source plus behavioral contracts, dependency
   context, and the Stage 4 hierarchical structural summary - (or JSON for
   debugging) ready to hand to an LLM coding agent.
6. **Runtime reconciliation** (`prism.runtime`): an optional `prism trace`
   pass records which statically-possible call edges are actually exercised
   (via pytest instrumentation or an ingested OpenTelemetry export) and
   raises their confidence in the graph.

Prism also runs as a **Model Context Protocol server** (`prism mcp`,
`prism.mcp`) exposing `get_symbol_context`, `get_architectural_invariants`,
`find_symbols_by_tag`, `get_graph_status`, and `reindex_repo` as tools an
agent can call directly against a lazily-indexed, in-memory-cached
repository - see `docs/mcp_setup.md`.

## Package layout

```
src/prism/
├── cli.py                     # `prism index`/`query`/`trace`/`status`/`mcp`
├── parser/                    # tree-sitter grammar loading + queries
├── graph/                     # symbol table, concrete graph builder, metamodel,
│                               # behavioral contracts, call-site context, and the
│                               # Layer 2-5 archetype/subsystem/flow/repository
│                               # classification (symbol_archetype.py, subsystems.py,
│                               # flows.py, repository_profile.py, hierarchy.py)
├── analysis/                  # flow_engine.py - the absorbing Markov chain core
├── taxonomy/                  # offline, zero-network sink effect taxonomy (JSON)
├── tagger/                    # deterministic tagging rules + engine
├── slicer/                    # distance, AST compressor, knapsack packer
├── serializers/                # markdown / json_debug output
├── runtime/                    # trace/reconciler + contract/flow disk caching
└── mcp/                        # Model Context Protocol server + repo cache
```

## Benchmarks

`benchmarks/` quantifies Prism's context packing against a naive whole-file
dump: token compression, call-graph/invariant-tag coverage, and syntactic
validity of every rendered code block - offline, on synthetic fixtures, and
live against a real OpenAI model and a real cloned GitHub repository. See
`benchmarks/README.md`.

```bash
python -m benchmarks.run_benchmark --suite                 # offline metrics
python -m benchmarks.live_eval --dry-run                   # live-eval tasks, no API key needed
python -m benchmarks.clone_eval --repo <git-url>            # index a real repo
python -m benchmarks.multi_repo_eval --suite all            # httpx/flask/marshmallow x 3 query types
```
