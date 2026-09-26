# Phase C Architecture Specification: Cross-Boundary External Dependency Retrieval

**Status: Steps 1-3 implemented and tested on `feature/phase-c-external-
deps` (`097ec4b`, `1f7af68`, and the commit syncing this document to
them) - `prism.external.index` (Step 1), the conditional three-pass
engine routing and partitioned knapsack sub-budget (Step 2), and a real
end-to-end `t018` simulation against the pinned FastAPI corpus and the
real installed Starlette package proving the whole pipeline (Step 3).
Sections 1, 2, and 5 below have been updated to describe what was
actually built, with every real deviation from the original draft
called out explicitly rather than silently absorbed - see each
section's own "as built" note. Sections 3, 4, and 6 (partitioned
budgeting's own packer-function split, scoring-contract updates beyond
the zero-change path already verified in Step 3, and the broader
rollout plan) remain the original design, not yet implemented beyond
what Steps 1-3 needed.**

## 0. Problem statement

Phase B's FastAPI evaluation exposed a real construct-validity gap,
diagnosed and disclosed rather than worked around: three tasks
(`fastapi_t02_008`, `_018`, `_019`, `_020`) have real, causally correct
answers that name a symbol outside the target repository - an inherited
Starlette method (`add_route`, `t018`) or a third-party library call
(`orjson.dumps`/`ujson.dumps`, `t019`/`t020`). Two of the four
(`t019`/`t020`) were fully resolved by a prompt-only fix once the
excluded symbol's literal name stopped being echoed into the model's
own answer (negative-constraint priming, confirmed empirically). `t018`
was not - the model reliably names `add_route` because its own
retrieved source context literally contains `self.add_route(...)`, and
no prompt wording changes what's visible in that context. `t008` was a
different case entirely (a real ground-truth gap - `APIWebSocketRoute`
turned out to be a genuinely indexed, directly reachable in-repo
symbol), not an external-dependency problem, and needs no Phase C work.

`t018`'s residual failure is a structural one: this repository's own
graph (`ConcreteGraphBuilder`, `builder.symbol_table`) only ever indexes
the target repo. `add_route` is real, present in the model's retrieved
context, and a causally correct answer - but it does not exist in any
index this system currently builds, so no scoring contract that only
ever validates against `builder.symbol_table` can ever accept it as
legitimate. Phase C's job is to build a second, narrow, leaf-only index
for exactly this class of symbol, and thread it through retrieval and
scoring without inflating context windows the way naively including
every reachable third-party symbol would.

**Explicit non-goals**: recursive external-to-external call graphs,
external symbol bodies (Phase C indexes signatures/docstrings only,
never third-party source), or generalized third-party dependency
analysis beyond what a task's own seed can reach in one hop. This is a
targeted fix for a diagnosed gap, not a new general-purpose subsystem.

## 1. Dynamic Pipeline Flow (Conditional 3-Pass)

### 1.1 Current two-pass contract (as-is)

`src/prism/engine.py`'s `PrismEngine.retrieve_two_pass` runs exactly two
turns, both engine-owned:

```python
RequestSymbolsCallback = Callable[[str, str], list[str]]  # (manifest_text, task_prompt) -> requested_symbols

def retrieve_two_pass(self, seed_id, budget_tokens, request_symbols: RequestSymbolsCallback, ...):
    manifest_text, candidate_universe = self.build_candidate_manifest(seed_id, ...)   # Turn 1
    requested_symbols = request_symbols(manifest_text, task_prompt)                    # caller's own LLM call
    pkg, skipped = self.retrieve_requested(seed_id, budget_tokens, requested_symbols, candidate_universe, ...)  # Turn 2
    ...
```

A third, answer-generation turn already exists today, but it is **never
engine-owned** - it happens entirely in the caller
(`benchmarks/run_two_pass_benchmark.py`'s own second LLM call, whose
response is what `turn2_response` records in every checkpoint cell seen
throughout Phase B). This matters for scoping Phase C correctly: only
two of the three conceptual "passes" below are ever `prism.engine`'s own
responsibility, in both the current and the proposed design.

### 1.2 New `RequestSymbolsCallback` contract

**As built (`1f7af68`) - revised from a breaking change to a backward-
compatible one**, per this task's own explicit instruction rather than
this section's original draft:

```python
RequestSymbolsCallback = Callable[[str, str], list[str] | tuple[list[str], bool]]
#  (manifest_text, task_prompt) -> requested_symbols                      # legacy
#                                -> (requested_symbols, needs_external_deps)  # Phase-C-aware
```

`prism.engine._normalize_request_result` unwraps either shape into
`(requested_symbols, needs_external_deps)`, treating a plain `list[str]`
as `needs_external_deps=False` - the exact behavior every pre-Phase-C
callback already had before this flag existed. `retrieve_two_pass`
itself calls the normalizer too (and simply discards the flag - it
stays a pure two-pass path by construction; a caller wanting the
conditional branch calls the new `retrieve_two_or_three_pass` instead).
**No existing caller needs to change** -
`tests/test_two_pass_engine.py`'s own deterministic callbacks and
`benchmarks/run_two_pass_benchmark.py`'s manual Turn-1 call both keep
returning a plain `list[str]`, unmodified.

