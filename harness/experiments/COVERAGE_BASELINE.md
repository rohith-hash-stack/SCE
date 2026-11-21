# PRISM test coverage baseline (2026-11-09, code `0e2f0809`)

**How measured:**
- `pytest --cov=prism -n 4` over `tests/`, without `tests/benchmarks` and without the 18 test files that index
  full real repositories (`benchmarks.corpora.resolver`: Django, FastAPI, express, tRPC). Those take 10+ minutes
  per test under coverage tracing, and run separately, without coverage.
- Results: 2,844 passed, 16 failed, 4 errors, 46 skipped, in 14 minutes.

**Overall line coverage of `src/prism`: 90.8%** (18,426 of 20,300 statements).

| Package | Coverage | | Package | Coverage |
|---|---|---|---|---|
| debug | 42.6% | | traversal | 92.8% |
| cache | 66.7% | | graph | 93.1% |
| serializers | 77.4% | | surface | 94.8% |
| analysis | 78.8% | | semantics | 95.0% |
| top-level modules | 84.1% | | deps | 95.6% |
| runtime | 88.1% | | parser | 96.2% |
| slicer | 89.2% | | query | 98.2% |
| scanner | 89.6% | | tagger | 98.4% |
| packer | 90.4% | | | |
| impact | 90.7% | | | |
| summaries | 90.9% | | | |
| mcp | 91.6% | | | |
| external | 91.6% | | | |

**The modules the required-behaviour spec builds on:**

| Module | Coverage | Missing lines |
|---|---|---|
| `summaries/digest.py` | 89.9% | 145 |
| `graph/concrete_builder.py` | 94.5% | 146 |
| `impact/dependencies.py` | 87.2% | 44 |
| `impact/breaks.py` | 89.3% | 30 |
| `mcp/server.py` | 89.2% | 34 |
| `impact/context.py` | 92.0% | 17 |
| `impact/testcontext.py` | 88.7% | 13 |
| `impact/newcode.py` | 95.3% | 6 |

**Lowest files (50 or more statements):**
- `debug/recorder.py` 0%;
- `analysis/hybrid_engine.py` 38.5%;
- `runtime/tracer.py` 63.6%;
- `cache/sqlite_cache.py` 66.7%;
- `slicer/blueprint.py` 67.7%.

**Failures (20), triaged:**
- **12: environment.** `transformers` is not installed (`test_ts_splitter`, `test_arm1_chunking`,
  `test_harness_foundation`, `test_m1_fixes`, `test_kaggle_m4`, the `test_ts_arms_index` errors). These should be
  clean skips when the package is missing.
- **5: behaviour drift against recorded expectations.** These are real: they need a decision on intended or
  regression.
  - `test_nlq_p0`: recorded search ranks and candidate counts differ, for example q12 (the test expects 185 candidates, the fixture has 172) and
    q15's ranks shifted by 1–2.
  - `test_nlq_p1`: proof states read `PROVED_WITH_WARNING` where `PROVED` was recorded.
- **3: Kaggle-runner tests** (`test_kaggle_m4`, `test_m1_fixes`) exit with code 2. Same root, `transformers`.

**A test-isolation issue:** some tests write PRISM caches into the shared fixtures
(`tests/fixtures/*/.prism/cache/*.db`). Tests can then depend on the order they run in. Fixture caches belong in a
temporary directory.

**What line coverage does not show:**
- 90% of lines does not mean the **tool outputs** are pinned. No test compares `change_context`, blast radius,
  digest or `new_function_context` output with a golden file, so a change in one module can silently change what
  the agent sees.
- That is the main gap for "one implementation should not break another" (spec R2, R3).
