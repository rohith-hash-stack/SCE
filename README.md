# Prism

Prism parses an uncompiled, cloned Git repository and constructs a **Dual-Layer
Coupled Semantic Graph**: a concrete call graph (`G_C`) of real functions and
methods, coupled through a bipartite tag matrix (`M`) to a small semantic
metamodel (`G_T`) of architectural/behavioral tags (`#auth_guard`,
`#db_write`, `#payment_charge`, ...).

Given a target symbol and a token budget, Prism walks a **hybrid
structural+semantic distance** outward from the target and packs a
**variable-resolution context package** (L0 full source, L1 control-flow
skeleton, L2 interface contract, L3 one-line alias) that fits the budget
while maximizing preserved architectural meaning.

Everything is deterministic, static, and 100% offline: no vector databases,
no embeddings, no network calls, no dynamic code execution. Parsing is done
with [tree-sitter](https://tree-sitter.github.io/tree-sitter/) grammars
(Python, JavaScript, TypeScript, Go); Python's native `ast` module drives the
L0-L3 compression transforms.

## Install

```bash
pip install -e ".[dev]"
```

## CLI

```bash
# Summarize the dual-layer graph built for a repository
prism index /path/to/repo

# Extract a variable-resolution context package for a target symbol
prism query /path/to/repo src.controllers.checkout.process_checkout --budget 4000
```

## Architecture

See the design document for the full mathematical formalism and pipeline
description. In short:

1. **Stage 1/2 - Parsing & Linking** (`prism.parser`, `prism.graph`): tree-sitter
   extracts definitions from every supported source file; a two-pass linker
   resolves imports, local instance bindings, and call targets into a
   concrete graph `G_C`.
2. **Stage 3 - Tagging** (`prism.tagger`): deterministic AST predicates (import
   boundaries, call sinks, decorators) populate the bipartite matrix `M`
   between concrete symbols and the tag vocabulary `G_T`
   (`prism.graph.metamodel`).
3. **Stage 4 - Slicing** (`prism.slicer`): a hybrid dual-graph distance ranks
   every node relative to the seed symbol; an AST compressor renders each
   node at its allocated resolution; a greedy knapsack packs the result into
   the token budget.
4. **Serialization** (`prism.serializers`): the packed context is rendered as
   structured Markdown (or JSON for debugging) ready to hand to an LLM
   coding agent.

## Package layout

```
src/prism/
├── cli.py                     # `prism index`, `prism query`
├── parser/                    # tree-sitter grammar loading + queries
├── graph/                     # symbol table, concrete graph builder, metamodel
├── tagger/                    # deterministic tagging rules + engine
├── slicer/                    # distance, AST compressor, knapsack packer
└── serializers/                # markdown / json_debug output
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
