# Phase B design: a reference pass for PRISM's graph (2026-11-20, before implementation)

**Starting point:** `harness/experiments/graph_coverage/BASELINE.md`, Python, 10 repositories, 171,188 uses.
- **Recorded: 33%.** Calls 70%, constructor calls 88%, base classes 87%.
- **Nearly everything else 0–15%:** attribute reads 2%, imports 3%, type annotations 10%, passed as an argument 7%,
  `__all__` 0%, decorators 0%, module level 9%, class body 6%.

## Where the gap is

`ConcreteGraphBuilder.pass2_resolve_calls` builds edges in four sub-passes:
- **2a:** import maps and exports;
- **2a-bis:** `EXTENDS`/`IMPLEMENTS`/`EMBEDS`;
- **2a-ter:** `OVERRIDES`;
- **2b:** `CALLS`/`INSTANTIATES` from each function's call sites;
- **2c:** virtual dispatch.

Only call sites are resolved, and only inside function and method bodies (`_resolve_calls_for_file_symbols` takes the
file's functions and methods). Nothing walks the other places a name is used, and nothing outside a definition has a
graph node to start an edge from.

**The resolver already exists:** `_resolve_reference_chain(segments, module, import_map)` turns a name or dotted chain
into an indexed qualified name through the file's imports, the export registry (re-exports, barrels), same-module
lookup and wildcard imports. The reference pass reuses it, so a reference resolves exactly as a call does.

## Revision after the edge-consumer audit (2026-11-20, before implementation)

**The reference edges go in a graph of their own,** `builder.reference_graph` (a `networkx.DiGraph`), not in
`builder.graph`. Two findings:
1. **The main graph keeps one edge per pair.** A `REFERENCES` edge added where `run` already calls `helper` would
   overwrite the `CALLS` edge.
2. **103 places in `src/prism` walk the main graph's edges, and several don't look at the relation:**
   - the packer's candidate index and knapsack;
   - `impact/overview` and `impact/context`;
   - the digest's callee map;
   - the MCP server's edge views;
   - `deps/host`.

   They would treat a reference as a call.

**So:**
- **Each reference-graph edge holds every kind of use between its two nodes** (`ref_kinds`, a set) and a count.
- **Module nodes exist only in the reference graph.**
- **The main graph, and everything that reads it, is unchanged** until B10 surfaces the references on purpose.
- **The index cache (`runtime/index_cache.py`) must save and restore the reference graph,** or a cached build would lose
  it.
- **The benchmark counts a use as recorded** if either graph has the connection; "surfaced" still means a main-graph
  edge the caller views traverse.

**The source of a use in a definition's header** (its decorators, parameter annotations and defaults): the definition
itself, matching its line range. A base class stays the class's `EXTENDS` edge.

## The change: sub-pass 2d, `_link_references`

**When it runs:** after 2c, when every import map, export and class relation is complete.

**What it walks:** every parsed file, over the tree-sitter tree the builder already holds (no second parser, so the
same code serves Python first and JS/TS next). It visits every identifier and dotted chain that is not the callee of
a call already resolved in 2b.

**For each one:**
1. **Resolve it** with `_resolve_reference_chain`. Unresolved names, and names that resolve to something not indexed,
   get no edge; no bare-name guessing, as in 2b's precision rules.
2. **Find the source node:** the innermost indexed definition containing it. A use in a definition's header
   (decorator, base class, parameter annotation, default value) belongs to the enclosing scope. Outside every
   definition, the source is the **module node** (B1).
3. **Add an edge** `source → target` with `relation="REFERENCES"` and `ref_kind`, one of:
   - `import` (B2): import or re-export;
   - `export` (B2): `__all__` entry, JS `export { x }`;
   - `annotation` (B3): parameter, return or variable annotation;
   - `generic` (B3): subscript, e.g. `Foo[int]`;
   - `isinstance` (B3): `isinstance`/`issubclass`, `instanceof`;
   - `except` (B3): `except Foo`, `catch`;
   - `decorator` (B4);
   - `value` (B4): passed as an argument, assigned, stored in a container, returned, compared;
   - `attribute` (B5): attribute or property read on it;
   - `string` (B6, later): `getattr`-style name strings, with `certainty="low"`.

   One edge per (source, target, `ref_kind`); a count attribute keeps how many uses it stands for.

**Module nodes (B1):**
- **Name:** the module's dotted name, the qualified-name prefix PRISM already uses.
- **Attributes:** `kind="module"`, `external=False`, `synthetic=True`.
- **When added:** only when a module-level use exists.
- **Class bodies:** the class's own node is the source; it already exists.
- **Not added to `symbol_table`,** so symbol lookups, digests and `prism_ask`'s index stay as they are.
- **Before relying on them,** each of the 32 places in `src/prism` that iterate graph nodes is checked for an assumption
  that every node has a symbol.

**Conditional definitions and aliases (B1):**
- **Conditional definitions:** a `def` under `if/else` or `try/except` is already registered by pass 1. The sibling
  binding of the same name (`GenericAlias = types.GenericAlias`) is recorded as a `value` reference.
- **Aliases:** `X = Y` at module level adds `module → Y` (`value`), and the alias name is registered in the module's
  own lookup, so `classmethod(X)` resolves to `Y`.

## What is not changed in Phase B's first step

- **`TRAVERSABLE_RELATIONS` stays as it is,** so the distance engine, the packer's budgets, `prism_change_context`'s
  callers and blast radius are unchanged.
  - **The new edges are visible to the benchmark** ("recorded") and to new code that asks for them.
  - **Surfacing them in the tools is B10:** a separate, measured change, a labelled "referenced by" section in the
    change context. That way the existing behaviour, and every PRISM test that pins it, is never moved by accident.
- **Tests stay hidden** (`include_tests=False`) until B7 adds them as their own group.

## How each step is accepted

1. **Unit tests per `ref_kind`,** on fixture repositories: Python first, then JS/TS.
2. **PRISM's own test suite** passes unchanged, which is possible because callers and traversal don't change.
3. **The benchmark's `check` against the baseline shows no drop,** and the "recorded" share of each `ref_kind` it
   targets rises. Done at ≥ 90% for every kind that can be resolved by reading the code (plan, Phase B).
4. **The build-time cost on the 10 repositories is recorded:** the pass walks every identifier, so its time and the
   graph's edge count are reported per repository.

## Order

1. B1 + B2 (module nodes, imports, exports): the largest zero-coverage group and the base for the rest.
2. B3 and B4 (type use, values, decorators).
3. B5 (attribute reads).
4. JS/TS for the same kinds; the benchmark's JS/TS step (A1) lands first, to measure it.
5. B6–B9.
6. B10 (surfacing, labelled).
