# Contract-family (scope) test: does a caller's scope relation to the anchor predict T5 gold?

_Analysis only: no code was modified and nothing ran on Kaggle. Graphs were rebuilt locally from the pinned M4 checkouts (`build_pipeline(use_cache=False)`). Candidates are every upstream CALLS/INSTANTIATES caller within 3 hops of each T5 seed (32 tasks, 4 corpora), with VERIFICATION-role symbols removed: 1,253 removed, including 3 gold, all in `django_t13_003`, whose curated gold is test call sites. Commit `a5f0877`. Cells read `gold / n = rate`. Express has n = 2 tasks._

**Answer in one line.** The hypothesis is **refuted as a ranking signal.**
- **Pooled, it looks strong:** in-family 0.76 vs out-of-family 0.29.
- **Within a seed, it vanishes or reverses:** within-task AUC on Django 0.503 for family and 0.553 for scope level, against 0.726 for hop.
- **Ordering by scope lowers precision** on the only corpus with competing callers (Django p@5 25/34 → 21–22/34).
- **The pooled gap is a between-seed composition effect,** the same mechanism found in the axis test.

---

## 1. Scope levels

Smallest common scope between caller Y and seed X:
- **L0:** Y is defined inside X's body (qualified name prefixed by X's).
- **L1:** same enclosing class.
- **L2:** same file (module).
- **L3:** same directory (package).
- **L4:** same top-level namespace.
- **L5:** different.

Python namespaces come from the module path, so the top level is `fastapi.*` / `django.*`. For TypeScript the top level is the first directory under the corpus root: Express `lib/`, tRPC `core/`, `http/`, `shared/`, `adapters/`, ….
- **No tsconfig resolution was needed.** PRISM resolves TS modules to file paths, and directories map one-to-one onto the import namespaces used here.
- **L0 never occurs.** No caller is nested inside its seed.
- **Every Python production caller shares the top-level package,** so L5 on FastAPI/Django is only the few non-package production files (e.g. top-level scripts).

## 2. Gold rate by scope level and hop

### fastapi

| level | hop 1 | hop 2 | hop 3 | all hops |
|---|---|---|---|---|
| L0 | — | — | — | — |
| L1 | 2/4 = 0.50 | — | — | 2/4 = 0.50 |
| L2 | 6/6 = 1.00 | 3/3 = 1.00 | — | 9/9 = 1.00 |
| L3 | 5/5 = 1.00 | 5/7 = 0.71 | — | 10/12 = 0.83 |
| L4 | 2/2 = 1.00 | 4/6 = 0.67 | 1/3 = 0.33 | 7/11 = 0.64 |
| L5 | — | 0/2 = 0.00 | 0/1 = 0.00 | 0/3 = 0.00 |

### django

| level | hop 1 | hop 2 | hop 3 | all hops |
|---|---|---|---|---|
| L0 | — | — | — | — |
| L1 | 1/2 = 0.50 | — | — | 1/2 = 0.50 |
| L2 | 5/10 = 0.50 | 1/3 = 0.33 | — | 6/13 = 0.46 |
| L3 | 2/11 = 0.18 | 0/7 = 0.00 | 0/5 = 0.00 | 2/23 = 0.09 |
| L4 | 21/80 = 0.26 | 8/94 = 0.09 | 3/56 = 0.05 | 32/230 = 0.14 |
| L5 | 0/19 = 0.00 | 0/7 = 0.00 | 0/5 = 0.00 | 0/31 = 0.00 |

### express (n=2 tasks)

| level | hop 1 | hop 2 | hop 3 | all hops |
|---|---|---|---|---|
| L0 | — | — | — | — |
| L1 | — | — | — | — |
| L2 | 2/3 = 0.67 | — | — | 2/3 = 0.67 |
| L3 | — | — | — | — |
| L4 | 0/1 = 0.00 | 0/1 = 0.00 | 0/3 = 0.00 | 0/5 = 0.00 |
| L5 | — | — | — | — |

### trpc

| level | hop 1 | hop 2 | hop 3 | all hops |
|---|---|---|---|---|
| L0 | — | — | — | — |
| L1 | — | — | — | — |
| L2 | 14/14 = 1.00 | 6/6 = 1.00 | — | 20/20 = 1.00 |
| L3 | 0/1 = 0.00 | 1/2 = 0.50 | — | 1/3 = 0.33 |
| L4 | 3/3 = 1.00 | — | 0/2 = 0.00 | 3/5 = 0.60 |
| L5 | 14/18 = 0.78 | 27/33 = 0.82 | 23/23 = 1.00 | 64/74 = 0.86 |

### Pooled (all corpora)

| level | hop 1 | hop 2 | hop 3 | all hops |
|---|---|---|---|---|
| L0 | — | — | — | — |
| L1 | 3/6 = 0.50 | — | — | 3/6 = 0.50 |
| L2 | 27/33 = 0.82 | 10/12 = 0.83 | — | 37/45 = 0.82 |
| L3 | 7/17 = 0.41 | 6/16 = 0.38 | 0/5 = 0.00 | 13/38 = 0.34 |
| L4 | 26/86 = 0.30 | 12/101 = 0.12 | 4/64 = 0.06 | 42/251 = 0.17 |
| L5 | 14/37 = 0.38 | 27/42 = 0.64 | 23/29 = 0.79 | 64/108 = 0.59 |

