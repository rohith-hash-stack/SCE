# Change-based semantic gold for the four Django t13 blast-radius seeds

_Analysis only: no harness or PRISM code was changed and nothing ran on Kaggle. The pinned Django 4.2.30 checkout was copied into a scratchpad, the probe was applied to the copy, and Django's own test suite was run there. Each arm's stored M4 answers (`reports/harness_m4/django/completions/`, `bundles/`) were scored against the result._

## Why

The t13 gold was curated from `benchmarks.metrics.bccr.compute_direct_and_transitive_callers`, which is PRISM's own `unpacks_return` detector run over PRISM's own call graph. Scoring PRISM on that gold is circular. This experiment builds gold that no engine produced: make the change each task describes and observe what actually depends on it.

## Method

| step | detail |
|---|---|
| change | Each seed's return value is wrapped in a transparent proxy: `reverse` returns a wrapper instead of a `str`; `QuerySet.get` a proxy instead of the instance; `Field.clean` a result object instead of the value; `Options.get_field` a wrapper instead of the Field. These are the changes the t13 prompts describe. The patch is appended at module end, so no line numbers move. |
| probe | The proxy forwards every operation to the real value, so the suite keeps running. It logs, once per site, the first Django-tree frame that depended on the value's shape: attribute access, operators, comparison, hashing, iteration, indexing, call, pickling. `str()`, `repr()`, `format()` and truthiness are treated as benign, because a wrapper with `__str__` survives them. It also logs each frame that called the seed directly. |
| run | Django's full test suite, one test app per process, 4 processes in parallel, 300 s cap per app. Baseline: 16,412 tests, 2 failures (root-permission tests). Under the changes: 16,447 tests per seed; 16,008 for `get_field`, because `model_fields` hit the cap. 605 / 387 / 513 / 145 failing. |
| gold (graph-independent) | Production functions (under `django/`, excluding `django/test/`, not VERIFICATION-role) **observed at runtime calling the seed directly** whose returned value was then used shape-dependently, by themselves or downstream. Symbols are mapped from file/line via PRISM's symbol table; only the names are borrowed, not the call graph. |

Limit: code that Django's tests never execute cannot appear, so the gold is a lower bound on the real blast radius.

## Results

### 1. Under a return-type change, almost every real direct caller is affected

| seed | direct callers observed at runtime | affected (semantic gold) | unaffected |
|---|---|---|---|
| `django.urls.base.reverse` | 24 | **21** | 3 (`AdminSite.password_change`, `sitemaps._get_sitemap_full_url`, `sitemaps.views.index`; they only format the URL) |
| `QuerySet.get` | 30 | **29** | 1 |
| `Field.clean` | 6 | **6** | 0 |
| `Options.get_field` | 87 | **79** | 8 |

### 2. PRISM's call graph is the bottleneck for method seeds

| seed | gold callers present in PRISM's hop-1 caller list | PRISM static callers actually seen calling the seed |
|---|---|---|
| `reverse` (module function) | **21 / 21** | 24 / 43 |
| `QuerySet.get` | **1 / 29** | 1 / 21 |
| `Field.clean` | **3 / 6** | 3 / 3 |
| `Options.get_field` | **29 / 79** | 36 / 42 |

- **`QuerySet.get`.** PRISM's static "callers" are mostly `dict.get`-style calls resolved to `QuerySet.get` by name: `kwargs.get("using")`, `self.options.get("proxy")`, `request.POST.get(...)`. Meanwhile it misses 28 of the 29 real callers, which reach `.get` through managers and dynamic receivers (`Model.objects.get`, `self.get_queryset().get`).
- **Curated t13 gold inherits these false edges.** `django_t13_002` lists `formfield_for_foreignkey` and `emit_post_migrate_signal`, which only call `dict.get`. **0 of its 8 curated callers are in the semantic gold.**
- **`Options.get_field`.** 50 of 79 real callers go through `model._meta.get_field(...)` and similar receivers that PRISM does not resolve.

### 3. PRISM's "binds the return value" flag does not separate real breakers

| seed | gold flagged `unpacks_return` | unaffected flagged |
|---|---|---|
| `reverse` | 13 / 21 | 3 / 3 |
| `QuerySet.get` | 0 / 29 | 0 / 1 |
| `Field.clean` | 2 / 6 | — |
| `Options.get_field` | 19 / 79 | 2 / 8 |

- **It misses inline consumers.** `reverse(...)` used directly in an expression, or passed into code that then calls methods on it, breaks without ever being bound to a local name.
- **On `reverse` it flags all 3 callers that only format the value,** which a change with a working `__str__` does not affect.
- **Curated t13 gold vs reality:** 4/8, 0/8, 1/4 and 5/8 of the curated callers are in the semantic gold.

### 4. No arm captures semantic blast radius

Mean over seeds 42/43/44: recall = gold callers named in the answer; delivered = gold callers present in the retrieved context.

| arm | `reverse` (21) | `QuerySet.get` (29) | `Field.clean` (6) | `get_field` (79) |
|---|---|---|---|---|
| A0 no retrieval | 0.016 | 0.000 | 0.000 | 0.000 |
| A1 RAG | 0.048 | 0.011 | **0.389** | 0.046 |
| A2 packing | 0.111 | 0.023 | 0.000 | 0.000 |
| A3 LSP | 0.000 | 0.000 | 0.333 | 0.000 |
| A4 agent | 0.063 | 0.011 | 0.111 | 0.000 |
| **A5 PRISM** | 0.048 | 0.000 | 0.111 | 0.008 |
| Oracle (delivers curated gold) | **0.175** | 0.000 | 0.167 | **0.046** |

Context delivery is just as low. PRISM delivered 0.048 / 0.000 / 0.222 / 0.008 of the semantic gold. The Oracle, built to deliver the curated gold, delivered 0.190 / 0.000 / 0.167 / 0.063.

## Conclusions

1. **The curated t13 gold is not a reliable semantic gold.** It inherits PRISM's call-graph errors: false `dict.get` edges and missed method callers. Only 10 of its 28 callers are among the callers that actually break.
2. **Under these changes, "affected" ≈ "every real direct caller that runs".** Semantic blast radius here is mostly a **reach and resolution** problem (find every real caller), not a ranking problem.
3. **Design C alone would not make PRISM hold semantic gold.** It walks PRISM's graph, whose hop-1 coverage of the real callers is 21/21, 1/29, 3/6 and 29/79. For method seeds the limit is call resolution on dynamic receivers (`objects.get`, `_meta.get_field`) and name collisions (`dict.get`), before traversal starts.
4. **The return-binding flag** is a weak semantic signal here (13/21 and 19/79 of real breakers) and should not be presented as semantic blast-radius detection.

## Caveats

- **Lower bound.** Only code exercised by Django's tests appears in the gold, and `model_fields` timed out under the `get_field` change (439 tests not run).
- **One change per seed.** A gentler change (e.g. a `str` subclass for `reverse`) would affect fewer callers.
- **Breaks outside the probe.** Breakage through C-level type checks (`json`, `re`, `type(x) is str`) is not logged by the proxy; those sites appear only as test failures.
- **Hop 1 only.** The gold covers direct callers. 2-hop affected callers were measured only within PRISM's own graph, so they are not graph-independent and are not used here.
- **Small n.** `Field.clean` has 6 callers and Express-style small-n caveats apply.
