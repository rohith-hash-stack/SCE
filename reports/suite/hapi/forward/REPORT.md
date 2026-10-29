# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 0 / 40 | 0 | 20 | None | None | 0 / 0 |
| all | PRISM | 20 | 19 / 40 | 8 | 23 | 0.34 | 0.51 | 13 / 19 |
| all | PRISM_BREAKS | 20 | 16 / 40 | 7 | 22 | 0.13 | 0.66 | 11 / 16 |
| easy | FILE | 7 | 0 / 10 | 0 | 7 | None | None | 0 / 0 |
| easy | PRISM | 7 | 1 / 10 | 1 | 7 | 0.5 | 0.5 | 0 / 1 |
| easy | PRISM_BREAKS | 7 | 4 / 10 | 3 | 8 | 0.53 | 0.65 | 2 / 4 |
| medium | FILE | 7 | 0 / 8 | 0 | 7 | None | None | 0 / 0 |
| medium | PRISM | 7 | 2 / 8 | 2 | 6 | 0.0 | 0.5 | 2 / 2 |
| medium | PRISM_BREAKS | 7 | 1 / 8 | 1 | 6 | 0.0 | 0.0 | 1 / 1 |
| hard | FILE | 6 | 0 / 22 | 0 | 6 | None | None | 0 / 0 |
| hard | PRISM | 6 | 16 / 22 | 5 | 10 | 0.37 | 0.51 | 11 / 16 |
| hard | PRISM_BREAKS | 6 | 11 / 22 | 3 | 8 | 0.0 | 0.73 | 8 / 11 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| hapi-forward-001 | easy | hapi | `lib.pack.load` | 2 | 0 | 0 | 2 |
| hapi-forward-002 | easy | hapi | `lib.server.generateKey` | 2 | 0 | 0 | 0 |
| hapi-forward-003 | easy | hapi | `lib.request._setCache` | 2 | 0 | 0 | 0 |
| hapi-forward-004 | easy | hapi | `lib.response.obj.update` | 1 | 0 | 0 | 0 |
| hapi-forward-005 | easy | hapi | `lib.request._setUrl` | 1 | 0 | 1 | 1 |
| hapi-forward-006 | easy | hapi | `lib.proxy.mapUri` | 1 | 0 | 0 | 0 |
| hapi-forward-007 | easy | hapi | `lib.response.end` | 1 | 0 | 0 | 1 |
| hapi-forward-008 | medium | hapi | `lib.response.payload.size` | 1 | 0 | 0 | 0 |
| hapi-forward-009 | medium | hapi | `lib.log.event` | 1 | 0 | 0 | 0 |
| hapi-forward-010 | medium | hapi | `lib.utils.map` | 1 | 0 | 1 | 0 |
| hapi-forward-011 | medium | hapi | `lib.auth.hawk.authenticatePayload` | 1 | 0 | 0 | 0 |
| hapi-forward-012 | medium | hapi | `lib.state.generateSetCookieHeader` | 1 | 0 | 1 | 1 |
| hapi-forward-013 | medium | hapi | `lib.reply.decorate` | 1 | 0 | 0 | 0 |
| hapi-forward-014 | medium | hapi | `lib.server.respond` | 2 | 0 | 0 | 0 |
| hapi-forward-015 | hard | hapi | `lib.ext.runProtected` | 3 | 0 | 3 | 3 |
| hapi-forward-016 | hard | hapi | `lib.utils.merge` | 2 | 0 | 2 | 2 |
| hapi-forward-017 | hard | hapi | `lib.toolkit.failAction` | 4 | 0 | 4 | 0 |
| hapi-forward-018 | hard | hapi | `lib.protect.run` | 6 | 0 | 0 | 6 |
| hapi-forward-019 | hard | hapi | `lib.reply._interface` | 4 | 0 | 4 | 0 |
| hapi-forward-020 | hard | hapi | `lib.pack._require` | 3 | 0 | 3 | 0 |
