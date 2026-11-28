# Manual sanity check: Prism on your own test repositories

Branch: `manual-sanity-check/test-framework-support`.

This branch makes Prism usable on test-automation repositories (Playwright/TypeScript UI tests, Robot
Framework + Python API tests). It also adds the MCP tool `prism.blast_radius`, which runs the
blast-radius retrieval the benchmark measured. This guide covers setup, a 30-minute sanity check, and an
objective scoring step.

## What changed

| Problem | Before | Now |
|---|---|---|
| Playwright / Jest / Vitest / Mocha tests | anonymous `test('...', async () => {})` callbacks were not symbols, so page objects had no test callers | every test, hook (`beforeEach`, …) and fixture callback is a symbol, `<module>.<describe>.<title>` (role: test) |
| Playwright fixtures | `async ({ loginPage }) => loginPage.login()` could not be typed | fixture names map to classes via `base.extend<{ loginPage: LoginPage }>` or `use(new LoginPage(page))`, so the call links to `LoginPage.login` |
| TypeScript typed parameters | `function f(lp: LoginPage) { lp.submit() }` was not linked | linked (all TS code, not only tests) |
| `this.page.goto()` inside a method named `goto` | false self-call edge | refused |
| Robot Framework | `.robot` / `.resource` not indexed | test cases and keywords are symbols; keyword calls link to user keywords and Python library methods |
| MCP | `prism.slice` uses a shallow caller search (≤3 callers per step, ≤1.5 hops) | `prism.blast_radius`: every transitive caller up to 6 hops, tests included, plus a budgeted context envelope |

**Robot support covers:**
- `Library` imports by path (`${CURDIR}` too) or by module/class name;
- `Resource` imports, transitive;
- `@keyword("Custom Name")`, `@library`, `ROBOT_AUTO_KEYWORDS = False` and `@not_keyword`;
- embedded arguments (`User ${name} Should Exist`);
- `[Setup]`/`[Teardown]`/`[Template]` and the `Test Setup`/`Test Teardown`/`Test Template` settings;
- `Run Keyword*`, `Run Keyword If … ELSE …`, `Wait Until Keyword Succeeds` and `Repeat Keyword`;
- FOR/IF/WHILE blocks;
- the pipe-separated format.

Standard and external libraries (BuiltIn, Collections, Browser, SeleniumLibrary, RequestsLibrary, …) are
not in your repo, so their keywords are not linked. That is expected.

**Known limits:**
- A keyword reachable only through Robot's search path (not imported in the file) is linked only when its
  name is unique in the repo, and then as a best-effort (tentative) edge.
- Inline `IF cond    Keyword` on one line is skipped. Block `IF … END` is handled.
- Variables in library paths other than `${CURDIR}` fall back to matching the file name.
- Playwright: fixtures declared through `mergeTests`, or in a fixture file that re-exports another
  fixture file's `test`, are not typed. Calls through them stay unlinked.

## Setup (once)

```bash
git clone -b manual-sanity-check/test-framework-support https://github.com/rohith-hash-stack/SCE.git
cd SCE && pip install -e .
```

Register the MCP server with your client. For example, in Claude Code:

```bash
claude mcp add prism -- prism mcp --transport stdio --repo /path/to/your/ui-tests
```

For VS Code, Cline or Roo, see `docs/mcp_setup.md`, using the branch above in the `git+https://...` URL
with `@manual-sanity-check/test-framework-support`. Use one server per repository, or omit `--repo` and
pass `repo_path` in each call.

## Step 0: sanity check (about 30 minutes)

1. **Index.** Run `prism index /path/to/repo` on each repository. Then call `get_graph_status`:
   - symbols should include your tests (Playwright test titles, Robot test case names);
   - the call-edge count should be well above zero.
2. **Pick 3–5 seeds you know well.** A page-object method used by many tests, a rarely used one, a Robot
   user keyword, and a Python library keyword method.
   - Seed names are `<module path with dots>.<Class>.<method>`, for example `pages.LoginPage.LoginPage.login`.
   - Robot keywords become `<module>.<Keyword_Name>`, for example `resources.common.Create_Test_User`.
   - A wrong name returns error `-32002` with close candidates.
3. **Ask for the blast radius.** In your client, ask "use prism.blast_radius on `<seed>`", or call it
   directly. The `callers` list shows every transitive caller with `hop`, `is_test`, `file` and `line`.
4. **Judge each seed:**
   - Are the tests you know use it listed? (recall)
   - Is anything listed that cannot reach it? (precision)
   - Note the pattern behind every miss: a fixture style, a helper wrapper, a keyword imported some
     unusual way, and so on.

If most known callers are missing, stop and send the misses. They point at a construct Prism doesn't read
yet, and that is cheaper to fix than to measure.

## Step 1: objective scoring

### API suite (Robot): gold from `output.xml`

`output.xml` records every keyword each test actually ran, so the tests that ran keyword K are the gold
blast radius of K. It doesn't depend on Prism.

```bash
robot --output output.xml tests/          # run the suites in full
python scripts/robot_blast_radius_check.py --repo /path/to/api-tests --output output.xml \
    --pair "UserApi.Create User=libraries.UserApi.UserApi.create_user" \
    --pair "common.Create Test User=resources.common.Create_Test_User"
```

The output gives recall, precision, and the missed/extra test names for each seed.

**Interpreting precision.** Prism is static, so an `ELSE` branch or a skipped step that did not run in
this execution still counts as a possible caller. Some "extra" entries are expected; check a few by hand
before calling them errors. Use `--pairs file.txt` for 20–30 seeds.

### UI suite (Playwright): gold

There is no equivalent log of which page-object methods each test ran. Two options:

- **Manual gold** (simplest). For each seed, list the tests that use it, either from your knowledge or
  from your IDE's "find references" checked by hand. Compare with `callers`.
- **Coverage gold.** Run tests one at a time with Node coverage (`npx c8 --reporter=json npx playwright
  test path/to.spec.ts:LINE`). A seed's gold is the tests whose coverage includes that method. Tell me
  which output your setup produces and I'll add the converter.

### With vs without Prism (model comparison)

For the same seeds, ask your coding agent "which tests are affected if I change `<seed>`?" twice: once
with the Prism MCP server enabled and once without. Score both answers against the same gold. Record
seed, gold count, Prism recall/precision, baseline recall/precision, and time.

20–30 seeds per repository is enough to see a real difference.

## Caveats

- The benchmark numbers (`reports/harness_r0r1/`) were measured before this branch. This branch also
  changes general TypeScript resolution (typed parameters, the self-call guard), so a re-run would not
  reproduce them exactly.
- The benchmark harness still excludes test code from blast-radius callers, because its gold is
  production callers. Only `prism.blast_radius` includes tests, and its `include_tests=false` reproduces
  the harness behaviour.

## Debugging a miss

To see exactly what Copilot asked Prism, what Prism found and sent, and what Copilot answered, run the
server through the debug logger. See `docs/debug_logger.md`.
