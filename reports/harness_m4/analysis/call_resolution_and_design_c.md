# PRISM call-resolution fixes + Design C — impact report

Branch `harness/eight-arm-slm`. No Kaggle runs; all numbers below are offline
measurements against the pinned corpora. All fixes are language-level rules — no
corpus, framework, seed or task names appear in any of them.

## 1. What changed

### Call resolution (`src/prism/graph/concrete_builder.py`)
| Rule | What it does | Edge kind |
|---|---|---|
| Virtual-dispatch expansion (CHA) | a call resolved to `Base.m` through a receiver that may hold a subclass also links every transitive override | `TENTATIVE_CALL`, `dispatch_of=Base.m` |
| Single-class-family resolution | unknown receiver, every repo definition of the method in one class family → link the family root (+ dispatch) | `TENTATIVE_CALL` |
| Builtin-method-name guard | unknown receiver + builtin-named method (`get`, `items`, `update`, …) + ≥2 repo definitions → no guess, `UnresolvedPolymorphic` sentinel (a unique repo definition keeps the existing G44 link) | sentinel |
| `*args`/`**kwargs` are builtins | `kwargs.get(...)` never resolves to a repo method | — |
| Receiver-name typing | `app_config.ready()` → the unique class whose normalised name matches and defines `ready` | `TENTATIVE_CALL` |
| Return-type inference | `qs = self.get_queryset(); qs.filter()` and fluent chains, from annotations or agreeing `return` statements | `TENTATIVE_CALL` (inferred binding) |
| Attribute-type index | `opts = model._meta; opts.get_field()` → classes ever assigned to `._meta` that define the method | `TENTATIVE_CALL` |

### Pricing best-effort evidence consistently (packer / causal graph)
The codebase already documented that `TENTATIVE_CALL` edges are priced at 0.60, but
only `prism.slicer.distance` applied it. Now:
* causal-graph edges (`causal_weights.compute_causal_edges`) carry the same discount
  and a `tentative` flag;
* synthetic data-flow/guard coupling is derived only when some common caller links
  both ends confidently (no inference stacked on a guess);
* upstream-caller weights (`blast_radius.compute_upstream_callers`) carry the discount,
  and only a confidently-resolved caller is force-admitted by the knapsack;
* call-site → target mapping for data flow ignores dispatch-widening edges.

### Design C (`src/prism/packer/candidate_index.py`, `harness/arms/arm5_prism.py`)
`build_candidate_manifest(..., direction="both", budget_tokens=B)`: hop-ordered
upstream BFS over CALLS/INSTANTIATES in-edges (test code excluded), direct callers
ordered by W_upstream, interleaved with downstream under a shared token budget;
upstream rows are labelled `caller`. Used by Arm 5 only for `T5_blast_radius`
(`PRISM_BLAST_MODE=True`; `False` reproduces M4). Default manifest unchanged.

## 2. Call-graph accuracy vs runtime ground truth

Runtime call graph recorded over Django's full test suite (`sys.setprofile`),
production code only, explicit call sites only (9,677 edges). FastAPI held out
(223 edges).

| | Django baseline | Django final | FastAPI baseline | FastAPI final |
|---|---|---|---|---|
| Edge recall | 0.590 | **0.707** | 0.991 | 0.991 |
| Static edges from exercised callers | 7,735 | 9,991 | 261 | 261 |
| …of those confirmed at runtime | 0.739 | 0.685 | 0.847 | 0.847 |

"Confirmed share" is a precision *lower bound*: a static edge from an exercised
caller may be real but simply not executed by the test suite. New edges are all
marked tentative and priced accordingly.

Django recall by call shape:

| Shape | baseline | final |
|---|---|---|
| bare name | 0.957 | 0.957 |
| `self.method()` | 0.711 | 0.879 |
| `self.attr.method()` | 0.396 | 0.584 |
| `var.method()` | 0.340 | 0.445 |
| `call().method()` | 0.522 | 0.569 |
| longer chain | 0.239 | 0.498 |

Remaining misses are dominated by receivers no static rule can type
(manager methods generated at run time via `from_queryset`, values from
untyped containers).

