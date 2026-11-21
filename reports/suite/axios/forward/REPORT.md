# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 12 | 4 / 14 | 3 | 6 | 0.71 | 1.0 | 3 / 4 |
| all | PRISM | 12 | 7 / 14 | 6 | 3 | 0.41 | 0.68 | 6 / 7 |
| all | PRISM_BREAKS | 12 | 9 / 14 | 8 | 4 | 0.45 | 0.72 | 7 / 9 |
| easy | FILE | 11 | 4 / 13 | 3 | 6 | 0.71 | 1.0 | 3 / 4 |
| easy | PRISM | 11 | 6 / 13 | 5 | 2 | 0.48 | 0.73 | 5 / 6 |
| easy | PRISM_BREAKS | 11 | 8 / 13 | 7 | 3 | 0.5 | 0.77 | 6 / 8 |
| medium | FILE | 1 | 0 / 1 | 0 | 0 | None | None | 0 / 0 |
| medium | PRISM | 1 | 1 / 1 | 1 | 1 | 0.0 | 0.33 | 1 / 1 |
| medium | PRISM_BREAKS | 1 | 1 / 1 | 1 | 1 | 0.0 | 0.33 | 1 / 1 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| axios-forward-001 | easy | axios | `lib.adapters.http.setProxy` | 1 | 0 | 1 | 1 |
| axios-forward-002 | easy | axios | `lib.adapters.http.setProxy` | 2 | 0 | 1 | 1 |
| axios-forward-003 | easy | axios | `lib.helpers.composeSignals.onabort` | 1 | 0 | 0 | 0 |
| axios-forward-004 | easy | axios | `lib.core.mergeConfig.mergeDeepProperties` | 1 | 0 | 1 | 1 |
| axios-forward-005 | easy | axios | `lib.adapters.http.setProxy` | 1 | 0 | 1 | 1 |
| axios-forward-006 | easy | axios | `lib.helpers.toFormData.build` | 1 | 0 | 0 | 1 |
| axios-forward-007 | easy | axios | `lib.core.AxiosHeaders.matchHeaderValue` | 1 | 1 | 1 | 1 |
| axios-forward-008 | medium | axios | `lib.utils.isFormData` | 1 | 0 | 1 | 1 |
| axios-forward-009 | easy | axios | `lib.axios.axios` | 2 | 2 | 0 | 0 |
| axios-forward-010 | easy | axios | `lib.helpers.composeSignals.unsubscribe` | 1 | 0 | 0 | 0 |
| axios-forward-011 | easy | axios | `lib.utils.visit` | 1 | 1 | 1 | 1 |
| axios-forward-012 | easy | axios | `lib.helpers.toFormData.build` | 1 | 0 | 0 | 1 |