**Pooling caveat.** The pooled table mixes tRPC, where 86% of candidates are gold and most sit at L5 (a different top-level directory), with Django, where 14% are gold and most sit at L4. The pooled L5 > L4 "rise" is therefore a corpus mix, not a scope effect. Read the per-corpus tables.

**Shape per corpus.**
- **FastAPI:** roughly monotone (L2 1.00 → L3 0.83 → L4 0.64 → L5 0.00), but only 35 candidates in total.
- **Django:** L2 0.46, L3 0.09, L4 0.14, L5 0.00. Not monotone (L3 < L4), and 230 of the 299 candidates, with 32 of the 41 gold, sit at L4.
- **tRPC:** L5 (different top-level directory) is 0.86 gold and L2 is 1.00. No useful gradient.
- **Express:** 8 candidates.

The expected monotone fall does not appear on the two corpora with enough candidates.

## 3. Scope vs hop: which dominates?

Gold rate by scope level within hop 1 and within hop 2 is in the per-corpus tables above (columns). The decisive test is **within a seed**: how often a gold caller ranks ahead of a non-gold caller of the same seed. That is AUC over within-task pairs, where 0.5 = chance and ties count half:

| corpus | pairs (hops 1–3) | hop | scope level | in-family | within hop 1: scope / family / `W_upstream` | within hop 2: scope / family |
|---|---|---|---|---|---|---|
| FastAPI | 35 | 0.714 | 0.643 | 0.600 | 0.20 / 0.20 / 0.50 (5 pairs) | 0.70 / 0.60 (10 pairs) |
| Django | 1,981 | **0.726** | 0.553 | 0.503 | 0.572 / 0.485 / **0.790** (526 pairs) | 0.530 / 0.528 (200 pairs) |
| Express (n=2) | 12 | 0.833 | 0.917 | 0.917 | 0.75 / 0.75 / 0.25 (4 pairs) | — |
| tRPC | 119 | 0.366 | 0.458 | 0.475 | 0.438 / 0.438 / 0.656 (16 pairs) | 0.484 / 0.484 (31 pairs) |

- **Django is the only corpus with enough pairs to read.** Hop (0.726) and, within hop 1, `W_upstream` (0.790) carry the signal. Scope level (0.553) and family (0.503) are at or near chance overall, and stay near chance within hop 1 and hop 2.
- **The hop-stratified rates look like signal but are not.** Django hop-1 in-family is 0.50 (7/14) vs out 0.20 (22/108). That gap comes from *which seeds* have in-family callers (`HttpResponse.__init__`: 6/7 in-family and gold-dense), not from in-family callers beating their own siblings.
- **On tRPC even hop is inverted (0.366).** Gold is the full transitive closure and nearly every candidate is gold, so deeper callers are gold as often as shallow ones.
- **Independence.** Scope does not add to hop. Within each hop stratum it is ≈ chance on Django. Hop also does not "absorb" scope, because there is no scope signal to absorb.

Ordering effect on the full 3-hop candidate list (pooled per corpus; precision@5, precision@10, R-precision):

| corpus | hop → `W_upstream` (Design C) | hop → family → `W_upstream` | hop → scope level → `W_upstream` | family → hop → `W_upstream` |
|---|---|---|---|---|
| FastAPI | 26/35, 28/39, 23/28 | 26/35, 28/39, 23/28 | 26/35, 28/39, **26/28** | 26/35, 28/39, 23/28 |
| Django | **25/34, 33/55, 30/41** | 22/34, 32/55, 30/41 | 21/34, 28/55, 26/41 | 21/34, 33/55, 29/41 |
| Express (n=2) | 2/5, 2/8, 0/2 | 2/5, 2/8, 1/2 | 2/5, 2/8, 1/2 | 2/5, 2/8, 1/2 |
| tRPC | 53/59, 81/91, 80/88 | 53/59, 81/91, 80/88 | 53/59, 81/91, 80/88 | 53/59, 81/91, 79/88 |

- **Scope ordering helps only FastAPI's R-precision** (+3 of 28), on 35 candidates.
- **It costs Django 3–4 gold** in the top 5 and up to 5 in the top 10.
- **tRPC is unchanged.**

## 4. Seed walkthroughs

**Task-ID note.** The brief's `django_t02_017` is a T2 task. Its seed, `url_has_allowed_host_and_scheme`, has T5 counterpart `django_t5_003_redirect_url_safety_check`, used here. `django.urls.base.reverse` is the T5 task `django_t13_001_blast_reverse`. `django_t02_016`'s seed is `BaseDatabaseWrapper.cursor`, which has **no T5 task and so no blast-radius gold**; it cannot be scored and is not estimated.

### `django_t13_001_blast_reverse` (`django.urls.base.reverse`)

99 production callers within 3 hops, 8 gold. same module (L0–L2): 0/1 = 0.00; cross-module (L3–L5): 8/98 = 0.08.
- **Every gold caller is cross-package** (admin, views, sitemaps). The seed's own package `django/urls/` holds one non-gold caller.
- **Scope does not separate gold from non-gold:** gold and non-gold admin methods sit side by side at L4 in the same modules.

