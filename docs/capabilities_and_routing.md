# PRISM: capabilities, boundaries and query routing

**Status: shipped behaviour, as of the set-4 ship decision.** What PRISM
does, what it leaves to the host model, where it stops, and how `prism.ask`
routes a query. Every limit below was checked against the code or measured;
the numbers come from the frozen held-out sets
(`harness/experiments/heldout_retrieval/`).

## 1. What the engine is

**A dual-layer code graph.**

- *Static layer*: Tree-sitter ASTs (Python, JavaScript, TypeScript/TSX,
  Robot Framework) turned into a symbol table and typed edges - `CALLS`,
  `EXTENDS`, `IMPLEMENTS`, `INSTANTIATES`, `OVERRIDES`, `EMBEDS`
  (`prism.graph.concrete_builder`). Registered entry points (routes, CLI
  commands, tasks, test callbacks) are indexed separately
  (`prism.graph.entry_points`).
- *Runtime layer*: traces recorded with `prism trace` are merged from
  `.prism/runtime_state.json`. An edge a trace observed is marked
  `CONFIRMED_RUNTIME`; an edge only a trace revealed is added as
  `RUNTIME_DISCOVERED` (`prism.runtime.reconciler`). Each trace carries an
  origin manifest (commit SHA, file fingerprints), and a stale trace is
  rejected (`prism.runtime.trace_validator`).

**Freshness.** The index is cached under a content hash of the
repository: a changed file means a rebuild the next time the index is
loaded. Nothing watches the filesystem. A running MCP server picks up
edits on `reindex_repo`. Git is read only for trace provenance - PRISM
does not mine commit history.

**Offline dependency summaries.** `prism.deps.summarizer` indexes each
installed pure-Python package once, statically (no model), and records
which I/O primitives each of its functions can reach; `prism ask --deps`
and the benchmark corpora use them so third-party I/O counts as
capability. Cached in `prism.deps.store`.

**Offline summary index.** `prism summarize` writes one plain-English
sentence per function to `.prism/summaries.json` with the locked
summarizer (section 4). Each summary is tied to a hash of its function's
source: an edited function loses its summary until it is summarized
again. Serving never calls a model.

## 2. Who does what

| PRISM (deterministic, no model at query time) | Host LLM |
|---|---|
| the structural graph: callers, callees, inheritance, entry points | reasoning about the code it is given |
| context slicing under a token budget: `prism.slice`, `prism.explain` | debugging, explanation, code generation |
| impact analysis: `prism.blast_radius` (every caller, by rule) | external library and framework knowledge |
| intent search: `prism.ask` (ranked symbols + entry breadcrumbs) | non-code documents (READMEs, tickets, specs) |
| capability tags (database, network, filesystem I/O; `find_symbols_by_tag`) | deciding which tool to call next |

The MCP surface (`prism.mcp.server`): `prism.ask`, `prism.slice`,
`prism.explain`, `prism.blast_radius`, `get_symbol_context`,
`get_architectural_invariants`, `find_symbols_by_tag`, `get_graph_status`,
`reindex_repo`. The usual chain is `prism.ask` -> a hit's `qname` ->
`prism.slice` or `prism.blast_radius`.

## 3. Hard limits

| limit | what happens | checked by |
|---|---|---|
| **Functions bound to a name instead of declared** - `export const f = () => {...}`, `const f = function () {...}`, arrow handlers in object literals | no symbol, so no node, no edges, no search hit; calls to them are unresolved. Hono's API is written this way, so set 4 replaced it with zod | probe: of `getPath` (const arrow), `getQuery` (declaration), `cors` (const function expression) only `getQuery` is indexed |
| **Dynamic dispatch the resolver cannot type** - `getattr(obj, name)()`, computed member calls, plugin registries filled at runtime | the edge is missing statically; a runtime trace can add it as `RUNTIME_DISCOVERED` | `prism.runtime.reconciler` |
| **Cross-service boundaries** - HTTP, gRPC, message queues between services | a client call is tagged as network I/O, but no edge links it to the handler in the other service; each service is its own graph | no cross-service linker in `src/prism` |
| **Cross-package imports inside a monorepo** | see `architecture_boundaries.md`, Category 10 | |
| **Callers' purpose** | a summary describes a function from its own card; the purpose its callers give it is not in it (Q2E q11 stays unrecovered) | `reports/purpose_search_py_14b_v2` |

## 4. Routing in `prism.ask`

Two independent, deterministic decisions, both reported in the result's
`intent` block (`intent.route`, `intent.summary_weight`).

**Summary weight - a rule, not a classifier** (`prism.summaries.index`):

    w = 0.5   when .prism/summaries.json was built by the locked pipeline
              and its valid summaries cover >= 95% of the functions and methods
    w = 0     otherwise (no index, another model or prompt, or too many
              functions edited since it was built)

