# Context Cleanliness, Not Recall: An Empirical Study of Two-Pass Retrieval for Agentic Code Generation Across Four Multi-Paradigm Corpora

**Status: draft manuscript, ready for author review and venue formatting.**
Synthesized directly from this project's own primary evaluation
artifacts - no figure in this document is projected, estimated, or
extrapolated beyond what those artifacts already report. Source
documents: `reports/trpc_benchmark_debrief.md`,
`reports/express_pilot_audit_gap_closure.md`,
`reports/fastapi_seed42_closure_debrief.md`,
`docs/roadmap_public_release.md`, `docs/architecture_boundaries.md`, and
the metric implementations they cite (`benchmarks/metrics/cpi.py`,
`benchmarks/metrics/fpr.py`, `benchmarks/tsr/scorer_debug.py`,
`scripts/apply_gate.py`). Prepared for ACM/IEEE-style submission
formatting; section numbering and structure follow standard empirical
software engineering conventions (problem formulation, theoretical
framework, system description, results, threats to validity,
conclusion).

---

## Abstract

Agentic code-generation systems that retrieve source context before
answering face a structural tension: a wide, undirected context
expansion (breadth-first neighborhood search) almost always makes the
correct answer *reachable*, but does so by diluting it inside a large
volume of structurally-adjacent but task-irrelevant code - a retrieval
strategy that saturates recall while starving the downstream model's own
reasoning capacity of a legible, uncluttered signal. We report a
controlled, four-corpus empirical study (FastAPI, Django, Express, and
tRPC - two Python paradigms and two TypeScript/JavaScript paradigms,
spanning decorator-driven dependency injection, class-based MVC/ORM,
dynamic CommonJS module resolution, and generics-heavy monorepo builder
chains) comparing a naive breadth-first baseline, a two-pass
manifest-then-hydrate retrieval architecture (`PRISM Two-Pass`), and a
zero-annotation-cost oracle substitute (`PragmaticOracle`) against a
pre-registered, headroom-aware statistical gate. Across every corpus
tested, the naive baseline saturates context-recall metrics (94.7-100%
`CPI_answer`) while its real task-success rate collapses to 53-58%;
Two-Pass's own curated retrieval - measured via a novel `Cleanliness`
metric (one minus the false-positive rate against ground truth) - trades
a small, sometimes statistically confirmed recall cost for a
consistent, large task-success gain (`{Delta}TSR` +33.87 to +43.00
percentage points, 95% CI excluding zero in every corpus). We formalize
this pattern as the **Haystack Trade-off** and report a second,
independently reproducing phenomenon - the **Oracle Inversion
Phenomenon** - in which a theoretical retrieval ceiling scores at or
below a naive baseline on task success, diagnosing two independent,
disclosed failure modes: an oracle construction gap (omitted external
dependencies) and a genuine, retrieval-independent limit on model
reasoning. We further report a class of TypeScript-specific
graph-construction defects (in-module symbol collisions and
object-literal factory-method scoping) invisible to Python corpora
entirely, motivating a cross-paradigm reformulation of static-analysis
retrieval graphs. All results are drawn from real, paired, bootstrapped
statistical comparisons (10,000-resample percentile bootstrap, n = 125
to 900 paired cells per comparison) with full cost, latency, and
turn-count accounting.

---

## 1. Introduction, Problem Formulation, and Research Questions

### 1.1 The context selection tension

Let a codebase be represented as a directed graph `G = (V, E)` over
symbol definitions, with a call-relation subgraph
`G_calls subset= G` connecting a symbol to every other symbol it
transitively invokes. For a given natural-language query `q` anchored
at a seed symbol `s in V`, a retrieval engine `R` must select a bounded
context package `S_M subset= V` (a "packed set", bounded by a token
budget `B`) such that a downstream generative model `M`, conditioned on
`S_M`, can correctly answer `q`.

Two failure modes bound this problem from opposite directions:

- **Neighborhood saturation** (the "wide net"): `R` selects `S_M` via an
  undirected or lightly-weighted breadth-first expansion from `s`,
  bounded only by `B`. As `|S_M|` grows, the probability that the true
  answer set `G* subset= S_M` approaches 1 - but so does the ratio of
  structurally-adjacent, task-*irrelevant* material inside `S_M`,
  degrading the signal-to-noise ratio the downstream model must reason
  over.
- **Context starvation**: `R` selects a minimal, aggressively pruned
  `S_M`, maximizing signal-to-noise but risking `G* not subset= S_M` -
  a real answer symbol genuinely absent from context, an unrecoverable
  failure regardless of the downstream model's own reasoning quality.

