# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 16 / 39 | 3 | 10 | 0.29 | 0.95 | 15 / 16 |
| all | PRISM | 20 | 30 / 39 | 14 | 8 | 0.29 | 0.89 | 25 / 30 |
| all | PRISM_BREAKS | 20 | 30 / 39 | 14 | 7 | 0.3 | 0.78 | 29 / 30 |
| easy | FILE | 8 | 4 / 13 | 2 | 6 | 0.31 | 1.0 | 4 / 4 |
| easy | PRISM | 8 | 6 / 13 | 3 | 2 | 0.21 | 0.67 | 6 / 6 |
| easy | PRISM_BREAKS | 8 | 7 / 13 | 3 | 2 | 0.18 | 0.5 | 7 / 7 |
| medium | FILE | 7 | 0 / 7 | 0 | 3 | None | None | 0 / 0 |
| medium | PRISM | 7 | 7 / 7 | 7 | 3 | 0.14 | 0.91 | 3 / 7 |
| medium | PRISM_BREAKS | 7 | 7 / 7 | 7 | 3 | 0.14 | 0.91 | 6 / 7 |
| hard | FILE | 5 | 12 / 19 | 1 | 1 | 0.28 | 0.93 | 11 / 12 |
| hard | PRISM | 5 | 17 / 19 | 4 | 3 | 0.37 | 0.96 | 16 / 17 |
| hard | PRISM_BREAKS | 5 | 16 / 19 | 4 | 2 | 0.42 | 0.85 | 16 / 16 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| fastify-forward-001 | easy | fastify | `lib.hookRunner.next` | 2 | 0 | 0 | 1 |
| fastify-forward-002 | easy | fastify | `lib.server.listen` | 1 | 0 | 0 | 0 |
| fastify-forward-003 | easy | fastify | `fastify.buildRoutePrefix` | 1 | 1 | 1 | 1 |
| fastify-forward-004 | easy | fastify | `fastify.handleNode` | 2 | 1 | 1 | 1 |
| fastify-forward-005 | easy | fastify | `lib.reply.handleError` | 2 | 0 | 0 | 0 |
| fastify-forward-006 | easy | fastify | `lib.reply.onSendEnd` | 2 | 2 | 2 | 2 |
| fastify-forward-007 | easy | fastify | `lib.server.listen` | 1 | 0 | 0 | 0 |
| fastify-forward-008 | medium | fastify | `lib.request.buildRequest` | 1 | 0 | 1 | 1 |
| fastify-forward-009 | medium | fastify | `lib.logger.reqIdGenFactory` | 1 | 0 | 1 | 1 |
| fastify-forward-010 | medium | fastify | `lib.logger.reqIdGenFactory` | 1 | 0 | 1 | 1 |
| fastify-forward-011 | medium | fastify | `lib.reply.send` | 1 | 0 | 1 | 1 |
| fastify-forward-012 | medium | fastify | `lib.validation.buildSchemaCompiler` | 1 | 0 | 1 | 1 |
| fastify-forward-013 | medium | fastify | `lib.validation.compileSchemasForValidation` | 1 | 0 | 1 | 1 |
| fastify-forward-014 | medium | fastify | `lib.logger.reqIdGenFactory` | 1 | 0 | 1 | 1 |
| fastify-forward-015 | hard | fastify | `lib.handleRequest.handler` | 3 | 1 | 3 | 3 |
| fastify-forward-016 | hard | fastify | `lib.handleRequest.handler` | 3 | 1 | 3 | 3 |
| fastify-forward-017 | hard | fastify | `lib.error-serializer.Serializer.asString` | 3 | 1 | 1 | 0 |
| fastify-forward-018 | hard | fastify | `lib.handleRequest.handler` | 3 | 2 | 3 | 3 |
| fastify-forward-019 | hard | fastify | `fastify._route` | 7 | 7 | 7 | 7 |
| fastify-forward-020 | easy | fastify | `lib.decorate.decorate` | 2 | 0 | 2 | 2 |