The boolean is still a caller-supplied judgment, made by the same
Turn-1 LLM call that already produces `requested_symbols` (the original
design's own reasoning is unchanged): the prompt asks the model to
additionally emit a single boolean flag alongside its causal-pipeline
request, e.g. extending Turn 1's own response schema from
`{"requested_symbols": [...]}` to `{"requested_symbols": [...],
"needs_external_deps": bool}`. This keeps the "is this even relevant"
judgment isolated to a structured field the model fills alongside work
it is already doing, rather than a whole separate LLM call - the
cheapest possible way to introduce the branch point.

A second, dedicated type was added for Turn 2b, not present in the
original draft (which had reused `RequestSymbolsCallback` itself for
it - inconsistent with Section 1.3's own stated `-> external_requested_
symbols` contract, a plain list, not a tuple; fixed here rather than
carried forward as written):

```python
RequestExternalSymbolsCallback = Callable[[str, str], list[str]]
#  (external_manifest_text, task_prompt) -> external_requested_symbols
```

### 1.3 Branching logic

```
Turn 1  (existing, unchanged): build_candidate_manifest(seed_id)
        -> (manifest_text, candidate_universe)

        request_symbols(manifest_text, task_prompt)
        -> (requested_symbols, needs_external_deps)

        needs_external_deps == False:
            -> standard two-pass path, UNCHANGED behavior
            Turn 2: retrieve_requested(seed_id, budget, requested_symbols, candidate_universe)
            -> (pkg, skipped)

        needs_external_deps == True:
            -> conditional third pass
            Turn 2a (new): build_external_candidate_manifest(seed_id)
                -> (external_manifest_text, external_candidate_universe)
            Turn 2b (new, caller's own LLM call, a second
                     RequestSymbolsCallback-shaped invocation against the
                     external manifest instead of the internal one):
                request_external_symbols(external_manifest_text, task_prompt)
                -> external_requested_symbols
            Turn 3 (new): retrieve_external_requested(seed_id, budget,
                     requested_symbols, external_requested_symbols,
                     candidate_universe, external_candidate_universe)
                -> (pkg, skipped, external_skipped)
```

Turn 2b is a **separate, dedicated LLM call**, not folded into Turn 1's
own prompt - this is the core lesson Phase B's own priming investigation
established: asking a small model to make two distinguishing judgments
in one shot (which internal symbols, *and* which external ones) is
measurably less reliable than isolating each judgment into its own
narrow turn. An **augmented two-pass** alternative (Turn 1 requests
external stubs directly, no separate Turn 2b) was considered and
rejected for the same reason - it reintroduces exactly the overloaded-
turn failure mode this design exists to avoid.

### 1.4 New engine methods (`src/prism/engine.py`)

**As built (`1f7af68`) - real signatures, revised from the original
draft below in three ways**: (1) no `ExternalSymbolIndex` class exists -
resolution goes straight through Step 1's `prism.external.index.
extract_external_symbol_all`, cached on the engine instance
(`self._external_symbol_cache`) rather than a separately-constructed
index object; (2) `build_external_candidate_manifest` takes
`(turn1_symbols, root_imports)`, not `(seed_id, external_index)` - this
task's own explicit, simpler signature, resolving against the repo's
own raw call expressions (`prism.parser.lang_config.
call_callee_segments`) rather than a pre-built index's own
`resolve_direct_callee`; (3) Turn 3's hydration is inlined directly into
`retrieve_two_or_three_pass` rather than a separate `retrieve_external_
requested` method - a scope simplification, not a rejection of that
method existing later if real reuse ever calls for it.

```python
def build_external_candidate_manifest(
    self, turn1_symbols: list[str], root_imports: list[str] | None = None,
) -> tuple[str, set[str]]:
    """Turn 2a: every external symbol directly (one-hop) reachable from
    turn1_symbols' own real outgoing call expressions, resolved against
    root_imports. Two paths, in precision order: (1) a call receiver
    naming a root_imports package directly (orjson.dumps(...)) resolves
    precisely against that package; (2) a self/this-receiver call whose
    leaf name has zero real in-repo candidates (GlobalSymbolTable.
    candidates_for_simple_name - the real t018 case) is tried against
    every root_imports package. Both paths use extract_external_symbol_
    all, not extract_external_symbol - discovered during Step 3's own
    t018 integration test: Starlette ships TWO real add_route
    definitions (Router.add_route and a distinct, delegating Starlette.
    add_route), and a single-result lookup would let an arbitrary
    file-locate ordering silently pick one rather than offering both as
    real candidates for Turn 2b to choose between. No LLM call - real
    static lookup, mirroring build_candidate_manifest's own no-LLM
    contract for Turn 1. Resolved ExternalSymbolInfo objects are cached
    on this engine instance so Turn 3 never re-parses."""

def retrieve_two_or_three_pass(
    self, seed_id: str, budget_tokens: int,
    request_symbols: RequestSymbolsCallback,
    request_external_symbols: RequestExternalSymbolsCallback | None = None,
    root_imports: list[str] | None = None,
    task_prompt: str = "", task_type: str | None = None,
    max_hops: float = CANDIDATE_INDEX_MAX_HOPS,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
) -> tuple[ContextPackage, dict[str, object]]:
    """A new, additive method alongside retrieve_two_pass (kept
    unchanged - Section 5's own note), not a replacement for it. Runs
    Turn 1 (shared with retrieve_two_pass), then branches on
    needs_external_deps. False: identical to retrieve_two_pass (full
    budget_tokens, zero external overhead - request_external_symbols/
    build_external_candidate_manifest are never called at all). True:
    split_budget_for_external reserves the external sub-budget up
    front; internal Turn 2 (retrieve_requested, unmodified) runs against
    internal_budget only; Turn 2a resolves external candidates from
    {seed_id} | requested_symbols; Turn 2b fires only when that
    candidate set is non-empty; Turn 3 (inlined here, not a separate
    method) resolves each requested name against the Turn-2a cache,
    admits it as a role="external" NodeEntry while running cost fits
    external_budget, and appends admitted nodes onto the internally-
    packed ContextPackage via model_copy. Raises ValueError if
    needs_external_deps=True with no request_external_symbols callback
    supplied. diagnostics gains needs_external_deps,
    external_candidate_count, external_requested_count,
    external_skipped_hallucinated alongside the existing candidate_
    count/requested_count/skipped_hallucinated - additive keys."""
```

Verified end-to-end in Step 3 (`tests/test_three_pass_integration.py`)
against the real, pinned FastAPI corpus and the real installed
Starlette package: `FastAPI.setup`'s real `self.add_route(...)` call
sites resolve to both real Starlette definitions, a deterministic Turn
2b callback selects `Router.add_route`, the resulting `ContextPackage`
carries both the internal FastAPI seed node and the external Starlette
stub, the external stub's token cost fits inside its 12.5%-of-budget
sub-budget, and `score_debug_causal` - via `selected_symbols(pkg)`,
with **zero changes to its own signature**, confirming Section 4.1's
"leaner path" claim empirically rather than just arguing it - scores a
response naming `add_route` as legitimate, while a genuinely invented
symbol still scores as hallucinated.

### 1.5 Latency vs. precision (informing the conditional design, not new analysis)

Already established in this evaluation's own prior architecture
discussion, carried into this spec as the reason Section 1.3's branch
exists rather than an unconditional three-pass: each additional turn on
a local Ollama deployment is a full sequential round-trip with no
cross-turn KV-cache reuse under the current stateless-completion client
design (`RequestSymbolsCallback` issues independent calls, not a
continued conversation). The conditional design pays that cost only on
`needs_external_deps=True` cells - for FastAPI's own 25-task suite, that
was a minority (t008, t013, t018, t019, t020, t021, t023, t024 are the
shallow/single-symbol cluster, of which only 3 genuinely need this path
post-Phase-B). An always-on three-pass would pay the third turn's
latency on every cell regardless of relevance.

## 2. External Dependency Subsystem

### 2.1 Indexing strategy - revised: reuse tree-sitter + `ContractExtractor`, don't reimplement parsing

**Superseded design note**: an earlier draft of this section proposed a
bespoke, per-language `BaseStubExtractor`/`PythonStubExtractor` pair,
with the Python implementation walking Python's own stdlib `ast` module
to strip function bodies. Rejected after checking the real codebase:
**nothing else in Prism uses stdlib `ast` anywhere** - the entire
indexing pipeline, Python included, runs on tree-sitter
(`prism.parser.tree_sitter_loader`), and Prism already has real,
per-language, already-tested signature/docstring extraction in
`prism.graph.contracts.ContractExtractor.extract_symbol` (`params`,
`return_type`, `docstring`, `is_async`, dispatched via
`parsed.language_id` - already covering Python, JavaScript, TypeScript,
Go, Java, C#, per `prism.parser.tree_sitter_loader.LanguageID`). A
bespoke per-language extractor with one hand-rolled implementation
doesn't actually make TypeScript or Go support cheaper later - each
would still need a from-scratch parser, exactly like the rejected
`PythonStubExtractor` did. The revised design gets real cross-language
reach for near-free by reusing what already exists:

1. **Locate** the external package's real source or stub files on disk -
   the one genuinely per-ecosystem step (Python: `site-packages`, a
   `.pyi` stub package, or `typeshed`; TypeScript: `node_modules`'s own
   `.d.ts` files; Go: a vendor directory or module cache). This is a
   thin, per-ecosystem adapter (`ExternalSourceLocator`, Section 2.2),
   not a parser.
2. **Parse** the located file through Prism's *existing* tree-sitter
   loader (the same `EXTENSION_LANGUAGE_MAP`/`LanguageID` dispatch every
   in-repo file already goes through) to get a real `ParsedFile`.
3. **Extract** signature + docstring via `ContractExtractor`'s *existing*
   `extract_symbol` (or a thin wrapper around it that only reads the
   fields Phase C needs and discards the rest) - the body is never
   rendered because nothing downstream of this step ever asks for it,
   not because of a special stripping pass.

**Explicitly not extracted** (unchanged from the prior draft): call
graphs between external symbols (Non-goal, Section 0), inheritance
chains beyond the single declaring class, or anything requiring real
bytecode/execution-order analysis - tree-sitter's own parse tree already
gives structural signature data without needing any of this.

**Real, existing prior art for graceful degradation**:
`prism.slicer.tokenizer`'s own fail-closed-to-heuristic pattern (real
BPE counting, falling back to a cheap heuristic only when the real
tokenizer can't run) is the template for this indexer's own error
handling - a package whose files can't be located or parsed at all
should degrade to "this task's `needs_external_deps` branch finds
nothing, falls back silently to the Turn-2 internal-only result," never
a hard failure.

**A named, current gap, stated plainly rather than implied away**: Rust
is not in Prism's language list at all (`LanguageID` above has no Rust
member) - no tree-sitter grammar loaded, in-repo or otherwise. "Rust
`pub` crates" is not reachable by this design, or by any stub-extractor
design, until Rust is added to Prism's core tree-sitter support - a
separate, materially larger undertaking outside Phase C's own scope.
Python (`.pyi`/real `.py`), TypeScript (`.d.ts`), and Go (vendored `.go`
source, following the same "real source, signature-only render" path
Starlette itself needs) are the three ecosystems this design actually
reaches, matching Prism's own current language coverage.

### 2.2 New index type

```python
# src/prism/external/index.py (new module)

@dataclass(frozen=True)
class ExternalSymbolInfo:
    qualified_name: str          # e.g. "orjson.dumps", "starlette.routing.Router.add_route"
    module_origin: str           # e.g. "orjson", "starlette"
    language: str                 # a real prism.parser.tree_sitter_loader.LanguageID value
    signature_text: str          # rendered from ContractExtractor's own params/return_type, real, not fabricated
    docstring: str | None
    kind: str                    # "function" | "method" | "class"

class ExternalSourceLocator(Protocol):
    """The one genuinely per-ecosystem piece (Section 2.1, step 1) - a
    thin adapter, not a parser. One real implementation per ecosystem
    Prism's tree-sitter loader already supports (Python, TypeScript, Go
    to start - see the Rust gap noted above)."""
    def locate(self, package_name: str, package_version: str | None) -> list[Path]: ...

class ExternalSymbolIndex:
    """Deliberately NOT a ConcreteGraphBuilder subclass or a graph at
    all - Non-goal (Section 0) rules out an external call graph, so
    there is no graph to build. A flat, queryable symbol table keyed by
    qualified name, built once per package via an ExternalSourceLocator
    + Prism's own tree-sitter loader + ContractExtractor (Section 2.1),
    with a real one-hop resolution method:

        def resolve_direct_callee(self, source_repo_symbol: str, external_ref: str) -> ExternalSymbolInfo | None

    `source_repo_symbol` lets a future extension distinguish "this
    external ref is really reachable from this specific in-repo call
    site" from "this name merely exists somewhere in an installed
    package" - out of scope for Phase C's own leaf-only resolution
    (which needs only "does resolve_direct_callee return a real
    ExternalSymbolInfo", not caller-specific disambiguation), included
    in the signature now so a future phase doesn't need a second
    breaking change to add it.
    """
```

### 2.3 Node taxonomy - a real schema extension, not a free-form tag

`prism.surface.models.NodeEntry.role` is a closed
`Literal["seed", "callee", "caller", "transitive"]` today - an external
dependency node cannot be represented by adding an ad-hoc `#external_dep`
string tag to an existing role; the type itself needs a new member. This
project already has a real, precedented mechanism for exactly this shape
of change: `ContextPackage.schema_version` (currently 2), gating the
`<causal_path>` addition made for Phase F. Phase C's own external-node
support should land as `schema_version=3`:

```python
class NodeEntry(_Frozen):
    role: Literal["seed", "callee", "caller", "transitive", "external"]  # new member
    # file/line/end_line: for an external node, this is the STUB's own
    # location (e.g. site-packages/orjson-stubs/__init__.pyi), never a
    # location inside the target repo - real, but a different filesystem
    # root than every other node's `file` field currently assumes.
    # body: the stub's own signature_text + docstring, rendered the same
    # way a signature-only "L2_skeleton" compression already renders an
    # in-repo stub today (real prior art: `_signature_stub` in
    # submodular_knapsack.py) - never a fabricated or invented body.
```

No new `EdgeEntry.type` member is needed under the leaf-only design
(Section 0's own non-goal): an external node is always a `CALLS` or
`INSTANTIATES` target from a real in-repo node, using the exact same
edge type vocabulary already defined - only the *node* being pointed to
is new, not a new *kind* of edge. `schema_version=1`/`2` consumers are
unaffected per the existing forward-compatible pattern (`renderer.py`'s
own documented guarantee: "a schema_version=1 consumer... simply
[ignores what it doesn't know]").

**Metadata boundary, stated explicitly**: an external node's `contract:
Optional[NodeContract]` field is always `None` (no `BehavioralContract`
can be computed for code this system never indexes at the AST level)
and its `compression` is always `"L2_skeleton"` (a full-body external
render is architecturally impossible, not merely undesired - there is
no real body to promote to `"L0_full"`).

## 3. Partitioned Knapsack Budgeting

### 3.1 Current packer state (grounded in the real code, not assumed)

`pack_symbol_context_requested` (`src/prism/packer/submodular_knapsack.py`)
- Turn 2's own packer today - does **zero cost-based exclusion**, per its
own docstring: every resolved requested symbol is included
unconditionally; real budget enforcement happens afterward, in
`_enforce_render_budget` (`src/prism/surface/build.py`), which trims/
downgrades the whole rendered `ContextPackage` to `target_budget` with
no concept of internal vs. external - it only ever sees total tokens.
`_default_costs` prices every candidate via `builder.symbol_table`/
`builder.parsed_file` lookups - both undefined for an external symbol by
construction (Section 2's whole reason for existing).

### 3.2 New sub-budget reservation

Rather than pooling internal and external candidates into one knapsack
competition (a real starvation risk: a verbose external stub could
outbid an internal pipeline symbol for the same budget dollar, the
mirror image of the existing `_PROTECTED_DOWNGRADE_ROLES` protection on
the internal side), reserve a fixed fraction of `budget_tokens` for
external stubs up front:

```python
DEFAULT_EXTERNAL_BUDGET_FRACTION = 0.125  # 12.5% - resolved (Section 7,
                                            # item 5), the midpoint of the
                                            # original 10-15% range
DEFAULT_EXTERNAL_BUDGET_FLOOR_TOKENS = 256  # resolved - guarantees a
                                              # minimum viable external
                                              # allocation even at the
                                              # tightest established
                                              # budget (2000): 12.5% of
                                              # 2000 = 250 < 256, so the
                                              # floor is the one that
                                              # actually binds there
DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS = 1024  # resolved - caps external
                                                 # spend at large budgets;
                                                 # never binds at any of
                                                 # 2000/4000/8000 (12.5%
                                                 # of 8000 = 1000 < 1024)

def split_budget_for_external(
    budget_tokens: int, fraction: float = DEFAULT_EXTERNAL_BUDGET_FRACTION,
    floor_tokens: int = DEFAULT_EXTERNAL_BUDGET_FLOOR_TOKENS,
    ceiling_tokens: int = DEFAULT_EXTERNAL_BUDGET_CEILING_TOKENS,
) -> tuple[int, int]:
    """(internal_budget, external_budget) - external_budget =
    clamp(fraction * budget_tokens, floor_tokens, ceiling_tokens), never
    exceeding budget_tokens itself (a pathologically small budget_tokens,
    e.g. under floor_tokens, degrades to external_budget=budget_tokens,
    internal_budget=0 - correct behavior, not a bug, since there would be
    nothing else to spend it on either). The existing internal packer
    (pack_symbol_context_requested, unmodified) runs against internal_budget;
    a new, parallel external packer (3.4) runs against external_budget."""
```

### 3.3 `SubmodularPackedItem.origin` field

```python
@dataclass
class SubmodularPackedItem:
    symbol: str
    cost: int
    feature_mask: int
    dist_w: float
    role: str = ROLE_TRANSITIVE
    compression: str = "L0_full"
    origin: str = "internal"  # new field, default preserves every
                                # existing caller's own construction
                                # calls without a required-argument
                                # breaking change
```

A dataclass field with a default is additive, not breaking, for every
existing caller that constructs `SubmodularPackedItem` positionally or
by keyword without `origin` - confirmed by grep: every current
construction site in this module already uses keyword arguments for
every field it sets, so a new trailing defaulted field changes nothing
about those call sites.

### 3.4 New external packer function

```python
def pack_external_context_requested(
    external_index: "ExternalSymbolIndex",
    requested_external_symbols: list[str],
    external_candidate_universe: set[str],
    external_budget_tokens: int,
) -> tuple[list[SubmodularPackedItem], list[str]]:
    """Mirrors pack_symbol_context_requested's own "ask, don't
    re-derive" selection strategy (no knapsack competition among
    externals either, by design - the sub-budget reservation in 3.2 is
    the only competition point, not a second nested knapsack), but
    against external_index instead of builder.symbol_table, and capped
    by external_budget_tokens: iterate requested_external_symbols in
    order, resolve each against external_candidate_universe, price via
    a new _default_external_costs (count_tokens on the stub's own
    signature_text + docstring - no source-file lookup, since none
    exists), stop admitting once external_budget_tokens is exhausted
    (an explicit running-cost check this function owns, since
    _enforce_render_budget's own post-hoc trim, 3.5, is scoped to the
    combined package and should not be the only thing enforcing the
    partition). Returns (items, skipped) - skipped covers both
    unresolved names and anything that didn't fit the sub-budget.
    """
```

### 3.5 `_enforce_render_budget` interaction

`_enforce_render_budget` (`src/prism/surface/build.py`) operates on a
single, already-rendered `ContextPackage` with no concept of a
partition - it trims/downgrades globally against `target_budget`. Two
options, and a recommendation:

- **(A) Keep partitions pre-enforced, let `_enforce_render_budget` run
  unmodified over the combined package.** Since 3.4's own external
  packer already stops at `external_budget_tokens` before rendering, and
  the internal packer already stops at `internal_budget` (3.2), the
  combined package should already be at or under `budget_tokens` before
  `_enforce_render_budget` ever runs - it becomes a safety-net pass
  (catching real render-time overshoot, e.g. metadata cost the packer
  under-priced) rather than the primary enforcement mechanism, exactly
  as it already is for the internal-only path today (`build_context_
  package_requested`'s own docstring: "an over-requesting caller still
  gets capped/trimmed... the budget axis stays comparable"). **Recommended** -
  no changes needed to `_enforce_render_budget` itself.
- **(B) Make `_enforce_render_budget` partition-aware** (skip trimming
  external nodes until internal nodes are already at
  `_PROTECTED_DOWNGRADE_ROLES`-equivalent floor, mirroring that existing
  protection for internal seed/direct-neighbor nodes). Only needed if
  (A)'s safety-net assumption turns out false in practice - i.e. if real
  overshoot is common enough that which partition absorbs the trim
  actually matters. Deferred: implement (A) first, measure real overshoot
  frequency on a rerun of t018/t019/t020 before deciding (B) is needed.

## 4. Scoring Contract Updates

### 4.1 The leaner path (recommended): no `score_debug_causal` signature change at all

`benchmarks.engines.base.selected_symbols(pkg) -> {node.id for node in
pkg.nodes}` is already generic over every node in a rendered package,
regardless of role - confirmed directly in the real code, not assumed.
`benchmarks/runner.py`'s existing scoring call site already computes
`candidate_symbols = selected_symbols(pkg)` and threads it into
`score_tsr_response` -> `score_debug_causal(response_text, pipeline,
candidate_symbols)` for every debug-type task today, with zero Phase-C-
specific code.

**Consequence**: once Section 2.3's external `NodeEntry` objects
(`role="external"`) are appended to `pkg.nodes` by Section 1.4's
`retrieve_external_requested`, `candidate_symbols` **automatically
includes every resolved external symbol** for that cell, with **no
change needed to `score_debug_causal`'s own signature, its illegitimacy
check, or its call site**. A model naming `add_route` or `orjson.dumps`
in its `symbols` array, when that name was genuinely resolved and
rendered as an external node this turn, is no longer flagged
illegitimate - `s not in pipeline_set and s not in candidates_n`
evaluates `s in candidates_n` as `True` because `candidates_n` now
contains it, exactly the same mechanism that already legitimizes a
`boundary_symbols`/`required_context` entry today.

This is the leaner design specifically because it reuses an existing,
already-correct generic mechanism rather than adding a parallel
parameter that would need its own plumbing through
`compute_gate_metrics`-adjacent call sites. **The explicit
`external_candidate_universe` parameter requested in the original
brief remains a considered alternative** (Section 4.2) for the case
where a caller needs the external universe available *without* a real
retrieval having happened yet (e.g. a dry-run diagnostic) - not needed
for the scoring path itself.

### 4.2 Considered alternative: explicit parameter threading

```python
def score_debug_causal(
    response_text: str, pipeline: list[str], candidate_symbols: set[str],
    external_candidate_universe: set[str] = frozenset(),  # new, optional, additive default
) -> float:
    ...
    candidates_n = {_normalize(s) for s in candidate_symbols} | {_normalize(s) for s in external_candidate_universe}
```

A strictly backward-compatible signature change (new parameter has a
default, no existing caller breaks) if ever needed - e.g. if a future
caller wants to score against a *hypothetical* external universe
without having actually run `retrieve_external_requested`. Not
recommended as the primary mechanism given 4.1's zero-change path
already covers the real scoring need.

### 4.3 Ground-truth schema: no new required field, one optional flag

`EvaluationTask`/`GroundTruthAnnotation` (`benchmarks/ground_truth/
schema.py`) need no new field to represent "this task's correct answer
may legitimately reference an external symbol" - `required_context`
already exists for exactly this kind of background-reference
annotation, and a real external symbol name is a valid string in that
set today (schema-wise; scoring-wise it was never checked against
before, hence Section 4.1). One new, optional field is worth adding for
harness-level routing rather than correctness:

```python
class EvaluationTask(BaseModel):
    ...
    allows_external_dependencies: bool = False  # new, defaults to
        # False for every existing task file (t018/019/020 alone would
        # set this True) - lets benchmarks.runner decide whether to run
        # Section 1's conditional-third-pass path at all for a given
        # task, rather than paying Turn 2a's own manifest-build cost on
        # every cell regardless of whether the task could ever need it.
```

## 5. Breaking interfaces - explicit inventory

**As built (`1f7af68`): the one row this table originally called
unavoidable turned out not to be** - `RequestSymbolsCallback`'s
signature was widened, not replaced, per this task's own explicit
backward-compatibility instruction (Section 1.2). No interface below is
breaking as actually shipped.

| Interface | Change | Breaking? | Migration |
|---|---|---|---|
| `prism.engine.RequestSymbolsCallback` | `Callable[[str,str], list[str]]` -> `Callable[[str,str], list[str] \| tuple[list[str], bool]]` | **No** (as built - revised from the original draft's breaking change) | None required. `_normalize_request_result` treats a plain `list[str]` as `needs_external_deps=False` - every existing caller (`tests/test_two_pass_engine.py`, `benchmarks/run_two_pass_benchmark.py`'s own Turn-1 closure) keeps working unmodified. |
| `prism.engine.RequestExternalSymbolsCallback` | New type alias | No (additive) | New callers only - not present in the original draft, which had reused `RequestSymbolsCallback` itself for Turn 2b (a real inconsistency with Section 1.3's own stated contract, fixed here). |
| `PrismEngine.retrieve_two_pass` | Unchanged signature/behavior | No | Kept as-is - a caller that never opts into Phase C sees no change at all. |
| `PrismEngine.retrieve_two_or_three_pass` | New method | No (additive) | New callers only. |
| `PrismEngine.build_external_candidate_manifest` | New method, `(turn1_symbols, root_imports)` (Section 1.4's "as built" note - not `(seed_id, external_index)` as originally drafted) | No (additive) | New callers only. |
| `prism.external.index.extract_external_symbol_all` | New function | No (additive) | New callers only - added during Step 3 for the genuinely-ambiguous bare-name case (two real Starlette `add_route` definitions); `extract_external_symbol` (single result) is unchanged and still used for the precise package-receiver resolution path. |
| `retrieve_two_pass`'s own `diagnostics` dict | Gains `needs_external_deps`, `external_*` keys when the new `retrieve_two_or_three_pass` is used | No (additive keys on a new method's own return, not the old method's) | N/A |
| `NodeEntry.role` (`prism.surface.models`) | `Literal[...]` gains `"external"` member | **Yes, in the type-checking sense** (a `match`/exhaustiveness check over the old 4-member Literal needs a 5th arm) | Grep every `role ==`/`match role` site before merging (one found already: `prism/surface/renderer.py:327`'s `role == "seed"` filter - unaffected since it's an equality check, not an exhaustive match, but every such site needs auditing, not assuming). |
| `ContextPackage.schema_version` | New value `3` | No (the existing mechanism is designed for exactly this) | `schema_version=1`/`2` consumers unaffected per `renderer.py`'s own documented forward-compatibility guarantee. |
| `SubmodularPackedItem` | New `origin: str = "internal"` field | No (defaulted, every existing construction site uses kwargs) | None required. |
| `score_debug_causal` | **No change** under the recommended path (4.1) | No | N/A |
| `EvaluationTask` | New `allows_external_dependencies: bool = False` field | No (defaulted) | None required for existing task YAMLs - pydantic default applies on load. |
| Checkpoint cell schema (`benchmarks/runner.py`, `run_two_pass_benchmark.py`) | New optional cell fields (`needs_external_deps`, `external_requested_count`, `external_skipped_hallucinated`) | No | `scripts/merge_pilot_checkpoints.py`/`scripts/apply_gate.py` only ever read `task_id`/`engine`/`budget`/`seed`/`tsr`/`cpi_answer` - confirmed by re-reading both scripts - unaffected by additive cell fields. |

**As built, every interface in this design is additive - nothing
breaks.** The original draft treated `RequestSymbolsCallback`'s
signature as the one unavoidable breaking change; Section 1.2's own
"as built" note explains why that turned out not to be necessary
(backward-compatible normalization instead of a hard replacement).
Section 4.1's discovery (reuse `pkg.nodes`/`selected_symbols` rather
than a parallel parameter) independently removes what would otherwise
have been the second-largest breaking change in this spec, and was
verified empirically in Step 3, not just argued for.

## 6. Rollout / validation plan

Mirroring the empirical discipline Phase B held itself to throughout -
verify against real data before trusting a design, never assume:

1. Build `ExternalSymbolIndex` against exactly three real packages
   (Starlette's `routing.py` signatures for `add_route`, `orjson`'s
   `.pyi` stub, `ujson`'s `.pyi` stub) - the minimum needed to re-test
   `t018`/`t019`/`t020` specifically, before generalizing to any other
   package.
2. Real dry-run (`--dry-run`, zero LLM calls) of
   `retrieve_two_or_three_pass` against all three tasks, confirming
   `build_external_candidate_manifest` resolves `add_route`/
   `orjson.dumps`/`ujson.dumps` as real, one-hop-reachable external
   candidates - the same kind of static verification every ground-truth
   symbol in this evaluation was held to before being trusted.
3. A small, targeted LLM rerun (mirroring the 36-cell/48-cell targeted
   reruns already used twice in Phase B) on exactly these 3 tasks x 4
   engines x 3 budgets, checking:
   - `t019`/`t020` (already fully fixed by the prompt-only change) do
     not regress - the conditional branch should rarely even fire for
     them post-fix, but if it does, results should be unchanged.
   - `t018` - currently a hard, unconditional 0/12 floor - is the one
     real test of this entire design. A result other than a material
     improvement here means the design has a real gap, not that the
     rerun was unlucky - the same "the data gets the final word"
     standard every prior round in this evaluation held to.
4. Only after step 3 succeeds on the 3 targeted tasks: consider whether
   `allows_external_dependencies=True` should be set on any other task
   in the 25-task suite, or left at its default `False` everywhere else
   pending a real, separate cost/benefit case for each one.

## 7. Decisions record (resolved) and remaining open items

Resolved, with real codebase verification behind each - not accepted at
face value:

1. **Stub extraction mechanism**: resolved as Section 2.1's revised
   design (reuse tree-sitter + `ContractExtractor`, no stdlib `ast`, no
   bespoke per-language parser). This also resolves the original
   question about Starlette specifically - its real `.py` source goes
   through the exact same tree-sitter parse + `ContractExtractor.
   extract_symbol` path any other language's real source would, so
   "can a stub-only walk reliably distinguish signature from body" is
   answered by "it doesn't need to - `ContractExtractor` already parses
   real source into structured params/return_type/docstring fields
   without ever needing the body slice for those fields," not by a new,
   separate distinguishing pass.
2. **`external_index` lifecycle / caching**: resolved as an immutable
   disk cache under `.prism/cache/external_deps/`, keyed by
   `(package_name, package_version, schema_version)`, mirroring
   `prism.runtime.contract_cache`'s own established load/save-by-
   signature pattern. Refinement over the original proposal: the cache
   key should also fold in a grammar/engine-version component
   (`prism.traversal._cache_keys.engine_and_grammar_version`, the same
   value `contract_cache.py` already includes in its own signature) so a
   tree-sitter grammar upgrade invalidates stale external entries the
   same way it already invalidates stale in-repo contract entries -
   not just package version.
3. **Namespace resolution scope**: resolved - bounded to top-level
   packages declared in the repository's own root dependency manifest
   (`pyproject.toml`/`requirements.txt` for Python; the equivalent per
   ecosystem once TS/Go locators exist). This bounds *which packages*
   get an `ExternalSymbolIndex` built at all; Section 2.2's one-hop
   `resolve_direct_callee` still bounds *which specific symbols* enter
   any one query's candidate universe. The two are complementary, not
   overlapping controls.
4. **Multi-package resolution**: still open, deliberately deferred -
   `build_external_candidate_manifest` as drafted takes one
   `ExternalSymbolIndex`; a seed with real one-hop external references
   into more than one third-party package needs either a composed/union
   index or a list of indices. Not needed for the 3-task Step 1
   validation (Section 6) - each of `t018`/`t019`/`t020` resolves into
   exactly one external package - so left open rather than
   speculatively designed now.
5. **Section 3.2's budget fraction/floor/ceiling**: refined to 12.5%
   default, a 256-token floor, and a 1024-token ceiling. Checked against
   this evaluation's own established 2000/4000/8000 budget grid: the
   floor only binds at budget=2000 (12.5%=250<256), the ceiling never
   binds at any of the three. A sensible clamp, but - same caution this
   evaluation already applied to the headroom-aware gate's own constants
   (Phase B's closure debrief, Section 7: "the specific constants...
   were sized to this dataset rather than derived beforehand") - still
   calibrated to exactly those three numbers, not yet measured against
   real external-stub token cost. Section 6's rollout plan should
   measure this on the 3 target tasks before the clamp is treated as
   settled for any budget outside 2000-8000.

**A named limitation carried forward from Section 2.1, not resolved
because it cannot be by Phase C's own scope**: Rust is unreachable by
this design (or any stub-extractor design) until Rust gains a
tree-sitter grammar in Prism's own core language support - separate,
larger work, tracked here as a real gap rather than implied away by the
`ExternalSourceLocator` interface's own apparent genericity.
