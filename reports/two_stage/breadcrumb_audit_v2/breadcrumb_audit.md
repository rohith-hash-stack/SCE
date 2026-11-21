# Breadcrumb static audit (no model calls)

Old rule: `prism.graph.entry_points` at `15baac1` (non-builtin decorator + no in-repo caller; every path of every slice symbol, appended as a block). New rule: decorator taxonomy + call registrations, slice-boundary paths, invariants, <= 80 tokens per slice, inline.

## prism_oracle slices (the gold slice)

| corpus | tasks | old fires | new fires | old tokens mean / max / total | new tokens mean / max / total | violations caught |
|---|---|---|---|---|---|---|
| fastapi | 25 | 4 | 0 | 116.8 / 213 / 467 | 0 / 0 / 0 | 0 |
| django | 20 | 8 | 4 | 270.1 / 543 / 2161 | 28.5 / 40 / 114 | 0 |
| express | 20 | 0 | 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| trpc | 25 | 0 | 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| **all** | 90 | 12 | 4 | 219.0 / 543 / 2628 | 28.5 / 40 / 114 | 0 |

## prism_ask slices

| corpus | tasks | old fires | new fires | old tokens mean / max / total | new tokens mean / max / total | violations caught |
|---|---|---|---|---|---|---|
| fastapi | 25 | 15 | 3 | 170.5 / 393 / 2557 | 29.3 / 53 / 88 | 0 |
| django | 20 | 8 | 2 | 115.6 / 302 / 925 | 35.5 / 46 / 71 | 0 |
| express | 20 | 0 | 3 | 0 / 0 / 0 | 17.7 / 20 / 53 | 0 |
| trpc | 25 | 0 | 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| **all** | 90 | 23 | 8 | 151.4 / 393 / 3482 | 26.5 / 53 / 212 | 0 |

## Entry points per corpus

| corpus | old | new | new by source |
|---|---|---|---|
| fastapi | 1320 | 799 | decorator 799 |
| django | 421 | 14 | call:django 10, decorator:registration 4 |
| express | 0 | 7 | call:express 7 |
| trpc | 0 | 0 |  |

## The 12 tasks where breadcrumbs fired in Stage 2 (prism_oracle slice)

| task | old paths / tokens / heads | new paths / tokens | new breadcrumb |
|---|---|---|---|
| fastapi_t02_005_request_validation_error_response | 6 / 213 / PUT /items/{item_id} x3, @app.exception_handler x3 | 0 / 0 | - |
| fastapi_t02_012_openapi_security_definitions_serialization | 3 / 104 / PUT /items/{item_id} x3 | 0 / 0 | - |
| fastapi_t02_013_swagger_ui_html_generation | 2 / 79 / GET /docs x2 | 0 / 0 | - |
| fastapi_t02_023_redoc_html_generation | 2 / 71 / GET /redoc x2 | 0 / 0 | - |
| django_t02_002_queryset_delete_cascade_pipeline | 4 / 201 / @action x3, @csrf_protect_m x1 | 0 / 0 | - |
| django_t02_004_url_resolve_traversal | 10 / 366 / @no_append_slash x4, @method_decorator x1, @sensitive_post_parameters_m x1, @register x4 | 1 / 25 | `[Entry: URL setlang/] -> set_language() -> translate_url() => enters at resolve()` |
| django_t02_009_queryset_filter_clone | 6 / 334 / @action x2, @sensitive_post_parameters_m x2, @x_robots_tag x2 | 1 / 40 | `[Entry: URL <path:url>] -> flatpage() -> get_object_or_404() -> QuerySet.get() => enters at QuerySet.filter()` |
| django_t02_011_permissions_backend_check | 1 / 69 / @sensitive_post_parameters_m x1 | 0 / 0 | - |
| django_t02_014_send_mail_pipeline | 2 / 104 / @admin.action x2 | 0 / 0 | - |
| django_t02_015_admin_each_context | 9 / 413 / @action x3, @method_decorator x3, @sensitive_post_parameters_m x3 | 0 / 0 | - |
| django_t02_016_db_cursor_connection | 9 / 543 / @async_unsafe x9 | 0 / 0 | - |
| django_t02_017_redirect_url_safety_check | 2 / 131 / @method_decorator x2 | 1 / 25 | `[Entry: URL setlang/] -> set_language() => enters at url_has_allowed_host_and_scheme()` |
