# PRISM (Arm 5) on T5: diagnosis from the M4 artifacts

_Artifacts: `reports/harness_m4/<corpus>/bundles/{arm2,arm5,oracle}_*t5*`, `cells.parquet`, task YAMLs; commit `23dfa3f`. Gold = the task's `pipeline_symbols` as loaded by `harness.tasks.loaders.load_tasks` (what the scorer used). No code was modified and nothing was re-run. Express T5 has n=2 tasks (6 cells): anecdotal._

**Bottom line.** PRISM's T5 shortfall is a retrieval problem, and it is structural: PRISM's candidate manifest admits at most 3 **direct** callers of the seed, while 54% of the T5 gold (86 of 158 symbols with hop data) (transitive callers) is 2–5 hops upstream. Of what it does deliver, PRISM names gold better than any other arm (81–100% vs the Oracle's 51–80%).

## 1. Did the gold reach the model?

gold_coverage = |delivered symbols ∩ gold| / |gold| (identical to the stored `uniform_cpi` on all 672 T5 cells). Naming rates use the scorer's own matcher (`names_symbol`) on the stored answers. T5 TSR is itself the fractional answer recall, so `answer_gold_recall` is not stored for T5 cells (it equals TSR).

| corpus | arm | cells | gold_coverage | TSR | named \| delivered | named \| not delivered | delivered tokens |
|---|---|---|---|---|---|---|---|
| fastapi | arm5 | 24 | 0.310 | 0.281 | 0.844 (27/32) | 0.016 (1/64) | 2780 |
| fastapi | arm2 | 24 | 0.615 | 0.418 | 0.588 (30/51) | 0.000 (0/45) | 12257 |
| fastapi | arm1 | 24 | 0.607 | 0.495 | 0.815 (44/54) | 0.000 (0/42) | 12306 |
| fastapi | oracle | 24 | 1.000 | 0.557 | 0.510 (49/96) | 0.000 (0/0) | 2867 |
| django | arm5 | 24 | 0.195 | 0.190 | 0.808 (21/26) | 0.009 (1/109) | 4639 |
| django | arm2 | 24 | 0.432 | 0.279 | 0.487 (19/39) | 0.000 (0/96) | 12083 |
| django | arm1 | 24 | 0.369 | 0.230 | 0.611 (22/36) | 0.000 (0/99) | 10587 |
| django | oracle | 24 | 1.000 | 0.693 | 0.637 (86/135) | 0.000 (0/0) | 1718 |
| express (n=2) | arm5 | 6 | 0.250 | 0.250 | 1.000 (3/3) | 0.000 (0/12) | 1306 |
| express (n=2) | arm2 | 6 | 0.750 | 0.806 | 0.833 (10/12) | 0.667 (2/3) | 12638 |
| express (n=2) | arm1 | 6 | 0.750 | 0.361 | 0.417 (5/12) | 0.000 (0/3) | 12650 |
| express (n=2) | oracle | 6 | 1.000 | 0.833 | 0.800 (12/15) | 0.000 (0/0) | 1027 |
| trpc | arm5 | 42 | 0.127 | 0.151 | 0.938 (30/32) | 0.043 (11/256) | 1678 |
| trpc | arm2 | 42 | 0.758 | 0.262 | 0.338 (70/207) | 0.000 (0/81) | 12730 |
| trpc | arm1 | 42 | 0.629 | 0.252 | 0.372 (58/156) | 0.000 (0/132) | 12975 |
| trpc | oracle | 42 | 1.000 | 0.570 | 0.524 (151/288) | 0.000 (0/0) | 2731 |

| corpus | PRISM cells | cells with coverage 0 | cells with coverage ≥ 0.5 | Pearson(coverage, TSR) | Spearman |
|---|---|---|---|---|---|
| fastapi | 24 | 6 | 7 | 0.888 | 0.862 |
| django | 24 | 10 | 5 | 0.791 | 0.806 |
| express | 6 | 3 | 3 | 1.000 | 1.000 |
| trpc | 42 | 20 | 4 | 0.854 | 0.975 |

**Verdict: retrieval, not generation.** PRISM's gold coverage (0.310 / 0.195 / 0.250 / 0.127) is the lowest of the retrieval arms shown, and its TSR tracks coverage almost one-for-one (Pearson 0.79–0.89; Express 1.0 on 6 cells). When gold is delivered, PRISM names it 84% / 81% / 100% / 94% of the time, higher than Arm 2 (59% / 49% / 83% / 34%) and the Oracle (51% / 64% / 80% / 52%). Gold that is not delivered is almost never named (2 of 441 PRISM cases). Per-cell values for every PRISM T5 cell are in the appendix.

## 2. Direction: upstream vs downstream

**Missing artifact (partial skip).** The bundles carry no call graph, so per-symbol hop distances (upstream 1/2/3/4+, unrelated) cannot be computed without rebuilding PRISM's graph, which is out of scope. What *is* recorded: each PRISM item's own `provenance.role` from `_classify_role` (`src/prism/packer/submodular_knapsack.py`): `caller` = a direct (1-hop) upstream caller, `callee` = a direct downstream callee, `transitive` = any other non-seed node (downstream at ≥2 hops, or the class that contains a member). For the gold, the derived-task YAMLs record `N direct, M transitive (up to K hops)`, and every gold symbol is upstream by definition (the seed's transitive callers).

| corpus | PRISM items | seed | caller (up, 1 hop) | callee (down, 1 hop) | transitive (down ≥2 / container) | upstream share of non-seed items | caller items that are gold |
|---|---|---|---|---|---|---|---|
| fastapi | 195 | 24 | 34 | 74 | 63 | 19.9% | 29/34 |
| django | 243 | 24 | 50 | 81 | 88 | 22.8% | 26/50 |
| express | 42 | 6 | 3 | 24 | 9 | 8.3% | 3/3 |
| trpc | 226 | 42 | 38 | 112 | 34 | 20.7% | 32/38 |

| corpus | T5 tasks | gold symbols | 1 hop (direct) | ≥2 hops (transitive) | deepest | tasks without hop data | PRISM ceiling (≤3 direct per task) | PRISM actual coverage |
|---|---|---|---|---|---|---|---|---|
| fastapi | 8 | 32 | 17 | 15 | 3 | 0 (0 gold) | 0.474 | 0.310 |
| django | 8 | 45 | 17 | 8 | 3 | 3 (20 gold) | 0.632 | 0.195 |
| express | 2 | 5 | 5 | 0 | 1 | 0 (0 gold) | 1.000 | 0.250 |
| trpc | 14 | 96 | 33 | 63 | 5 | 0 (0 gold) | 0.389 | 0.127 |

Across the 29 tasks with hop data, 86 of 158 gold symbols (54%) are ≥2 hops upstream. Django's `t13_002–004` are curated return-binding sets whose headers give no direct/transitive split.

Distance constants (quoted):

- `src/prism/packer/candidate_index.py:57`: `CANDIDATE_INDEX_MAX_HOPS = 3.0` (downstream manifest horizon)
- `src/prism/packer/submodular_knapsack.py:321`: `DEFAULT_UPSTREAM_MAX_HOPS = 1.5`. Its docstring says: "Upstream candidates are restricted to direct (1-hop) callers whose own `dist_w_upstream = 1/W_upstream(u, s)` falls at or below this"
- `src/prism/packer/submodular_knapsack.py:424`: `UPSTREAM_FRONTIER_CAP = 3`, reused by `build_candidate_manifest`: "the top `UPSTREAM_FRONTIER_CAP` are admitted unconditionally"
- `src/prism/packer/submodular_knapsack.py:314`: `DEFAULT_MAX_HOPS = 6.0` (packer)
- `src/prism/packer/blast_radius.py:112`: `compute_upstream_callers` returns "every direct (`CALLS`/`INSTANTIATES`) predecessor of `seed_id`"; there is no transitive upstream walk

**Mechanism.** Gold is 100% upstream, with 54% at ≥2 hops. PRISM's non-seed delivery is 77–92% downstream, and its only upstream channel is ≤3 direct callers per cell. Even a perfect pick of 3 direct callers caps coverage at 0.474 (FastAPI), 0.389 (tRPC) and 0.632 (the Django tasks with hop data). The actual coverage is lower: 0.309, 0.127 and 0.244. When PRISM does go upstream it is precise: 29/34, 26/50, 3/3 and 32/38 of its caller items are gold. In Django, 10 of the 50 caller slots went to test functions, and the gold excludes test code.

## 3. Turn-1 manifest content

**Missing artifact (partial skip).** Bundles record the manifest *size* (`manifest_candidates`) and Turn 1's `requested_symbols`, but not the manifest text, so the manifest's own upstream/downstream split cannot be counted. What can be counted: the role of each item Turn 1 requested, and of each item Turn 2 auto-added.

| corpus | cells | manifest size (mean) | requested (mean) | requested gold (mean) | gold size (mean) | requested by role: caller / callee / transitive | auto-added in Turn 2: caller / downstream | Turn 1 parsed OK | degenerate |
|---|---|---|---|---|---|---|---|---|---|
| fastapi | 24 | 11.1 | 8.9 | 1.29 | 4.0 | 34 / 57 / 45 | 0 / 35 | 100% | 0 |
| django | 24 | 18.6 | 4.5 | 1.08 | 5.6 | 50 / 18 / 18 | 0 / 133 | 100% | 0 |
| express | 6 | 9.5 | 8.0 | 0.50 | 2.5 | 3 / 24 / 9 | 0 / 0 | 100% | 0 |
| trpc | 42 | 8.0 | 5.4 | 0.76 | 6.9 | 38 / 92 / 29 | 0 / 25 | 100% | 0 |

Turn-1 system prompt, verbatim (`benchmarks/run_two_pass_benchmark.py:325`, `TURN1_SYSTEM_PROMPT`; `prism_turn1_strict_prompt` = false and `prism_manifest_strict` = false on every cell, so it is used unmodified):

```text
You are a senior software engineer investigating a codebase. You will be given a compact <candidate_index> - every symbol reachable from a seed function, one per line as qualified_name|role|kind|signature|calls=[...] (role is one of seed/callee/caller/transitive; signature is the symbol's own raw declaration line; calls lists the names it directly invokes in its own body, deterministically extracted, never a docstring or comment) - followed by a real task. Examine the symbol signatures and their direct call targets to trace the complete causal execution path from the seed to termination. Request all necessary intermediate and helper symbols required to form an unbroken execution chain. Only name symbols that appear in the index - never invent one.
```

Turn-1 user prompt template (`_turn1_user_prompt`, same file:491), where `{task}` is the T5 prompt, e.g. "What breaks if `fastapi.dependencies.utils.get_dependant` changes?":

```text
<candidate_index>…</candidate_index>

Task:
{task}

Respond with a JSON object: {"thought_process": "1-2 sentences on why", "requested_symbols": ["qualified.name", ...]} - requested_symbols ordered seed first, then the causal stages in execution order, using each symbol's own full qualified_name exactly as given in the index (never a bare name from a calls=[...] list). Respond with this JSON object and nothing else.
```

The prompt never mentions callers, dependents, or "what breaks". It asks the model to "trace the complete causal execution path from the seed to termination" and to "form an unbroken execution chain" ordered "in execution order": a downstream framing. The only upstream cue is that `caller` is one of the listed roles. Turn 2 reinforces this: `harness/arms/arm5_prism.py` calls `retrieve_requested(..., task_type="debug")` for T5 too, and `PrismEngine.retrieve_requested` auto-includes the seed's direct callees. 193 of the downstream items delivered on T5 came from that auto-inclusion, and no callers were auto-added. All 125 caller items PRISM delivered were requested by Turn 1, and in its own `thought_process` the model consistently restates the downstream chain (see the §4 examples).

## 4. What Arm 2 (Priompt) delivers that PRISM doesn't

### django: `django_t5_001_form_clean_validation`, seed 42

Seed `django.forms.forms.BaseForm.full_clean` in `django/forms/forms.py`. Gold (2): `django.forms.forms.BaseForm.errors`, `django.forms.formsets.BaseFormSet.management_form`.  
Arm 2: 82 items, 12933 tokens, 2/2 gold delivered, TSR 1.000. PRISM: 15 items, 2027 tokens, manifest 17, 0/2 gold delivered, TSR 0.000.

| arm | rank | source_id | symbols | role | gold | mentions seed name | content (first 80 chars) |
|---|---|---|---|---|---|---|---|
| Arm 2 | 1 | `django/forms/forms.py:420-435` | django.forms.forms.BaseForm.full_clean | — |  | yes | `class BaseForm(RenderableFormMixin):⏎    def full_clean(self):⏎        """⏎     ` |
| Arm 2 | 2 | `django/forms/forms.py:414-418` | django.forms.forms.BaseForm.has_error | — |  |  | `class BaseForm(RenderableFormMixin):⏎    def has_error(self, field, code=None):⏎` |
| Arm 2 | 3 | `django/forms/forms.py:437-451` | django.forms.forms.BaseForm._clean_fields | — |  |  | `class BaseForm(RenderableFormMixin):⏎    def _clean_fields(self):⏎        for na` |
| Arm 2 | 4 | `django/forms/forms.py:363-412` | django.forms.forms.BaseForm.add_error | — |  |  | `class BaseForm(RenderableFormMixin):⏎    def add_error(self, field, error):⏎    ` |
| Arm 2 | 22 | `django/forms/forms.py:192-197` | django.forms.forms.BaseForm.errors | — | **yes** | yes | `class BaseForm(RenderableFormMixin):⏎    @property⏎    def errors(self):⏎       ` |
| Arm 2 | 57 | `django/forms/formsets.py:146-169` | django.forms.formsets.BaseFormSet.management_form | — | **yes** | yes | `class BaseFormSet(RenderableFormMixin):⏎    @cached_property⏎    def management_` |
| PRISM | 1 | `django/forms/forms.py:56-530` | django.forms.forms.BaseForm | transitive |  |  | `class BaseForm(RenderableFormMixin):⏎    ...` |
| PRISM | 2 | `django/forms/forms.py:420-435` | django.forms.forms.BaseForm.full_clean | seed |  | yes | `    def full_clean(self):⏎        """⏎        Clean all of self.data and populat` |
| PRISM | 3 | `tests/forms_tests/tests/test_forms.py:83-4598` | tests.forms_tests.tests.test_forms.FormsTestCase | transitive |  |  | `class FormsTestCase(SimpleTestCase):⏎    ...` |
| PRISM | 4 | `tests/forms_tests/tests/test_forms.py:3637-3673` | tests.forms_tests.tests.test_forms.FormsTestCase.test_multivalue_field | caller |  | yes | `    def test_multivalue_field_validation(self):⏎        def bad_names(value):⏎  ` |
| PRISM | 5 | `django/forms/forms.py:437-451` | django.forms.forms.BaseForm._clean_fields | callee |  |  | `    def _clean_fields(self):⏎        for name, bf in self._bound_items():⏎      ` |
| PRISM | 6 | `django/forms/fields.py:1016-1038` | django.forms.fields.ComboField | transitive |  |  | `class ComboField(Field):⏎    """⏎    A Field whose clean() method calls multiple` |
| PRISM | 7 | `django/forms/fields.py:1030-1038` | django.forms.fields.ComboField.clean | transitive |  |  | `    def clean(self, value):⏎        ...` |
| PRISM | 8 | `django/forms/fields.py:617-686` | django.forms.fields.FileField | transitive |  |  | `class FileField(Field):⏎    widget = ClearableFileInput⏎    default_error_messag` |
| PRISM | 9 | `django/forms/fields.py:661-680` | django.forms.fields.FileField.clean | transitive |  |  | `    def clean(self, data, initial=None):⏎        ...` |
| PRISM | 10 | `django/forms/forms.py:363-412` | django.forms.forms.BaseForm.add_error | transitive |  |  | `    def add_error(self, field, error):⏎        ...` |
| PRISM | 11 | `django/core/exceptions.py:133-236` | django.core.exceptions.ValidationError | transitive |  |  | `class ValidationError(Exception):⏎    ...` |
| PRISM | 12 | `django/forms/forms.py:453-460` | django.forms.forms.BaseForm._clean_form | callee |  |  | `    def _clean_form(self):⏎        try:⏎            cleaned_data = self.clean()⏎` |
| PRISM | 13 | `django/forms/forms.py:462-467` | django.forms.forms.BaseForm._post_clean | callee |  |  | `    def _post_clean(self):⏎        """⏎        An internal hook for performing a` |
| PRISM | 14 | `django/forms/forms.py:478-480` | django.forms.forms.BaseForm.has_changed | callee |  |  | `    def has_changed(self):⏎        """Return True if data differs from initial."` |
| PRISM | 15 | `django/forms/utils.py:110-135` | django.forms.utils.ErrorDict | callee |  |  | `class ErrorDict(dict, RenderableErrorMixin):⏎    """⏎    A collection of errors ` |

### express: `express_t5_002_path_to_regexp_alias_mismatch`, seed 43 (n=2 corpus)

Seed `lib.router.layer.Layer` in `lib/router/layer.js`. Gold (3): `lib.router.route`, `lib.router.route.all`, `lib.router.use`.  
Arm 2: 59 items, 12718 tokens, 3/3 gold delivered, TSR 1.000. PRISM: 2 items, 194 tokens, manifest 2, 0/3 gold delivered, TSR 0.000.

| arm | rank | source_id | symbols | role | gold | mentions seed name | content (first 80 chars) |
|---|---|---|---|---|---|---|---|
| Arm 2 | 1 | `lib/router/layer.js:33-50` | lib.router.layer.Layer | — |  | yes | `function Layer(path, options, fn) {⏎  if (!(this instanceof Layer)) {⏎    return` |
| Arm 2 | 2 | `lib/router/layer.js:1-31` |  | — |  | yes | `/*!⏎ * express⏎ * Copyright(c) 2009-2013 TJ Holowaychuk⏎ * Copyright(c) 2013 Rom` |
| Arm 2 | 3 | `lib/router/layer.js:52-60` |  | — |  |  | `/**⏎ * Handle the error for the layer.⏎ *⏎ * @param {Error} error⏎ * @param {Req` |
| Arm 2 | 4 | `lib/router/layer.js:62-75` | lib.router.layer.handle_error | — |  | yes | `Layer.prototype.handle_error = function handle_error(error, req, res, next) {⏎  ` |
| Arm 2 | 16 | `lib/router/index.js:502-515` | lib.router.route | — | **yes** | yes | `proto.route = function route(path) {⏎  var route = new Route(path);⏎⏎  var layer` |
| Arm 2 | 17 | `lib/router/route.js:184-204` | lib.router.route.all | — | **yes** | yes | `Route.prototype.all = function all() {⏎  var handles = flatten(slice.call(argume` |
| Arm 2 | 19 | `lib/router/index.js:439-487` | lib.router.use | — | **yes** | yes | `proto.use = function use(fn) {⏎  var offset = 0;⏎  var path = '/';⏎⏎  // default` |
| PRISM | 1 | `lib/router/layer.js:33-50` | lib.router.layer.Layer | seed |  | yes | `function Layer(path, options, fn) {⏎  if (!(this instanceof Layer)) {⏎    return` |
| PRISM | 2 | `lib/router/layer.js:17-17` | lib.router.layer.debug | callee |  |  | `var debug = require('debug')('express:router:layer');` |

### fastapi: `fastapi_t5_007_openapi_security_definitions_serialization`, seed 42

Seed `fastapi.openapi.utils.get_openapi_security_definitions` in `fastapi/openapi/utils.py`. Gold (3): `fastapi.applications.FastAPI.openapi`, `fastapi.openapi.utils.get_openapi`, `fastapi.openapi.utils.get_openapi_path`.  
Arm 2: 59 items, 12949 tokens, 3/3 gold delivered, TSR 1.000. PRISM: 2 items, 1867 tokens, manifest 4, 0/3 gold delivered, TSR 0.000.

| arm | rank | source_id | symbols | role | gold | mentions seed name | content (first 80 chars) |
|---|---|---|---|---|---|---|---|
| Arm 2 | 1 | `fastapi/openapi/utils.py:78-92` | fastapi.openapi.utils.get_openapi_security_definitions | — |  | yes | `def get_openapi_security_definitions(⏎    flat_dependant: Dependant,⏎) -> Tuple[` |
| Arm 2 | 2 | `fastapi/openapi/utils.py:1-75` |  | — |  |  | `import http.client⏎import inspect⏎import warnings⏎from typing import Any, Dict, ` |
| Arm 2 | 3 | `fastapi/openapi/utils.py:95-167` | fastapi.openapi.utils._get_openapi_operation_parameters | — |  |  | `def _get_openapi_operation_parameters(⏎    *,⏎    dependant: Dependant,⏎    sche` |
| Arm 2 | 4 | `fastapi/openapi/utils.py:170-204` | fastapi.openapi.utils.get_openapi_operation_request_body | — |  |  | `def get_openapi_operation_request_body(⏎    *,⏎    body_field: Optional[ModelFie` |
| Arm 2 | 8 | `fastapi/openapi/utils.py:265-274#part1of3` | fastapi.openapi.utils.get_openapi_path | — | **yes** |  | `def get_openapi_path(⏎    *,⏎    route: routing.APIRoute,⏎    operation_ids: Set` |
| Arm 2 | 9 | `fastapi/openapi/utils.py:275-442#part2of3` | fastapi.openapi.utils.get_openapi_path | — | **yes** | yes | `def get_openapi_path(⏎    *,⏎    route: routing.APIRoute,⏎    operation_ids: Set` |
| Arm 2 | 10 | `fastapi/openapi/utils.py:443-443#part3of3` | fastapi.openapi.utils.get_openapi_path | — | **yes** |  | `def get_openapi_path(⏎    *,⏎    route: routing.APIRoute,⏎    operation_ids: Set` |
| Arm 2 | 12 | `fastapi/openapi/utils.py:493-568#part1of2` | fastapi.openapi.utils.get_openapi | — | **yes** |  | `def get_openapi(⏎    *,⏎    title: str,⏎    version: str,⏎    openapi_version: s` |
| Arm 2 | 13 | `fastapi/openapi/utils.py:569-569#part2of2` | fastapi.openapi.utils.get_openapi | — | **yes** |  | `def get_openapi(⏎    *,⏎    title: str,⏎    version: str,⏎    openapi_version: s` |
| Arm 2 | 33 | `fastapi/applications.py:966-996` | fastapi.applications.FastAPI.openapi | — | **yes** |  | `class FastAPI(Starlette):⏎    def openapi(self) -> Dict[str, Any]:⏎        """⏎ ` |
| PRISM | 1 | `fastapi/openapi/utils.py:78-92` | fastapi.openapi.utils.get_openapi_security_definitions | seed |  | yes | `def get_openapi_security_definitions(⏎    flat_dependant: Dependant,⏎) -> Tuple[` |
| PRISM | 2 | `fastapi/encoders.py:102-343` | fastapi.encoders.jsonable_encoder | callee |  |  | `def jsonable_encoder(⏎    obj: Annotated[⏎        Any,⏎        Doc(⏎            ` |

### trpc: `trpc_t5_013_deprecated_procedure_migration`, seed 42

Seed `deprecated.interop.migrateProcedure` in `deprecated/interop.ts`. Gold (2): `deprecated.interop.migrateRouter`, `deprecated.router.Router.interop`.  
Arm 2: 45 items, 12546 tokens, 2/2 gold delivered, TSR 1.000. PRISM: 7 items, 2395 tokens, manifest 11, 0/2 gold delivered, TSR 0.000.

| arm | rank | source_id | symbols | role | gold | mentions seed name | content (first 80 chars) |
|---|---|---|---|---|---|---|---|
| Arm 2 | 1 | `deprecated/interop.ts:171-199` | deprecated.interop.migrateProcedure | — |  | yes | `function migrateProcedure<⏎  TProcedure extends AnyOldProcedure,⏎  TType extends` |
| Arm 2 | 2 | `deprecated/interop.ts:71-169#part2of2` |  | — |  |  | `export type MigrateRouter<⏎  TInputContext extends Record<string, any>,⏎  TConte` |
| Arm 2 | 3 | `deprecated/interop.ts:200-232` | deprecated.interop.migrateRouter | — | **yes** | yes | `export function migrateRouter<TOldRouter extends AnyOldRouter>(⏎  oldRouter: TOl` |
| Arm 2 | 4 | `deprecated/interop.ts:1-69#part1of2` |  | — |  |  | `/* eslint-disable @typescript-eslint/ban-types */⏎import type { ProcedureParams,` |
| Arm 2 | 14 | `deprecated/router.ts:912-926` | deprecated.router.Router.interop | — | **yes** |  | `export class Router<⏎  TInputContext extends Record<string, any>,⏎  TContext ext` |
| PRISM | 1 | `deprecated/interop.ts:171-199` | deprecated.interop.migrateProcedure | seed |  | yes | `function migrateProcedure<⏎  TProcedure extends AnyOldProcedure,⏎  TType extends` |
| PRISM | 2 | `core/internals/getParseFn.ts:49-56` | core.internals.getParseFn.getParseFnOrPassThrough | callee |  |  | `export function getParseFnOrPassThrough<TType>(⏎  procedureParser: Parser \| unde` |
| PRISM | 3 | `core/internals/procedureBuilder.ts:207-282` | core.internals.procedureBuilder.createBuilder | callee |  |  | `export function createBuilder<TConfig extends AnyRootConfig>(⏎  initDef: Partial` |
| PRISM | 4 | `core/middleware.ts:227-257` | core.middleware.createInputMiddleware | callee |  |  | `export function createInputMiddleware<TInput>(parse: ParseFn<TInput>) {⏎  const ` |
| PRISM | 5 | `core/middleware.ts:262-285` | core.middleware.createOutputMiddleware | callee |  |  | `export function createOutputMiddleware<TOutput>(parse: ParseFn<TOutput>) {⏎  con` |
| PRISM | 6 | `deprecated/internals/procedure.ts:101-281` | deprecated.internals.procedure.Procedure | transitive |  |  | `export class Procedure<⏎  TInputContext,⏎  TContext,⏎  TMeta,⏎  TInput,⏎  TParse` |
| PRISM | 7 | `deprecated/internals/procedure.ts:137-145` | deprecated.internals.procedure.Procedure._def | callee |  |  | `  public _def() {⏎    return {⏎      middlewares: this.middlewares,⏎      resolv` |

Arm 2's gold items across **all** T5 cells, compared with the seed:

| corpus | Arm 2 gold-bearing items | same file as seed | same directory | content mentions the seed's name | mean rank |
|---|---|---|---|---|---|
| fastapi | 72 | 75% | 79% | 33% | 16.9 |
| django | 39 | 62% | 69% | 85% | 61.0 |
| express | 18 | 50% | 100% | 100% | 10.2 |
| trpc | 189 | 19% | 21% | 41% | 22.0 |

In all four pairs PRISM delivers **zero** gold and Arm 2 delivers **all** of it. PRISM spends its slots on the seed's downstream chain: `_clean_fields`, `ErrorDict` (Django); `debug` (Express); `jsonable_encoder` (FastAPI); `createBuilder`, `createInputMiddleware` (tRPC). Arm 2 packs the seed's own file and neighbourhood densely (45–82 items, ~12.5k tokens), so the callers arrive as a side effect of locality. Most of its gold items share the seed's directory (FastAPI 79%, Express 100%), and many share its file. Lexical mention of the seed name also helps in Django (85%) and Express (100%), less in FastAPI (33%) and tRPC (41%). On tRPC neither locality (21% same directory) nor the seed's name explains most of Arm 2's gold, and Arm 2's own naming rate there is low (34%), so its tRPC win is narrow (0.262 vs 0.151). PRISM's set does not include the seed file's siblings unless they are graph neighbours: on Express `t5_002` the manifest had 2 entries (the seed and `debug`), so none of `Layer`'s 3 real callers was a candidate at all. On Django `t5_001`, the single caller slot that was used went to a test method (`test_multivalue_field_validation`).

## 5. Third-party noise

| corpus | PRISM T5 cells | items | role `external` | items with a node_modules / site-packages path | Turn 2b (external hydration) triggered | external symbols requested |
|---|---|---|---|---|---|---|
| fastapi | 24 | 195 | 0 | 0 | 0 | 0 |
| django | 24 | 243 | 0 | 0 | 0 | 0 |
| express | 6 | 42 | 0 | 0 | 0 | 0 |
| trpc | 42 | 226 | 0 | 0 | 0 | 0 |

PRISM delivered **no** third-party symbols on any T5 cell: Turn 2b only runs for tasks that declare `root_imports`, and no T5 task does. The examples in the request (`etag.index.etag`, `shared.createProxy.createFlatProxy`) are T2 Oracle boundary/context symbols, not PRISM T5 deliveries. The only crowding visible in PRISM's upstream slots is test code: 10 of 50 caller items in Django.

## 6. Oracle comparison: retrieval vs generation loss

Total loss = Oracle TSR − PRISM TSR. Retrieval loss ≈ (1 − PRISM gold_coverage) × Oracle TSR (the approximation requested). Generation loss = total − retrieval.

| corpus | Oracle TSR | PRISM TSR | total loss | PRISM coverage | retrieval loss | generation loss | PRISM naming rate on delivered gold | Oracle naming rate |
|---|---|---|---|---|---|---|---|---|
| fastapi | 0.557 | 0.281 | 0.276 | 0.310 | 0.385 | -0.109 | 0.844 | 0.510 |
| django | 0.693 | 0.190 | 0.504 | 0.195 | 0.558 | -0.055 | 0.808 | 0.637 |
| express (n=2) | 0.833 | 0.250 | 0.583 | 0.250 | 0.625 | -0.042 | 1.000 | 0.800 |
| trpc | 0.570 | 0.151 | 0.419 | 0.127 | 0.498 | -0.078 | 0.938 | 0.524 |

The shortfall is **entirely retrieval**. Under the requested approximation, retrieval loss exceeds the total loss on every corpus, so the generation term is negative. PRISM's small, focused context lets the model name a larger share of the gold it receives than the Oracle's does. Better generation would not close this gap; delivering more of the upstream gold would.

## 7. Hypothesis verdicts

| hypothesis | verdict | evidence |
|---|---|---|
| **H1** direction asymmetry: PRISM delivers mostly downstream while T5 gold is upstream | **Confirmed** | Non-seed PRISM items are 77–92% downstream (`callee` + `transitive`). Gold is 100% upstream, and 54% of it is ≥2 hops away, beyond `DEFAULT_UPSTREAM_MAX_HOPS = 1.5` / direct-only `compute_upstream_callers`. Upstream admission is capped at `UPSTREAM_FRONTIER_CAP = 3`. Coverage is 0.310 / 0.195 / 0.250 / 0.127, below even the 3-direct-caller ceiling (0.474 / 0.632* / 1.0 / 0.389; *Django tasks with hop data). Exact per-symbol hop histograms are not computable from the artifacts (no call graph). |
| **H2** Turn-1 bias toward downstream | **Partially confirmed** | The Turn-1 prompt asks for "the complete causal execution path from the seed to termination" and never mentions callers or impact. Turn 1 requested 125 caller vs 292 downstream items, and Turn 2 auto-added 193 downstream items and 0 callers (`task_type="debug"`). But the manifest text is not stored, so it is unknown how many available callers Turn 1 declined. The binding constraint is the manifest's ≤3 direct callers (H1), which no prompt can lift. |
| **H3** third-party supporters crowd out upstream callers | **Not supported** | 0 external items, 0 third-party paths and 0 Turn-2b triggers across 96 PRISM T5 cells. The token budget is not binding either: PRISM uses 1.3k–4.6k of 13k tokens, and `budget_dropped` is empty on every cell. The only crowding seen is test-code callers in Django (10/50). |

## Appendix: every PRISM T5 cell

| corpus | task | seed | gold size | delivered_symbols_resolved | delivered_symbols_named_only | uniform_cpi = gold_coverage | gold named / delivered gold | caller items | manifest | TSR |
|---|---|---|---|---|---|---|---|---|---|---|
| django | django_t13_001_blast_reverse | 42 | 8 | 13 | 0 | 0.250 | 2/2 | 3 | 22.0 | 0.250 |
| django | django_t13_001_blast_reverse | 43 | 8 | 13 | 0 | 0.250 | 2/2 | 3 | 22.0 | 0.250 |
| django | django_t13_001_blast_reverse | 44 | 8 | 13 | 0 | 0.250 | 2/2 | 3 | 22.0 | 0.250 |
| django | django_t13_002_blast_queryset_get | 42 | 8 | 14 | 0 | 0.375 | 1/3 | 3 | 57.0 | 0.125 |
| django | django_t13_002_blast_queryset_get | 43 | 8 | 12 | 0 | 0.250 | 1/2 | 2 | 57.0 | 0.125 |
| django | django_t13_002_blast_queryset_get | 44 | 8 | 14 | 0 | 0.375 | 1/3 | 3 | 57.0 | 0.125 |
| django | django_t13_003_blast_field_clean | 42 | 4 | 11 | 0 | 0.000 | 0/0 | 3 | 12.0 | 0.000 |
| django | django_t13_003_blast_field_clean | 43 | 4 | 7 | 0 | 0.000 | 0/0 | 1 | 12.0 | 0.000 |
| django | django_t13_003_blast_field_clean | 44 | 4 | 11 | 0 | 0.000 | 0/0 | 3 | 12.0 | 0.000 |
| django | django_t13_004_blast_options_get_field | 42 | 8 | 7 | 0 | 0.000 | 0/0 | 3 | 11.0 | 0.000 |
| django | django_t13_004_blast_options_get_field | 43 | 8 | 5 | 0 | 0.000 | 0/0 | 1 | 11.0 | 0.000 |
| django | django_t13_004_blast_options_get_field | 44 | 8 | 7 | 0 | 0.000 | 0/0 | 3 | 11.0 | 0.000 |
| django | django_t5_001_form_clean_validation | 42 | 2 | 15 | 0 | 0.000 | 0/0 | 1 | 17.0 | 0.000 |
| django | django_t5_001_form_clean_validation | 43 | 2 | 13 | 0 | 0.000 | 0/0 | 0 | 17.0 | 0.000 |
| django | django_t5_001_form_clean_validation | 44 | 2 | 13 | 0 | 0.000 | 0/0 | 0 | 17.0 | 0.500 |
| django | django_t5_002_send_mail_pipeline | 42 | 2 | 10 | 0 | 0.500 | 1/1 | 2 | 14.0 | 0.500 |
| django | django_t5_002_send_mail_pipeline | 43 | 2 | 12 | 0 | 0.500 | 1/1 | 2 | 14.0 | 0.500 |
| django | django_t5_002_send_mail_pipeline | 44 | 2 | 12 | 0 | 0.500 | 1/1 | 2 | 14.0 | 0.500 |
| django | django_t5_003_redirect_url_safety_check | 42 | 7 | 5 | 0 | 0.143 | 1/1 | 2 | 9.0 | 0.143 |
| django | django_t5_003_redirect_url_safety_check | 43 | 7 | 5 | 0 | 0.143 | 1/1 | 2 | 9.0 | 0.143 |
| django | django_t5_003_redirect_url_safety_check | 44 | 7 | 5 | 0 | 0.143 | 1/1 | 2 | 9.0 | 0.143 |
| django | django_t5_004_response_init_headers_cookies | 42 | 6 | 10 | 0 | 0.500 | 3/3 | 3 | 7.0 | 0.500 |
| django | django_t5_004_response_init_headers_cookies | 43 | 6 | 6 | 0 | 0.000 | 0/0 | 0 | 7.0 | 0.000 |
| django | django_t5_004_response_init_headers_cookies | 44 | 6 | 10 | 0 | 0.500 | 3/3 | 3 | 7.0 | 0.500 |
| express | express_t5_001_routing_dispatch_loop | 42 | 2 | 14 | 0 | 0.500 | 1/1 | 1 | 17.0 | 0.500 |
| express | express_t5_001_routing_dispatch_loop | 43 | 2 | 11 | 0 | 0.500 | 1/1 | 1 | 17.0 | 0.500 |
| express | express_t5_001_routing_dispatch_loop | 44 | 2 | 11 | 0 | 0.500 | 1/1 | 1 | 17.0 | 0.500 |
| express | express_t5_002_path_to_regexp_alias_mismatch | 42 | 3 | 2 | 0 | 0.000 | 0/0 | 0 | 2.0 | 0.000 |
| express | express_t5_002_path_to_regexp_alias_mismatch | 43 | 3 | 2 | 0 | 0.000 | 0/0 | 0 | 2.0 | 0.000 |
| express | express_t5_002_path_to_regexp_alias_mismatch | 44 | 3 | 2 | 0 | 0.000 | 0/0 | 0 | 2.0 | 0.000 |
| fastapi | fastapi_t5_001_dependant_tree_construction | 42 | 7 | 33 | 0 | 0.571 | 4/4 | 3 | 34.0 | 0.571 |
| fastapi | fastapi_t5_001_dependant_tree_construction | 43 | 7 | 13 | 0 | 0.429 | 3/3 | 2 | 34.0 | 0.429 |
| fastapi | fastapi_t5_001_dependant_tree_construction | 44 | 7 | 11 | 0 | 0.429 | 1/3 | 2 | 34.0 | 0.143 |
| fastapi | fastapi_t5_002_request_params_coercion | 42 | 2 | 11 | 0 | 0.500 | 1/1 | 1 | 12.0 | 0.500 |
| fastapi | fastapi_t5_002_request_params_coercion | 43 | 2 | 7 | 0 | 0.000 | 0/0 | 0 | 12.0 | 0.000 |
| fastapi | fastapi_t5_002_request_params_coercion | 44 | 2 | 9 | 0 | 0.500 | 1/1 | 1 | 12.0 | 0.500 |
| fastapi | fastapi_t5_003_add_api_route_default_resolution | 42 | 5 | 7 | 0 | 0.200 | 1/1 | 2 | 7.0 | 0.200 |
| fastapi | fastapi_t5_003_add_api_route_default_resolution | 43 | 5 | 8 | 0 | 0.400 | 2/2 | 3 | 7.0 | 0.600 |
| fastapi | fastapi_t5_003_add_api_route_default_resolution | 44 | 5 | 8 | 0 | 0.400 | 1/2 | 3 | 7.0 | 0.200 |
| fastapi | fastapi_t5_004_add_api_websocket_route | 42 | 5 | 7 | 0 | 0.400 | 1/2 | 3 | 5.0 | 0.200 |
| fastapi | fastapi_t5_004_add_api_websocket_route | 43 | 5 | 7 | 0 | 0.400 | 1/2 | 3 | 5.0 | 0.200 |
| fastapi | fastapi_t5_004_add_api_websocket_route | 44 | 5 | 5 | 0 | 0.200 | 1/1 | 1 | 5.0 | 0.200 |
| fastapi | fastapi_t5_005_get_openapi_path_operation_metadata | 42 | 2 | 10 | 0 | 0.000 | 0/0 | 0 | 17.0 | 0.000 |
| fastapi | fastapi_t5_005_get_openapi_path_operation_metadata | 43 | 2 | 15 | 0 | 0.500 | 1/1 | 1 | 17.0 | 0.500 |
| fastapi | fastapi_t5_005_get_openapi_path_operation_metadata | 44 | 2 | 11 | 0 | 0.000 | 0/0 | 0 | 17.0 | 0.000 |
| fastapi | fastapi_t5_006_get_fields_from_routes_recursion | 42 | 2 | 5 | 0 | 0.500 | 1/1 | 1 | 7.0 | 0.500 |
| fastapi | fastapi_t5_006_get_fields_from_routes_recursion | 43 | 2 | 5 | 0 | 0.500 | 1/1 | 1 | 7.0 | 0.500 |
| fastapi | fastapi_t5_006_get_fields_from_routes_recursion | 44 | 2 | 5 | 0 | 0.500 | 1/1 | 1 | 7.0 | 0.500 |
| fastapi | fastapi_t5_007_openapi_security_definitions_serialization | 42 | 3 | 2 | 0 | 0.000 | 0/0 | 0 | 4.0 | 0.000 |
| fastapi | fastapi_t5_007_openapi_security_definitions_serialization | 43 | 3 | 2 | 0 | 0.000 | 0/0 | 0 | 4.0 | 0.000 |
| fastapi | fastapi_t5_007_openapi_security_definitions_serialization | 44 | 3 | 2 | 0 | 0.000 | 0/0 | 0 | 4.0 | 0.000 |
| fastapi | fastapi_t5_008_get_value_or_default_utility | 42 | 6 | 4 | 0 | 0.333 | 2/2 | 2 | 3.0 | 0.333 |
| fastapi | fastapi_t5_008_get_value_or_default_utility | 43 | 6 | 4 | 0 | 0.333 | 2/2 | 2 | 3.0 | 0.333 |
| fastapi | fastapi_t5_008_get_value_or_default_utility | 44 | 6 | 4 | 0 | 0.333 | 2/2 | 2 | 3.0 | 0.333 |
| trpc | trpc_t5_001_procedure_caller_error_normalization | 42 | 4 | 3 | 0 | 0.000 | 0/0 | 0 | 4.0 | 0.000 |
| trpc | trpc_t5_001_procedure_caller_error_normalization | 43 | 4 | 4 | 0 | 0.250 | 1/1 | 1 | 4.0 | 0.250 |
| trpc | trpc_t5_001_procedure_caller_error_normalization | 44 | 4 | 4 | 0 | 0.250 | 1/1 | 1 | 4.0 | 0.250 |
| trpc | trpc_t5_002_router_factory_path_registration | 42 | 4 | 3 | 0 | 0.000 | 0/0 | 0 | 8.0 | 0.000 |
| trpc | trpc_t5_002_router_factory_path_registration | 43 | 4 | 3 | 0 | 0.000 | 0/0 | 0 | 8.0 | 0.000 |
| trpc | trpc_t5_002_router_factory_path_registration | 44 | 4 | 3 | 0 | 0.000 | 0/0 | 0 | 8.0 | 0.000 |
| trpc | trpc_t5_003_procedure_builder_new_builder | 42 | 9 | 7 | 0 | 0.333 | 3/3 | 3 | 9.0 | 1.000 |
| trpc | trpc_t5_003_procedure_builder_new_builder | 43 | 9 | 4 | 0 | 0.111 | 0/1 | 1 | 9.0 | 0.000 |
| trpc | trpc_t5_003_procedure_builder_new_builder | 44 | 9 | 7 | 0 | 0.333 | 2/3 | 3 | 9.0 | 0.778 |
| trpc | trpc_t5_004_input_middleware_error_handling | 42 | 4 | 6 | 0 | 0.250 | 1/1 | 1 | 6.0 | 0.250 |
| trpc | trpc_t5_004_input_middleware_error_handling | 43 | 4 | 5 | 0 | 0.250 | 1/1 | 1 | 6.0 | 0.250 |
| trpc | trpc_t5_004_input_middleware_error_handling | 44 | 4 | 4 | 0 | 0.000 | 0/0 | 0 | 6.0 | 0.000 |
| trpc | trpc_t5_005_output_middleware_error_handling | 42 | 4 | 5 | 0 | 0.500 | 2/2 | 2 | 4.0 | 0.500 |
| trpc | trpc_t5_005_output_middleware_error_handling | 43 | 4 | 3 | 0 | 0.250 | 1/1 | 1 | 4.0 | 0.250 |
| trpc | trpc_t5_005_output_middleware_error_handling | 44 | 4 | 2 | 0 | 0.000 | 0/0 | 0 | 4.0 | 0.000 |
| trpc | trpc_t5_006_http_response_context_pipeline | 42 | 9 | 12 | 0 | 0.000 | 0/0 | 0 | 16.0 | 0.000 |
| trpc | trpc_t5_006_http_response_context_pipeline | 43 | 9 | 7 | 0 | 0.111 | 1/1 | 1 | 16.0 | 0.111 |
| trpc | trpc_t5_006_http_response_context_pipeline | 44 | 9 | 10 | 0 | 0.000 | 0/0 | 0 | 16.0 | 0.000 |
| trpc | trpc_t5_007_http_response_status_code | 42 | 10 | 5 | 0 | 0.100 | 1/1 | 1 | 5.0 | 0.100 |
| trpc | trpc_t5_007_http_response_status_code | 43 | 10 | 5 | 0 | 0.100 | 1/1 | 1 | 5.0 | 0.100 |
| trpc | trpc_t5_007_http_response_status_code | 44 | 10 | 3 | 0 | 0.100 | 1/1 | 1 | 5.0 | 0.100 |
| trpc | trpc_t5_008_input_to_procedure_call | 42 | 10 | 4 | 0 | 0.000 | 0/0 | 0 | 10.0 | 0.000 |
| trpc | trpc_t5_008_input_to_procedure_call | 43 | 10 | 8 | 0 | 0.000 | 0/0 | 0 | 10.0 | 0.000 |
| trpc | trpc_t5_008_input_to_procedure_call | 44 | 10 | 8 | 0 | 0.100 | 1/1 | 1 | 10.0 | 0.100 |
| trpc | trpc_t5_009_caught_error_to_data | 42 | 10 | 4 | 0 | 0.000 | 0/0 | 0 | 10.0 | 0.000 |
| trpc | trpc_t5_009_caught_error_to_data | 43 | 10 | 4 | 0 | 0.000 | 0/0 | 0 | 10.0 | 0.000 |
| trpc | trpc_t5_009_caught_error_to_data | 44 | 10 | 4 | 0 | 0.000 | 0/0 | 0 | 10.0 | 0.000 |
| trpc | trpc_t5_010_shared_error_shape | 42 | 13 | 6 | 0 | 0.154 | 2/2 | 3 | 6.0 | 0.154 |
| trpc | trpc_t5_010_shared_error_shape | 43 | 13 | 6 | 0 | 0.154 | 2/2 | 3 | 6.0 | 0.154 |
| trpc | trpc_t5_010_shared_error_shape | 44 | 13 | 5 | 0 | 0.154 | 2/2 | 3 | 6.0 | 0.154 |
| trpc | trpc_t5_011_procedure_resolver_finalization | 42 | 3 | 3 | 0 | 0.000 | 0/0 | 0 | 11.0 | 0.000 |
| trpc | trpc_t5_011_procedure_resolver_finalization | 43 | 3 | 7 | 0 | 0.000 | 0/0 | 0 | 11.0 | 0.000 |
| trpc | trpc_t5_011_procedure_resolver_finalization | 44 | 3 | 5 | 0 | 0.000 | 0/0 | 0 | 11.0 | 0.000 |
| trpc | trpc_t5_012_response_serialization | 42 | 12 | 5 | 0 | 0.167 | 2/2 | 3 | 5.0 | 0.167 |
| trpc | trpc_t5_012_response_serialization | 43 | 12 | 5 | 0 | 0.167 | 2/2 | 3 | 5.0 | 0.167 |
| trpc | trpc_t5_012_response_serialization | 44 | 12 | 3 | 0 | 0.000 | 0/0 | 1 | 5.0 | 0.000 |
| trpc | trpc_t5_013_deprecated_procedure_migration | 42 | 2 | 7 | 0 | 0.000 | 0/0 | 0 | 11.0 | 0.000 |
| trpc | trpc_t5_013_deprecated_procedure_migration | 43 | 2 | 9 | 0 | 0.000 | 0/0 | 0 | 11.0 | 0.000 |
| trpc | trpc_t5_013_deprecated_procedure_migration | 44 | 2 | 8 | 0 | 0.500 | 1/1 | 1 | 11.0 | 0.500 |
| trpc | trpc_t5_014_client_message_validation | 42 | 2 | 7 | 0 | 0.500 | 1/1 | 1 | 7.0 | 0.500 |
| trpc | trpc_t5_014_client_message_validation | 43 | 2 | 6 | 0 | 0.000 | 0/0 | 0 | 7.0 | 0.000 |
| trpc | trpc_t5_014_client_message_validation | 44 | 2 | 7 | 0 | 0.500 | 1/1 | 1 | 7.0 | 0.500 |
