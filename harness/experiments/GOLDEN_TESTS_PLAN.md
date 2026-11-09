# Golden-output tests, cross-feature tests, suite hygiene: the plan (approved 2026-11-09; not yet built)

**Purpose:** "one implementation should not break another feature of PRISM" (spec R2–R4,
`PRISM_REQUIRED_BEHAVIOR.md` §8).
- Line coverage is already 90.8%, but no test pins what PRISM's tools **return**.
- These tests pin it, on two small repositories built to contain every touch point.

## 1. The two fixture repositories

Two new directories: `tests/fixtures/golden/py_repo/` and `tests/fixtures/golden/ts_repo/`.
- They are small (about 15–25 files each) and hand-written. Each element is listed in the repository's own
  `README.md`, with the requirement it exercises.
- They are never written to: tests copy them to a temporary folder first (§5).

**`py_repo` (Python):**

| Element | Exercises |
|---|---|
| a package with `__init__.py` re-exporting a function (`from .core import parse`), and callers importing it both ways | T2 callers through re-exports; T5 import sites |
| an alias import (`from .core import parse as p`) and a call through it | T2 through an alias |
| a base class with a method, two subclasses overriding it, and calls on the base type | T4 overrides; virtual dispatch |
| a decorator registration (`@register("name")`, collected into a registry dict) and a CLI entry point in `pyproject.toml` | T6 registrations |
| a `.pyi` stub declaring one function | T7 type declarations |
| a `docs/` page naming two functions | T9 docs |
| `tests/` calling three functions directly and one through a helper | T8 tests that reach a function; `include_tests=False` |
| **the D1 trap:** a module function `set_value` and an unrelated class method `Store.set_value`; a third module calls bare `set_value(...)` without importing it | Q1: the bare call must not resolve to the method |
| **the D2 trap:** a function whose `return` embeds a call to a name defined nowhere (`return build(missing_helper(x))`) | Q2: the unresolved call is listed, not dropped |
| a function changed in the working tree (an uncommitted edit adding a required parameter) with an unchanged caller | V2: the break check against the committed version |
| a near-duplicate pair (the same body, identifiers renamed) | N1 (pinned now as "not detected"; changes when N1 is built) |
| raises, empty returns and state changes in the digest | T1, T3 |

**`ts_repo` (TypeScript / JavaScript):**

