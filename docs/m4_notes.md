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

- **Python:** PRISM's call graph, cross-verified against Pyright
  (`--verify-with-pyright`, below).
- **TypeScript:** typescript-language-server references, the tool Arm 3 uses.

Two independent runs, the second in reverse seed order, become
`annotation_a` and `annotation_b`. The loader's agreement gate therefore
checks determinism: every task agrees at 1.0. Seeds with fewer than 2
production callers are skipped. No T2 task file is modified.

T5 gold sets are capped at 30 symbols. Above this, the task measures
enumeration rather than retrieval. Four derived Django tasks were excluded on
this rule. The rule also excluded one tRPC task, `trpc_t02_018` (47 callers).
Excluded tasks are dropped, not truncated.

| Corpus | Derived | Existing T5 | Total | Excluded: cap | Excluded: Pyright |
|---|---|---|---|---|---|
| fastapi | 8 | 0 | 8 | 0 | 0 |
| django | 4 | 4 (t13, hand-annotated) | 8 | 4 | 1 |
| express | 2 | 0 | 2 | 0 | n/a |
| trpc | 14 | 0 | 14 | 1 | n/a |

Excluded by the cap:

| T2 seed task | Gold size |
|---|---|
| django_t02_004 | 39 |
| django_t02_009 | 55 |
| django_t02_016 | 218 |
| django_t02_019 | 84 |
| trpc_t02_018 | 47 |

Excluded by the Pyright check: `django_t02_015` (`AdminSite.each_context`).
PRISM finds 18 production callers and Pyright 6, a strict subset: 12 only in
PRISM, 0 only in Pyright. The 12 call `self.admin_site.each_context(...)`,
where `admin_site` is an untyped constructor parameter. Pyright cannot type
that receiver; PRISM still connects the call. Every other checked task (8
FastAPI, 4 Django) agrees exactly.

Shortfalls (accepted; T5 runs on all four corpora, stratified by corpus,
with the uneven n disclosed):

- **FastAPI:** many T2 seeds are only invoked by the framework at run time
  (security `__call__`, response `render`), so they have no static callers.
- **Express:** methods are attached at run time (`app.use = function`,
  `res.send = function`) on untyped objects, so tsserver resolves almost no
  call sites.
- **Django:** several seeds (middleware, signals, cache, sessions) are reached
  only through framework dispatch or test code.

### Python gold: cross-verified with Pyright

PRISM is Arm 5, so gold taken from PRISM's graph alone would be
tautological for it. With `--verify-with-pyright`, the same transitive walk
is repeated through Pyright's `textDocument/references` (call sites only,
credited to the enclosing function, method or class). A task is kept only if
both tools find exactly the same production callers.

Callers are compared as definition identities (file, line of the `def`),
not names, because PRISM flattens nested functions where Pyright nests them.
Gold names stay PRISM's, the convention of the T2 gold.

The 4 hand-annotated Django T13 tasks predate this and are not
re-verified. Their gold also came from PRISM's graph (BCCR's caller
detector).

### TypeScript gold: caller rule

A caller is a function, method, constructor or class: tsserver outline kinds
12, 6, 9 and 5, the same kinds as the Python cross-check. These are not
callers:
- local variables and `var`/`let`/`const` bindings (kinds 13, 14);
- object-literal properties (kind 7);
- anonymous callbacks.

A call inside one of those is credited to the innermost enclosing function,
method, constructor or class. Example: `new Layer(...)` in
`var layer = new Layer(...)` inside `use` is credited to `lib.router.use`.
Arrow functions bound to a `const` are reported as functions (kind 12), so
they remain callers.

An earlier derivation credited calls to the innermost named outline entry of
any kind, putting local variables into the gold (`lib.router.use.layer`).
That was a bug in the gold and was fixed before any M4 session. Task counts
did not change (Express 2, tRPC 14); 11 of the 16 TypeScript gold sets did.

Oracle reachability (checked locally: the Oracle's retrieval needs no GPU):
the Oracle delivers 98 of the 101 TypeScript gold names. The other 3 are
real functions, present in tsserver's outline, that PRISM's symbol table,
which the Oracle reads, names differently or lacks:

| Task | Gold name (tsserver) | In PRISM |
|---|---|---|
| express_t5_001 | `lib.router.handle.trim_prefix` | `lib.router.trim_prefix` |
| express_t5_002 | `lib.router.route.Route.all` | `lib.router.route.all` |
| trpc_t5_002 | `core.initTRPC.createTRPCInner.initTRPCInner` | not indexed |

### TypeScript gold: bias disclosed

TypeScript T5 gold is derived from tsserver's references, the same language
server Arm 3 uses. This is a known limitation: Arm 3 has privileged knowledge
of TS T5 gold. Python T5 gold is cross-verified against Pyright; TS gold is
not. This asymmetry is disclosed and treated as a limitation in the paper.

Control metric (`harness/reporting/t5_bias_control.py`): for TypeScript T5
cells, it reports each arm's overlap of retrieved context with the gold
(`uniform_cpi` = |delivered ∩ gold| / |gold|), per arm and per corpus. It
also reports `arm3_ratio`: Arm 3's mean overlap over the mean of Arms 1, 2, 4
and 5.
- **Ratio well above 1:** the bias is measurable.
- **Ratio near 1:** the bias is theoretical.