| hop | caller | module | scope | in family | gold |
|---|---|---|---|---|---|
| 1 | `django.contrib.admin.helpers.AdminReadonlyField.get_admin_url` | `django.contrib.admin.helpers` | L4 | no | no |
| 1 | `django.contrib.admin.options.ModelAdmin._get_obj_does_not_exist_redirect` | `django.contrib.admin.options` | L4 | no | **yes** |
| 1 | `django.contrib.admin.options.ModelAdmin._response_post_save` | `django.contrib.admin.options` | L4 | no | no |
| 1 | `django.contrib.admin.options.ModelAdmin.response_add` | `django.contrib.admin.options` | L4 | no | **yes** |
| 1 | `django.contrib.admin.options.ModelAdmin.response_change` | `django.contrib.admin.options` | L4 | no | **yes** |
| 1 | `django.contrib.admin.options.ModelAdmin.response_delete` | `django.contrib.admin.options` | L4 | no | **yes** |
| 1 | `django.contrib.admin.sites.AdminSite._build_app_dict` | `django.contrib.admin.sites` | L4 | no | no |
| 1 | `django.contrib.admin.sites.AdminSite.inner` | `django.contrib.admin.sites` | L4 | no | no |
| 1 | `django.contrib.admin.sites.AdminSite.login` | `django.contrib.admin.sites` | L4 | no | no |
| 1 | `django.contrib.admin.sites.AdminSite.password_change` | `django.contrib.admin.sites` | L4 | no | **yes** |
| 1 | `django.contrib.admin.utils.format_callback` | `django.contrib.admin.utils` | L4 | no | no |
| 1 | `django.contrib.admin.widgets.ForeignKeyRawIdWidget.get_context` | `django.contrib.admin.widgets` | L4 | no | no |
| 1 | `django.contrib.admin.widgets.ForeignKeyRawIdWidget.label_and_url_for_value` | `django.contrib.admin.widgets` | L4 | no | no |
| 1 | `django.contrib.sitemaps.views.index` | `django.contrib.sitemaps.views` | L4 | no | no |
| 1 | `django.template.defaulttags.URLNode.render` | `django.template.defaulttags` | L4 | no | no |
| 1 | `django.urls.base.translate_url` | `django.urls.base` | L2 | yes | no |
| 1 | `django.views.generic.base.RedirectView.get_redirect_url` | `django.views.generic.base` | L4 | no | **yes** |
| 1 | `django.contrib.sitemaps._get_sitemap_full_url` | `django.contrib.sitemaps` | L4 | no | no |
| 1 | `django.contrib.admin.models.LogEntry.get_admin_url` | `django.contrib.admin.models` | L4 | no | no |
| 1 | `django.contrib.admin.options.BaseModelAdmin.get_view_on_site_url` | `django.contrib.admin.options` | L4 | no | no |
| 1 | `django.contrib.admin.views.main.ChangeList.url_for_result` | `django.contrib.admin.views.main` | L4 | no | no |
| 1 | `django.contrib.admin.widgets.AutocompleteMixin.get_url` | `django.contrib.admin.widgets` | L4 | no | no |
| 1 | `django.contrib.admin.widgets.RelatedFieldWidgetWrapper.get_related_url` | `django.contrib.admin.widgets` | L4 | no | no |
| 1 | `django.contrib.auth.admin.UserAdmin.user_change_password` | `django.contrib.auth.admin` | L4 | no | no |
| 1 | `django.contrib.flatpages.models.FlatPage.get_absolute_url` | `django.contrib.flatpages.models` | L4 | no | no |
| 1 | `django.contrib.gis.sitemaps.kml.KMLSitemap.location` | `django.contrib.gis.sitemaps.kml` | L4 | no | no |
| 1 | `django.shortcuts.resolve_url` | `django.shortcuts` | L4 | no | no |
| 1 | `tests.admin_custom_urls.models.CarAdmin.response_add` | `tests.admin_custom_urls.models` | L5 | no | no |
| 1 | `tests.admin_custom_urls.models.PersonAdmin.response_post_save_add` | `tests.admin_custom_urls.models` | L5 | no | no |
| 1 | `tests.admin_custom_urls.models.PersonAdmin.response_post_save_change` | `tests.admin_custom_urls.models` | L5 | no | no |
| 1 | `tests.generic_views.models.Artist.get_absolute_url` | `tests.generic_views.models` | L5 | no | no |
| 1 | `tests.generic_views.views.SpecializedAuthorCreate.get_success_url` | `tests.generic_views.views` | L5 | no | no |
| 1 | `tests.generic_views.views.SpecializedAuthorUpdate.get_success_url` | `tests.generic_views.views` | L5 | no | no |
| 1 | `tests.sitemaps_tests.models.I18nTestModel.get_absolute_url` | `tests.sitemaps_tests.models` | L5 | no | no |
| 1 | `django.contrib.admindocs.utils.parse_rst` | `django.contrib.admindocs.utils` | L4 | no | no |
| 1 | `tests.generic_views.views.AuthorDeleteFormView.get_success_url` | `tests.generic_views.views` | L5 | no | no |
| 1 | `tests.messages_tests.urls.add` | `tests.messages_tests.urls` | L5 | no | no |
| 1 | `tests.messages_tests.urls.add_template_response` | `tests.messages_tests.urls` | L5 | no | no |
| 1 | `tests.urlpatterns_reverse.middleware.ReverseInnerInResponseMiddleware.process_response` | `tests.urlpatterns_reverse.middleware` | L5 | no | no |
| 1 | `tests.urlpatterns_reverse.middleware.ReverseInnerInStreaming.stream` | `tests.urlpatterns_reverse.middleware` | L5 | no | no |
| 1 | `tests.urlpatterns_reverse.middleware.ReverseOuterInResponseMiddleware.process_response` | `tests.urlpatterns_reverse.middleware` | L5 | no | no |
| 1 | `tests.urlpatterns_reverse.middleware.ReverseOuterInStreaming.stream` | `tests.urlpatterns_reverse.middleware` | L5 | no | no |
| 1 | `tests.user_commands.management.commands.reverse_url.Command.handle` | `tests.user_commands.management.commands.reverse_url` | L5 | no | no |
| 2 | `django.contrib.admin.helpers.AdminReadonlyField.contents` | `django.contrib.admin.helpers` | L4 | no | no |
| 2 | `django.contrib.admin.helpers.InlineAdminFormSet.__iter__` | `django.contrib.admin.helpers` | L4 | no | no |
| 2 | `django.contrib.admin.options.ModelAdmin._changeform_view` | `django.contrib.admin.options` | L4 | no | no |
| 2 | `django.contrib.admin.options.ModelAdmin._delete_view` | `django.contrib.admin.options` | L4 | no | no |
| 2 | `django.contrib.admin.options.ModelAdmin.history_view` | `django.contrib.admin.options` | L4 | no | no |
| 2 | `django.contrib.admin.options.ModelAdmin.render_change_form` | `django.contrib.admin.options` | L4 | no | no |
| 2 | `django.contrib.admin.options.ModelAdmin.response_post_save_add` | `django.contrib.admin.options` | L4 | no | no |
| 2 | `django.contrib.admin.options.ModelAdmin.response_post_save_change` | `django.contrib.admin.options` | L4 | no | no |
| 2 | `django.contrib.admin.sites.AdminSite.get_app_list` | `django.contrib.admin.sites` | L4 | no | no |
| 2 | `django.contrib.admin.templatetags.admin_list.items_for_result` | `django.contrib.admin.templatetags.admin_list` | L4 | no | no |
| 2 | `django.contrib.admin.utils.NestedObjects._nested` | `django.contrib.admin.utils` | L4 | no | no |
| 2 | `django.contrib.admin.utils.get_deleted_objects` | `django.contrib.admin.utils` | L4 | no | no |
| 2 | `django.contrib.admin.widgets.AutocompleteMixin.build_attrs` | `django.contrib.admin.widgets` | L4 | no | no |
| 2 | `django.contrib.admin.widgets.ManyToManyRawIdWidget.get_context` | `django.contrib.admin.widgets` | L4 | no | no |
| 2 | `django.contrib.admin.widgets.RelatedFieldWidgetWrapper.get_context` | `django.contrib.admin.widgets` | L4 | no | no |
| 2 | `django.contrib.admindocs.views.ModelDetailView.get_context_data` | `django.contrib.admindocs.views` | L4 | no | no |
| 2 | `django.contrib.admindocs.views.TemplateFilterIndexView.get_context_data` | `django.contrib.admindocs.views` | L4 | no | no |
| 2 | `django.contrib.admindocs.views.TemplateTagIndexView.get_context_data` | `django.contrib.admindocs.views` | L4 | no | no |
| 2 | `django.contrib.admindocs.views.ViewDetailView.get_context_data` | `django.contrib.admindocs.views` | L4 | no | no |
| 2 | `django.contrib.auth.admin.UserAdmin.response_add` | `django.contrib.auth.admin` | L4 | no | no |
| 2 | `django.contrib.auth.decorators._wrapper_view` | `django.contrib.auth.decorators` | L4 | no | no |
| 2 | `django.contrib.auth.mixins.AccessMixin.handle_no_permission` | `django.contrib.auth.mixins` | L4 | no | no |
| 2 | `django.contrib.auth.views.LoginView.get_default_redirect_url` | `django.contrib.auth.views` | L4 | no | no |
| 2 | `django.contrib.auth.views.LogoutView.get_default_redirect_url` | `django.contrib.auth.views` | L4 | no | no |
| 2 | `django.contrib.auth.views.PasswordResetCompleteView.get_context_data` | `django.contrib.auth.views` | L4 | no | no |
| 2 | `django.contrib.auth.views.RedirectURLMixin.get_default_redirect_url` | `django.contrib.auth.views` | L4 | no | no |
| 2 | `django.contrib.auth.views.logout_then_login` | `django.contrib.auth.views` | L4 | no | no |
| 2 | `django.contrib.auth.views.redirect_to_login` | `django.contrib.auth.views` | L4 | no | no |
| 2 | `django.contrib.sitemaps.ping_google` | `django.contrib.sitemaps` | L4 | no | **yes** |
| 2 | `django.shortcuts.redirect` | `django.shortcuts` | L4 | no | no |
| 2 | `django.views.generic.base.RedirectView.get` | `django.views.generic.base` | L4 | no | **yes** |
| 2 | `django.views.i18n.set_language` | `django.views.i18n` | L4 | no | no |
| 2 | `tests.admin_views.customadmin.Admin2.password_change` | `tests.admin_views.customadmin` | L5 | no | no |
| 2 | `tests.urlpatterns_reverse.middleware.ReverseInnerInStreaming.process_view` | `tests.urlpatterns_reverse.middleware` | L5 | no | no |
| 2 | `tests.urlpatterns_reverse.middleware.ReverseOuterInStreaming.process_view` | `tests.urlpatterns_reverse.middleware` | L5 | no | no |
| 3 | `django.contrib.admin.options.ModelAdmin.changeform_view` | `django.contrib.admin.options` | L4 | no | no |
| 3 | `django.contrib.admin.options.ModelAdmin.delete_view` | `django.contrib.admin.options` | L4 | no | no |
| 3 | `django.contrib.admin.options.ModelAdmin.get_deleted_objects` | `django.contrib.admin.options` | L4 | no | no |
| 3 | `django.contrib.admin.sites.AdminSite.app_index` | `django.contrib.admin.sites` | L4 | no | no |
| 3 | `django.contrib.admin.sites.AdminSite.each_context` | `django.contrib.admin.sites` | L4 | no | no |
| 3 | `django.contrib.admin.sites.AdminSite.index` | `django.contrib.admin.sites` | L4 | no | no |
| 3 | `django.contrib.admin.templatetags.admin_list.results` | `django.contrib.admin.templatetags.admin_list` | L4 | no | no |
| 3 | `django.contrib.admin.utils.NestedObjects.nested` | `django.contrib.admin.utils` | L4 | no | no |
| 3 | `django.contrib.auth.mixins.LoginRequiredMixin.dispatch` | `django.contrib.auth.mixins` | L4 | no | no |
| 3 | `django.contrib.auth.mixins.PermissionRequiredMixin.dispatch` | `django.contrib.auth.mixins` | L4 | no | no |
| 3 | `django.contrib.auth.mixins.UserPassesTestMixin.dispatch` | `django.contrib.auth.mixins` | L4 | no | no |
| 3 | `django.contrib.auth.views.RedirectURLMixin.get_success_url` | `django.contrib.auth.views` | L4 | no | no |
| 3 | `django.contrib.flatpages.views.render_flatpage` | `django.contrib.flatpages.views` | L4 | no | no |
| 3 | `django.contrib.sitemaps.management.commands.ping_google.Command.handle` | `django.contrib.sitemaps.management.commands.ping_google` | L4 | no | no |
| 3 | `django.views.generic.base.RedirectView.delete` | `django.views.generic.base` | L4 | no | no |
| 3 | `django.views.generic.base.RedirectView.head` | `django.views.generic.base` | L4 | no | no |
| 3 | `django.views.generic.base.RedirectView.options` | `django.views.generic.base` | L4 | no | no |
| 3 | `django.views.generic.base.RedirectView.patch` | `django.views.generic.base` | L4 | no | no |
| 3 | `django.views.generic.base.RedirectView.post` | `django.views.generic.base` | L4 | no | no |
| 3 | `django.views.generic.base.RedirectView.put` | `django.views.generic.base` | L4 | no | no |
| 3 | `tests.admin_views.customadmin.Admin2.get_app_list` | `tests.admin_views.customadmin` | L5 | no | no |