This paper reports on `PRISM Two-Pass`, a retrieval architecture that
resolves this tension via an explicit two-turn protocol (Turn 1: a
zero-body candidate manifest, scored by an LLM into a `requested_symbols`
selection; Turn 2: full hydration of exactly that selection, and only
that selection, into the final answer context) rather than a single
undirected expansion, and reports the first controlled, cross-paradigm
empirical measurement of where each strategy actually lands on the
recall/cleanliness trade-off this formulation predicts.

### 1.2 Research questions

**RQ1 (Downstream code-generation impact of prompt cleanliness).** Does
context *cleanliness* - the fraction of a packed context that is
genuinely relevant to the task's own real causal pipeline, as opposed to
context *recall* (whether the correct answer is merely present
somewhere in the package) - predict real, LLM-generated task success
independent of recall? We test this by comparing `Cleanliness` and
`CPI_answer` as independent predictors of `TSR` across four corpora,
three engines, and (for the Python corpora) two model families.

**RQ2 (Headroom-aware retrieval recall).** Does a flat, corpus-agnostic
statistical threshold on `{Delta}CPI_answer` generalize across corpora
whose baseline engines sit at materially different points on the
`CPI_answer` ceiling, or does a fixed absolute bar systematically
misclassify a real, reproducible retrieval-quality effect as a gate
failure once a baseline is already near-saturated? We test this via a
before/after comparison of a flat versus headroom-adjusted decision rule
applied to the same real, raw checkpoint data.

**RQ3 (Ambient type scaffolding in strictly-typed languages).** Does a
statically-typed, generics-heavy language ecosystem (TypeScript) surface
graph-construction failure modes in a static-analysis retrieval engine
that a comparably mature dynamically-typed ecosystem (Python) does not -
specifically, does the *ambient type scaffolding* convention (factory
functions returning object literals of typed methods; module-scoped
helper functions colliding on bare names inside closures) require
retrieval-graph construction rules a Python-only design would never
need? We test this via two concrete graph-construction defects found and
fixed during the tRPC evaluation and absent from every prior Python
corpus in this portfolio.

---

## 2. Theoretical and Mathematical Framework

All metrics below are computed exactly as implemented in this project's
own evaluation harness (`benchmarks/metrics/`, `benchmarks/tsr/`,
`scripts/apply_gate.py`), not re-derived or approximated for this
manuscript.

### 2.1 Context Precision Index (`CPI`)

Let `G*_pipeline = [g_1, ..., g_n]` be a task's ordered, human-annotated
ground-truth causal pipeline (`benchmarks.ground_truth.schema.
GroundTruthAnnotation.pipeline_symbols`). Two base recall functions are
defined over a candidate set `S`:

```
CPI_strict(S, G*)     = 1  if G* subset= S, else 0            (1)
CPI_fractional(S, G*) = |S intersect G*| / |G*|                (2)
```

(both vacuously 1.0 for an empty pipeline). The reported `CPI_answer`
metric is **engine-architecture-dependent**, not a single fixed formula
- a real, disclosed asymmetry this manuscript preserves rather than
flattens:

```
CPI_answer :=  CPI_strict(S_retrieved, G*_pipeline)   for a single-pass engine   (3a)
CPI_answer :=  CPI_fractional(A_final, G*_pipeline)    for the two-pass engine    (3b)
```

