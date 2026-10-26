# PRISM digest: coverage and accuracy

`prism.summaries.digest` (digest version 1) turns each function's own code into line-cited facts, each under the
conditions it runs in, with a certainty level (`certain` / `possible` / `runtime` / `unknown`). There is one
syntax walk for every supported language (Python, JavaScript / TypeScript, Java, C#, Go), the same for every
function whatever its length. It was not tuned on the 60 functions of the earlier summary-accuracy audit;
they are excluded from every sample below. Method: `harness/experiments/digest/README.md`.

## 1. Whole-repository checks (no model)

Nine repositories, five languages, every function and method (test files included), each digested twice.
Numbers: `reports/digest/summary.json`.

| repo | language | functions | crashes | violations | nondeterministic | certain / possible / unknown | median digest chars | digest / source (median) |
|---|---|---|---|---|---|---|---|---|
| httpx | Python | 1,136 | 0 | 0 | 0 | 89% / 9% / 2% | 313 | 1.1 |
| rich | Python | 1,818 | 0 | 0 | 0 | 88% / 8% / 4% | 308 | 1.03 |
| elysia | TypeScript | 2,023 | 0 | 0 | 0 | 57% / 24% / 19% | 461 | 1.41 |
| oclif | TypeScript | 1,324 | 0 | 0 | 0 | 78% / 7% / 15% | 273 | 0.99 |
| gin | Go | 1,342 | 0 | 0 | 0 | 72% / 23% / 5% | 358 | 1.44 |
| gson | Java | 3,986 | 0 | 0 | 0 | 78% / 8% / 14% | 271 | 1.12 |
| newtonsoft | C# | 8,056 | 0 | 0 | 0 | 83% / 14% / 4% | 367 | 0.97 |
| flask | Python | 1,456 | 0 | 0 | 0 | 76% / 18% / 6% | 194 | 1.26 |
| express | JavaScript | 1,455 | 0 | 0 | 0 | 45% / 13% / 42% | 293 | 1.09 |
| **all** | 5 languages | **22,596** | **0** | **0** | **0** | 77% / 13% / 10% | | |

Outside the per-function fact budget (40 facts; lower-ranked facts are counted under `omitted:`, never
dropped silently), the digest of a 100+ line function is 0.3-0.8x the length of its source. For short
functions it is about the same length as the source: there the gain is structure (conditions, call
targets, certainty), not size.

## 2. Accuracy on fresh random samples (judged by hand)

One production function per repository and length band, 27 per sample, drawn after the extractor was
written. Each sample was judged with the code frozen; fixes came afterwards, each with a regression test in
`tests/test_digest.py`. The judgments are the author's, not blind.

| sample (seed) | role | fact lines | lines with a wrong element | false `certain` claims | wrong conditions | invented dynamic targets |
|---|---|---|---|---|---|---|
| 1 (20261026) | development: used to find bugs | ~270 | not counted (bugs listed below) | ~11, counted afterwards (graph name matches) | 0 | 0 |
| 2 (1730000000) | measurement, then fixes | 195 | 23 (11.8%) | 17 | 0 | 0 |
| 3 (424242) | measurement, then fixes | 188 | 5 (2.7%) | 0 | 0 | 0 |

The fixes made after sample 3 have not been measured on a fourth sample.

**Found by sample 1 and fixed**
- **Call targets from the graph.** PRISM's call graph resolves some method calls by name alone (`out.beginObject()`
  on a `JsonWriter` linked to `JsonTreeReader.beginObject`). The digest now keeps a target `certain` only when
  the receiver is self / this, a bare function, an imported name or class, or a variable whose declared type
  owns the method; otherwise it is `possible`. A declared type picks the method of that type.
- `__import__` was described as reflection, and a closure calling a function argument of its enclosing
  function was guessed as a method.
- Fall-through `case` labels were dropped from the condition.
- A function value held in a local variable was called without being marked dynamic.
- Java / C# annotations were described as decorators that wrap the function.

**Found by sample 2 and fixed**
- 17 lines: a C# `using` alias (`Assert`) and a nested static class that PRISM indexes with kind "method"
  (`StringAssert`) were called "a type outside the index".
- 3 lines: members of objects the repository defines were labelled `library`; they are now `unindexed`.
- 1 line: the callback of `.filter(...).map(...)` was labelled as passed to `filter`.
- 1 line: a C# object initialiser (`new S { MaxDepth = 3 }`) was shown as a statement.
- 1 line: two separate `if (x)` blocks with the same condition were rendered under one heading.
- Omission: C# local declarations (`string json = ...`) were left out.

**Found by sample 3 and fixed**
- 1 line: an imported library call (`yaml.Marshal`) listed same-named repository methods as possible targets.
  This was caused by PRISM's Go import map, which names `gopkg.in/yaml.v3` as `yaml.v3`. The digest now applies
  Go's package-name rule itself.
- 3 lines: cases of a Go `switch {}` with no subject were phrased as "value matches <condition>".
- 1 line: a fully qualified type (`com.fasterxml.jackson.core.JsonParser`) was matched to a same-named class
  of the repository.
- Noise: an increment inside an assignment was stated twice, and a C# lambda argument was not labelled by its call.

**Known, not fixed**
- **Missed dynamic calls**: a function held in an object property (`fws.helpFunction?.(...)`) or a
  package-level function variable (`DebugPrintFunc(...)`) is shown as a call with target `?`, not as `dynamic`.
- **Nested calls**: a call inside a return or assignment is listed only when it resolves to an index symbol.
  Library and unknown calls stay visible in the statement's own text.
- **Missing facts**:
  - writes to closure variables and to elements of an argument (`c.id = ...` inside a callback) are shown as
    local assignments;
  - TypeScript parameter properties (`constructor(public x)`) are not shown as state.
- **Graph gaps reach the digest as `possible`, never as `certain`**: no Java / C# inheritance edges, and
  ambiguous name matches.

## 3. Reproduce

```
python -m harness.experiments.digest.coverage check reports/digest NAME=PATH ...
python -m harness.experiments.digest.coverage sample OUT --seed 424242 --exclude IDS.tsv NAME=PATH ...
```

Corpora: set-6 checkouts of httpx, rich, elysia and oclif (`harness/experiments/heldout_retrieval/set6`); plus
- gin `0f09c3a9b4626d9fe9979cf4d0521d6ae32ab646`
- gson `57731049da3e415be3d5d34db1ca3b80f3c73810`
- Newtonsoft.Json `52fa3aef1f2cadcd3a3f874251eddc98d3efbbaa`
- flask `d086db856be187255b8ec61ef409357393020f32`
- express `1c6f1b0fe4000977b9a7b4f34097e4d527dd9824`
