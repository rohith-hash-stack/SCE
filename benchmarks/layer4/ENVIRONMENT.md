# Layer 4 task environments

The tasks in `tasks/` were authored, traced and gated in these environments
(`harness/experiments/layer4/author.py`). The Layer 4 runner must use the
same ones: `{python}` in a task's commands is the corpus interpreter below.
Corpus checkouts are the pinned commits (`benchmarks/corpora/pinned_commits.json`).

## FastAPI 0.116.1 (`313723494b`)

A Python 3.11 venv with the checkout installed editable with its `[all]`
extras (as its `requirements-tests.txt` does: httpx for the test client,
jinja2, ...) plus the test requirements of the commit era. Current releases break the suite (FastAPI turns warnings
into errors), so pin:

```
pip install -e ".[all]" "anyio[trio]==4.6.2" "pydantic==2.11.7" "bcrypt==4.0.1" "python-multipart==0.0.20" \
    pydantic-settings fastapi-cli email-validator "flask>=1.1.2,<4.0.0" orjson ujson "sqlmodel==0.0.24" \
    "PyJWT==2.8.0" "passlib[bcrypt]>=1.7.2,<2.0.0" "inline-snapshot>=0.21.1" "dirty-equals==0.9.0" \
    "pyyaml>=5.3.1,<7.0.0" pytest
```

Baseline at these pins: 2,338 passed, 129 skipped, 2 failed. The two
failures are unrelated to every focal (`test_fastapi_cli.py::test_fastapi_cli`
- CLI help text; `test_tutorial/test_custom_request_and_route/test_tutorial002.py::test_exception_handler_body_access`)
and are deselected in trace commands.

Traces: `tests/test_tutorial/test_websockets/test_tutorial003.py` is
ignored (it deadlocks under the profiler, passes untraced), and the other
websocket tests deadlock intermittently under the profiler too (the test
client's portal thread): the author kills a traced run after 600 s and
reruns it, up to 3 attempts (`trace_attempts` in the manifest). A clean
traced run of the suite takes about a minute.

## Django 4.2.30 (`3396992e83`)

A venv with the checkout installed editable plus `tblib`; the default
SQLite settings (`tests/test_sqlite.py`). Tests run through
`tests/runtests.py <labels> --parallel=1`. Forward traces use
`trace_django --in-tests-only`: focal calls during test-database setup
(migrations, post-migrate signals) and class-level fixtures are not
counted - they are not the tested behaviour.

## Express 4.21.0 (`7e562c6d8d`)

`npm install` **with dev dependencies** (Mocha), Node 20 or 22. Mocha
runs with `--exit` (a candidate that never answers a request would keep it
waiting on open sockets). Traces
and gates use the unit suite (`test/*.js`); `test/acceptance/` drives the
`examples/` apps (the example zone) and is not used.

## tRPC v10.45.4 (`2ec29bfa2e`)

Authored on Kaggle (`kaggle/trpc_layer4_authoring_cell.py`, report in
`reports/layer4/trpc_authoring/`): the workspace install needs
`registry.npmjs.com`. Node 20.18.1, `pnpm@8.5.1 install --frozen-lockfile
--ignore-scripts`, then **every** `@trpc/*` package built (`pnpm run
build`: server, client, react-query, next) - the tests import
`@trpc/client` and, through shared helpers, `@trpc/react-query` by their
package entries, which point at `dist/`. Tests run with Vitest 0.32
`--run --no-threads` from `packages/tests` (Vitest root `packages/`); traces
load the runtime through `NODE_OPTIONS="--require trace_node_runtime.cjs
--enable-source-maps"`. Prism indexes `packages/server/src`, so focal
functions live there.

## Gold and oracle names

Only functions and methods Prism indexes outside test files and outside
the example zone (`docs_src/`, `examples/`, ... -
`prism.graph.entry_points.example_zone`) can be gold or oracle names: no
arm sees anything else. Django tables from test apps (`tests/<app>/`
models) and SQLite's `sqlite_master` introspection are dropped from the
gold tables, and the side-effect tags are derived after that filter (each
manifest lists `sql_dropped_tables`).