where `S_retrieved` is the single-pass engine's own retrieved/packed
node set (there is no separate "requested" stage) and `A_final` is the
symbol set the generative model's own *final answer* names (parsed by
`extract_flat_symbols`), not the two-pass engine's intermediate
hydration set. Equation 3b's own two-pass-specific companion,
`CPI_retrieval := CPI_fractional(R_turn1, G*_pipeline)`, measures Turn
1's manifest-selection layer in isolation and can diverge from Equation
3b when the model correctly infers a pipeline stage from an
already-hydrated caller's own source text without having explicitly
requested it in Turn 1 (a documented, captured case in
`benchmarks/metrics/cpi.py`'s own "Track 3" note).

### 2.2 Prompt Cleanliness

Let `G*_universe = G*_pipeline union C union RC union B` be a task's
full annotated ground-truth universe (pipeline symbols, critical
callers, required context, and boundary symbols combined). The
**false-positive rate against ground truth** is:

```
FPR_gt(S, G*_universe) = |S \ G*_universe| / |S|,   FPR_gt = 0 for S = {empty set}   (4)
```

and this paper's own **Cleanliness** metric is its complement:

```
Cleanliness(S) = 1 - FPR_gt(S, G*_universe)                    (5)
```

Cleanliness is the fraction of a packed context that is genuinely
relevant to the task's own annotated ground truth; a naive wide-net
retriever can achieve `CPI_answer = 1` (Equation 3a) while
`Cleanliness -> 0` as `|S|` grows without bound - the formal statement
of the neighborhood-saturation failure mode (Section 1.1).

### 2.3 Task Success Rate (`TSR`)

Let `A = extract_flat_symbols(response)` be the ordered list of symbols
a model's raw response names, and let `Cands` be the retrieval engine's
own real candidate universe for this query (its retrieved/hydrated node
set - the set a "legitimate but not literally in the ground truth"
symbol must belong to). The causal-sequence scorer used for every
reported `TSR` figure in this paper (`score_debug_causal`) is:

```
                 { 0                                    if exists a in A: a not in (G*_pipeline union Cands)
TSR(A, G*) :=    {                                                                                          (6)
                 { LCS(G*_pipeline, A) / |G*_pipeline|   otherwise
```

where `LCS` is the length of the longest common (not necessarily
contiguous) ordered subsequence between the ground-truth pipeline and
the model's own named-symbol sequence, computed by standard `O(nm)`
dynamic programming. `TSR` is a strict, hallucination-gated
**generation-quality** metric: a single symbol named that belongs to
neither the real ground truth nor the engine's own legitimate candidate
set zeroes the entire score, regardless of how much of the real pipeline
the response otherwise recovers correctly and in order - "more thorough
than expected" is credited via the ordered-subsequence partial-credit
term, "makes something up" is not, and the two are never conflated into
one fuzzy number.

### 2.4 The headroom-aware gate threshold (`tau_headroom`)

Let `bar{CPI}_baseline` be the baseline engine's own mean `CPI_answer`
across all paired cells in a comparison, and let
`headroom = 1 - bar{CPI}_baseline` be the total remaining distance to
the `CPI_answer` ceiling. The gate's own decision threshold on
`{Delta}CPI_answer` (percentage points) is:

```
                { tau_flat                              if headroom >= tau_cutoff
tau_headroom := {                                                                  (7)
                { f_closure * headroom * 100             if headroom <  tau_cutoff
```

with defaults `tau_flat = 15` (pp), `tau_cutoff = 0.20`, and
`f_closure = 0.35` (35% closure of whatever headroom remains). A
comparison is gated **PASS** only when both
`{Delta}TSR >= 15` pp with a 95% bootstrap CI (10,000 resamples)
strictly excluding zero, *and*
`{Delta}CPI_answer >= tau_headroom` with its own CI strictly excluding
zero (positive side); **STOP** when neither axis clears its bar with a
confirmed-positive CI; **MIXED** when exactly one axis clears while the
other does not (a genuine five-outcome partition of every possible
`({Delta}TSR, {Delta}CPI_answer, CI)` combination, not a two-outcome
PASS/STOP binary with an ad hoc catch-all).

### 2.5 The Haystack Trade-off (theoretical framing)

We define the **Haystack Trade-off** as the empirically observed inverse
relationship, within a single retrieval engine's own design space,
between `CPI_answer` (Equation 3) and `Cleanliness` (Equation 5) as a
function of the engine's own selection breadth: a wide, undirected
expansion strategy drives `CPI_answer -> 1` (the needle is *in* the
haystack) at the direct cost of `Cleanliness -> 0` (the haystack is
large), and - critically - `TSR` (Equation 6) tracks `Cleanliness` far
more tightly than it tracks `CPI_answer` across every corpus in this
study (Section 4), because a generative model's own capacity to extract
the *correct, ordered* answer from a context package is bounded by that
package's own signal-to-noise ratio, not merely by whether the answer is
technically retrievable from it.

### 2.6 The Oracle Inversion Phenomenon (theoretical framing)

We define the **Oracle Inversion Phenomenon** as the empirical
observation that a nominal retrieval *ceiling* - an engine constructed
to select exactly, and only, a task's own annotated ground-truth
universe (`Cleanliness = 1` by construction) - can score at or *below* a
naive baseline on `TSR`, inverting the expected ceiling/floor ordering.
We report two independently confirmed, disclosed root causes for this
inversion, each diagnostic of a distinct failure mode invisible to
retrieval-quality metrics alone:

1. **Construction-gap inversion** (Section 4.3, Express): the oracle
   engine's own implementation systematically omits a real class of
   ground-truth symbols (externally-resolved dependencies) from its
   candidate set, so a model that correctly names the true external
   symbol anyway is *penalized* by the hallucination gate (Equation 6)
   for citing a symbol outside the oracle's own (incomplete) candidate
   universe - a bug in the reference implementation, not a reflection of
   task difficulty.
2. **Reasoning-limited inversion** (Section 4.4, tRPC): once the
   construction gap above is fixed, an oracle handed a perfectly clean,
   ground-truth-exact context *still* fails to reach `TSR = 1.0`
   (0.760 on tRPC) - a residual gap attributable entirely to the
   generative model's own causal-ordering and completeness reasoning,
   wholly independent of retrieval quality, since retrieval quality is
   by construction already optimal in this condition.

Oracle Inversion is therefore not a single phenomenon but a diagnostic
*signal*: its presence indicates that at least one of (a) the oracle's
own construction, or (b) the downstream model's own reasoning capacity,
is the binding constraint - information a retrieval-only metric such as
`CPI_answer` cannot surface on its own.

---

## 3. Multi-Paradigm System Architecture and Implementation

`PRISM`'s static-analysis retrieval graph (`G_C`, `G` in Section 1.1) is
built once per repository by a tree-sitter-based two-pass linker
(Pass 1: global definition collection; Pass 2: scoped call-edge
resolution) and is deliberately reused, unmodified in its core
traversal/scoring machinery, across every language paradigm in this
study - the paradigm-specific work reported here is entirely in Pass 1's
own *definition registration* rules, not in the downstream
retrieval/ranking logic.

### 3.1 Python: decorator-driven DI and class-based MVC/ORM (FastAPI, Django)

FastAPI's own architecture (decorator-registered route handlers,
constructor-injected dependencies) and Django's (class-based views,
ORM-mediated model methods) share Python's own AST shape closely enough
that Pass 1's ordinary `class_definition`/`function_definition` walk,
plus real constructor-based instance-type binding
(`InstanceTypeMap`), resolves both architectures' own call graphs
without paradigm-specific extension. The one real, cross-paradigm defect
this portfolio found and fixed in the Python corpora (`G41`, per
`docs/design_formalism.md`) is a receiver-type resolution gap: a
`self.<attribute>.<method>()` call where `<method>` exists on more than
one class in the codebase can resolve, via bare-simple-name matching, to
the *wrong* class's definition - a real, reachable edge on the graph,
but not the one the source's own declared/assigned receiver type
actually invokes (two confirmed instances in Django:
`self.nodelist.render(context)` resolving to `Template.render` instead
of `NodeList.render`, and `self.filter_expression.resolve(context)`
resolving to `Variable.resolve` instead of `FilterExpression.resolve`).

