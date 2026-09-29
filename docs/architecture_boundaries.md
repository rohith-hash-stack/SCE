# Architectural Boundaries: Explicit v1 Non-Goals

**Status: living document.** Records scope boundaries deliberately drawn
around Prism's core graph/retrieval engine, each one surfaced by the
architectural audit conducted against `src/prism/` and confirmed, by
direct code inspection, to be a real structural limit rather than an
oversight. A boundary recorded here is not "unimplemented" in the sense
of a missing feature on the roadmap - it is a considered decision that
building it would require compromising an invariant the rest of the
engine depends on (single-seed traversal determinism, bounded graph
diameter, a fixed causal-edge vocabulary), and that the tradeoff is not
worth it for v1. Each entry states the limitation, why it exists, and
what would have to change architecturally to lift it - so a future
proposal to lift one starts from the real tradeoff, not from "why wasn't
this done."

---

## Category 1: Sibling Continuity & Temporal State

**Limitation.** Prism's graph (`G_C`, built by `ConcreteGraphBuilder` in
`src/prism/graph/concrete_builder.py`) is a purely syntactic AST-derived
structure - `CALLS`/`EXTENDS`/`IMPLEMENTS`/`INSTANTIATES`/`OVERRIDES`/
`EMBEDS` edges (`TRAVERSABLE_RELATIONS`) all come from static call/
inheritance relationships visible in source text. It has no
representation of *runtime execution order* across independent,
sequentially-dependent steps that share no direct call relationship -
the canonical case being a multi-step migration or test setup/teardown
sequence (`test_step_1_create_user`, `test_step_2_update_user`,
`test_step_3_delete_user` as three siblings in one file, each depending
on state the previous one left behind, with no `CALLS` edge between
them at all).

**Why this is architectural, not an oversight.** Recovering "sibling B
runs after sibling A and depends on state A left behind" is not a static
analysis problem - it requires either a real execution trace (`prism.
runtime.reconciler`'s domain, which already exists for a different
purpose: confirming/discounting individual *call* edges after the fact,
not discovering new non-call ordering relationships) or heuristics over
naming/file position that would introduce non-deterministic, guessed
edges into a graph whose entire value proposition is deterministic,
explainable traversal. Adding a "temporal sibling" edge type would mean
either (a) a second, non-causal edge class with its own weight model
threaded through `DistanceEngine`/`causal_weights.py`, doubling the
traversal engine's own conceptual surface for a narrow case, or (b) a
runtime-trace dependency for a class of relationships the static engine
was never meant to observe.

**What would have to change to lift this.** A real, disclosed runtime
trace ingestion path purpose-built for temporal/sequential dependency
discovery (distinct from `reconciler`'s existing confirm/discount role),
plus a new edge relation and an explicit decision on how it composes
with `D_hybrid`'s existing weight model. Out of scope for v1.

---

## Category 2: Infilling & Bidirectional Sandwich Boundaries

**Limitation.** Prism's retrieval architecture (`PrismEngine.retrieve_*`,
`src/prism/engine.py`; the knapsack packer, `src/prism/packer/
submodular_knapsack.py`) is single-seed: one entry point, one forward/
undirected blast-radius expansion from it (`DistanceEngine.compute_all`).
It has no concept of extracting a "successor-invariant" context for an
*insertion point* between two pieces of existing code (an infilling/
fill-in-the-middle task, where the relevant context is bounded on both
sides by code that must remain unchanged and mutually consistent after
the insertion) - the sandwich boundary itself is never a seed, and there
is no mechanism to treat "everything between fixed point A and fixed
point B" as the object being retrieved for.

**Why this is architectural, not an oversight.** Every distance/cost
computation in the engine (`DistanceEngine.compute_all`, the knapsack's
own frontier expansion) is anchored to exactly one seed identifier. A
genuinely bidirectional sandwich retrieval needs either two simultaneous
seeds with a joint, order-aware budget allocation, or a new "span"
abstraction distinct from "node reachable from a seed" - either one is a
different retrieval primitive, not a parameterization of the existing
single-seed one. This is the same tradeoff the audit's proposed
"single-seed, not multi-seed" architectural invariant was written to
protect: multi-seed joint traversal is a materially different engine,
not an incremental extension.

**What would have to change to lift this.** A second retrieval mode
(explicitly not a modification of the existing single-seed path) with
its own budget-splitting and distance-composition rules for two fixed
boundary points. Out of scope for v1.

---

## Category 5: `READS_STATE` as a Traversable Graph Edge