### `django_t5_003_redirect_url_safety_check` (`django.utils.http.url_has_allowed_host_and_scheme`)

7 production callers, all 7 gold. same module (L0–L2): —; cross-module (L3–L5): 7/7 = 1.00. All are cross-package (`django.contrib.auth.views`, `django.views.i18n`) and outside the seed's contract family (`django/utils/`). A family filter would drop **all 7 gold**.

| hop | caller | module | scope | in family | gold |
|---|---|---|---|---|---|
| 1 | `django.contrib.auth.views.RedirectURLMixin.get_redirect_url` | `django.contrib.auth.views` | L4 | no | **yes** |
| 1 | `django.views.i18n.set_language` | `django.views.i18n` | L4 | no | **yes** |
| 2 | `django.contrib.auth.views.LoginView.get_context_data` | `django.contrib.auth.views` | L4 | no | **yes** |
| 2 | `django.contrib.auth.views.RedirectURLMixin.get_success_url` | `django.contrib.auth.views` | L4 | no | **yes** |
| 3 | `django.contrib.auth.views.LoginView.dispatch` | `django.contrib.auth.views` | L4 | no | **yes** |
| 3 | `django.contrib.auth.views.LoginView.form_valid` | `django.contrib.auth.views` | L4 | no | **yes** |
| 3 | `django.contrib.auth.views.LogoutView.post` | `django.contrib.auth.views` | L4 | no | **yes** |