### 3.2 JavaScript/TypeScript: dynamic CommonJS export binding and Node subpath resolution (Express)

Express's own single-package, CommonJS-and-ESM-mixed source required
extending Pass 1's import/export handling with a **generic**,
package-name-unaware `require()` binding walk
(`ConcreteGraphBuilder._parse_js_requires`) covering all five real Node
binding shapes (side-effect-only, default/namespace, destructuring,
aliased destructuring, and property-access-on-call), funneled into the
same `LocalImportMap` ES `import` resolution already used elsewhere -
plus a **Node.js Package Exports**-conformant subpath resolver
(`TypeScriptSourceLocator.locate(package_name, package_version,
subpath)`: exact-key match, then single-`*` pattern match via
longest-prefix tie-break, then filesystem fallback) for real npm subpath
exports (`@trpc/server/adapters/express`-shaped imports), verified
generic against both Express's own dependencies and a cross-repo case
(`@trpc/server`) before being trusted as non-Express-specific. A
disclosed non-goal: a bare call directly on a required value
(`x(...)`) needs the module's own real primary export, and a local
call-site alias is not reliably that export's own name (confirmed live
against real `path-to-regexp`, locally aliased `pathRegexp` but
internally named `pathToRegexp`) - resolved at the one real consumer via
a `module.exports = <identifier>` redirect, not by corrupting the
shared binding map.

### 3.3 TypeScript: generic builder chains, factory object-literal method scoping, and in-module symbol collision suffixing (tRPC)

tRPC's own architecture - deeply chained, heavily generic procedure
builders (`t.procedure.input(x).use(y).query(z)`) implemented as
factory functions returning object literals of methods, plus a
pnpm-workspace monorepo layout - surfaced two real graph-construction
defects invisible to every prior corpus in this portfolio (directly
answering RQ3):

**Object-literal factory-method scoping.** `_register_definition`'s
ancestor walk previously recognized only an enclosing *class* as a real
qualified-name scope boundary. A factory function returning
`{ method() {...}, ... }` (tRPC's own `createRouterFactory`'s returned
router object, and `createBuilder`'s own `.input()`/`.output()`/`.use()`
/`.query()` methods) is neither a class nor caught by that walk, so each
such method registered as a flat, bare top-level symbol
(`core.router.createCaller`) indistinguishable from a genuine top-level
export - and, because `iter_scoped_nodes` correctly treats any
separately-registered `method_definition` as its own scope boundary, the
method's own real outgoing calls never appeared in *its own* Turn-1
candidate manifest at all. Fixed by extending the ancestor walk to
recognize the function that *owns/returns* the object literal as the
real scope, when a name for that owning function is derivable (its own
`name:` field, or the variable it is assigned to) - `core.router.
createRouterInner.createCaller` instead of the flat, colliding-prone
`core.router.createCaller` - with an explicit, deliberately narrow
fallback (flat registration, unchanged) for the one case no real
evidence resolves (a fully anonymous IIFE owner).

