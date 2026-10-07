# Axis-compatibility test: does neighbour-conditional axis overlap predict T5 gold?

_Analysis only: no code was modified and nothing ran on Kaggle. Graphs were rebuilt locally from the pinned M4 checkouts (`build_pipeline(use_cache=False)`), with masks from `prism.semantics.extractor.compute_feature_masks` (uncached) and caller weights from `prism.packer.blast_radius.compute_upstream_callers`. Commit `80f12db`. All 32 T5 tasks on 4 corpora. Neighbours are CALLS/INSTANTIATES edges up to 3 hops in each direction; VERIFICATION-role symbols are removed, as Design C would. Rates read `gold rate (gold / n)`. Express has n = 2 tasks._

**Answer in one line.** The hypothesis is **refuted**. Inside the seed's neighbourhood, no axis, combination or role-compatibility rule beats chance consistently. On the only corpus where callers need ranking (Django), axis-weighted ordering is *worse* than PRISM's existing caller weight (top-5 gold 16/30 vs 22/30; within-task AUC 0.405 vs 0.790).

---

## 1. Gold rate by overlap count, per axis, hop and direction

Overlap = `popcount(mask_seed & mask_neighbour & axis_bits)`. "no mask" = a neighbour with no feature mask: masks exist only for functions and methods, so classes reached through INSTANTIATES have none. Mask coverage of neighbours: FastAPI 0.63, Django 0.97, Express 0.66, tRPC 0.91.

**Downstream is vacuous for T5.** T5 gold is the seed's *callers*, so callees are gold only when a symbol sits on both sides: 2 of 127 FastAPI callees, 1 of 24 Express callees, 0 elsewhere. The downstream columns are shown for completeness and carry no signal.

### fastapi

**Substance** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.82 (9/11) | 0.80 (4/5) | 0.33 (1/3) | 0.00 (0/2) | — | — |
| 1 | 1.00 (6/6) | 0.62 (8/13) | 0.00 (0/1) | 0.05 (1/20) | 0.05 (1/21) | 0.00 (0/8) |
| no mask | — | — | — | 0.00 (0/16) | 0.00 (0/40) | 0.00 (0/20) |

**Form** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.85 (11/13) | 0.58 (7/12) | 0.25 (1/4) | 0.06 (1/17) | 0.07 (1/15) | 0.00 (0/4) |
| 1 | 1.00 (3/3) | 0.83 (5/6) | — | 0.00 (0/5) | 0.00 (0/5) | 0.00 (0/3) |
| 2 | 1.00 (1/1) | — | — | — | 0.00 (0/1) | 0.00 (0/1) |
| no mask | — | — | — | 0.00 (0/16) | 0.00 (0/40) | 0.00 (0/20) |

**Output** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.82 (9/11) | 0.50 (3/6) | 0.00 (0/2) | 0.00 (0/11) | 0.00 (0/14) | 0.00 (0/4) |
| 1 | 1.00 (6/6) | 0.75 (9/12) | 0.50 (1/2) | 0.09 (1/11) | 0.14 (1/7) | 0.00 (0/4) |
| no mask | — | — | — | 0.00 (0/16) | 0.00 (0/40) | 0.00 (0/20) |

**Role (shared bit)** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.88 (14/16) | 0.69 (11/16) | 0.25 (1/4) | 0.05 (1/19) | 0.05 (1/21) | 0.00 (0/8) |
| 1 | 1.00 (1/1) | 0.50 (1/2) | — | 0.00 (0/3) | — | — |
| no mask | — | — | — | 0.00 (0/16) | 0.00 (0/40) | 0.00 (0/20) |

### django

**Substance** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.35 (7/20) | 0.13 (2/15) | 0.00 (0/7) | 0.00 (0/7) | 0.00 (0/11) | 0.00 (0/2) |
| 1 | 0.22 (22/102) | 0.07 (7/96) | 0.05 (3/59) | 0.00 (0/15) | 0.00 (0/10) | 0.00 (0/7) |
| no mask | — | — | — | 0.00 (0/12) | 0.00 (0/20) | 0.00 (0/16) |