### `django_t13_004_blast_options_get_field`: top 10 by hop

129 callers, 8 gold. same module (L0–L2): —; cross-module (L3–L5): 8/129 = 0.06. Gold and non-gold are interleaved at L4 across `admin.options`, `admin.utils` and `contenttypes.fields`.

| hop | caller | module | scope | in family | gold |
|---|---|---|---|---|---|
| 1 | `django.contrib.admin.checks.BaseModelAdminChecks._check_filter_item` | `django.contrib.admin.checks` | L4 | no | no |
| 1 | `django.contrib.admin.checks.BaseModelAdminChecks._check_raw_id_fields_item` | `django.contrib.admin.checks` | L4 | no | no |
| 1 | `django.contrib.admin.options.BaseModelAdmin.to_field_allowed` | `django.contrib.admin.options` | L4 | no | **yes** |
| 1 | `django.contrib.admin.options.ModelAdmin.get_changeform_initial_data` | `django.contrib.admin.options` | L4 | no | **yes** |
| 1 | `django.contrib.admin.utils._get_non_gfk_field` | `django.contrib.admin.utils` | L4 | no | no |
| 1 | `django.contrib.admin.utils.lookup_spawns_duplicates` | `django.contrib.admin.utils` | L4 | no | **yes** |
| 1 | `django.contrib.admin.utils.reverse_field_path` | `django.contrib.admin.utils` | L4 | no | **yes** |
| 1 | `django.contrib.contenttypes.fields.GenericForeignKey.__get__` | `django.contrib.contenttypes.fields` | L4 | no | **yes** |
| 1 | `django.contrib.contenttypes.fields.GenericRelation.get_extra_restriction` | `django.contrib.contenttypes.fields` | L4 | no | no |
| 1 | `django.contrib.contenttypes.fields.GenericRelation.get_path_info` | `django.contrib.contenttypes.fields` | L4 | no | no |

