# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 12 | 3 / 20 | 1 | 7 | 0.67 | 0.72 | 3 / 3 |
| all | PRISM | 12 | 9 / 20 | 6 | 0 | 0.44 | 0.63 | 9 / 9 |
| all | PRISM_MAP | 12 | 8 / 20 | 5 | 0 | 0.5 | 0.71 | 8 / 8 |
| easy | FILE | 4 | 1 / 4 | 1 | 2 | 1.0 | 0.17 | 1 / 1 |
| easy | PRISM | 4 | 2 / 4 | 2 | 0 | 0.5 | 0.58 | 2 / 2 |
| easy | PRISM_MAP | 4 | 1 / 4 | 1 | 0 | 1.0 | 0.17 | 1 / 1 |
| medium | FILE | 4 | 0 / 4 | 0 | 0 | None | None | 0 / 0 |
| medium | PRISM | 4 | 2 / 4 | 2 | 0 | 0.5 | 0.75 | 2 / 2 |
| medium | PRISM_MAP | 4 | 2 / 4 | 2 | 0 | 0.5 | 0.5 | 2 / 2 |
| hard | FILE | 4 | 2 / 12 | 0 | 5 | 0.5 | 1.0 | 2 / 2 |
| hard | PRISM | 4 | 5 / 12 | 2 | 0 | 0.4 | 0.6 | 5 / 5 |
| hard | PRISM_MAP | 4 | 5 / 12 | 2 | 0 | 0.4 | 0.9 | 5 / 5 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_MAP found |
|---|---|---|---|---|---|---|---|
| ch01 | easy | flask | `scripts.flask-07-upgrade.scan_path` | 1 | 1 | 1 | 1 |
| ch02 | easy | jinja | `jinja2.compiler.CodeGenerator.signature` | 1 | 0 | 0 | 0 |
| ch03 | easy | requests | `src.requests.adapters._urllib3_request_context` | 1 | 0 | 1 | 0 |
| ch04 | easy | requests | `requests.sessions.SessionRedirectMixin.resolve_redirects` | 1 | 0 | 0 | 0 |
| ch05 | medium | express | `lib.router.param` | 1 | 0 | 0 | 0 |
| ch06 | medium | httpx | `httpx._utils.NetRCInfo.get_credentials` | 1 | 0 | 1 | 1 |
| ch07 | medium | jinja | `jinja2.lexer.Lexer.tokenize` | 1 | 0 | 1 | 1 |
| ch08 | medium | express | `lib.router.use` | 1 | 0 | 0 | 0 |
| ch09 | hard | jinja | `jinja2.parser.Parser.parse_tuple` | 3 | 0 | 0 | 0 |
| ch10 | hard | jinja | `jinja2.compiler.CodeGenerator.pull_locals` | 4 | 0 | 0 | 0 |
| ch11 | hard | click | `src.click.core.Argument.make_metavar` | 3 | 2 | 3 | 3 |
| ch12 | hard | click | `src.click.termui.get_terminal_size` | 2 | 0 | 2 | 2 |