**Form** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.19 (18/93) | 0.08 (5/63) | 0.02 (1/41) | 0.00 (0/17) | 0.00 (0/15) | 0.00 (0/8) |
| 1 | 0.38 (11/29) | 0.08 (4/48) | 0.08 (2/25) | 0.00 (0/5) | 0.00 (0/6) | 0.00 (0/1) |
| no mask | — | — | — | 0.00 (0/12) | 0.00 (0/20) | 0.00 (0/16) |

**Output** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.27 (17/63) | 0.09 (4/47) | 0.09 (3/34) | 0.00 (0/8) | 0.00 (0/15) | 0.00 (0/5) |
| 1 | 0.20 (12/59) | 0.08 (5/64) | 0.00 (0/32) | 0.00 (0/14) | 0.00 (0/6) | 0.00 (0/4) |
| no mask | — | — | — | 0.00 (0/12) | 0.00 (0/20) | 0.00 (0/16) |

**Role (shared bit)** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.24 (29/120) | 0.07 (8/108) | 0.05 (3/66) | 0.00 (0/22) | 0.00 (0/20) | 0.00 (0/7) |
| 1 | 0.00 (0/2) | 0.33 (1/3) | — | — | 0.00 (0/1) | 0.00 (0/2) |
| no mask | — | — | — | 0.00 (0/12) | 0.00 (0/20) | 0.00 (0/16) |

### express (n=2 tasks)

**Substance** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.50 (2/4) | 0.00 (0/1) | 0.00 (0/3) | 0.12 (1/8) | 0.00 (0/3) | 0.00 (0/2) |
| no mask | — | — | — | 0.00 (0/2) | 0.00 (0/4) | 0.00 (0/5) |

**Form** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.50 (1/2) | 0.00 (0/1) | — | 0.00 (0/4) | 0.00 (0/1) | 0.00 (0/1) |
| 1 | 1.00 (1/1) | — | 0.00 (0/2) | 0.25 (1/4) | — | 0.00 (0/1) |
| 2 | — | — | 0.00 (0/1) | — | 0.00 (0/2) | — |
| 3 | 0.00 (0/1) | — | — | — | — | — |
| no mask | — | — | — | 0.00 (0/2) | 0.00 (0/4) | 0.00 (0/5) |

**Output** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.00 (0/2) | 0.00 (0/1) | 0.00 (0/2) | 0.00 (0/3) | 0.00 (0/2) | 0.00 (0/1) |
| 1 | 1.00 (2/2) | — | 0.00 (0/1) | 0.20 (1/5) | 0.00 (0/1) | 0.00 (0/1) |
| no mask | — | — | — | 0.00 (0/2) | 0.00 (0/4) | 0.00 (0/5) |

**Role (shared bit)** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.50 (2/4) | 0.00 (0/1) | 0.00 (0/3) | 0.12 (1/8) | 0.00 (0/3) | 0.00 (0/2) |
| no mask | — | — | — | 0.00 (0/2) | 0.00 (0/4) | 0.00 (0/5) |

### trpc

**Substance** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 1 | 0.86 (31/36) | 0.83 (34/41) | 0.92 (23/25) | 0.00 (0/32) | 0.00 (0/18) | 0.00 (0/7) |
| no mask | — | — | — | 0.00 (0/9) | 0.00 (0/7) | — |

**Form** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.80 (4/5) | 0.91 (10/11) | 1.00 (4/4) | 0.00 (0/13) | 0.00 (0/7) | 0.00 (0/2) |
| 1 | 0.87 (27/31) | 0.80 (24/30) | 0.90 (18/20) | 0.00 (0/19) | 0.00 (0/11) | 0.00 (0/5) |
| 2 | — | — | 1.00 (1/1) | — | — | — |
| no mask | — | — | — | 0.00 (0/9) | 0.00 (0/7) | — |

**Output** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.62 (8/13) | 0.79 (22/28) | 1.00 (16/16) | 0.00 (0/22) | 0.00 (0/11) | 0.00 (0/6) |
| 1 | 1.00 (22/22) | 0.92 (12/13) | 0.78 (7/9) | 0.00 (0/9) | 0.00 (0/7) | 0.00 (0/1) |
| 2 | 1.00 (1/1) | — | — | 0.00 (0/1) | — | — |
| no mask | — | — | — | 0.00 (0/9) | 0.00 (0/7) | — |

**Role (shared bit)** — overlap count × (direction, hop)

