# Phase 3 tasks (AMENDMENTS "Phase 3", amendments 1-3; built 2026-11-22)

**What a task asks:** rename a method, function or class with 15-30 places in 3+ files. The places come from PRISM's
resolved uses, site by site, and jedi's Find All References agrees on every one.

**What is checked:** the scored tests (the repository's own, plus a generated test that the new name exists and the
old does not), and the `rename_sites` static check. Per file, scope and receiver, the uses of the old name must equal
the answer key's: a place left over fails, and so does a look-alike renamed.

**Files:**
- `poc.json`: the proof of concept, 4 hidden tasks + `r4s-delete-05`, 3 seeds (42-44) x 3 arms = 45 runs;
  sha256 `ab9da8802c1e481d`.
- `tasks.json`: the confirmatory stage, 12 hidden tasks + 3 controls, 5 seeds (101-105) x 3 arms = 225 runs;
  sha256 `e85a90d1fd6e52d1`. Run only after a GO.
- `candidates.json`: all 20 validated tasks. `gold/`: their answer keys, never given to a run.

**Selection** (pre-registered): grep's precision lowest first, then the most look-alikes.
- POC: the top 4, at most 1 per repository.
- Confirmatory: the POC's 4 and the next 8, at most 2 per repository.

| id | repo | target | places | files | grep precision | stage |
|---|---|---|---|---|---|---|
| p3-hidden-01 | litestar | `openapi.spec.server.Server` | 15 | 7 | 0.158 | POC + confirmatory |
| p3-hidden-02 | pydantic | `v1.parse.Protocol` | 16 | 4 | 0.167 | POC + confirmatory |
| p3-hidden-03 | werkzeug | `werkzeug.datastructures.range.Range.from_header` | 16 | 4 | 0.211 | POC + confirmatory |
| p3-hidden-04 | fastmcp | `fastmcp.utilities.mcp_server_config.v1.mcp_server_config.Deployment` | 22 | 5 | 0.222 | POC + confirmatory |
| p3-hidden-05 | fastmcp | `fastmcp.server.transforms.Transform` | 24 | 11 | 0.244 | confirmatory |
| p3-hidden-06 | fastmcp | `fastmcp.server.dependencies.get_access_token` | 20 | 6 | 0.263 | spare |
| p3-hidden-07 | scrapy | `spiders.crawl.Rule` | 21 | 4 | 0.3 | confirmatory |
| p3-hidden-08 | instructor | `v2.core.response.process_response` | 22 | 5 | 0.312 | confirmatory |
| p3-hidden-09 | pydantic-ai | `pydantic_ai.agent.abstract.AbstractAgent` | 21 | 7 | 0.333 | confirmatory |
| p3-hidden-10 | werkzeug | `werkzeug.sansio.multipart.Data` | 20 | 4 | 0.375 | confirmatory |
| p3-hidden-11 | aiohttp | `web_routedef.RouteTableDef` | 18 | 5 | 0.4 | confirmatory |
| p3-hidden-12 | aiohttp | `client_exceptions.ClientPayloadError` | 18 | 6 | 0.455 | confirmatory |
| p3-hidden-13 | aiohttp | `web_ws.WebSocketResponse.send_bytes` | 21 | 7 | 0.462 | spare |
| p3-hidden-14 | pydantic-ai | `pydantic_ai.format_prompt.format_as_xml` | 18 | 6 | 0.5 | confirmatory |
| p3-hidden-15 | litestar | `middleware.session.base.SessionMiddleware` | 15 | 6 | 0.556 | spare |
| p3-hidden-16 | pydantic-ai | `pydantic_ai.messages.ToolReturn` | 21 | 5 | 0.556 | spare |
| p3-hidden-17 | litestar | `dto.field.Mark` | 15 | 5 | 0.571 | spare |
| p3-hidden-18 | instructor | `v2.core.usage.update_total_usage` | 19 | 8 | 0.727 | spare |
| p3-hidden-19 | instructor | `v2.core.errors.MultimodalError` | 25 | 6 | 0.75 | spare |
| p3-hidden-20 | pydantic | `_internal._mock_val_ser.MockCoreSchema` | 25 | 8 | 0.875 | spare |

**Controls** (unchanged from Phase 2): `r4s-delete-05` (POC and confirmatory), `r4s-delete-08` and `r4s-delete-10`
(confirmatory).