### `trpc_t5_012_response_serialization`: top 10 by hop

18 callers, 11 gold. same module (L0–L2): —; cross-module (L3–L5): 11/18 = 0.61. Gold spans `http/` and every `adapters/*` handler, all at L5 relative to the seed in `shared/`. The non-gold callers (`adapters.ws.respond`, `createContextAsync`, `handleRequest`) are in the *same* adapter modules as gold ones.

| hop | caller | module | scope | in family | gold |
|---|---|---|---|---|---|
| 1 | `http.resolveHTTPResponse.caughtErrorToData` | `http.resolveHTTPResponse` | L5 | no | **yes** |
| 1 | `http.resolveHTTPResponse.resolveHTTPResponse` | `http.resolveHTTPResponse` | L5 | no | **yes** |
| 1 | `adapters.ws.respond` | `adapters.ws` | L5 | no | no |
| 2 | `adapters.aws-lambda.awsLambdaRequestHandler` | `adapters.aws-lambda` | L5 | no | **yes** |
| 2 | `adapters.fastify.fastifyRequestHandler.fastifyRequestHandler` | `adapters.fastify.fastifyRequestHandler` | L5 | no | **yes** |
| 2 | `adapters.fetch.fetchRequestHandler.fetchRequestHandler` | `adapters.fetch.fetchRequestHandler` | L5 | no | **yes** |
| 2 | `adapters.node-http.nodeHTTPRequestHandler.nodeHTTPRequestHandler` | `adapters.node-http.nodeHTTPRequestHandler` | L5 | no | **yes** |
| 2 | `adapters.ws.applyWSSHandler` | `adapters.ws` | L5 | no | **yes** |
| 2 | `adapters.ws.createContextAsync` | `adapters.ws` | L5 | no | no |
| 2 | `adapters.ws.handleRequest` | `adapters.ws` | L5 | no | no |

**In none of the four seeds does scope distance cleanly separate gold from non-gold.** Blast-radius gold is, by construction, the code that *consumes* the anchor from elsewhere, and in these frameworks consumers live in other packages (admin consumes `urls`, adapters consume `shared`).

## 5. The anchor's contract family

Family definition, applied mechanically:
- **Method seed:** the enclosing class, plus every class linked to it by EXTENDS/IMPLEMENTS edges in either direction (its hierarchy), plus the seed's file.
- **Module-level function:** the seed's package (directory).
- **Framework hook:** the hook namespace. None of the 32 T5 seeds is registered through a framework hook table. The middleware and dependency seeds (`core.middleware.*`, `fastapi.dependencies.utils.*`) are plain module functions, so they fall under the package rule.