| overlap | up1 | up2 | up3 | down1 | down2 | down3 |
|---|---|---|---|---|---|---|
| 0 | 0.86 (30/35) | 0.81 (30/37) | 0.91 (21/23) | 0.00 (0/32) | 0.00 (0/18) | 0.00 (0/7) |
| 1 | 1.00 (1/1) | 1.00 (4/4) | 1.00 (2/2) | — | — | — |
| no mask | — | — | — | 0.00 (0/9) | 0.00 (0/7) | — |


**Reading.**
- **FastAPI and tRPC** are near ceiling at hop 1 (0.88 and 0.86 base), so no ordering can change much.
- **Django** is the only corpus with a large, gold-sparse caller set (hop 1: 29/122). Substance, Output and Role are flat or inverted. Form is the one apparent positive: at hop 1, overlap 1 gives 0.38 (11/29) against 0.19 (18/93) at overlap 0. That lift disappears within a seed (within-task AUC 0.519, §4), so it is the same between-seed composition effect described in §2, not a signal that can rank one seed's callers.
- **Hop is the dominant variable everywhere.** Django goes 0.24 → 0.08 → 0.05 across hops 1–3, FastAPI 0.88 → 0.67 → 0.25.

## 2. Why Substance anti-correlated on Django

**The cause is a composition (between-task) effect from a near-constant bit, not noise in the axis and not real signal.**

- **Substance is effectively one bit.** Among Django upstream candidates within 3 hops, `SINK_PURE_COMPUTE` appears on 263 of 299 (88%; gold rate 0.144). 35 have no Substance bit (gold rate 0.057), and 1 has `SINK_NETWORK_IO`. Six of the eight Django seeds are `PURE_COMPUTE`, and the other two have no Substance bit at all.
- **Inside a task the axis is constant.** For `django.urls.base.reverse`, all 43 production hop-1 callers share `SINK_PURE_COMPUTE` with the seed. That covers all 6 gold callers and all 37 non-gold ones, so the axis cannot rank them. The top-20 overlap callers are below; every row matched on that same bit.
- **Between tasks, it separates two kinds of seed.** The two seeds with *no* Substance bit (`BaseForm.full_clean`, `HttpResponse.__init__`) have small, gold-dense caller sets (gold 2/2 and 6/7), whose callers therefore score overlap 0. The hub seeds (`reverse`, `Options.get_field`, `QuerySet.get`) are `PURE_COMPUTE`, with large, gold-sparse caller sets that nearly all overlap. Pooling the two kinds gives "overlap → lower gold rate" (hop 1: 0.216 with overlap vs 0.35 without) while saying nothing about any single seed.
  - Within-task AUC for Substance on Django is 0.564 over 526 gold/non-gold pairs, close to chance.
- **The axis itself is coarse here, but working as specified.** `PURE_COMPUTE` means "no registered I/O sink and no detected state mutation". Framework methods such as `ModelAdmin.response_add` reach the database only indirectly, through calls that the literal sink registry and the one-hop thin-wrapper fold do not catch. So almost all of Django's call graph reads as pure compute.

Top 20 Substance-overlap hop-1 callers of `django.urls.base.reverse` (seed Substance = `SINK_PURE_COMPUTE`):

| # | caller | Substance bits | gold | matched bit |
|---|---|---|---|---|
| 1 | `django.contrib.admin.options.ModelAdmin._get_obj_does_not_exist_redirect` | SINK_PURE_COMPUTE | **yes** | SINK_PURE_COMPUTE |
| 2 | `django.contrib.admin.options.ModelAdmin.response_add` | SINK_PURE_COMPUTE | **yes** | SINK_PURE_COMPUTE |
| 3 | `django.contrib.admin.options.ModelAdmin.response_change` | SINK_PURE_COMPUTE | **yes** | SINK_PURE_COMPUTE |
| 4 | `django.contrib.admin.options.ModelAdmin.response_delete` | SINK_PURE_COMPUTE | **yes** | SINK_PURE_COMPUTE |
| 5 | `django.contrib.admin.sites.AdminSite.password_change` | SINK_PURE_COMPUTE | **yes** | SINK_PURE_COMPUTE |
| 6 | `django.views.generic.base.RedirectView.get_redirect_url` | SINK_PURE_COMPUTE | **yes** | SINK_PURE_COMPUTE |
| 7 | `django.contrib.admin.helpers.AdminReadonlyField.get_admin_url` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 8 | `django.contrib.admin.models.LogEntry.get_admin_url` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 9 | `django.contrib.admin.options.BaseModelAdmin.get_view_on_site_url` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 10 | `django.contrib.admin.options.ModelAdmin._response_post_save` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 11 | `django.contrib.admin.sites.AdminSite._build_app_dict` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 12 | `django.contrib.admin.sites.AdminSite.inner` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 13 | `django.contrib.admin.sites.AdminSite.login` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 14 | `django.contrib.admin.utils.format_callback` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 15 | `django.contrib.admin.views.main.ChangeList.url_for_result` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 16 | `django.contrib.admin.widgets.AutocompleteMixin.get_url` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 17 | `django.contrib.admin.widgets.ForeignKeyRawIdWidget.get_context` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 18 | `django.contrib.admin.widgets.ForeignKeyRawIdWidget.label_and_url_for_value` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 19 | `django.contrib.admin.widgets.RelatedFieldWidgetWrapper.get_related_url` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |
| 20 | `django.contrib.admindocs.utils.parse_rst` | SINK_PURE_COMPUTE | no | SINK_PURE_COMPUTE |

