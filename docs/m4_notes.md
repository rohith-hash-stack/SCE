# M4 notes

## Derived caches

Derived caches (`.prism/`) are cleared before each run. The pinned commit is
the source of truth, not the cache.

The M4 Kaggle script must remove `.benchmarks/corpora/<corpus>/.prism/`
before indexing each corpus. For tRPC the corpus root is
`packages/server/src`, so the path is
`.benchmarks/corpora/trpc/packages/server/src/.prism/`.

## T5 task expansion (item 4)

T5 (blast) tasks are derived from each corpus's T2 tasks by
`benchmarks/scripts/derive_t5_from_t2.py` (its docstring has the full rules).
The seed is the T2 seed. The gold affected set is the seed's transitive
production callers:

- **Python:** PRISM's call graph.
- **TypeScript:** typescript-language-server references, the tool Arm 3 uses.

Two independent runs, the second in reverse seed order, become
`annotation_a` and `annotation_b`. The loader's agreement gate therefore
checks determinism: every task agrees at 1.0. Seeds with fewer than 2
production callers are skipped. No T2 task file is modified.

| Corpus | Derived | Existing T5 | Total | Target |
|---|---|---|---|---|
| fastapi | 8 | 0 | 8 | 15 |
| django | 9 | 4 (t13, hand-annotated) | 13 | 15 |
| express | 2 | 0 | 2 | 15 |
| trpc | 15 | 0 | 15 | 15 |

Shortfalls:

- **FastAPI:** many T2 seeds are only invoked by the framework at run time
  (security `__call__`, response `render`), so they have no static callers.
- **Express:** methods are attached at run time (`app.use = function`,
  `res.send = function`) on untyped objects, so tsserver resolves almost no
  call sites.
- **Django:** several seeds (middleware, signals, cache, sessions) are reached
  only through framework dispatch or test code.

Caveats for M4:

- **Large Django gold sets:** 218 (`django_t5_006`), 84, 55 and 39 callers.
  Fractional recall over sets this size is far below what any answer can
  name. No cap is applied; whether to cap is an open decision.
- **Gold source overlaps with arms:** Python gold comes from PRISM's own
  graph (Arm 5 and the Oracle use it), and TypeScript gold from the server
  Arm 3 uses. The existing hand-annotated Django T5 tasks also came from
  PRISM's graph.