| corpus | seed | contract family | callers in family (gold / n) | callers outside (gold / n) |
|---|---|---|---|---|
| fastapi | `fastapi.dependencies.utils.get_dependant` | function: package `fastapi/dependencies/` | 4/4 = 1.00 | 3/4 = 0.75 |
| fastapi | `fastapi.dependencies.utils.request_params_to_args` | function: package `fastapi/dependencies/` | 1/1 = 1.00 | 1/2 = 0.50 |
| fastapi | `fastapi.routing.APIRouter.add_api_route` | method: class `APIRouter` + 0 related classes, ∪ file `fastapi/routing.py` | 1/2 = 0.50 | 3/3 = 1.00 |
| fastapi | `fastapi.routing.APIRouter.add_api_websocket_route` | method: class `APIRouter` + 0 related classes, ∪ file `fastapi/routing.py` | 1/2 = 0.50 | 2/3 = 0.67 |
| fastapi | `fastapi.openapi.utils.get_openapi_path` | function: package `fastapi/openapi/` | 1/1 = 1.00 | 1/3 = 0.33 |
| fastapi | `fastapi.openapi.utils.get_fields_from_routes` | function: package `fastapi/openapi/` | 1/1 = 1.00 | 1/3 = 0.33 |
| fastapi | `fastapi.openapi.utils.get_openapi_security_definitions` | function: package `fastapi/openapi/` | 2/2 = 1.00 | 1/2 = 0.50 |
| fastapi | `fastapi.utils.get_value_or_default` | function: package `fastapi/` | 5/6 = 0.83 | — |
| django | `django.urls.base.reverse` | function: package `django/urls/` | 0/1 = 0.00 | 8/98 = 0.08 |
| django | `django.db.models.query.QuerySet.get` | method: class `QuerySet` + 9 related classes, ∪ file `django/db/models/query.py` | 1/4 = 0.25 | 6/42 = 0.14 |
| django | `django.forms.fields.Field.clean` | method: class `Field` + 36 related classes, ∪ file `django/forms/fields.py` | 1/5 = 0.20 | 0/2 = 0.00 |
| django | `django.db.models.options.Options.get_field` | method: class `Options` + 0 related classes, ∪ file `django/db/models/options.py` | — | 8/129 = 0.06 |
| django | `django.forms.forms.BaseForm.full_clean` | method: class `BaseForm` + 91 related classes, ∪ file `django/forms/forms.py` | 1/1 = 1.00 | 1/1 = 1.00 |
| django | `django.core.mail.send_mail` | function: package `django/core/mail/` | — | 2/2 = 1.00 |
| django | `django.utils.http.url_has_allowed_host_and_scheme` | function: package `django/utils/` | — | 7/7 = 1.00 |
| django | `django.http.response.HttpResponse.__init__` | method: class `HttpResponse` + 17 related classes, ∪ file `django/http/response.py` | 6/7 = 0.86 | — |
| express | `lib.router.next` | function: package `lib/router/` | 2/3 = 0.67 | 0/5 = 0.00 |
| express | `lib.router.layer.Layer` | function: package `lib/router/` | — | — |
| trpc | `core.internals.procedureBuilder.createProcedureCaller` | function: package `core/internals/` | 4/4 = 1.00 | — |
| trpc | `core.router.createRouterFactory` | function: package `core/` | 0/2 = 0.00 | 3/5 = 0.60 |
| trpc | `core.internals.procedureBuilder.createNewBuilder` | function: package `core/internals/` | 9/9 = 1.00 | — |
| trpc | `core.middleware.createInputMiddleware` | function: package `core/` | — | 4/4 = 1.00 |
| trpc | `core.middleware.createOutputMiddleware` | function: package `core/` | — | 4/4 = 1.00 |
| trpc | `http.resolveHTTPResponse.resolveHTTPResponse` | function: package `http/` | — | 9/9 = 1.00 |
| trpc | `http.resolveHTTPResponse.initResponse` | function: package `http/` | 1/1 = 1.00 | 8/8 = 1.00 |
| trpc | `http.resolveHTTPResponse.inputToProcedureCall` | function: package `http/` | 1/1 = 1.00 | 8/8 = 1.00 |
| trpc | `http.resolveHTTPResponse.caughtErrorToData` | function: package `http/` | 1/1 = 1.00 | 8/8 = 1.00 |
| trpc | `shared.getErrorShape.getErrorShape` | function: package `shared/` | — | 10/13 = 0.77 |
| trpc | `core.internals.procedureBuilder.createResolver` | function: package `core/internals/` | 3/3 = 1.00 | — |
| trpc | `shared.transformTRPCResponse.transformTRPCResponse` | function: package `shared/` | — | 11/18 = 0.61 |
| trpc | `deprecated.interop.migrateProcedure` | function: package `deprecated/` | 2/2 = 1.00 | — |
| trpc | `rpc.parseTRPCMessage.parseTRPCMessage` | function: package `rpc/` | — | 2/2 = 1.00 |

Totals per corpus and hop:

| corpus | in family, hop 1 | out, hop 1 | in, hop 2 | out, hop 2 | in, hop 3 | out, hop 3 | in, all | out, all | gold inside family |
|---|---|---|---|---|---|---|---|---|---|
| fastapi | 10/12 = 0.83 | 5/5 = 1.00 | 6/7 = 0.86 | 6/11 = 0.55 | — | 1/4 = 0.25 | 16/19 = 0.84 | 12/20 = 0.60 | 16/28 |
| django | 7/14 = 0.50 | 22/108 = 0.20 | 2/4 = 0.50 | 7/107 = 0.07 | — | 3/66 = 0.05 | 9/18 = 0.50 | 32/281 = 0.11 | 9/41 |
| express | 2/3 = 0.67 | 0/1 = 0.00 | — | 0/1 = 0.00 | — | 0/3 = 0.00 | 2/3 = 0.67 | 0/5 = 0.00 | 2/2 |
| trpc | 14/15 = 0.93 | 17/21 = 0.81 | 7/8 = 0.88 | 27/33 = 0.82 | — | 23/25 = 0.92 | 21/23 = 0.91 | 67/79 = 0.85 | 21/88 |
| ALL | 33/44 = 0.75 | 44/135 = 0.33 | 15/19 = 0.79 | 40/152 = 0.26 | — | 27/98 = 0.28 | 48/63 = 0.76 | 111/385 = 0.29 | 48/159 |