## 3. Role compatibility

**Role values** (`prism.semantics.bitmask.FeatureBit`, conditions from `prism/semantics/role.py`; a symbol may carry several or none):
- `ROLE_ENTRYPOINT`: fan_in = 0, fan_out > 0
- `ROLE_ORCHESTRATOR`: fan_out ≥ 4, fan_in ≤ 2
- `ROLE_ADAPTER`: bridges two distinct Substance domains
- `ROLE_LEAF_UTILITY`: fan_in ≥ 5, fan_out ≤ 1, pure
- `ROLE_BRIDGE`: fan_in ≥ 5, fan_out ≥ 5
- `ROLE_PUBLIC_API`: exported, fan_in = 0
- `ROLE_LEAF_SERVICE`: fan_out ≤ 1, has an I/O sink

**PRISM defines no caller/callee compatibility rule between roles.** The two rules below are mine, stated explicitly:
- **Rule A (caller-shaped):** a caller Y is *compatible* if it has any of {ENTRYPOINT, PUBLIC_API, ORCHESTRATOR, BRIDGE, ADAPTER}, the roles that describe outgoing coordination. It is *incompatible* if it has only leaf roles {LEAF_UTILITY, LEAF_SERVICE}, and *unassigned* if it has no role bit.
- **Rule B (not-a-leaf):** *incompatible* if Y has a leaf role and no caller-shaped role, otherwise *compatible*. On this data it produces exactly the same buckets as Rule A.
- I also report the plain "shares a Role bit with the seed".

Seed roles (and Substance / Output, for reference):

