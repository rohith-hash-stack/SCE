# K4 diagnosis: absolute imports of a package that is not at the repository root (2026-11-10)

**What this is:** the defect the first golden recording found (`tests/golden/KNOWN_DEFECTS.md` K4), diagnosed before
any fix, as instructed.

**Scripts:** the session scratch area (`k4/k4.py` for the layout per repository; `k4/k4b.py` for the edges marked
external whose target exists once the module's directory prefix is added).

## 1. What triggers it

- **The trigger:** an **absolute** import of the repository's own package, when the package does not sit at the
  repository root. Examples: `src/shelf/` imported as `shelf`; `fastmcp_slim/fastmcp/` imported as `fastmcp`;
  `pydantic_ai_slim/pydantic_ai/` imported as `pydantic_ai`; `src/poetry/` (a namespace package, no `__init__.py`)
  imported as `poetry`.
- **What does not trigger it:**
  - relative imports (`from .core import parse`);
  - a missing `__init__.py` (poetry's namespace layout is affected only through its absolute imports);
  - a package at the root (sqlglot, scrapy, litestar, aiohttp, rich, and others).

## 2. The cause in the code: an import-resolution gap

- **How modules are named:** by their repository-relative path. `src/shelf/core/parse.py` becomes
  `src.shelf.core.parse` (`graph/symbol_table.path_to_module`).
- **Relative imports** resolve from the importing module's own name (`_resolve_relative_module`), so they land on
  `src.shelf.…`.
- **Absolute imports** keep the import's own text (`ConcreteGraphBuilder._python_module_ref_text`, around
  `concrete_builder.py:1208`). `from shelf.core.parse import parse` binds `parse` to `shelf.core.parse.parse`.
- **The result:** that name is not in the symbol table, so the call's target node is added with `external=True`
  (`concrete_builder.py:2762`).

**Two consequences:**
1. **Callers across modules are not linked.** The call graph, blast radius, change context §4, the break check and
   the scope analysis all miss them.
2. **The repository's own functions are classified as third-party.** Under the planned internal/external rule
   (`CHECK_CHANGES_DESIGN.md` §2), the agent would be told "do NOT edit" about the repository's own code. So K4 must
   be fixed before that classification is built.

**Not the cause:**
- **graph linking:** a symbol, once resolved, links correctly;
- **namespace handling as such:** the gap is the missing mapping from an import name to the source root. That mapping
  is what any namespace or src-layout support needs.

## 3. How widespread

**Layout** of the Python repositories with gated tasks:
- **package not at the root:** pydantic-ai, fastmcp, textual, werkzeug, click, marshmallow, packaging, poetry, and
  pydantic's `pydantic-core/python`;
- **package at the root:** sqlglot, scrapy, instructor, more-itertools, aiohttp, multidict, litestar, starlette,
  typer, rich, yarl, tornado, pydantic.

**Measured** (at each clone's HEAD; production calls only; "inside" means the calling code is in the package, not in
`examples/`, `benchmarks/` or scripts):

| Repository | Calls into its own package marked external (inside / outside) | Calls linked across files (inside) | Effect |
|---|---|---|---|
| pydantic-ai | **4,581** / 8 | 6,462 | severe |
| textual | **1,005** / 184 | 815 | severe |
| fastmcp | **973** / 47 | 300 | severe |
| poetry | **291** / 0 | 403 | large |
| marshmallow | **50** / 0 | 10 | large in proportion |
| click | 6 / 1 | 200 | minor (mostly relative imports) |
| packaging | 5 / 17 | 200 | none in the package (the 5 are root scripts: `noxfile`, `tasks/`) |
| werkzeug | 0 / 45 | 303 | none in the package (relative imports) |
| sqlglot (control: package at the root) | 0 / 0 | 4,744 | none |

**So:** the defect is narrow in mechanism but widespread in effect. It strikes repositories that use absolute
self-imports from a non-root package. Those include the two largest contributors to R4's pools (pydantic-ai,
fastmcp).

**JS/TS:** not covered by this diagnosis. A monorepo package importing itself by its published name is the analogous
case, and is not checked here.

## 4. Packaging and Family B's B-13

- **Packaging's package code is not affected.** It imports itself relatively. Its 5 calls marked external come from
  `noxfile.py` and `tasks/`, outside the package.
- **B-13 stands.** Its anchor (`markers._eval_op`) and its caller (`markers._evaluate_markers`) are in the same file.
- **The "caller lists never leave one file" pattern in packaging's tasks** is the code's real structure, not K4.

## 5. Tasks on affected repositories

The repositories counted as affected are pydantic-ai, fastmcp, textual, poetry and marshmallow.