| Element | Exercises |
|---|---|
| `export function` and `export const f = (…) => {}` and `export const g = async (…) => {}` | the arrow-function parse (the `async` bug); const-bound functions |
| a barrel `index.ts` re-exporting (`export { f } from './a'`, `export * from './b'`) | T5; T2 through barrels |
| `import * as ns` and `ns.f()`; a default export | T2 through namespaces and defaults |
| a class hierarchy with an overridden method and an interface implementation | T4 |
| a `.d.ts` declaration for one module | T7 |
| a route-registration table (`router.get('/x', handler)`) | T6 |
| `*.test.ts` files and one `test-d` type test | T8; the backlog's `test-d` gap |
| **the D1 trap in JS:** a bare call to a name that matches only a class method elsewhere | Q1 |
| **the D2 trap in JS:** an unresolved call embedded in a `return` and in a `Promise.race([...])` (pilot B-02's shape) | Q2 |
| a working-tree edit: an object-destructured parameter replacing positional ones (pilot B-02's shape) | V2 and the arrow-function parse |

## 2. What is pinned

For each repository, a fixed list of queries (about 8–12 per tool) in `tests/golden/queries.json`. Each query's
output is saved as text in `tests/golden/<repo>/<tool>/<name>.txt`.

| Tool | Pinned output | Queries (examples) |
|---|---|---|
| `prism_change_context` | the full rendered text, as an agent receives it | the D1 caller's anchor, the D2 function, the re-exported function, the overridden method, the working-tree-edited function |
| `prism_blast_radius` | the caller list (hop, symbol, file, line, `is_test`) and the envelope, both normalized; with `include_tests` false and true | the same anchors |
| `prism_digest` | the rendered digest | every function with raises, returns, state changes, a D1 or D2 trap |
| `prism_new_function_context` | the rendered text | a new function beside the near-duplicate pair; a new method in the overridden class |
| `prism_ask` | the ranked list (rank, qname, location, score to 4 decimals) | 5 orientation questions |

**Normalization:**
- absolute paths become repository-relative;
- the temporary-folder prefix is removed;
- floats are rounded;
- nothing time-dependent is included.

**The tokenizer must be exact.** Golden tests **skip, with a reason**, when tiktoken's real encoding is
unavailable. Otherwise the saved outputs would depend on the machine, as Kaggle and this sandbox differ today. This
makes committing the encoding (`DRIFT_INVESTIGATION.md`, option 2) a prerequisite.

**Known-wrong outputs are pinned as they are, and marked.**
- The first recording captures today's behaviour, including D1's wrong fact and D2's silence. A sidecar
  `tests/golden/KNOWN_DEFECTS.md` lists which saved lines are wrong and why.
- The correctness fixes then change exactly those lines, and the diff shows it.

## 3. How updates work

- `pytest tests/golden` compares each output with its saved file. A mismatch fails with a unified diff.
- `pytest tests/golden --update-golden` rewrites the saved files. The rewritten files are committed **in the same
  commit** as the code change that caused them, with a line in the commit message per changed golden ("golden: …
  why").
- A test fails if a query has no saved file, or a saved file has no query, so nothing goes stale silently.
- `tests/golden/KNOWN_DEFECTS.md` is updated when a fix lands: the defect is removed, and the golden shows the
  corrected output.

## 4. Cross-feature tests (on the same fixtures)

| Invariant | Test |
|---|---|
| The tools agree on callers | for each anchor, `change_context`'s direct callers (§4) equal `blast_radius`'s hop-1 callers, with the same `include_tests` |
| Formatting never turns "possible" into "certain" | every `possible` or `unknown` digest fact is rendered with `one of`, `?` or "possible target", never as a bare `-> X`; checked over every digest and change context |
| No test code leaks when tests are excluded | with `include_tests=False`, no caller, helper or node in any tool's output is in a test file, unless the anchor is itself test code |
| The index rebuilt after edits equals a fresh build | apply a scripted edit sequence to a temporary copy (add a caller, change a signature, delete a function, add a file). After each step, the live index's answers to every pinned query equal a cold build's, i.e. E1–E3 on the golden fixtures |

## 5. Suite hygiene (alongside)

- **Environment skips:** the 15 tests needing `transformers` get `pytest.importorskip("transformers")` (or a skip
  marker with the reason), so they skip rather than fail where it is not installed.
- **Fixture caches to temporary folders:**
  - tests that index `tests/fixtures/*` write `.prism/cache/*.db` into the fixture itself today;
  - a shared `conftest.py` fixture will copy a fixture repository to `tmp_path` before indexing;
  - the `.prism/` directories already under `tests/fixtures/` are removed from version control and added to
    `.gitignore`.
- **The 5 drift failures:** as decided after `DRIFT_INVESTIGATION.md`.

## 6. Order of work

1. **The tokenizer encoding** made available (needs your choice among the three options in
   `DRIFT_INVESTIGATION.md`). Golden outputs are recorded only with the exact tokenizer.
2. **Hygiene** (§5), so the suite's baseline is clean.
3. **The two fixture repositories and the golden recording** of current behaviour, with `KNOWN_DEFECTS.md`.
4. **Cross-feature tests** (§4).
5. **The correctness fixes** (D1, D2, the break check), each with its unit tests. The goldens change only on the
   lines `KNOWN_DEFECTS.md` names. Any other golden change is a regression to explain.

**Estimate *(guess)*:** steps 2–4 about 3–4 days; step 5 about 2–3 days, with re-verification (E1–E3, the
blast-radius test-code check, dry runs).
