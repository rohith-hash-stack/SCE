# Security Policy

Prism (Source Context Engine) statically parses arbitrary, potentially
untrusted repositories, can optionally ingest OpenTelemetry runtime
traces, and exposes a Model Context Protocol (MCP) server surface to an
LLM agent. This document states the real, current security posture of
each of those three surfaces - verified against the shipping source at
the time of writing, not aspirational.

## Execution boundary

The static analysis core (parsing, graph construction, tagging,
contracts, the Semantic Knowledge Engine, slicing/compression, and
serialization - everything under `src/prism/parser`, `src/prism/graph`,
`src/prism/tagger`, `src/prism/analysis`, `src/prism/slicer`,
`src/prism/serializers`) performs **zero outbound network calls** and
**never executes code from the repository it indexes**. Parsing is
Tree-sitter (a pure grammar-based parser, not an interpreter) plus
Python's own `ast` module (also parse-only) for the Python-specific
compression transforms - at no point is any function, method, or module
from a scanned repository imported or invoked.

**One documented exception:** `src/prism/slicer/tokenizer.py` prefers a
real `tiktoken` BPE encoding for token counting and, on first use only,
may attempt to fetch that encoding's public vocabulary table from
`tiktoken`'s remote host. This is the one network-capable code path in
the entire static core. It is best-effort and fails closed: any failure
(no network, a restrictive egress policy, an unreachable host) degrades
to a deterministic, conservative local regex approximation for the rest
of that process's lifetime (see that module's own docstring) - it never
raises, retries indefinitely, or blocks indexing. A deployment that
requires a hard network-free guarantee even for this one path can
pre-populate an offline `TIKTOKEN_CACHE_DIR` (see
`tests/fixtures/tokens/README.md`) or simply accept the fallback, which
this project's own CI and this document's own verification both do
today (the fallback backend, not a live encoding, is what this branch
was actually developed and tested against).

## Telemetry & runtime traces

`prism trace` is an **opt-in-only** command - it never runs unless a
caller explicitly invokes it, and it never reads any runtime data unless
one of `--trace-file`/`--ingest-otel` is also explicitly passed (or a
command is given to run under `prism trace --repo . -- <command>`,
capturing pytest instrumentation locally). Both `--trace-file` and
`--ingest-otel` read a **local JSON file path** the caller provides
(`prism.runtime.tracer.ingest_otel_file` and friends) - there is no
remote trace-collector endpoint, agent, or listener anywhere in this
codebase; "ingest an OpenTelemetry export" means *parse a file someone
already exported to disk*, not connect to a live OTEL pipeline. Ingested
trace data (call sites, timings) is merged into the local, on-disk
`.prism/` cache and never transmitted anywhere.

## MCP server surface

`prism mcp` defaults to the `stdio` transport (`--transport stdio`),
which opens no network socket at all - this is what every documented
integration (`docs/mcp_setup.md`: Claude Desktop, Cursor, VS Code) uses.
`--transport sse`/`--transport streamable-http` are available and do
bind a local HTTP server, standard MCP practice for that use case; a
deployment choosing either of those opt-in transports is responsible for
its own network exposure (binding address, auth in front of it, etc.) -
Prism itself adds no authentication layer at that transport level. The
MCP tools themselves (`get_symbol_context`, `get_architectural_invariants`,
`find_symbols_by_tag`, `get_graph_status`, `reindex_repo`) only ever read
and index the one repository path the server was started against - none
of them accept an arbitrary filesystem path or URL from the calling
agent.

## Parser safeguards against adversarial input

Because Prism indexes arbitrary (potentially adversarial or
machine-generated) source, a handful of recursive tree-walking code
paths were audited specifically for stack-exhaustion risk on
pathologically deep-but-syntactically-valid input, and three real,
reproducible gaps were found and closed:

- **`prism.slicer.compressor.compress_python`**: a deeply nested-but-valid
  expression (confirmed directly: a several-hundred-deep chain of `not`
  operators) previously raised an uncaught `RecursionError` from
  `ast.NodeTransformer.visit`/`copy.deepcopy` well before CPython's own
  parser-level nesting guards (which do already reject some pathological
  shapes, like excess parenthesis nesting, with a clean `SyntaxError`)
  would ever trigger. Both the L1 (`ArgPreservingSkeletonizer`) and L2
  (`ControlFlowSkeletonizer`) paths now catch `RecursionError` and
  degrade to a raw source slice for that one symbol, the same contract
  the pre-existing `SyntaxError` handling already had - one adversarial
  file degrades one compression call, not the whole `index`/`query` run.
- **`prism.parser.lang_config.iter_scoped_nodes`**: the CST-walking
  scan used for instance-binding/state-read detection across every
  non-Python language now stops descending past
  `MAX_SCOPED_NODE_DEPTH` (300) instead of raising - confirmed directly
  against a ~600-deep nested Go `if` block, which previously crashed it.
- **`prism.graph.concrete_builder.ConcreteGraphBuilder._mro_ancestors`**:
  the EXTENDS-edge ancestor walk (Python/JS/TS inheritance traversal)
  is now bounded to the same 300-level depth, defense-in-depth against a
  pathologically long linear inheritance chain (its existing cycle guard
  already prevented an infinite loop, but not a stack overflow on a very
  long *acyclic* chain).

These are the recursive traversals identified and fixed during this
audit, not a claim that every possible recursive code path in this
codebase has an explicit depth cap - see `tests/test_recursion_safeguards.py`
for the regression coverage proving each of the three above degrades
rather than crashes.

## Reporting a vulnerability

If you find a security issue in Prism, please open a private security
advisory on this repository (GitHub: Security -> Advisories -> Report a
vulnerability) rather than a public issue, so a fix can be prepared
before public disclosure. Include a minimal reproduction (a repository
fixture or input snippet, and the command that triggers the issue) where
possible - the parser-safeguard fixes above were all found and confirmed
this same way, with a concrete failing repro before any fix was written.
