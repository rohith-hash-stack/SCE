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
