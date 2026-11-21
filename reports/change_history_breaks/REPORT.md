# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 19 | 7 / 32 | 3 | 11 | 0.62 | 0.9 | 7 / 7 |
| all | PRISM | 19 | 26 / 32 | 15 | 3 | 0.56 | 0.79 | 22 / 26 |
| all | PRISM_BREAKS | 19 | 26 / 32 | 13 | 1 | 0.56 | 0.79 | 23 / 26 |
| easy | FILE | 8 | 4 / 12 | 3 | 6 | 0.75 | 1.0 | 4 / 4 |
| easy | PRISM | 8 | 9 / 12 | 6 | 0 | 0.66 | 0.82 | 6 / 9 |
| easy | PRISM_BREAKS | 8 | 11 / 12 | 7 | 0 | 0.76 | 0.86 | 8 / 11 |
| medium | FILE | 8 | 1 / 11 | 0 | 4 | 0.33 | 0.33 | 1 / 1 |
| medium | PRISM | 8 | 9 / 11 | 7 | 1 | 0.31 | 0.68 | 8 / 9 |
| medium | PRISM_BREAKS | 8 | 8 / 11 | 5 | 1 | 0.41 | 0.68 | 8 / 8 |
| hard | FILE | 3 | 2 / 9 | 0 | 1 | 0.5 | 1.0 | 2 / 2 |
| hard | PRISM | 3 | 8 / 9 | 2 | 2 | 0.69 | 0.88 | 8 / 8 |
| hard | PRISM_BREAKS | 3 | 7 / 9 | 1 | 0 | 0.43 | 0.82 | 7 / 7 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| ch01 | easy | jinja | `jinja2.environment.TemplateStream.generator` | 1 | 1 | 1 | 1 |
| ch02 | easy | flask | `flask.cli.ScriptInfo.get_app_import_path` | 1 | 0 | 0 | 0 |
| ch03 | easy | flask | `flask.cli._called_with_wrong_args` | 2 | 0 | 2 | 2 |
| ch04 | easy | express | `lib.router.process_params` | 1 | 1 | 1 | 1 |
| ch05 | easy | httpx | `httpx.config.SSLConfig._create_default_ssl_context` | 2 | 2 | 2 | 2 |
| ch06 | easy | httpx | `httpx.client._proxies_to_dispatchers` | 2 | 0 | 2 | 2 |
| ch07 | easy | jinja | `jinja2.compiler.CodeGenerator.blockvisit` | 2 | 0 | 0 | 2 |
| ch08 | easy | click | `click.core.Option._parse_decls` | 1 | 0 | 1 | 1 |
| ch09 | medium | requests | `requests.hooks.dispatch_hook` | 1 | 0 | 1 | 0 |
| ch10 | medium | click | `click.parser.OptionParser.add_option` | 1 | 0 | 1 | 1 |
| ch11 | medium | requests | `requests.utils.get_environ_proxies` | 2 | 0 | 2 | 2 |
| ch12 | medium | flask | `flask.helpers.is_ip` | 1 | 0 | 1 | 0 |
| ch13 | medium | flask | `flask.testing.make_test_environ_builder` | 2 | 1 | 2 | 1 |
| ch14 | medium | httpx | `httpx.concurrency.asyncio.AsyncioBackend.get_semaphore` | 1 | 0 | 1 | 1 |
| ch15 | medium | click | `src.click.core.BaseCommand.shell_complete` | 2 | 0 | 0 | 2 |
| ch16 | medium | jinja | `jinja2.compiler.generate` | 1 | 0 | 1 | 1 |
| ch17 | hard | jinja | `jinja2.parser.Parser.parse_primary` | 3 | 1 | 3 | 2 |
| ch18 | hard | httpx | `httpx.dispatch.base.Dispatcher.send` | 3 | 1 | 3 | 3 |
| ch19 | hard | jinja | `jinja2.environment.Environment.compile` | 3 | 0 | 2 | 2 |
