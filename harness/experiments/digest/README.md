# PRISM digest: whole-repository checks and accuracy samples

`prism.summaries.digest` turns every function's own code into line-cited facts (returns, raises, catches,
state changes, calls and their targets, reads, dynamic dispatch, decorators, entry points), each under the
conditions it runs in and with a certainty level (`certain` / `possible` / `runtime` / `unknown`). The
extractor is one syntax walk shared by every supported language. It is not tuned on any benchmark function:
the 60 functions of the earlier summary-accuracy audit are excluded from every sample here.

## Checks (no model)

```
python -m harness.experiments.digest.coverage check OUT NAME=PATH [NAME=PATH ...]
```

Every function and method of each repository (test files included) is digested twice. The run fails if any
of these is non-zero:

- crashes;
- invariant violations: a fact outside its function's lines, an empty fact, or a `certain` call target that
  is neither an index symbol nor a labelled builtin / library / standard-library name;
- functions whose two renders differ.

## Accuracy samples (judged by hand)

```
python -m harness.experiments.digest.coverage sample OUT --seed N [--exclude IDS.tsv] NAME=PATH [...]
```

One production function per repository and length band (under 10 lines, 10-40, over 40), drawn with a fixed
seed, written as numbered source next to its digest. Each digest line is judged against the source:

- **wrong element**: a false statement, a wrong `certain` target, a wrong condition, or a misleading
  label or candidate;
- **omission**: behaviour the digest leaves out.

Each sample is judged with the code frozen. Fixes found by a sample are made afterwards, with a regression
test, and measured on the next fresh sample (`--exclude` skips the functions already drawn).

Results: `reports/digest/REPORT.md`.