| Pool | Gated passes | On affected repositories |
|---|---|---|
| delete | 71 | **22** (fastmcp 10, pydantic-ai 7, marshmallow 3, textual 1, poetry 1) |
| feature | 23 | **14** (pydantic-ai 7, fastmcp 7) |
| move | 10 | 4 |
| tests | 11 | 2 |
| Family N | 9 | **8** |
| Family B (R3a's 80) | 4 survivors | 0. The 3 poetry tasks (B-11, B-54, B-71) were judged with poetry's callers partly hidden |

**What K4 does and does not invalidate:**
- **The delete, feature, move, tests and N gates do not use the call graph.** They use tests, definitions and name
  matches. Those gated passes stay valid as gated.
- **What K4 affects for those tasks:**
  - the scope check (Layer 1, task selection), which uses the graph;
  - what PRISM would show the agent on them in Sanity 1 (missing callers; "external" internal code).
- **So, after the fix:** re-run the scope check on the affected tasks, then select Sanity 1's sample. The gates need
  no re-run.

**R3a:** its PRISM arm ran with this defect on its 3 poetry tasks. Recorded as a fact; R3a's result and its
interpretation are unchanged.

## 6. The fix (started 2026-11-10)

**The approach:** derive the repository's **import roots**, the directories from which its top-level packages are
imported (`src/`, `fastmcp_slim/`, `pydantic_ai_slim/`, and `src/` for namespace packages like `src/poetry/`).
- **How:** an index of every Python module and directory in the repository, keyed by the name it would be imported
  under from each directory that is not itself a package.
- **Retrying an import:** an absolute import that names no module at the repository root is retried as
  `<root>.<import>`.
- **Scope:** contained to import resolution, deterministic, with no change to module naming.

**The safeguard (strengthened at the user's request, 2026-11-10).** The retry binds only if **all** of these hold:
1. **The module exists** at the prefixed path, a module or a package directory in the repository.
2. **The import's top-level name is not a known external dependency.**
   - **Where dependencies are read:** every `pyproject.toml` in the repository (`[project]` dependencies, optional
     dependencies and dependency groups; `[tool.poetry]` dependencies and groups), `requirements*.txt`, and the
     package names in `uv.lock` and `poetry.lock`.
   - **How names are compared:** normalized (lower case, `-` and `.` as `_`).
   - **A name found in both the internal tree and the dependency list stays external** (the safer default). A local
     `requests.py` therefore never captures the real `requests`.
3. **A refinement found while checking the impact:** a dependency that is itself a distribution **defined inside the
   repository** counts as internal. That is, the `[project].name` or `[tool.poetry].name` of any `pyproject.toml` in
   the repository, which covers uv workspace members and path sources.
   - **Without it, the guard defeats the fix in monorepos.** pydantic-ai's `pydantic_ai_slim` declares
     `pydantic-graph` (a workspace member). fastmcp's example projects declare `fastmcp` itself. Both would stay
     external: no drop for fastmcp's 973, and only a partial drop for pydantic-ai's 4,581 (the calls into
     `pydantic_graph` and `pydantic_evals` would stay hidden).
   - **With the refinement,** the expected drop is unchanged for all five affected repositories. Their packages are
     either repo-local distributions (pydantic-ai, fastmcp) or not declared as dependencies of themselves (textual,
     poetry, marshmallow).
   - **Poetry's** `poetry-core` dependency provides `poetry.core`. It stays external through rule 1: there is no
     `src/poetry/core`.
4. **The prefix is not vendored** (`vendor/`, `third_party/`, `_vendor/`, `site-packages/`, `node_modules/`).
   Vendored code stays external (`PRISM_REQUIRED_BEHAVIOR.md` V1a).
5. **Exactly one root matches.** When several do, `src` is preferred, then the roots outside test, example, doc and
   benchmark directories. If more than one still remains, the import stays unresolved, never guessed.

**Known limit:** a distribution whose import name differs from its distribution name (for example `PyYAML` provides
`yaml`) is not matched by name. A local `yaml.py` would then be preferred over the dependency. Recorded as a limit,
not handled.

**Verification:**
- the K4 golden lines gain the expected callers (`parse`: `cli.main`, `cli.check` through the alias,
  `plugins.load_csv`; `price`: `checkout.total`);
- the measurement in §3 drops to near zero for the five affected repositories;
- the control's numbers (sqlglot) are unchanged.

## 7. JS/TS: the K4 equivalent is not yet checked (K4b)

"JS/TS K4 equivalent: not yet checked. Requires spot-check on 2–3 JS monorepo candidates before R4's JS tasks are gated.
If present, treated as K4b — same fix class, different resolver." (the user, 2026-11-10)

- **The case:** a workspace package importing itself, or a sibling, by its published name (`import { x } from
  "pkg/sub"`) instead of a relative path.
- **The candidates to check:** the JS repositories with gated tasks: zod, hono, nitro, h3, prettier, execa.
- **Status:** not fixed now; noted. R4's JS tasks may be affected.