**In-module symbol collision suffixing ("Option C").** The same ancestor
walk's blind spot for nested scopes produces a second, distinct defect:
a plain function declared inside another function's body (a closure,
never a class or object-literal method) *also* registers under the same
flat `module.name` shape a genuine top-level function would get - so two
same-named, unrelated definitions in one module (a router's own real
public `param(name, fn)` registration API, and an unrelated private
`param(err)` closure nested inside a different function) silently
collide on one dictionary key, with the later-registered definition
winning and the earlier one's own graph node becoming permanently
unreachable under its real name. Fixed by detecting a genuine collision
(same qualified name, different file/line-range) and assigning the
colliding definition a deterministic `#N` suffix rather than
overwriting, with one further, empirically necessitated refinement: a
`typing`-analogue `@overload`-decorated stub (`TSX`/TS's own multiple
signature-only declarations immediately followed by the real,
fully-bodied implementation - a common, intentional pattern, not a bug)
must have its real implementation *promote* over the stub into the
canonical, unsuffixed slot, since plain first-registered-wins would
otherwise make the first `@overload` stub - never itself callable - the
permanent answer for names like `Jinja2Templates.__init__`, a regression
confirmed live against real Starlette/Jinja2 external-symbol resolution
before this refinement was added.

Neither defect is Python-relevant: Python's own class-method binding
already threads through the pre-existing `class_types` ancestor check,
and Python has no object-literal-method or `@overload`-stub-then-impl
idiom in the same structural shape. Both defects were found via, and
validated against, real, independently-authored ground-truth tasks in
the tRPC suite itself (`trpc_t02_008/009/010/019` exercise the
object-literal fix directly; `trpc_t02_017` tests the same simple name,
"getErrorShape", correctly disambiguated across two real, separate
definitions in different modules), not merely a synthetic reproduction.

---

## 4. Consolidated Empirical Results

### 4.1 Unified cross-corpus master table

| Corpus | Model(s) / Runner | n (paired cells) | Engine | TSR | CPI_answer | `{Delta}TSR` vs. baseline | `{Delta}CPI_answer` vs. baseline | Gate decision |
|---|---|---|---|---|---|---|---|---|
| **FastAPI** | `qwen2.5-coder:7b-instruct-q8_0`, local Ollama (Kaggle GPU T4x2) | 225/engine (3-seed aggregate, 25-task suite) | baseline_bfs_bidirectional | 0.560 | 0.947 | - | - | - |
| | | | oracle | 0.742 | 0.920 | - | - | - |
| | | | prism_v11 (single-pass) | 0.502 | 0.920 | - | - | - |
| | | | **prism_two_pass** | **0.905** | **0.976** | **+34.47pp** CI [28.50, 40.70] | **+2.93pp** CI [0.09, 6.10] | **MIXED (flat) / PASS (headroom-aware)** |
| **Django** | `qwen` (pilot-4-patched, 1800 cells) + `DeepSeek` (1200 cells), real API/local, 5-seed + 2-seed holdout | 1800 (qwen), 1200 (DeepSeek) | prism_two_pass vs. baseline (qwen) | - | - | +7.67pp, CI excl. 0 | +12.33pp, CI excl. 0 | **EXPAND** |
| | | | prism_two_pass vs. prism_v11 (qwen) | - | - | +0.89pp, CI crosses 0 | +2.33pp, CI crosses 0 | **STOP** |
| | | | prism_two_pass vs. baseline (DeepSeek) | - | - | +7.86pp, CI excl. 0 | +4.72pp, CI excl. 0 | **EXPAND** |
| | | | prism_two_pass vs. prism_v11 (DeepSeek) | - | - | +4.72pp, CI excl. 0 | **-5.28pp** (confirmed negative) | **STOP** |
| **Express** | `gpt-4o-mini`, real OpenAI API (DeepSeek endpoint network-blocked) | 200 (2 budgets x 5 seeds) | baseline_bfs_bidirectional | 53.0% | 97.5% | - | - | - |
| | | | PragmaticOracle | 51.7% | 100.0% | - | - | - |
| | | | **prism_two_pass** | **96.0%** | 96.0% | **+43.00pp** CI [36.08, 49.71] | -1.54pp CI [-3.50, 0.62] (inconclusive) | **MIXED** |
| **tRPC** | `gpt-4o-mini`, real OpenAI API (same substitution) | 125/engine (1 budget x 5 seeds x 3 engines, 25-task suite) | baseline_bfs_bidirectional | 0.584 | 1.000 | - | - | - |
| | | | oracle (PragmaticOracle) | 0.760 | 1.000 | - | - | - |
| | | | **prism_two_pass** | **0.923** | 0.959 | **+33.87pp** CI [25.20, 42.67] | **-4.07pp** CI [-6.13, -2.33] (confirmed negative) | **MIXED** |

