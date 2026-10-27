# Change context: end-to-end flow

What PRISM gives an AI before it changes a function (the **anchor**), so the change is made knowing who
depends on the anchor, what the anchor depends on, and how the surrounding code is written.

```
prism impact <symbol> --repo <path> [--budget 12000] [--no-tests] [--json]
MCP tool: prism.change_context(repo_path, symbol, budget_tokens, include_tests, format)
```

## Flow

```
 repository
     |
     v
 [1] index (prism.cli.build_pipeline)
     parse every file (tree-sitter) -> symbols, call graph, entry points
     |
     v
 [2] digest every function it needs (prism.summaries.digest)
     line-cited facts: conditions, returns, raises, catches, state changes,
     calls and their targets, dynamic dispatch - each with a certainty
     |
     +------------------------------+-------------------------------+
     v                              v                               v
 [3] who depends on it          [4] what it depends on         [5] where it lives
     (prism.impact.dependencies)    (same module)                  (prism.impact.groups)
     per caller, at each call:      per callee it calls:           group = its directory:
     - what it does with the        - errors the callee raises     API, dependencies,
       result (ignores, checks,       and whether the anchor       entry points;
       branches, returns, passes)     catches them                 codebase: largest
     - which anchor errors it       - when the callee returns      directories and the
       catches / lets propagate       nothing / None               calls between them
     - arguments, await             - state the callee changes
     |                              |                               |
     +------------------------------+-------------------------------+
     v
 [6] style to follow (prism.impact.style)
     conventions counted in its group (error types, shared helpers, logging,
     naming, async, decorators) + the full code of its 2 closest siblings
     |
     v
 [7] assemble under a token budget (prism.impact.context)
     anchor code + digest, dependency statements, conventions, siblings,
     then callers' code (full; long callers as windows around the call site;
     digests once code no longer fits), then wider impact (transitive callers
     by hop, entry points and tests that reach it)
     |
     v
 [8] the AI reads it, then writes the change with the real code in view
```

## The package, section by section

| section | what it answers | where it comes from |
|---|---|---|
| 1. Codebase | how the repository is organised | directories, call edges between them, entry points |
| 2. Group | what the anchor's area does and exposes | the anchor's directory: API, dependencies, users |
| 3. Anchor | what the anchor does now | full code + digest |
| 4. Who depends on it | what breaks if the result, errors or arguments change | each caller's call site, read from its syntax |
| 5. What it depends on | what the anchor must handle from its callees | callees' digests + the anchor's try/catch |
| 6. Style to follow | how to write code that fits | conventions with counts + 2 sibling functions' code |
| 7. Callers' code | the code that must keep working | callers' real code, budgeted |
| 8. Wider impact | how far a change can reach | transitive callers, entry points, tests |

## Honesty rules carried through

- A caller linked only by a method-name match, with no type, import or `self` to establish the target, is
  marked *possible*, never certain (in the digest and in section 4).
- A convention is reported only with its evidence (n of m), and only from certain call targets.
- Every statement cites a file and line. Anything over the budget is counted under "Omitted", never
  dropped silently.

## Example

`EXAMPLE_flask_find_error_handler.md`: Flask `d086db856be187255b8ec61ef409357393020f32`,
anchor `src.flask.sansio.app.App._find_error_handler`, budget 9000 tokens. The full run, including
indexing, took about 6 s.

## Not yet measured

Whether this package makes an AI's changes more correct has not been tested yet. The planned test uses
real git history: take commits where a function and its callers were changed together, give the AI the
function and the change request with and without this package, and compare which callers it updates, and
how (helpers reused, error types, linter), with what the developer actually did.
