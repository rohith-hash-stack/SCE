# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 10 / 21 | 10 | 29 | 0.43 | 0.47 | 10 / 10 |
| all | PRISM | 20 | 14 / 21 | 13 | 6 | 0.4 | 0.86 | 13 / 14 |
| all | PRISM_BREAKS | 20 | 15 / 21 | 14 | 3 | 0.4 | 0.78 | 14 / 15 |
| easy | FILE | 12 | 10 / 13 | 10 | 17 | 0.43 | 0.47 | 10 / 10 |
| easy | PRISM | 12 | 11 / 13 | 10 | 5 | 0.51 | 0.82 | 10 / 11 |
| easy | PRISM_BREAKS | 12 | 11 / 13 | 10 | 2 | 0.51 | 0.77 | 10 / 11 |
| medium | FILE | 8 | 0 / 8 | 0 | 12 | None | None | 0 / 0 |
| medium | PRISM | 8 | 3 / 8 | 3 | 1 | 0.0 | 1.0 | 3 / 3 |
| medium | PRISM_BREAKS | 8 | 4 / 8 | 4 | 1 | 0.07 | 0.82 | 4 / 4 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| aiohttp-forward-001 | easy | aiohttp | `aiohttp.formdata.FormData._gen_form_urlencoded` | 1 | 1 | 1 | 1 |
| aiohttp-forward-002 | easy | aiohttp | `aiohttp.cookiejar.CookieJar._expire_cookie` | 1 | 1 | 1 | 1 |
| aiohttp-forward-003 | easy | aiohttp | `aiohttp.helpers.AccessLogger._format_line` | 1 | 1 | 1 | 1 |
| aiohttp-forward-004 | easy | aiohttp | `aiohttp.web_response.StreamResponse._do_start_compression` | 1 | 1 | 0 | 0 |
| aiohttp-forward-005 | easy | aiohttp | `aiohttp.streams.StreamReader.readuntil` | 1 | 1 | 1 | 1 |
| aiohttp-forward-006 | easy | aiohttp | `aiohttp.web_protocol.RequestHandler.finish_response` | 1 | 0 | 1 | 1 |
| aiohttp-forward-007 | easy | aiohttp | `aiohttp.http_parser.DeflateBuffer.feed_data` | 2 | 0 | 2 | 2 |
| aiohttp-forward-008 | medium | aiohttp | `aiohttp.tracing.Trace.send_response_chunk_received` | 1 | 0 | 1 | 1 |
| aiohttp-forward-009 | medium | aiohttp | `aiohttp.tracing.Trace.send_response_chunk_received` | 1 | 0 | 1 | 1 |
| aiohttp-forward-010 | medium | aiohttp | `aiohttp.connector.BaseConnector.connect` | 1 | 0 | 0 | 0 |
| aiohttp-forward-011 | medium | aiohttp | `aiohttp.connector.BaseConnector.connect` | 1 | 0 | 0 | 0 |
| aiohttp-forward-012 | medium | aiohttp | `aiohttp.web_app.Application.make_handler` | 1 | 0 | 1 | 1 |
| aiohttp-forward-013 | medium | aiohttp | `aiohttp.tracing.Trace.send_dns_cache_hit` | 1 | 0 | 0 | 1 |
| aiohttp-forward-014 | medium | aiohttp | `aiohttp.streams.StreamWriter.acquire` | 1 | 0 | 0 | 0 |
| aiohttp-forward-015 | medium | aiohttp | `aiohttp.connector.BaseConnector.connect` | 1 | 0 | 0 | 0 |
| aiohttp-forward-016 | easy | aiohttp | `aiohttp.web_response.StreamResponse._do_start_compression` | 1 | 1 | 0 | 0 |
| aiohttp-forward-017 | easy | aiohttp | `aiohttp.web_fileresponse.FileResponse._sendfile_fallback` | 1 | 1 | 1 | 1 |
| aiohttp-forward-018 | easy | aiohttp | `aiohttp.web_protocol.RequestHandler._handle_request` | 1 | 1 | 1 | 1 |
| aiohttp-forward-019 | easy | aiohttp | `aiohttp.connector.BaseConnector._close` | 1 | 1 | 1 | 1 |
| aiohttp-forward-020 | easy | aiohttp | `aiohttp.connector.TCPConnector._resolve_host_with_throttle` | 1 | 1 | 1 | 1 |