Django's per-engine raw `TSR`/`CPI_answer` means are not part of the
verified, re-derived record this manuscript draws from
(`reports/fastapi_seed42_closure_debrief.md` Section 7 reports only the
paired deltas and gate decisions for Django, having been re-run
specifically to correct an earlier, inaccurate "gate PASS" claim about
Django) - reported here as deltas only, not backfilled or estimated.
FastAPI and Django ran on **self-hosted, zero-marginal-cost open-weights
models** (local Ollama on a Kaggle GPU runtime); Express and tRPC ran
directly against a **commercial frontier API** with real per-token cost
(Section 4.5) - a genuinely different economic and infrastructure
profile across the portfolio, not merely a different corpus (Section
5.3).

### 4.2 Cleanliness as the mediating variable (RQ1)

| Corpus | Baseline Cleanliness | Two-Pass Cleanliness | Baseline TSR | Two-Pass TSR | Baseline CPI_answer |
|---|---|---|---|---|---|
| FastAPI | - | - | 56.0% | 90.5% | 94.7% (near ceiling) |
| Express | 42.0% | 70.6% | 53.0% | 96.0% | 97.5% (near ceiling) |
| tRPC | 36.2% | 76.7% | 58.4% | 92.3% | 100% (ceiling) |

In every corpus for which Cleanliness was directly measured, the naive
baseline achieves near-perfect or perfect `CPI_answer` while `TSR`
collapses to roughly half of Two-Pass's own figure - the correct answer
is essentially always *present*, but only 36-42% of the surrounding
context is genuinely relevant, and the model fails to reliably extract
the right symbols in the right causal order from that noise. Two-Pass's
own curated, Turn-1-manifest-selected context (70.6-76.7% clean, roughly
double the baseline's signal-to-noise ratio) is the direct, sufficient
explanation for the large, consistent `{Delta}TSR` effect reported in
Section 4.1 - this is the Haystack Trade-off (Section 2.5), reproduced
identically across three architecturally unrelated corpora.

### 4.3 The 3-engine matrix: Express (Oracle Inversion, construction-gap case)

| Engine | Mean TSR (causal) | Mean CPI_answer | Cleanliness |
|---|---|---|---|
| baseline_bfs_bidirectional | 53.0% | 97.5% | 42.0% |
| **prism_two_pass** | **96.0%** | 96.0% | 70.6% |
| PragmaticOracle | 51.7% | 100.0% | 100.0% |

PragmaticOracle - by construction the cleanest possible context - scores
*below* the naive baseline on `TSR`, the first confirmed instance of
Oracle Inversion in this portfolio. Root-caused by splitting Oracle's own
score by task category:

| PragmaticOracle subset | Mean TSR (causal) |
|---|---|
| Internal tasks (n = 100) | 93.8% |
| External-dependency tasks (n = 100) | **9.7%** |

The oracle engine's own package construction did not surface
externally-resolved dependency symbols into its candidate set at all (a
real, disclosed implementation gap, not a reflection of task
difficulty); a model that correctly named the real external symbol
anyway was zeroed by the hallucination gate (Equation 6) for citing a
symbol outside the oracle's own incomplete candidate universe. On
internal-only tasks, Oracle performs exactly as a ceiling should
(93.8%, comparable to Two-Pass's own 96.0%) - this is the
**construction-gap** variant of Oracle Inversion (Section 2.6, item 1),
and was subsequently fixed in this codebase's own oracle engine
implementation.

### 4.4 The 3-engine matrix: tRPC (Oracle Inversion, reasoning-limited case)

| Engine | n | Mean TSR | Mean CPI_answer | Cleanliness | Mean Prompt Tokens | Cost/Cell |
|---|---|---|---|---|---|---|
| baseline_bfs_bidirectional | 125 | 0.584 | 1.000 | 0.362 | 5,511 | $0.00090 |
| **oracle** (post-fix) | 125 | 0.760 | 1.000 | 1.000 | 2,224 | $0.00041 |
| **prism_two_pass** | 125 | **0.923** | 0.959 | 0.767 | 3,909 | $0.00073 |

With the construction gap already fixed for this run, Oracle no longer
inverts *below* baseline - but it still falls well short of Two-Pass
(0.760 vs. 0.923) despite a perfectly clean, ground-truth-exact context.
This is the **reasoning-limited** variant of Oracle Inversion (Section
2.6, item 2): the residual 24-point gap from `TSR = 1.0` is attributable
entirely to the model's own causal-ordering and completeness reasoning
(confirmed via direct inspection of raw Turn-2 responses -
`reports/trpc_pilot_debrief.md` Section 5 documents a consistent,
reproducing `recursiveGetPaths`/`omitPrototype` order-reversal mistake
and a `createBuilder` omission on the richest pipeline, both against a
context in which every required symbol was genuinely present). Retrieval
quality and generation-reasoning quality are independent, additive
bottlenecks; Oracle Inversion is the empirical instrument that separates
them.

### 4.5 Latency, turn-count SLA, and compute cost profile

**Express** (`reports/express_pilot_audit_gap_closure.md` Section 5.7,
reconstructed from real per-call log timestamps at zero additional LLM
spend):

| Metric | single-pass | two-pass |
|---|---|---|
| p50 wall-clock latency | 1.327s | 2.940s |
| p95 wall-clock latency | 1.957s | 4.015s |
| turns per cell | 1 (always) | mean 2.50 |
| fraction hitting the 3-turn branch | n/a | 50.0% (100/200 cells) |

Two-pass's own p50/p95 latency is approximately 2.2x/2.1x single-pass's
- the direct, expected cost of an explicit Turn 1 (candidate selection)
plus a conditional Turn 2b (external-dependency resolution, on exactly
half of Express's own Category-5-heavy 20-task suite) ahead of Turn 2's
final answer.

**Real compute cost** (commercial-API corpora only; FastAPI/Django's
self-hosted local-Ollama runs carry GPU-time cost, not comparable
per-token API cost):

| Corpus | Engines | Cells | Total real cost |
|---|---|---|---|
| Express | baseline + two-pass + oracle | 600 | $0.2962 |
| tRPC (small pilot) | two-pass only | 30 | $0.0214 |
| tRPC (full sweep) | baseline + two-pass + oracle | 375 | $0.2551 |
| **Combined (Express + tRPC)** | | **1,005** | **$0.5727** |

---

## 5. Threats to Validity and Architectural Boundaries

### 5.1 Category 10: unbuilt monorepo cross-package workspace resolution

`docs/architecture_boundaries.md` Category 10 documents a real,
disclosed limitation found during tRPC's own monorepo locator
validation: `TypeScriptSourceLocator` correctly resolves a genuine
third-party dependency inside a pnpm workspace (`@tanstack/react-query`,
verified live against real pnpm-installed `node_modules`), but a
same-repo *sibling* package resolved through a real workspace symlink
(`@trpc/client` importing `@trpc/server`) returns an empty result rather
than falling back to the sibling's own unbuilt `src/` - the sibling
package's own `package.json` declares a `dist/`-relative entry point
that is never built in a source checkout. This is architecturally a
different resolution problem than the locator's own external-dependency
contract was built for (recognizing a `node_modules` entry as a
workspace symlink back into the same repository, then re-entering
in-repo indexing on it, is a third resolution tier neither "resolve in
this repo" nor "resolve as an external stub" currently covers), not a
locator defect. This portfolio's own tRPC ground-truth suite was
deliberately scoped to `packages/server` - a package with zero runtime
dependencies of its own - specifically to sidestep this gap rather than
report results against a known-unresolvable retrieval path.

### 5.2 "Category 5": a genuine terminology overlap, disambiguated

Two independent categorization schemes in this project's own primary
sources both use the label "Category 5" for materially different
things, and this manuscript preserves that distinction rather than
silently resolving it in either direction:

1. **`docs/architecture_boundaries.md` Category 5** ("`READS_STATE` as a
   Traversable Graph Edge") is a permanent, measured v1 non-goal: a
   resolved attribute *read* is deliberately excluded from the
   traversable-relation set `TRAVERSABLE_RELATIONS`, since an earlier,
   broader inclusion caused a real hub-flooding regression (a busy
   shared node reading as artificially close to everything that touches
   it, the same failure mode Category 9's own hub-fan-in damping exists
   to contain).
2. **The Express pilot's own task-taxonomy "Category 5"**
   (`reports/express_pilot_audit_gap_closure.md`) is an unrelated,
   corpus-local labeling convention for *external-dependency-sink*
   ground-truth tasks (a debug pipeline terminating in a real,
   externally-resolved npm symbol) - the category responsible for
   Section 4.3's own Oracle construction-gap finding. The Express report
   itself flags this overlap explicitly ("every Category-5-in-the-
   *Express-pilot-sense* task").

Neither is a threat to the validity of any reported result; both are
real, and conflating them would misattribute Section 4.3's finding to an
unrelated, permanent graph-construction non-goal rather than the
oracle-implementation gap it actually was.

### 5.3 Cross-model variance: local open-weights vs. commercial frontier APIs

This portfolio does not hold model family constant across corpora.
FastAPI and Django ran against `qwen2.5-coder:7b-instruct-q8_0` (and,
for one Django comparison, DeepSeek) via a locally-hosted Ollama
instance on a Kaggle GPU runtime; Express and tRPC ran against
`gpt-4o-mini` via OpenAI's real, commercial API, since DeepSeek's own
endpoint is network-blocked in the evaluation container used for those
two corpora. This is a genuine confound this manuscript discloses rather
than absorbs: the large, consistent `{Delta}TSR` effect (Section 4.1)
reproduces across both a 7B locally-hosted open-weights model and a
commercial frontier API model, which is independent evidence the effect
is not an artifact of one specific model family - but a within-corpus,
cross-model controlled comparison (the same corpus, both model
families) was performed only for Django, where it additionally revealed
a real, model-size-dependent asymmetry: the smaller model (DeepSeek)
showed a confirmed *negative* `{Delta}CPI_answer` against `prism_v11`
that the larger model (qwen) did not, at otherwise-comparable `{Delta}
TSR`. No corpus in this portfolio has been run against both a local
open-weights model and a commercial frontier API on the *same* task
suite - the natural next controlled comparison this finding motivates.

### 5.4 Construct validity: the manual audit gate

Per this project's own pre-registered exit criteria
(`docs/roadmap_public_release.md` Section 2, item 4), a formal gate
result is not considered final without a manual construct-validity audit
of every FAIL/MIXED cell - the exact mechanism that found FastAPI's own
`t018`/`t019`/`t020` scoring-contract artifacts
(`reports/fastapi_seed42_closure_debrief.md` Section 1) during that
corpus's own closure arc. This audit has been performed for
Express's own MIXED result (Section 4.3's root-cause analysis, and the
five non-perfect two-pass tasks individually traced to real Turn-1
selection gaps rather than hydration defects) but has **not yet been
performed** for tRPC's own MIXED result as of this manuscript's
drafting - an explicitly named, open item, not silently treated as
closed.

---

## 6. Conclusion and Key Takeaways for Agentic Software Engineering

1. **Retrieval recall is not the bottleneck; context cleanliness is.**
   Across every corpus in this study, a naive wide-net retriever
   achieves near-perfect or perfect answer-recall (`CPI_answer`) while
   real task success (`TSR`) collapses to roughly half of a curated
   retriever's own figure. An agentic code-generation system's own
   retrieval layer should be evaluated, and optimized, primarily on
   signal-to-noise ratio (`Cleanliness`), not on whether the correct
   answer is technically present in context.

2. **A "ceiling" reference is only as good as its own construction and
   the model's own reasoning capacity.** The Oracle Inversion Phenomenon
   demonstrates that a nominal upper-bound retrieval engine can score at
   or below a naive floor, and that this inversion is itself a
   diagnostic signal - not noise to be explained away - separating a
   real implementation gap in the reference engine from a genuine,
   retrieval-independent limit on the generative model's own causal
   reasoning.

3. **Statistical gate thresholds must be headroom-aware, not flat.** A
   fixed absolute bar on a `[0, 1]`-bounded metric is a real design flaw
   once a comparison's own baseline sits near that metric's ceiling
   (FastAPI's own 5.3-8.0% `CPI_answer` headroom made a flat +15pp bar
   mathematically unreachable regardless of retrieval quality) - and a
   headroom-adjusted rule, cross-checked against an independent corpus's
   own raw data before being trusted (Section 4.1's Django re-run),
   correctly avoids retroactively manufacturing a false PASS anywhere it
   was not already warranted.

4. **Static-analysis retrieval graphs built for one language paradigm do
   not transfer silently to another.** TypeScript's own idiomatic
   factory-function-returning-object-literal and closure-nested-function
   patterns produced two real, previously-undetected graph-construction
   defects (Section 3.3) with no Python analogue - a retrieval engine's
   own AST-walking rules must be validated per target paradigm, not
   assumed correct by extension from a single language's own success.

5. **The trade-off is real and disclosed, not eliminated.** Two-Pass's
   own large, consistent `TSR` gain comes with a small, real (and, on
   tRPC, statistically confirmed) `CPI_answer` cost from its own tighter
   selection budget - this portfolio reports every gate result as MIXED
   or EXPAND rather than a clean PASS in three of four corpora precisely
   because this cost is real and this project's own gate methodology
   refuses to round it away.
