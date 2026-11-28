# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 7 / 27 | 5 | 14 | 0.27 | 0.56 | 6 / 7 |
| all | PRISM | 20 | 22 / 27 | 15 | 9 | 0.18 | 0.59 | 18 / 22 |
| all | PRISM_BREAKS | 20 | 22 / 27 | 17 | 4 | 0.25 | 0.63 | 18 / 22 |
| easy | FILE | 8 | 5 / 9 | 5 | 11 | 0.38 | 0.47 | 5 / 5 |
| easy | PRISM | 8 | 8 / 9 | 7 | 1 | 0.35 | 0.61 | 8 / 8 |
| easy | PRISM_BREAKS | 8 | 8 / 9 | 7 | 1 | 0.35 | 0.61 | 8 / 8 |
| medium | FILE | 10 | 0 / 10 | 0 | 3 | None | None | 0 / 0 |
| medium | PRISM | 10 | 7 / 10 | 7 | 5 | 0.14 | 0.93 | 4 / 7 |
| medium | PRISM_BREAKS | 10 | 9 / 10 | 9 | 3 | 0.27 | 0.83 | 5 / 9 |
| hard | FILE | 2 | 2 / 8 | 0 | 0 | 0.0 | 0.8 | 1 / 2 |
| hard | PRISM | 2 | 7 / 8 | 1 | 3 | 0.02 | 0.23 | 6 / 7 |
| hard | PRISM_BREAKS | 2 | 5 / 8 | 1 | 0 | 0.03 | 0.32 | 5 / 5 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| werkzeug-forward-001 | easy | werkzeug | `src.werkzeug.datastructures.structures.HeaderSet.find` | 1 | 1 | 1 | 1 |
| werkzeug-forward-002 | easy | werkzeug | `werkzeug.routing.Rule.suitable_for` | 2 | 0 | 2 | 2 |
| werkzeug-forward-003 | easy | werkzeug | `werkzeug.routing.Rule.match` | 1 | 1 | 1 | 1 |
| werkzeug-forward-004 | easy | werkzeug | `werkzeug.urls.url_unparse` | 1 | 0 | 0 | 0 |
| werkzeug-forward-005 | easy | werkzeug | `werkzeug.http.CacheControl._get_cache_value` | 1 | 1 | 1 | 1 |
| werkzeug-forward-006 | easy | werkzeug | `src.werkzeug._reloader._find_stat_paths` | 1 | 1 | 1 | 1 |
| werkzeug-forward-007 | easy | werkzeug | `werkzeug._reloader._find_observable_paths` | 1 | 0 | 1 | 1 |
| werkzeug-forward-008 | medium | werkzeug | `werkzeug.http.parse_cache_control_header` | 1 | 0 | 1 | 1 |
| werkzeug-forward-009 | medium | werkzeug | `werkzeug._internal._decode_unicode` | 1 | 0 | 0 | 1 |
| werkzeug-forward-010 | medium | werkzeug | `werkzeug.utils.import_string` | 1 | 0 | 0 | 0 |
| werkzeug-forward-011 | medium | werkzeug | `werkzeug.http.generate_etag` | 1 | 0 | 1 | 1 |
| werkzeug-forward-012 | medium | werkzeug | `werkzeug.debug.util.ThreadedStream.install` | 1 | 0 | 1 | 1 |
| werkzeug-forward-013 | medium | werkzeug | `werkzeug.utils.parse_form_data` | 1 | 0 | 0 | 1 |
| werkzeug-forward-014 | medium | werkzeug | `werkzeug.debug.tbtools.Traceback.paste` | 1 | 0 | 1 | 1 |
| werkzeug-forward-015 | hard | werkzeug | `src.werkzeug.datastructures.range.Range.range_for_length` | 3 | 0 | 3 | 3 |
| werkzeug-forward-016 | hard | werkzeug | `werkzeug.urls.url_quote` | 5 | 2 | 4 | 2 |
| werkzeug-forward-017 | medium | werkzeug | `werkzeug.debug.tbtools.render_console_html` | 1 | 0 | 1 | 1 |
| werkzeug-forward-018 | medium | werkzeug | `werkzeug.debug.tbtools.Traceback.paste` | 1 | 0 | 1 | 1 |
| werkzeug-forward-019 | medium | werkzeug | `werkzeug.debug.tbtools.get_current_traceback` | 1 | 0 | 1 | 1 |
| werkzeug-forward-020 | easy | werkzeug | `bench.wzbench.run` | 1 | 1 | 1 | 1 |
