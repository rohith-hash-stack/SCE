# Breadcrumb verification

Model `qwen2.5-coder:14b-instruct-q8_0`, code `867cf015161d`. New cells: this run; baselines: the frozen Stage 2 cells (`prism_oracle` against `normal_oracle` = `pragmatic_oracle`; `prism_ask` against the old `prism_ask`). TSR is the causal-trace success rate (`score_debug_causal`).

## Pre-registered criteria

- PASS - no task drops by >= 3/10 (worst drop: 2)
- PASS - mean per-task delta TSR >= -0.01 (observed +0.015)
- PASS - django_t02_016 recovers to 10/10 under prism_oracle (its prompt is now byte-identical to normal_oracle's; identical prompts varied by up to 1/10 in Stage 2)

**Overall: PASS**

## prism_oracle (7 tasks)

| task | normal_oracle | old prism_oracle | new prism_oracle | delta TSR | new breadcrumb tokens | errors |
|---|---|---|---|---|---|---|
| django_t02_004_url_resolve_traversal | 10/10 | 10/10 | 10/10 | +0.00 | 20 | 0 |
| django_t02_009_queryset_filter_clone | 10/10 | 10/10 | 10/10 | +0.00 | 31 | 0 |
| django_t02_016_db_cursor_connection | 10/10 | 4/10 | 10/10 | +0.00 | 0 | 0 |
| django_t02_017_redirect_url_safety_check | 10/10 | 10/10 | 10/10 | +0.00 | 21 | 0 |
| django_t02_019_request_get_host_validation | 10/10 | 10/10 | 10/10 | +0.00 | 18 | 0 |
| fastapi_t02_013_swagger_ui_html_generation | 10/10 | 10/10 | 10/10 | +0.00 | 23 | 0 |
| fastapi_t02_023_redoc_html_generation | 10/10 | 10/10 | 10/10 | +0.00 | 21 | 0 |

## prism_ask (26 tasks)

| task | old prism_ask | new prism_ask | delta TSR | new breadcrumb tokens | errors |
|---|---|---|---|---|---|
| django_t02_002_queryset_delete_cascade_pipeline | 0/10 | 0/10 | +0.00 | 0 | 0 |
| django_t02_004_url_resolve_traversal | 0/10 | 0/10 | +0.00 | 34 | 0 |
| django_t02_006_auth_get_user_resolution | 0/10 | 1/10 | +0.10 | 0 | 0 |
| django_t02_009_queryset_filter_clone | 0/10 | 0/10 | +0.00 | 0 | 0 |
| django_t02_010_i18n_catalog_translation | 0/10 | 0/10 | +0.00 | 12 | 0 |
| django_t02_011_permissions_backend_check | 0/10 | 1/10 | +0.10 | 0 | 0 |
| django_t02_015_admin_each_context | 7/10 | 7/10 | +0.00 | 0 | 0 |
| django_t02_017_redirect_url_safety_check | 0/10 | 0/10 | +0.00 | 21 | 0 |
| express_t02_006_response_json_send | 9/10 | 7/10 | -0.20 | 15 | 0 |
| express_t02_010_redirection | 0/10 | 0/10 | +0.00 | 15 | 0 |
| express_t02_018_type_is_alias_mismatch | 0/10 | 0/10 | +0.00 | 11 | 0 |
| fastapi_t02_001_dependant_tree_construction | 0/10 | 0/10 | +0.00 | 38 | 0 |
| fastapi_t02_004_route_registration_pipeline | 0/10 | 0/10 | +0.00 | 19 | 0 |
| fastapi_t02_005_request_validation_error_response | 6/10 | 6/10 | +0.00 | 13 | 0 |
| fastapi_t02_011_get_fields_from_routes_recursion | 0/10 | 0/10 | +0.00 | 38 | 0 |
| fastapi_t02_012_openapi_security_definitions_serialization | 10/10 | 10/10 | +0.00 | 19 | 0 |
| fastapi_t02_013_swagger_ui_html_generation | 10/10 | 10/10 | +0.00 | 16 | 0 |
| fastapi_t02_014_http_basic_auth_extraction | 10/10 | 10/10 | +0.00 | 38 | 0 |
| fastapi_t02_015_http_bearer_auth_extraction | 10/10 | 10/10 | +0.00 | 19 | 0 |
| fastapi_t02_016_oauth2_password_bearer_extraction | 10/10 | 10/10 | +0.00 | 19 | 0 |
| fastapi_t02_017_api_key_header_validation | 10/10 | 10/10 | +0.00 | 19 | 0 |
| fastapi_t02_021_openid_connect_extraction | 10/10 | 10/10 | +0.00 | 19 | 0 |
| fastapi_t02_022_http_digest_auth_extraction | 10/10 | 10/10 | +0.00 | 19 | 0 |
| fastapi_t02_023_redoc_html_generation | 10/10 | 10/10 | +0.00 | 46 | 0 |
| fastapi_t02_024_get_value_or_default_utility | 0/10 | 5/10 | +0.50 | 60 | 0 |
| fastapi_t02_025_api_key_cookie_validation | 10/10 | 10/10 | +0.00 | 38 | 0 |

