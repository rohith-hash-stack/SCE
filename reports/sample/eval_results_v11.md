# Prism v1.1+ Empirical Benchmark Results

10 (engine, task, budget) cells evaluated.

## Task Success Rate (TSR)

Bootstrap 95% CI, `n_resamples=10000`.

+----------------------------+--------+-------+----------------+---+---------------+
| Engine                     | Budget | TSR   | 95% CI         | N | Seed Variance |
+----------------------------+--------+-------+----------------+---+---------------+
| baseline_bfs_bidirectional | 4000   | 0.500 | [0.000, 1.000] | 2 | 0.0000        |
| baseline_bfs_forward       | 4000   | 0.500 | [0.000, 1.000] | 2 | 0.0000        |
| baseline_rag               | 4000   | 0.500 | [0.000, 1.000] | 2 | 0.0000        |
| oracle                     | 4000   | 0.500 | [0.000, 1.000] | 2 | 0.0000        |
| prism_v11                  | 4000   | 1.000 | [1.000, 1.000] | 2 | 0.0000        |
+----------------------------+--------+-------+----------------+---+---------------+

## Diagnostic Metrics (mean per engine)

+----------------------------+----------------+------------+--------+------------+-------+
| Engine                     | cpi_fractional | cpi_strict | fcc    | fpr_oracle | src   |
+----------------------------+----------------+------------+--------+------------+-------+
| baseline_bfs_bidirectional | 1.000          | 1.000      | —      | 0.542      | 0.698 |
| baseline_bfs_forward       | 1.000          | 1.000      | —      | 0.500      | 0.667 |
| baseline_rag               | 0.433          | 0.000      | —      | 0.970      | 0.978 |
| oracle                     | 1.000          | 1.000      | 81.189 | 0.000      | 0.267 |
| prism_v11                  | 1.000          | 1.000      | 79.759 | 0.833      | 0.758 |
+----------------------------+----------------+------------+--------+------------+-------+

### Notes on Diagnostic Metrics

- **FPR redefined (divergence from Oracle).** `fpr` used to mean `|S_M \ G*| / |S_M|` against the human-annotated ground truth `G*` alone - typically only 3-4 symbols per task, so *any* other real context an engine pulled in (imports, helpers, callers) counted as a false positive, regardless of whether it was actually relevant. Two metrics are now reported: `fpr_gt` (the original definition, kept in the raw JSON for transparency, excluded from this table) and **`fpr_oracle`** (shown above) - `|S_M \ S_Oracle| / |S_M|`, divergence from the Oracle engine's own package for the same (task, budget). `fpr_oracle` is `—` wherever no Oracle run was configured/available for that cell.
- **FCC (`—` = n/a, not zero).** FCC is an internal packing-density diagnostic defined over Prism's four-axis coordinate space. It is structurally inapplicable to topological and lexical baselines that do not operate over that space - their rows show `—`, never a fabricated `0.000`.

## Raw LLM Responses

Truncated to 200 characters for readability - the full, untruncated text for every call is in `eval_results_v11.json`'s own `raw_responses` field per record.

+------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Task                                     | Engine                     | Budget | Seed Index | TSR Score | Response (truncated)                                                                                                                                                                                      |
+------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| django_t02_001_request_middleware_chain  | oracle                     | 4000   | 0          | 0.0       | { "reasoning": "The request starts at `BaseHandler._get_response`, where the view callback is resolved and wrapped with middleware. If an exception is raised during the view's execution, it is caught … |
| django_t02_001_request_middleware_chain  | prism_v11                  | 4000   | 0          | 1.0       | { "reasoning": "The request starts at `BaseHandler._get_response`, where the view callback is resolved via `resolve_request`. The callback is then wrapped with `make_view_atomic` to handle atomic tran… |
| django_t02_001_request_middleware_chain  | baseline_rag               | 4000   | 0          | 0.0       | { "reasoning": "The request starts with BaseHandler._get_response, where the view is resolved and wrapped. The wrapped view is then invoked, and any exceptions raised during its execution are caught a… |
| django_t02_001_request_middleware_chain  | baseline_bfs_forward       | 4000   | 0          | 0.0       | { "reasoning": "The request starts at `BaseHandler._get_response`, where the view callback is resolved and wrapped in an atomic transaction. If an exception is raised during the view's execution, it i… |
| django_t02_001_request_middleware_chain  | baseline_bfs_bidirectional | 4000   | 0          | 0.0       | { "reasoning": "The request starts at `BaseHandler._get_response`, where the view callback is resolved and wrapped in an atomic transaction. If an exception is raised during the view's execution, it i… |
| django_t02_011_permissions_backend_check | oracle                     | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `PermissionsMixin.has_perm`, which calls `_user_has_perm`. `_user_has_perm` then iterates over all authentication backends, calling `has_perm` on each, and fin… |
| django_t02_011_permissions_backend_check | prism_v11                  | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with the `has_perm` method on `PermissionsMixin`, which calls `_user_has_perm` to check permissions across all backends. `_user_has_perm` then iterates over the bac… |
| django_t02_011_permissions_backend_check | baseline_rag               | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with the `has_perm` method in `PermissionsMixin`, which calls `_user_has_perm` to determine if the user has the specified permission. This method iterates over the … |
| django_t02_011_permissions_backend_check | baseline_bfs_forward       | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `PermissionsMixin.has_perm` which delegates to `_user_has_perm`. `_user_has_perm` then calls `auth.get_backends` to retrieve the list of authentication backend… |
| django_t02_011_permissions_backend_check | baseline_bfs_bidirectional | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `PermissionsMixin.has_perm`, which delegates to `_user_has_perm`. `_user_has_perm` then calls `BaseBackend.has_perm`, which in turn calls `get_backends` to ret… |
+------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