## 3. Blast-radius seeds (t13): callers found

Hop-1 production callers of the four M4 blast seeds (static graph vs runtime):

| Seed | runtime callers | static callers (final) | runtime-confirmed | semantic gold found |
|---|---|---|---|---|
| `reverse` | 24 | 28 | 24 | 21/21 |
| `QuerySet.get` | 30 | 4 | 4 | 4/29 |
| `Field.clean` | 6 | 4 | 4 | 4/6 |
| `Options.get_field` | 87 | 81 | 70 | 63/79 |

**Finding on the M4 seed screen.** Under the original graph `QuerySet.get` had 34
return-binding callers and passed the ≥20-caller richness screen; **none of those
34 ever called `QuerySet.get` in Django's full test suite** (they were misresolved
`.get` calls on dicts/other classes). With the fixes it has 1 return-binding caller,
runtime-confirmed, so `tests/benchmarks/test_harness_metrics.py::
test_all_four_existing_blast_seeds_pass_the_richness_screen` now fails. Left failing
deliberately — whether to replace this seed is a benchmark-design decision.

## 4. Design C: gold reach of PRISM's Turn-1 candidate list

Every M4 T5 task, arm budget 13,000 tokens. "M4" = original graph + default manifest;
"Design C" = fixed graph + blast-mode manifest. Gold = the tasks' own
`pipeline_symbols` (note the existing caveat: this gold was derived from PRISM's
original graph).

| Corpus | M4 gold reach | Design C gold reach | tasks fully covered (M4 → C) | mean manifest lines (M4 → C) |
|---|---|---|---|---|
| fastapi | 14/32 = 0.44 | **28/32 = 0.88** | 0/8 → 5/8 | 11.1 → 14.1 |
| django | 14/45 = 0.31 | **33/45 = 0.73** | 2/8 → 4/8 | 18.6 → 32.1 |
| express | 1/5 = 0.20 | 2/5 = 0.40 | 0/2 → 1/2 | 9.5 → 11.0 |
| trpc | 25/96 = 0.26 | **95/96 = 0.99** | 1/14 → 13/14 | 8.0 → 14.2 |
| **all** | **54/178 = 0.30** | **158/178 = 0.89** | 3/32 → 23/32 | |

Against the graph-independent change-based semantic gold (t13 seeds):

| Seed | M4 | Design C |
|---|---|---|
| `reverse` | 1/21 | **20/21** |
| `Field.clean` | 2/6 | **6/6** |
| `Options.get_field` | 1/79 | **22/79** |
| `QuerySet.get` | 0/29 | 4/29 |

`Options.get_field` is budget-bound, not graph-bound: 63 of its 79 gold callers are
in the fixed graph at hop 1, but 13,000 tokens of hydratable bodies hold ~34 symbols.
`QuerySet.get` is graph-bound (callers reach it through run-time-generated manager
methods).

This is reach (the ceiling of what the LLM can select), not end-to-end accuracy;
an M5 Kaggle run is needed for that.

## 5. Test status

Full suite (`pytest -n 4`): **2,102 passed, 6 failed**, 30 skipped.
* Pre-existing on the commit before these changes (unchanged):
  `test_m1_fixes` ×2, `test_fuzzy_seed_suggestion::test_fuzzy_match_performance_on_real_corpus`.
* Richness screen — see §3 (intentional).
* `test_arm3_ts_lsp::test_quiescence_probe[express]` and
  `test_index_cache_consistency::test_cache_write_then_cache_hit_matches_fresh_build`
  fail only under the 4-worker run; both pass when run on their own.

Fixed along the way: `test_prism_retrieves_private_helper_in_pipeline[t02_017-_urlparse]`
(failing before these changes) now passes. Two pinned candidate counts were
updated (t02_002 52→51, t02_009 dry run 14→12): the dropped symbols are non-gold
boundary nodes reached only through tentative edges; the gold-survival assertions
are unchanged and pass. One fuzzy-anchor test's precondition was updated (the
decorated method is now statically linked, tentatively).