Scoring is `BM25(code) + w * BM25(summary)`, the summary an independent
field (own IDF and length norm); at w = 0 the field is not consulted. The
locked pipeline (`prism.summaries.pipeline`, recorded in
`harness/experiments/purpose_search/CANDIDATE.md`):
`qwen2.5-coder:14b-instruct-q8_0` via Ollama, card v2, the v2 prompt, a
lint + repair pass (2 attempts). Set 4, scored once under a pre-registered
rule, shipped w = 0.5: 22 questions better / 7 worse (sign test p = 0.008),
recall@5 0.521 -> 0.615, MRR 0.442 -> 0.486
(`reports/purpose_search_set4/DECISION.md`).

**Route and exact-name pin** (`prism.query.route`):

- `identifier` - not a sentence, and code-shaped (`Router.include_router`,
  `url_for()`, `createApp`) or at most two plain words (`redirect`);
- `mixed` - a sentence with a code-shaped token ("where is `user_id`
  checked?");
- `natural_language` - everything else.

For `identifier` and `mixed` queries the symbols the query's names denote
(`prism.query.locate.qualified_name_matches`: the name itself, or names
ending in it on whole dotted segments) come first, best-scored first, marked
`pinned: true`; the ranked hits follow. Natural-language queries are not
pinned. On the frozen sets, 156 of 164 natural-language questions route
`natural_language`, 8 `mixed`, none `identifier`; all 104 exact-name
lookups built from set-4 gold names route `identifier`.

Exact-name lookups (104 queries: each set-4 gold name asked bare and as
`Class.method`; `reports/purpose_search_equivalence/exact_name_lookups.json`):

| ranking | top-1 | top-5 | MRR |
|---|---|---|---|
| BASE (code only) | 0.625 | 0.885 | 0.736 |
| FIELD w=0.5 | 0.712 | 0.894 | 0.784 |
| served (FIELD w=0.5 + pin) | **0.894** | **0.933** | **0.916** |

Every gold symbol lands inside its pinned block. The 11 lookups not at
rank 1 are names shared by several symbols - zod's `_parse` (36 matches),
fastify's `validate` (8), `save_session` and `make_response` (2 each) - where
the pinned block is ordered by score; a qualified query (`ZodObject._parse`)
names one symbol.

## 5. Guardrails

- **One pipeline.** The harness imports the prompts, the card builder and
  the lint from the shipped modules (`prism.summaries`), so what was
  evaluated is what ships. Card keys are identical, so a cache produced in
  the harness (for example on a GPU notebook) installs with
  `prism summarize --from-cache` and is checked card by card.
- **Equivalence on sets 2-4** (`harness/experiments/purpose_search/equivalence.py`,
  `reports/purpose_search_equivalence/EQUIVALENCE.md`): **PASS** on all 8
  repositories, 158 questions. Each committed summary cache installed in
  full through the shipped code (card keys identical); the automatic rule
  chose w = 0.5 on every repository (coverage 0.992-1.000); every
  question's ranking equals the harness FIELD w=0.5 ranking, and the
  per-question ranks of BASE and FIELD w=0.5 equal the committed results.
  The pin touched none of the 158 questions (the 8 `mixed` ones name no
  indexed symbol), so the served metrics are the FIELD w=0.5 metrics:

  | set | BASE recall@5 / MRR | served recall@5 / MRR |
  |---|---|---|
  | set 2 (60) | 0.834 / 0.678 | 0.933 / 0.748 |
  | set 3 (50) | 0.740 / 0.502 | 0.770 / 0.549 |
  | set 4 (48) | 0.521 / 0.442 | 0.614 / 0.486 |

  (Set 4 recall@5 reads 0.615 in `DECISION.md`: there it is the mean over
  48 questions, here the weighted mean of rounded per-repository means.)
- **Blast radius never sees summaries.** `prism.blast_radius` requests
  every caller by rule (R0); `tests/test_query_routing.py` and the
  equivalence run check its output is byte-identical with and without a
  summary index (173 of 173 gold seeds across the 8 repositories).
- **Staleness is automatic.** A summary whose function changed is dropped
  at load time; enough drops and coverage falls under 95%, which turns the
  field off rather than rank on stale text. `get_graph_status` reports
  `summary_index` (coverage, stale count, locked or not, the weight).

## 6. Operating it

    prism summarize --repo . --dry-run          # how many functions need a summary
    prism summarize --repo . --base-urls http://127.0.0.1:11434
    prism summarize --repo . --from-cache summary_cache_<repo>.json
    prism ask "where are notifications saved?" --repo .
    prism ask "Router.include_router" --repo .  # pinned exact name first

`prism summarize` is incremental: unchanged functions keep their summary.
After it runs, a live MCP server uses the new index on the next
`prism.ask` (its cache is keyed on the index file's mtime).
