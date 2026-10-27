# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 11 | 13 / 29 | 1 | 4 | 0.68 | 0.71 | 12 / 13 |
| all | PRISM | 11 | 22 / 29 | 6 | 1 | 0.7 | 0.63 | 21 / 22 |
| easy | FILE | 4 | 3 / 7 | 1 | 2 | 0.1 | 0.47 | 3 / 3 |
| easy | PRISM | 4 | 3 / 7 | 2 | 0 | 0.1 | 0.07 | 3 / 3 |
| medium | FILE | 4 | 1 / 5 | 0 | 0 | 1.0 | 1.0 | 1 / 1 |
| medium | PRISM | 4 | 2 / 5 | 1 | 0 | 0.6 | 0.75 | 2 / 2 |
| hard | FILE | 3 | 9 / 17 | 0 | 2 | 0.83 | 0.8 | 8 / 9 |
| hard | PRISM | 3 | 17 / 17 | 3 | 1 | 0.82 | 0.78 | 16 / 17 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found |
|---|---|---|---|---|---|---|
| ch01 | easy | click | `src.click.types.Choice.normalize_choice` | 2 | 1 | 0 |
| ch02 | easy | click | `src.click.termui._interpret_color` | 1 | 1 | 1 |
| ch03 | easy | flask | `src.flask.cli.load_dotenv` | 2 | 0 | 0 |
| ch04 | easy | flask | `src.flask.helpers._prepare_send_file_kwargs` | 2 | 1 | 2 |
| ch05 | medium | flask | `src.flask.sansio.app.App.redirect` | 1 | 0 | 0 |
| ch06 | medium | flask | `src.flask.ctx.RequestContext.auto_pop` | 2 | 1 | 2 |
| ch07 | medium | express | `lib.router.use` | 1 | 0 | 0 |
| ch08 | medium | click | `src.click.utils.echo` | 1 | 0 | 0 |
| ch09 | hard | httpx | `httpx._api.request` | 7 | 4 | 7 |
| ch10 | hard | httpx | `httpx._api.request` | 7 | 4 | 7 |
| ch11 | hard | flask | `src.flask.helpers.get_env` | 3 | 1 | 3 |
