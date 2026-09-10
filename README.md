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

### Precision tiers

Language support is not uniform, and `src/prism/language_tiers.py` is the
source of truth for exactly what each language gets:

| Tier | Languages | What it means |
|---|---|---|
| **Tier 1 - Semantic & Instance Precision** | Python | Full AST instance binding, Rule A-D reference-chain resolution, relative-import/barrel-file resolution. |
| **Tier 2 - Structural & Lexical** | JavaScript, TypeScript/TSX, Java, C# | CST-based import/export and class-relation (`EXTENDS`/`IMPLEMENTS`) linking where applicable; no instance-based binding precision. |
| **Tier 3 - Lexical & Package-level** | Go | CST-based package/import resolution and compiler-directive/struct-tag capture; no class-relation edges (Go has no classes), no instance binding. |

A caller that needs every symbol in its context to carry Tier 1-grade
resolution can pass `--language-tier tier1-only` to `prism index`/`prism
query`, restricting indexing to Tier 1 languages only (currently Python)
instead of the default `permissive` behavior (index every supported
language at whatever precision it actually has).

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
