# Graph-coverage progress (Phase 2, Phase B)

Same 10 Python repositories and commits as `BASELINE.md`; uses of uniquely named definitions, recorded in either graph.
`check` against the previous step: **no drops**.

| Step | Commit | Recorded | Surfaced (shown by the caller views) | Data |
|---|---|---|---|---|
| Phase A baseline | `5b98c2ec` | 32.9% | 31.7% | `baseline/` |
| B1–B5, Python (reference pass 2d) | `fc602f09` | **62.1%** | 31.7% (unchanged: surfacing is B10) | `after_B1-B5/` |

## By kind of use (171,188 uses)

| Kind | Uses | Baseline | After B1–B5 | Note |
|---|---|---|---|---|
| call | 36,517 | 70% | 73% | calls inside functions are 2b's resolution; module-level calls added |
| attribute read on it | 26,868 | 2% | 13% | mostly `obj.attr` on instances: needs types, next step |
| instantiation | 26,532 | 88% | 99% | |
| type annotation | 18,925 | 10% | 84% | |
| import | 17,102 | 3% | 94% | |
| passed as an argument | 16,785 | 7% | 30% | qualified `obj.method` passed as a value: needs types |
| compared | 5,307 | 2% | 7% | same |
| stored in a container | 3,633 | 10% | 62% | |
| generic/subscript | 3,165 | 3% | 38% | |
| isinstance/issubclass | 2,945 | 10% | 73% | |
| base class | 2,331 | 87% | 94% | |
| `__all__` entry | 2,329 | 0% | 96% | |
| decorator | 1,868 | 0% | 73% | |
| assigned as a value | 1,527 | 5% | 21% | |
| returned as a value | 956 | 15% | 26% | |
| except clause | 216 | 7% | 76% | |
| getattr-style string | 86 | 1% | 1% | B6 |

By location: module level 9% → 91%; class body 6% → 86%; function body 39% → 50%; test files 37% → 59%.

**Where the remaining gap is:** uses through an instance or a qualified attribute (`self.client.send`, `obj.method`
passed as a value, `x.attr == ...`). The resolver reaches a definition through imports and modules, not through a
variable's type. Covering these needs the builder's instance-type maps (the ones 2b uses for method calls) in the
reference pass: the next B step.

## Build cost of pass 2d

| Repository | Pass off | Pass on | Reference edges |
|---|---|---|---|
| multidict | 1.1 s | 1.2 s | 331 |
| litestar | 15.5 s | 19.3 s (+25%) | 13,213 |
| pydantic | 39.2 s | 40.2 s (+3%) | 8,486 |

## PRISM's full test suite, before and after pass 2d

Run on 2026-11-20, test by test from the junit files (`pytest tests`, about 1.5 h per run).
- **Before** (code before the reference pass): 3,363 passed, 17 failed, 4 errors, 1 xpassed.
- **After** (`dfaf570d`): 3,380 passed, 18 failed, 4 errors. The added passes are the 17 new reference tests
  plus 1 fixture test.

**The 17 failures and 4 errors are the same in both runs,** and they also fail when rerun on commit `0d20bc67`, before
the reference pass. Their causes:
- `transformers` is not installed in this container (the 4 TS-arm errors);
- the external npm dependency is not found (the express etag cases);
- packer budget and compression thresholds.

**Two tests changed state, neither from the pass:**
- `test_fuzzy_match_performance_on_real_corpus` took 0.0507 s against its 0.05 s limit. It passes when run alone, and
  it only reads the symbol table, which pass 2d does not change.
- `test_cached_and_uncached_feature_masks_identical_on_real_django_corpus` is a non-strict xfail. It is documented as
  intermittent on minified vendor JavaScript; it xpassed before and xfailed after. Pass 2d only covers Python.

**No regression.** The 7 Phase 2 requirement tests are strict xfails, as intended.

## After B10: the connections shown to the agent

**B10** (`src/prism/graph/references.py` `references_to`; `src/prism/impact/context.py`; `src/prism/mcp/server.py`;
`harness/experiments/r3/tools.py`):
- **Both tools now list a symbol's uses other than calls:** `prism_change_context` in section 4, after the callers;
  `prism_blast_radius` after the transitive callers.
- **Each row gives** the source (a definition, or a module for module-level code), its file and lines, and a label
  per kind: "imports it", "lists it in `__all__`", "uses it as a type annotation", "type-checks against it
  (isinstance)", "catches it (except)", "uses it as a decorator", "calls it (outside a function)", "uses it as a
  value", "reads an attribute of it", "subclasses it".
- **Subclasses:** inheritance edges the caller list leaves out are listed as "subclasses it" (they were shown
  nowhere).
- **Limits:**
  - 40 rows per tool, then "… N more";
  - test code left out where test callers are (the harness: `include_tests=False`).
- **A symbol with no calls but with other uses** now reads "No calls to it in the index; its other uses are listed
  below", not "… or unused" (delete-05's misleading line).
- **Reference edges keep their lines** (up to 5) and module nodes their file; index cache schema 7 → 8.
- **Golden outputs:** 5 changed, the added list only (`tests/golden/py_repo`).

**Full audit** (`after_B10/`, all 171,188 uses; tests included; list lengths and token caps ignored):
- **Recorded:** 62.1% (unchanged).
- **Surfaced:** 31.7% → **60.9%**. `check` against `after_B1-B5`: no drops.

**Shown, sampled** (`after_B10/shown/`, `benchmark.py shown`): what the agent actually gets.
- **Sample:** 300 uses outside test files per repository (seed 0; multidict has 117), 2,817 in all.
- **How:** both tools called through the harness's tool layer (tests left out, each answer cut at 4,000 Qwen2.5
  tokens). A use counts when its source's name is in the part of an answer that lists dependents.

| | Share of sampled uses |
|---|---|
| Recorded | 68% |
| Surfaced | 67% |
| **Shown** | **61%** |
| shown among the callers (what the agent got before B10, approximately) | 27% |
| shown only in the new other-uses list | 34% |

"Approximately": before B10 the callers' code had the budget the new list now takes. PRISM gives the callers' list
(section 4) in full, ahead of the budgeted code; the harness's 4,000-token cut can still end it.

| Kind (sample) | Recorded | Shown |
|---|---|---|
| import | 96% | 90% |
| `__all__` entry | 93% | 92% |
| except clause | 92% | 92% |
| instantiation | 99% | 88% |
| call | 77% | 76% |
| isinstance/issubclass | 75% | 73% |
| type annotation | 85% | **62%** |
| passed as an argument | 21% | 15% |
| attribute read on it | 27% | 18% |

**Why a surfaced use is not shown** (scrapy, the largest gap: 75% surfaced, 58% shown; 55 uses):
- **43: the answer is cut at the 4,000-token cap** before the row;
- **11: beyond the 40 listed rows;**
- 1: not listed.

These are uses of hub classes (`Request`, `Spider`, `Crawler`): hundreds of callers and annotations, more than one
answer holds. **The next fix for that is a grouped listing for hubs** (counts per file and kind, with the nearest rows
in full), not a bigger graph. Type annotations lose the most here because hub classes are what gets annotated.

**PRISM's tests after B10:** the 42 test files that touch the changed tools (change context, blast radius, MCP
server, index cache, harness tool layer, golden outputs): 675 passed, 5 failed, 2 skipped, 8 xfailed. The 5 failures
(`test_nlq_p1` ×2, `test_query_to_evidence` ×3) are among the 17 that fail before the reference pass (above); every
other test has the same result as in the full run before B10. The 9 B10 tests are new and pass.