**The family contains a minority of the gold:** 9 of 41 gold callers on Django and 21 of 88 on tRPC.
- **Seeds where every gold caller is outside the family:** 11 of the 31 seeds that have any gold within 3 hops. On Django these are `reverse`, `send_mail`, `url_has_allowed_host_and_scheme` and `Options.get_field`. On tRPC they are `createRouterFactory`, `createInputMiddleware`, `createOutputMiddleware`, `resolveHTTPResponse`, `getErrorShape`, `transformTRPCResponse` and `parseTRPCMessage`.
- **Seeds where gold is concentrated inside the family:** `HttpResponse.__init__` (6/7, subclass constructors), `createNewBuilder` (9/9) and `createResolver` (3/3).
- **Pooled gaps come from these opposite kinds of seed** (Django in-family 0.50 vs out 0.11; pooled 0.76 vs 0.29), not from in-family callers outranking siblings.
- **As a filter it would discard most gold:** skipping out-of-family callers loses 32 of 41 gold on Django and 67 of 88 on tRPC.

## 6. Overlap with Arm 2's directory locality

Fraction of each corpus's T5 gold that each arm delivered, split by the gold symbol's contract-family membership, over all seeds' cells. "Not within 3 hops" is gold beyond 3 upstream hops or unresolved in the graph.

| corpus | Arm 2: in family | Arm 2: outside | Arm 2: not within 3 hops | PRISM: in family | PRISM: outside |
|---|---|---|---|---|---|
| FastAPI | 36/48 = 0.75 | 15/36 = 0.42 | 0/12 | 21/48 = 0.44 | 11/36 = 0.31 |
| Django | 21/27 = 0.78 | 15/105 = 0.14 | 3/3 | 6/27 = 0.22 | 20/105 = 0.19 |
| Express (n=2) | 3/6 = 0.50 | — | 9/9 | 3/6 = 0.50 | — |
| tRPC | 63/63 = 1.00 | 144/201 = 0.72 | 0/24 | 14/63 = 0.22 | 18/201 = 0.09 |

- **Arm 2's locality and the contract family coincide.** Arm 2 delivers in-family gold at 0.75–1.00 and out-of-family gold at much lower rates on FastAPI and Django (0.42, 0.14). Its directory packing is a lexical proxy for "same file / same package", which is what the family rule mostly is.
- **But Arm 2's win is not a contract-family *ranking* signal.** Within a seed, family membership does not predict gold (§3). What the table shows is a **reach** difference: PRISM misses in-family gold at nearly the same rate as out-of-family gold (0.22 vs 0.19 on Django, 0.22 vs 0.09 on tRPC), because its upstream channel is ≤3 direct callers. Arm 2 picks up the in-family share for free by packing the seed's neighbourhood. On tRPC it also reaches 72% of out-of-family gold, through volume (about 12.5k tokens) rather than locality.
- **Design C's reverse walk covers both shares.** The 3-hop production walk reaches 91.1% of Django and 91.7% of tRPC T5 gold (`dynamic_hop_investigation.md` §4) regardless of family.

## 7. Recommendation

The contract-family (scope) hypothesis is **refuted as a signal for Design C**. Pooled, callers inside the anchor's contract family look far more likely to be gold (0.76 vs 0.29; Django 0.50 vs 0.11), but the gap comes from *which* seeds have in-family callers. Within a seed it disappears: Django within-task AUC is 0.503 for family and 0.553 for scope level, against 0.726 for hop and 0.790 for `W_upstream` within hop 1.

Used as a filter (skip L4/L5 or out-of-family callers), it would discard 32 of 41 Django and 67 of 88 tRPC gold callers, all 7 gold of `url_has_allowed_host_and_scheme`, and all 8 of `reverse`, because blast-radius consumers characteristically live in other packages. Used as a boost (same-scope first within each hop), it lowers Django precision@5 from 25/34 to 21–22/34 and leaves tRPC unchanged. The only gain is +3 R-precision on FastAPI's 35 candidates.

Design C therefore proceeds as planned: walk upstream and downstream by hop, order callers within each hop by PRISM's existing `W_upstream` tier, drop VERIFICATION-role symbols, stop at the shared budget. No scope boost, no scope filter.

What Arm 2's directory locality reveals is a **reach** gap, not a ranking signal: PRISM fails to deliver in-family gold almost as often as out-of-family gold. Design C's multi-hop upstream walk closes that gap without needing a scope rule. Retest scope if a future corpus has framework hook registries (where "contract family" can be read from registration, not from directory layout). None of the 32 current T5 seeds is such a hook.
