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

Gold names follow PRISM's convention where PRISM has the same definition. A
tsserver caller is renamed to PRISM's name when PRISM indexes the same
definition: same file, same leaf name, and PRISM's range contains tsserver's
name line. This is the flattened convention the T2 gold uses
(`lib.router.next`). It renamed two Express names and nothing else:

| Task | Before | After |
|---|---|---|
| express_t5_001 | `lib.router.handle.trim_prefix` | `lib.router.trim_prefix` |
| express_t5_002 | `lib.router.route.Route.all` | `lib.router.route.all` |

### Oracle: decoupled from PRISM's symbol table (T5)

The Oracle is the ceiling, so it must deliver the gold set whatever PRISM
indexes. For T5 its item set is the gold affected set, exactly: no seed and
no context symbols.

- A name PRISM's symbol table has is read from there.
- Any other name is read from its recorded definition location,
  `benchmarks/ground_truth/tasks/<corpus>/gold_locations.json`, written by
  the derivation script. TypeScript locations come from tsserver's outline.
  The pipeline passes the locations to the Oracle only.
- `adapt_oracle` raises `OracleCeilingError` when the delivered symbols
  differ from the gold set, for example a missing location or a budget drop.
- T2 is unchanged: it delivers the whole universe, external dependencies
  included.

Verified locally through the real cell path (retrieve, budget, adapter,
score), with no GPU needed:

| Corpus | Oracle delivered |
|---|---|
| Express | 5/5 |
| tRPC | 96/96 |
| FastAPI | 32/32 |
| Django | 45/45 |

One name comes from its recorded location:
`core.initTRPC.createTRPCInner.initTRPCInner` (tRPC, not indexed by PRISM).
The largest Oracle context is 7,059 tokens, under the 13,000 budget.

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

## M4 results notes

### Oracle T2 ceiling

Oracle T2 mean TSR on FastAPI is 0.931 (not 1.00). On t02_010 the Oracle
delivered all 5 gold bodies on all 3 seeds; the model named 4 of 5, omitting
`fastapi.openapi.utils.generate_operation_summary` every time. Two other
cells miss gold symbols on one seed each (t02_002 seed 43: 3 of 4 missed;
t02_011 seed 44: 1 of 2 missed). This is the model's answer-extraction
ceiling, not a retrieval failure: the bundles show the Oracle delivered every
gold body in all 5 cells. On T5, the `delivered_symbols == gold_affected`
assertion in `adapt_oracle` enforces this on every cell. The strict "name
every gold" T2 rule is unchanged, and no task is excluded.

| Task | Seed | Gold size | Named | Missing | Delivered |
|---|---|---|---|---|---|
| fastapi_t02_002_solve_dependencies_runtime_resolution | 43 | 4 | 1 | `fastapi.dependencies.utils.request_params_to_args`, `fastapi.dependencies.utils.request_body_to_args`, `fastapi.dependencies.utils.solve_generator` | 4/4 |
| fastapi_t02_010_get_openapi_path_operation_metadata | 42 | 5 | 4 | `fastapi.openapi.utils.generate_operation_summary` | 5/5 |
| fastapi_t02_010_get_openapi_path_operation_metadata | 43 | 5 | 4 | `fastapi.openapi.utils.generate_operation_summary` | 5/5 |
| fastapi_t02_010_get_openapi_path_operation_metadata | 44 | 5 | 4 | `fastapi.openapi.utils.generate_operation_summary` | 5/5 |
| fastapi_t02_011_get_fields_from_routes_recursion | 44 | 2 | 1 | `fastapi.dependencies.utils.get_flat_params` | 2/2 |

### T5: prompt-gold gap

T5 gold is transitive closure. The T5 prompt asks what breaks if X changes,
which the model interprets as direct callers. This is a prompt-gold gap:
Oracle T5 on FastAPI is 0.557 despite delivering the full transitive gold set
on every cell. Reported as "direct-caller coverage at transitive-gold
scoring". A transitive-prompt variant is a documented ablation for a
follow-up, not part of M4.

The FastAPI T5 numbers are kept; they are valid under this interpretation.
For the paper: T5 numbers measure direct-caller retrieval against a
transitive-reference gold, which is a conservative lower bound.

### Cross-session drift

Cross-session drift on FastAPI T2 was 50% of same-prompt cells (12 of 24) with
1 TSR change. Prior runs: 2-8%. Under investigation; Django will indicate
whether systemic.