| corpus | seed | Role | Substance | Output |
|---|---|---|---|---|
| fastapi | `fastapi.dependencies.utils.get_dependant` | (none) | PURE_COMPUTE | QUERY |
| fastapi | `fastapi.dependencies.utils.request_params_to_args` | ORCHESTRATOR | PURE_COMPUTE | AGGREGATOR |
| fastapi | `fastapi.routing.APIRouter.add_api_route` | (none) | (none) | COMMAND |
| fastapi | `fastapi.routing.APIRouter.add_api_websocket_route` | (none) | (none) | COMMAND |
| fastapi | `fastapi.openapi.utils.get_openapi_path` | ORCHESTRATOR | PURE_COMPUTE | QUERY |
| fastapi | `fastapi.openapi.utils.get_fields_from_routes` | (none) | PURE_COMPUTE | QUERY |
| fastapi | `fastapi.openapi.utils.get_openapi_security_definitions` | (none) | PURE_COMPUTE | QUERY |
| fastapi | `fastapi.utils.get_value_or_default` | (none) | PURE_COMPUTE | TRANSFORMER |
| django | `django.urls.base.reverse` | BRIDGE | PURE_COMPUTE | QUERY |
| django | `django.db.models.query.QuerySet.get` | BRIDGE | PURE_COMPUTE | QUERY |
| django | `django.forms.fields.Field.clean` | (none) | PURE_COMPUTE | TRANSFORMER |
| django | `django.db.models.options.Options.get_field` | LEAF_UTILITY | PURE_COMPUTE | QUERY |
| django | `django.forms.forms.BaseForm.full_clean` | BRIDGE | (none) | COMMAND |
| django | `django.core.mail.send_mail` | (none) | PURE_COMPUTE | QUERY |
| django | `django.utils.http.url_has_allowed_host_and_scheme` | (none) | PURE_COMPUTE | PREDICATE |
| django | `django.http.response.HttpResponse.__init__` | (none) | (none) | COMMAND |
| express | `lib.router.next` | BRIDGE | TIME_IO | QUERY |
| express | `lib.router.layer.Layer` | ENTRYPOINT, PUBLIC_API | (none) | FACTORY |
| trpc | `core.internals.procedureBuilder.createProcedureCaller` | (none) | PURE_COMPUTE | AGGREGATOR |
| trpc | `core.router.createRouterFactory` | (none) | PURE_COMPUTE | QUERY |
| trpc | `core.internals.procedureBuilder.createNewBuilder` | (none) | PURE_COMPUTE | QUERY |
| trpc | `core.middleware.createInputMiddleware` | (none) | PURE_COMPUTE | QUERY |
| trpc | `core.middleware.createOutputMiddleware` | (none) | PURE_COMPUTE | AGGREGATOR |
| trpc | `http.resolveHTTPResponse.resolveHTTPResponse` | (none) | PURE_COMPUTE | AGGREGATOR, ASYNC_DEFERRED |
| trpc | `http.resolveHTTPResponse.initResponse` | (none) | PURE_COMPUTE | AGGREGATOR |
| trpc | `http.resolveHTTPResponse.inputToProcedureCall` | ORCHESTRATOR | PURE_COMPUTE | AGGREGATOR, ASYNC_DEFERRED |
| trpc | `http.resolveHTTPResponse.caughtErrorToData` | ORCHESTRATOR | PURE_COMPUTE | AGGREGATOR |
| trpc | `shared.getErrorShape.getErrorShape` | LEAF_UTILITY | PURE_COMPUTE | QUERY |
| trpc | `core.internals.procedureBuilder.createResolver` | (none) | PURE_COMPUTE | QUERY |
| trpc | `shared.transformTRPCResponse.transformTRPCResponse` | (none) | PURE_COMPUTE | QUERY |
| trpc | `deprecated.interop.migrateProcedure` | ORCHESTRATOR | PURE_COMPUTE | QUERY |
| trpc | `rpc.parseTRPCMessage.parseTRPCMessage` | ORCHESTRATOR | PURE_COMPUTE | AGGREGATOR |

19 of the 32 seeds carry no Role bit at all. Among those that do, ORCHESTRATOR and BRIDGE dominate.

Hop-1 upstream callers (production) by compatibility bucket:

| corpus | base rate | Rule A compatible | Rule A incompatible | no Role bit | shares a Role bit with seed | shares none |
|---|---|---|---|---|---|---|
| fastapi | 0.88 (15/17) | 0.83 (10/12) | — | 1.00 (5/5) | 1.00 (1/1) | 0.88 (14/16) |
| django | 0.24 (29/122) | 0.23 (18/77) | 0.00 (0/1) | 0.25 (11/44) | 0.00 (0/2) | 0.24 (29/120) |
| express | 0.50 (2/4) | 0.50 (1/2) | — | 0.50 (1/2) | — | 0.50 (2/4) |
| trpc | 0.86 (31/36) | 0.84 (21/25) | 0.00 (0/1) | 1.00 (10/10) | 1.00 (1/1) | 0.86 (30/35) |
| ALL | 0.43 (77/179) | 0.43 (50/116) | 0.00 (0/2) | 0.44 (27/61) | 0.50 (2/4) | 0.43 (75/175) |

Rule A buckets at hops 2 and 3 (all corpora): hop 2 compatible 0.28 (34/123), no role 0.45 (21/47); hop 3 compatible 0.31 (20/64), no role 0.21 (7/33).

**Result: no signal.**
- The "compatible" bucket (0.43, 50/116) matches the base rate (0.43, 77/179) and the no-role bucket (0.44).
- The "incompatible" bucket holds only 2 callers in total (0/2), too few to estimate anything.
- Sharing a Role bit with the seed happens 4 times in 179.
- Role is assigned from global fan-in/fan-out thresholds. A seed's callers are mostly ENTRYPOINT / PUBLIC_API / ORCHESTRATOR whether or not they are gold, because a caller by definition has fan_out ≥ 1, and many are top-of-chain functions.