**Limitation.** `ConcreteGraphBuilder._link_state_reads` (`src/prism/
graph/concrete_builder.py:2229`) already creates real `READS_STATE`
edges in `G_C` for a resolved `self.<attr>`/`this.<attr>` read, with a
real qualified target (exactly like a `CALLS` edge's own resolution).
These edges are deliberately excluded from `TRAVERSABLE_RELATIONS`
(`concrete_builder.py:108`) - `DistanceEngine`/the knapsack's frontier
expansion never walk them. A bare
attribute *read* (not a call, not a mutation) is not treated as a
causal/structural relationship for blast-radius purposes.

**Why this is architectural, not an oversight.** This was a real,
measured decision, not an unconsidered gap - the module's own comment
on `TRAVERSABLE_RELATIONS` records that an earlier, broader inclusion of
non-call relations caused a real regression (flooding a seed's
reachable set with an entire base class's call graph at full priority);
`READS_STATE` specifically was kept excluded because a bare attribute
read pulls in unrelated attribute nodes with "no comparable
'this is now unreachable without it' failure mode to justify the same
trade" the `EXTENDS`/`IMPLEMENTS`/`OVERRIDES` inclusion earned. Making
`READS_STATE` traversable would reintroduce that same class of
regression at graph scale: every function that reads any attribute of
any object it touches would gain a new, low-signal edge, materially
raising average node degree and collapsing effective graph diameter -
directly undermining `D_hybrid`'s own hub-fan-in discount (Category 9,
`_hub_fanin_penalty` in `src/prism/slicer/distance.py`), which already
exists specifically to keep a busy shared node from reading as
artificially close to everything that touches it. Adding a whole new
class of busy nodes (any attribute) would multiply the problem Category
9 was built to contain, not just add to it.

**Module-level constants are the one part of this already handled, by
a different mechanism.** A function reading a module-level constant
(`MAX_RETRIES`, `STATUS_CODES`) is architecturally distinct from reading
an instance attribute: the audited "Two-Tier Visibility Pipeline"
design (see the architectural feasibility audit delivered this session)
resolves this case *without* a `READS_STATE`-style graph edge at all -
constant references are discovered and priced as sidecar extraction
data, attached to a selected function's context at packing time, never
inserted into `G_C` and never seen by `DistanceEngine`/the knapsack's
graph-traversal frontier. This is the same "real data, deliberately kept
outside the traversable graph" shape `READS_STATE` itself already uses
for instance attributes - lexical bundling extends that same discipline
to module-scope constants, rather than reopening the traversable-edge
question this section documents as closed.

**What would have to change to lift the traversable-edge exclusion
itself** (as opposed to the already-planned lexical-bundling path
above, which does not require it): a materially different edge-weight
model that prices a `READS_STATE` hop cheaply enough to avoid the
degree-explosion failure mode above, validated against the same real
corpora (FastAPI, Django) Category 9's own before/after evaluation used.
Out of scope for v1.

---

## Category 10: Monorepo-Internal Cross-Package Resolution

**Limitation.** `TypeScriptSourceLocator` (`src/prism/external/
locator_ts.py`, Category 8's own external-dependency resolver) resolves
a package's typed entry point strictly from its `package.json`'s
declared build output (`types`/`typings`, `exports["."]`'s own `types`
condition, or a `main`-adjacent `.d.ts`) - the correct, and only
correct, contract for a genuinely *external* npm dependency, whose
published tarball already contains real, pre-built `dist/` output.
Inside a real pnpm/yarn/npm-workspace monorepo, a *sibling* package
(`@trpc/client` importing `@trpc/server`, confirmed live against the
real, pinned tRPC v10.45.4 corpus) resolves through `node_modules` to a
real workspace symlink pointing at that sibling's own package directory
(`packages/client/node_modules/@trpc/server -> ../../../server`) - but
that directory's `package.json` still declares `"types": "dist/
index.d.ts"` / `"main": "dist/index.js"`, and `dist/` is never built in
a source checkout with no compile step run (confirmed: no `dist/`
directory exists in the pinned commit at all). `TypeScriptSourceLocator`
correctly finds the symlink, correctly reads the real `package.json`,
and correctly returns `[]` - there is no fabricated fallback to the
sibling's own `src/index.ts`, since nothing in Category 8's contract
says a locator should ever read a package's *unbuilt* source instead of
its own declared, built entry point. A genuine third-party dependency in
the same monorepo (`@tanstack/react-query`, a real, separately-published
package with its own real built output already in pnpm's content-
addressable store) resolves correctly through this exact same code path
- confirmed live in the same validation pass - so this is not a general
monorepo/pnpm incompatibility, only the same-repo-sibling case
specifically.

**Why this is architectural, not an oversight.** Recognizing "this
`node_modules` entry is actually a workspace symlink back into my own
repo, so read its `src/` instead of its declared build output" is a
fundamentally different resolution question than anything Category 8's
`ExternalSourceLocator` protocol was designed to answer - it requires
detecting a symlink (or an equivalent workspace-manifest cross-
reference) *before* falling into the ordinary `package.json`-driven
chain, then re-entering indexing on that sibling package's own `src/`
tree via the ordinary in-repo path (`ConcreteGraphBuilder`, not
`extract_external_symbol`) rather than the external-stub rendering path
`external_symbol_to_node_entry` produces - a same-repo sibling's real
source should arguably never be rendered as an `L2_skeleton` external
stub at all, since (unlike a genuine third party) its real body is
right there on disk. Building this properly means a third resolution
tier alongside "resolve in this repo" and "resolve as an external
stub," not a parameterization of either existing one.

**What would have to change to lift this.** A workspace-awareness layer
that (a) detects a `node_modules/<pkg>` entry that is a symlink pointing
back inside the same repository root (not just an ordinary installed
copy), (b) resolves the sibling package as a second in-repo indexing
root rather than an external stub, and (c) defines how cross-package
in-repo symbols compose with the existing single-repo `GlobalSymbolTable`
(a second, separate table per workspace package, or one table spanning
the whole workspace - a real design decision, not a given). Out of scope
for the tRPC pilot: ground-truth tasks are scoped to `packages/server`
(itself a zero-runtime-dependency package with no cross-package imports
of its own), sidestepping this gap entirely rather than authoring tasks
against a known-unresolvable retrieval path.
