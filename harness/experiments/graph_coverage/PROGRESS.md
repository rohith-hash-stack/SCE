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

## After the grouped listing: widely used symbols

**The change** (`src/prism/impact/dependents.py`; `src/prism/impact/context.py`; `harness/experiments/r3/tools.py`):
- **Section 4 of the change context** keeps 40% of the budget. When its one-by-one listing fits, it is unchanged.
  When it does not, or there are more than 40 other uses:
  - the nearest uses are given in full;
  - the rest are grouped by file, with kinds, counts and names;
  - a closing line counts the files past the budget;
  - every use is named or counted.
- **The anchor's code and digest** keep 30% of the budget. A longer anchor is given as an outline: its first line and
  each definition's own line, with line numbers, and a pointer to `prism_read_code`.
- **Why both:** on scrapy, `Request`'s code and digest (3,400 tokens) came before section 4 in a 4,000-token answer.
- **The blast radius's list** keeps 55% of the cap and is grouped the same way.
- **Unchanged:** small answers are listed exactly as before (the golden outputs do not change). Tests:
  `tests/test_grouped_dependents.py`; the tool tests pass (221; the 7 xfails are Phase 2 requirements).

**Shown, the same sample** (`after_grouped/shown/`):

| | After B10 | With grouping |
|---|---|---|
| **Shown, all 2,817 sampled uses** | 61% | **63%** |
| type annotation | 62% | 75% |
| scrapy | 58% | 64% |
| fastmcp | 57% | 63% |

**Why a surfaced use is still not shown** (all 10 repositories, 155 uses):
- **130: counted in a grouped listing, not named.** The used definition has more dependents than one answer can
  name: `typer.run` (hundreds of docs examples), `Litestar` (its docs), scrapy's `Request`. Every one is counted by
  file; the benchmark counts only named ones.
- **25: PRISM counts its file as test code, and the harness leaves test code out.** `prism.scanner.entrypoint_gate.
  is_test_file` treats:
  - any `spec/` directory as tests (an RSpec/JS convention). This hides litestar's production package
    `litestar/openapi/spec` (33 files);
  - a module named `test.py` as tests. This hides werkzeug's public `werkzeug.test` (its test client).

  This is not new, and it hides these files' callers too. A fix changes which code every arm treats as tests, so it
  waits for a decision.

## After the test-file rule fix

`prism.scanner.entrypoint_gate.is_test_file`:
- **`spec/` directories** count as tests only for JS/TS/Ruby files.
- **A module named `test.py`/`tests.py`** counts as tests only when its code is test code.

Tests: `tests/test_test_file_rule.py`. Index cache schema 8 → 9.

Shown, the same sample, on the two repositories the rule affected (`after_testrule/shown/`):

| Repository | Before the fix | After |
|---|---|---|
| litestar | 72% | 75% |
| werkzeug | 55% | 59% |

The other 8 repositories have no production file the old rule hid, so their sample is unchanged. **All 10: 63%
→ 64% (1,792 of 2,817).**

## Variable-type tracking, measured with a corrected benchmark

### The benchmark, corrected first

While sizing this step, the largest "unrecorded" groups turned out to be mostly uses that are not uses: names that
share a definition's name but are another object. Three corrections (`benchmark.py`; each in both runs below):
1. **Locals:** a bare name bound locally is not a use of the definition sharing its name. This covers a parameter, an
   assigned name, a loop or comprehension variable, or a module-level assignment in another file. Example:
   `client.get("/")` on a parameter `client` was counted as a use of a `client` fixture.
2. **Outside names:** neither is a bare name imported from outside the repository (`from typing import Callable`,
   `import json`, `from pprint import pformat`), that import itself, or a builtin the file does not define or import
   (`type`).
3. **The repository's own packages** are every module path name, so `pydantic_ai` (under `pydantic_ai_slim/`) is
   not taken for an outside package.

**About 33,000 of the 171,188 earlier uses were such names.** The figures below are not comparable with the
sections above, which counted them: each stage is compared with the one before it under the same method.

### The change

`src/prism/graph/references.py`, `_PythonFile.typed_target`; `tests/test_typed_references.py`.
- **What was missing:** the reference pass skipped every `self.…` chain, because `self` is a parameter.
- **What it does now:** a chain through a typed variable is resolved with call resolution's own resolver
  (`_resolve_segments`) and the same instance-type maps (2b), built once per definition:
  - `self.client.send`;
  - an annotated parameter's `c.send`;
  - a module-level instance's `client.close`.
- **Only clean resolutions are kept:** not tentative, ambiguous, a builtin receiver or an unbound local. An untyped
  parameter's `c.send` stays unlinked.

### Results (`before_tt/`, `after_tt/`)

"Before" is PRISM at `7103faa2`, after the grouped listing and the test-file fix; "after" adds type tracking. Same
benchmark, 10 repositories, 137,891 uses (48,067 in production code). `check`: no drops.

| Recorded | Before | After |
|---|---|---|
| **All uses** | 76.6% | **77.8%** |
| **Production code** | 82.0% | **83.1%** |
| production, bare names | 94.0% | 94.0% |
| production, `obj.name` | 49.8% | 53.7% |
| production, decorators | 54.0% | 92.6% |
| production, passed as an argument | 57.2% | 60.8% |
| production, assigned as a value | 37.1% | 46.1% |
| typer (production) | 76.7% | 89.9% |

**The `obj.name` figure understates PRISM.** The benchmark can match such a use only by its attribute's name. A
sample of scrapy's misses was mostly other objects sharing the name: `logger.info`, `dict.items`, `self.port`. A few
were real, such as a nested class reached through its class (`MediaPipeline.SpiderInfo`).

**Build time:** +0.2 s to +16 s per repository (instructor +2%, fastmcp +53%). Both audits ran at once, so these are
rough.

**Shown** (`*/shown/`; 280 production uses per repository, 2,800 in all; the same sampling for both):

| | Before | After |
|---|---|---|
| Recorded | 81.0% | 83.2% |
| Surfaced | 79.1% | 81.3% |
| **Shown to the agent** | **74.1%** | **77.3%** |

**Of the uses surfaced but not shown after:**
- 188 are counted in a grouped listing but not named (widely used definitions);
- 1 is in a file PRISM counts as test code.