## 4. Pairwise combinations (hop-1 upstream)

Scores use Rule A compatibility as 0/1, plus Output and Form overlap counts. "W321" is the proposed weighting `3·Role + 2·Output + 1·Form`.

**Role + Output**
| corpus | score 0 | score 1 | score 2 |
|---|---|---|---|
| fastapi | 1.00 (3/3) | 0.80 (8/10) | 1.00 (4/4) |
| django | 0.32 (6/19) | 0.23 (16/70) | 0.21 (7/33) |
| express | 0.00 (0/1) | 0.50 (1/2) | 1.00 (1/1) |
| trpc | 0.67 (2/3) | 0.76 (13/17) | 1.00 (16/16) |
| ALL | 0.42 (11/26) | 0.38 (38/99) | 0.52 (28/54) |

**Role + Form**
| corpus | score 0 | score 1 | score 2 | score 3 | score 4 |
|---|---|---|---|---|---|
| fastapi | 1.00 (5/5) | 0.75 (6/8) | 1.00 (3/3) | 1.00 (1/1) | — |
| django | 0.24 (8/34) | 0.19 (13/70) | 0.44 (8/18) | — | — |
| express | 0.00 (0/1) | 1.00 (2/2) | — | — | 0.00 (0/1) |
| trpc | 1.00 (3/3) | 0.80 (8/10) | 0.87 (20/23) | — | — |
| ALL | 0.37 (16/43) | 0.32 (29/90) | 0.70 (31/44) | 1.00 (1/1) | 0.00 (0/1) |

**Output + Form**
| corpus | score 0 | score 1 | score 2 | score 3 |
|---|---|---|---|---|
| fastapi | 0.75 (6/8) | 1.00 (8/8) | — | 1.00 (1/1) |
| django | 0.26 (12/46) | 0.17 (11/64) | 0.50 (6/12) | — |
| express | 0.00 (0/1) | 1.00 (1/1) | 1.00 (1/1) | 0.00 (0/1) |
| trpc | 0.50 (1/2) | 0.71 (10/14) | 1.00 (19/19) | 1.00 (1/1) |
| ALL | 0.33 (19/57) | 0.34 (30/87) | 0.81 (26/32) | 0.67 (2/3) |

**W321 = 3·Role + 2·Output + 1·Form**
| corpus | score 0 | score 1 | score 2 | score 3 | score 4 | score 5 | score 6 | score 7 |
|---|---|---|---|---|---|---|---|---|
| fastapi | 1.00 (3/3) | — | 1.00 (2/2) | 0.60 (3/5) | 1.00 (3/3) | 1.00 (3/3) | — | 1.00 (1/1) |
| django | 0.33 (4/12) | 0.29 (2/7) | 0.18 (4/22) | 0.24 (9/38) | 0.30 (3/10) | 0.08 (2/25) | 0.62 (5/8) | — |
| express | 0.00 (0/1) | — | — | 1.00 (1/1) | — | 1.00 (1/1) | 0.00 (0/1) | — |
| trpc | 1.00 (1/1) | 0.50 (1/2) | 1.00 (2/2) | 0.83 (5/6) | 0.67 (6/9) | 1.00 (2/2) | 1.00 (14/14) | — |
| ALL | 0.47 (8/17) | 0.33 (3/9) | 0.31 (8/26) | 0.36 (18/50) | 0.55 (12/22) | 0.26 (8/31) | 0.83 (19/23) | 1.00 (1/1) |

**Pooled rates are misleading.** The "ALL" row of W321 peaks at score 6 (19/23), but 14 of those 23 are tRPC callers, where nearly everything is gold. Inside Django the W321 rates are not monotone (score 5: 2/25, score 6: 5/8, score 3: 9/38). The test that matters for reordering callers is **within the same seed**. Below is the probability that a gold caller outscores a non-gold caller of the same seed (AUC over within-task pairs; 0.5 = chance; ties count half; the number of pairs is in parentheses):

