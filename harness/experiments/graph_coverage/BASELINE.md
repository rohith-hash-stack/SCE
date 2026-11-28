# Graph-coverage baseline (Phase 2, Phase A2; PRISM at the commit of this file)

How many real uses of each repository's uniquely named functions, methods and classes PRISM's graph connects, by kind
of use. Method and definitions: `benchmark.py`'s docstring. Python files only (JS/TS is the next step of A1). One commit
per repository: the parent of its first task in `harness/experiments/r4/sanity/tasks_v3.json`. Per-repository data:
`baseline/*.json`. Regression check after any PRISM change:

    python -B -m harness.experiments.graph_coverage.benchmark audit --repo CLONE --rev SHA --label NAME --out new/NAME.json
    python -B -m harness.experiments.graph_coverage.benchmark check --baseline harness/experiments/graph_coverage/baseline --new new

**Recorded:** PRISM's graph has an edge from the definition containing the use (or, outside every definition, the
module) to the used definition. **Surfaced:** that edge is one `prism_change_context`/`prism_blast_radius` show.

| Repository | Commit | Definitions | Unique names | Uses | Recorded | Surfaced |
|---|---|---|---|---|---|---|
| aiohttp | `3c608498c6` | 6,674 | 4,405 | 19,877 | 33% | 32% |
| fastmcp | `7d40b1dd4c` | 8,574 | 5,976 | 23,895 | 35% | 35% |
| instructor | `6969bb6849` | 4,777 | 3,290 | 13,548 | 33% | 32% |
| litestar | `d6e5c78570` | 7,458 | 4,313 | 26,994 | 26% | 25% |
| multidict | `fbf28ae0c5` | 485 | 323 | 1,129 | 44% | 43% |
| pydantic-ai | `5f5a9eac51` | 4,643 | 2,802 | 24,390 | 44% | 43% |
| pydantic | `a656b75d58` | 14,062 | 7,358 | 31,649 | 23% | 21% |
| scrapy | `60b1badacd` | 7,234 | 4,571 | 20,754 | 40% | 39% |
| typer | `85ca5b5adc` | 1,661 | 339 | 2,494 | 23% | 23% |
| werkzeug | `c993048614` | 2,458 | 1,405 | 6,458 | 36% | 34% |

| Kind | Uses | Recorded | Surfaced |
|---|---|---|---|
| call | 36,517 | 70% | 70% |
| attribute read on it | 26,868 | 2% | 1% |
| instantiation | 26,532 | 88% | 88% |
| type annotation | 18,925 | 10% | 10% |
| import | 17,102 | 3% | 3% |
| passed as an argument | 16,785 | 7% | 1% |
| compared | 5,307 | 2% | 1% |
| other use | 4,096 | 6% | 1% |
| stored in a container | 3,633 | 10% | 2% |
| generic/subscript | 3,165 | 3% | 2% |
| isinstance/issubclass | 2,945 | 10% | 9% |
| base class | 2,331 | 87% | 87% |
| __all__ entry | 2,329 | 0% | 0% |
| decorator | 1,868 | 0% | 0% |
| assigned as a value | 1,527 | 5% | 0% |
| returned as a value | 956 | 15% | 1% |
| except clause | 216 | 7% | 6% |
| getattr-style string | 86 | 1% | 1% |

| Location | Uses | Recorded | Surfaced |
|---|---|---|---|
| test file | 111,841 | 37% | 36% |
| function body | 33,685 | 39% | 37% |
| module level | 18,097 | 9% | 9% |
| class body | 7,565 | 6% | 6% |

| Form | Uses | Recorded | Surfaced |
|---|---|---|---|
| bare | 131,729 | 30% | 29% |
| qualified | 39,459 | 41% | 40% |

Non-Python files mentioning a sampled unique name (up to 400 names per repository): docs 3,131, other 1,426, config 175, c/c++ 60, templates 19, js/ts 3
