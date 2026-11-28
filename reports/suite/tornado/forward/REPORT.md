# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 9 / 28 | 6 | 17 | 0.27 | 0.5 | 9 / 9 |
| all | PRISM | 20 | 17 / 28 | 14 | 6 | 0.35 | 0.46 | 16 / 17 |
| all | PRISM_BREAKS | 20 | 28 / 28 | 20 | 9 | 0.36 | 0.52 | 27 / 28 |
| easy | FILE | 9 | 8 / 11 | 6 | 4 | 0.3 | 0.5 | 8 / 8 |
| easy | PRISM | 9 | 9 / 11 | 8 | 0 | 0.37 | 0.56 | 9 / 9 |
| easy | PRISM_BREAKS | 9 | 11 / 11 | 9 | 0 | 0.35 | 0.54 | 11 / 11 |
| medium | FILE | 8 | 0 / 8 | 0 | 11 | None | None | 0 / 0 |
| medium | PRISM | 8 | 5 / 8 | 5 | 6 | 0.24 | 0.37 | 4 / 5 |
| medium | PRISM_BREAKS | 8 | 8 / 8 | 8 | 9 | 0.24 | 0.45 | 7 / 8 |
| hard | FILE | 3 | 1 / 9 | 0 | 2 | 0.0 | 0.5 | 1 / 1 |
| hard | PRISM | 3 | 3 / 9 | 1 | 0 | 0.44 | 0.28 | 3 / 3 |
| hard | PRISM_BREAKS | 3 | 9 / 9 | 3 | 0 | 0.48 | 0.57 | 9 / 9 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| tornado-forward-001 | easy | tornado | `tornado.autoreload._check_file` | 1 | 1 | 1 | 1 |
| tornado-forward-002 | easy | tornado | `tornado.websocket.WebSocketProtocol76.write_message` | 1 | 0 | 1 | 1 |
| tornado-forward-003 | easy | tornado | `tornado.websocket.WebSocketProtocol13._get_compressor_options` | 1 | 1 | 1 | 1 |
| tornado-forward-004 | easy | tornado | `tornado.websocket._PerMessageDeflateCompressor._create_compressor` | 2 | 1 | 2 | 2 |
| tornado-forward-005 | easy | tornado | `tornado.web.StaticFileHandler.get_modified_time` | 1 | 1 | 1 | 1 |
| tornado-forward-006 | easy | tornado | `tornado.web.StaticFileHandler.set_extra_headers` | 1 | 1 | 1 | 1 |
| tornado-forward-007 | easy | tornado | `tornado.curl_httpclient.CurlAsyncHTTPClient._curl_header_callback` | 1 | 0 | 1 | 1 |
| tornado-forward-008 | medium | tornado | `tornado.util.GzipDecompressor.flush` | 1 | 0 | 1 | 1 |
| tornado-forward-009 | medium | tornado | `tornado.http1connection.HTTP1Connection.write_headers` | 1 | 0 | 0 | 1 |
| tornado-forward-010 | medium | tornado | `tornado.http1connection.HTTP1Connection.write` | 1 | 0 | 0 | 1 |
| tornado-forward-011 | medium | tornado | `tornado.escape.utf8` | 1 | 0 | 1 | 1 |
| tornado-forward-012 | medium | tornado | `tornado.web.Application.start_request` | 1 | 0 | 1 | 1 |
| tornado-forward-013 | medium | tornado | `tornado.httputil._get_content_range` | 1 | 0 | 1 | 1 |
| tornado-forward-014 | medium | tornado | `tornado.platform.posix.Waker.wake` | 1 | 0 | 1 | 1 |
| tornado-forward-015 | hard | tornado | `tornado.iostream.BaseIOStream._run_read_callback` | 3 | 1 | 0 | 3 |
| tornado-forward-016 | hard | tornado | `tornado.iostream.BaseIOStream._finish_read` | 3 | 0 | 0 | 3 |
| tornado-forward-017 | hard | tornado | `tornado.web.StaticFileHandler.get_absolute_path` | 3 | 0 | 3 | 3 |
| tornado-forward-018 | medium | tornado | `tornado.netutil.bind_sockets` | 1 | 0 | 0 | 1 |
| tornado-forward-019 | easy | tornado | `tornado.web.StaticFileHandler.validate_absolute_path` | 1 | 1 | 1 | 1 |
| tornado-forward-020 | easy | tornado | `tornado.http1connection.HTTP1Connection._finish_request` | 2 | 2 | 0 | 2 |