| score | fastapi | django | express | trpc |
|---|---|---|---|---|
| w_up | 0.500 (5) | 0.790 (526) | 0.250 (4) | 0.656 (16) |
| compat | 0.400 (5) | 0.414 (526) | 0.500 (4) | 0.500 (16) |
| ovS | 0.500 (5) | 0.564 (526) | 0.500 (4) | 0.500 (16) |
| ovF | 0.500 (5) | 0.519 (526) | 0.375 (4) | 0.594 (16) |
| ovO | 0.700 (5) | 0.465 (526) | 1.000 (4) | 0.656 (16) |
| RO | 0.600 (5) | 0.407 (526) | 0.875 (4) | 0.656 (16) |
| RF | 0.400 (5) | 0.436 (526) | 0.500 (4) | 0.594 (16) |
| OF | 0.700 (5) | 0.482 (526) | 0.500 (4) | 0.719 (16) |
| W321 | 0.500 (5) | 0.405 (526) | 0.500 (4) | 0.688 (16) |

`w_up` is PRISM's existing caller weight `W_upstream` (return-binding / non-trivial-argument tiers). Only Django has enough pairs (526) to read. There:
- **W_upstream reaches 0.790.** It is the only signal well above chance.
- **Every axis score is at or below chance:** W321 0.405, Role 0.414, Output 0.465, Form 0.519, Substance 0.564.
- **Combinations do not beat the single axes,** and none is monotone.

FastAPI (5 pairs), Express (4) and tRPC (16) are too small to support any weighting.

## 5. Effect on Design C: top-5 hop-1 callers per seed

Ordering 1: `W_upstream` descending, then name, which is PRISM's current rule. Ordering 2: W321 descending, then `W_upstream`, then name. Gold among the top-5 (or all callers if there are fewer), pooled per corpus:

| corpus | seeds with > 5 callers | top-5 gold, W_upstream order | top-5 gold, axis order |
|---|---|---|---|
| fastapi | 0 | 15/17 = 0.882 | 15/17 = 0.882 |
| django | 4 | 22/30 = 0.733 | 16/30 = 0.533 |
| express | 0 | 2/4 = 0.500 | 2/4 = 0.500 |
| trpc | 2 | 28/33 = 0.848 | 29/33 = 0.879 |

The orderings differ only where a seed has more than 5 callers, which happens only on Django and once on tRPC. Django per seed:

| task | callers | gold callers | top-5 gold, W_upstream | top-5 gold, axis |
|---|---|---|---|---|
| `django_t13_001_blast_reverse` | 43 | 6 | 3/5 | 0/5 |
| `django_t13_002_blast_queryset_get` | 21 | 5 | 5/5 | 3/5 |
| `django_t13_004_blast_options_get_field` | 42 | 6 | 2/5 | 2/5 |
| `django_t5_004_response_init_headers_cookies` | 6 | 5 | 5/5 | 4/5 |

**Axis ordering costs 6 gold callers out of 30 slots on Django** (0.733 → 0.533). On `reverse()` alone it drops from 3/5 to 0/5. Elsewhere the two orderings are identical.

## 6. Verdict

**Refuted.** The user's axis-compatibility hypothesis does not hold as a neighbour-conditional signal on these 32 T5 tasks:
- **Role compatibility shows no signal:** 0.43 compatible vs 0.44 no-role, with only 2 "incompatible" callers in total.
- **No other axis is load-bearing.** Substance is a near-constant `PURE_COMPUTE` bit whose apparent Django anti-correlation is a between-seed composition effect. Output and Form are at chance within seeds (Django AUC 0.465 and 0.519).
- **The proposed weighting hurts.** `Role +3 / Output +2 / Form +1` is non-monotone, has within-seed AUC 0.405 on Django (worse than chance), and lowers Design C's hop-1 top-5 gold from 22/30 to 16/30 there.

**Design C should not add axis-aware confidence weighting.** It should order callers by hop, then by PRISM's existing `W_upstream` tier: within-seed AUC 0.790 on Django, already the rule `build_candidate_manifest` uses for its 3 direct callers. It should drop VERIFICATION-role symbols, as recommended in `four_axis_relevance.md`.

**Where the idea could still work** is narrower than proposed. The data shows signal only from the caller's *contract relationship to the seed* (does it bind the return value or pass real arguments — what `W_upstream` already encodes), not from the caller's own semantic profile. Any future axis use should be retested on a corpus with more large-fan-in seeds than Django's four, because FastAPI, Express and tRPC had too few competing callers (5, 4 and 16 pairs) to show anything either way.
