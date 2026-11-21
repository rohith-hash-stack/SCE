"""Hand-authored part of the Layer 4 tasks: focal function, the change
(forward) or the adaptation (backward), the test commands and the depth of
the blast radius. Everything else - gold, oracle callers, gates, hashes - is
derived by `harness/experiments/layer4/author.py` from runtime traces and
test runs, never from Prism.

Commands: `{python}` is the corpus's own interpreter (see ENVIRONMENT.md),
`{runtime}` the Node tracer runtime; `cwd` is the clone root.
"""
from __future__ import annotations

# FastAPI's suite minus the two tests that fail at the pinned environment for
# reasons unrelated to any focal (CLI help text, one tutorial's handler body),
# and one websocket tutorial that deadlocks under the profiler (it passes
# untraced; the other websocket tests still register websocket routes).
FASTAPI_DESELECT = ["--deselect", "tests/test_fastapi_cli.py::test_fastapi_cli",
                    "--deselect", "tests/test_tutorial/test_custom_request_and_route/test_tutorial002.py"
                                  "::test_exception_handler_body_access",
                    "--ignore", "tests/test_tutorial/test_websockets/test_tutorial003.py"]
# The websocket tests (the test client's portal thread) deadlock under the
# profiler intermittently; a task whose focal never runs on the websocket path
# leaves them out of its trace (get_openapi_path: 3 of 3 attempts hung).
FASTAPI_NO_WEBSOCKETS = [arg for f in (
    "tests/test_http_connection_injection.py", "tests/test_route_scope.py", "tests/test_ws_dependencies.py",
    "tests/test_ws_router.py", "tests/test_tutorial/test_websockets") for arg in ("--ignore", f)]
PYTEST = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
# --exit: a wrong res.format candidate that never responds leaves sockets open and
# Mocha would wait for them forever; with --exit it fails after the per-test timeouts
MOCHA = ["node_modules/.bin/mocha", "--exit", "--reporter", "dot", "--check-leaks"]
EXPRESS_UNIT = "test/*.js"  # expanded by the author: the unit suite (test/acceptance drives examples/)
# tRPC: Vitest 0.32 in fork mode from packages/tests; filters are paths relative
# to Vitest's root (packages/), so `packages/<filter>` is the gating file.
VITEST = ["{node}", "{vitest}", "--run", "--no-threads"]
TRPC_ERROR_PATHS = ["tests/server/errors.test.ts", "tests/server/errorFormatting.test.ts",
                    "tests/server/middlewares.test.ts", "tests/server/websockets.test.ts",
                    "tests/server/TRPCError.test.ts", "tests/server/caller.test.ts", "tests/server/createCaller.test.ts"]
# the same without the websocket tests, which may time out under the tracer's slowdown
TRPC_ERROR_PATHS_NO_WS = [p for p in TRPC_ERROR_PATHS if "websockets" not in p]

SPECS = [
    # ------------------------------------------------------------- FastAPI
    {
        "task_id": "l4_fwd_fastapi_01", "corpus": "fastapi", "family": "forward_blast",
        "qname": "fastapi.dependencies.utils.get_dependant",
        "change": {"kind": "return_contract",
                   "description": "get_dependant() stops resolving sub-dependencies: the Dependant it returns always has "
                                  "dependencies=[], and the Depends() parameters of the endpoint are left for the caller "
                                  "to resolve."},
        "trace": PYTEST + ["tests", *FASTAPI_DESELECT], "max_depth": 2,
    },
    {
        "task_id": "l4_fwd_fastapi_02", "corpus": "fastapi", "family": "forward_blast",
        "qname": "fastapi.openapi.utils.get_openapi_path",
        "change": {"kind": "return_contract",
                   "description": "get_openapi_path() returns only the path item dict instead of the (path, "
                                  "security_schemes, definitions) tuple, so security schemes and definitions must be "
                                  "collected elsewhere."},
        "trace": PYTEST + ["tests", *FASTAPI_DESELECT, *FASTAPI_NO_WEBSOCKETS], "max_depth": 4,
    },
    {
        "task_id": "l4_bwd_fastapi_01", "corpus": "fastapi", "family": "backward_adaptation",
        "qname": "fastapi.utils.deep_dict_update",
        "tests": PYTEST + ["-x", "tests/test_openapi_route_extensions.py", "tests/test_openapi_query_parameter_extension.py"],
    },
    {
        "task_id": "l4_bwd_fastapi_02", "corpus": "fastapi", "family": "backward_adaptation",
        "qname": "fastapi.dependencies.utils.get_flat_dependant",
        "tests": PYTEST + ["-x", "tests/test_dependency_cache.py", "tests/test_dependency_duplicates.py",
                           "tests/test_dependency_class.py", "tests/test_security_oauth2.py"],
    },
    # ------------------------------------------------------------- Django
    {
        "task_id": "l4_fwd_django_01", "corpus": "django", "family": "forward_blast",
        "qname": "django.contrib.sessions.backends.db.SessionStore.save",
        "change": {"kind": "signature",
                   "description": "SessionStore.save() gains a required keyword-only argument `using` (the database "
                                  "alias to write the session row to); calls without it raise TypeError."},
        "trace_labels": ["sessions_tests"], "max_depth": 2,
    },
    {
        "task_id": "l4_fwd_django_02", "corpus": "django", "family": "forward_blast",
        "qname": "django.db.migrations.recorder.MigrationRecorder.record_applied",
        "change": {"kind": "signature",
                   "description": "MigrationRecorder.record_applied(app, name) becomes record_applied(migration), "
                                  "taking the Migration instance and reading app_label and name from it."},
        "trace_labels": ["migrations.test_executor", "migrations.test_loader"], "max_depth": 3,
    },
    {
        "task_id": "l4_bwd_django_01", "corpus": "django", "family": "backward_adaptation",
        "qname": "django.db.models.query.QuerySet.update",
        "test_labels": ["update"],
    },
    {
        "task_id": "l4_bwd_django_02", "corpus": "django", "family": "backward_adaptation",
        "qname": "django.core.cache.backends.locmem.LocMemCache.incr",
        "test_labels": ["cache.tests.LocMemCacheTests"],
    },
    # ------------------------------------------------------------- Express
    {
        "task_id": "l4_fwd_express_01", "corpus": "express", "family": "forward_blast",
        "qname": "lib.response.send",
        "change": {"kind": "return_contract",
                   "description": "res.send() no longer returns the response object (it returns undefined), so it can "
                                  "no longer be chained or returned as `this`."},
        "trace": MOCHA + [EXPRESS_UNIT], "max_depth": 2,
    },
    {
        "task_id": "l4_fwd_express_02", "corpus": "express", "family": "forward_blast",
        "qname": "lib.response.header",
        "change": {"kind": "signature",
                   "description": "res.set() / res.header() no longer accepts an object of header fields; only "
                                  "(field, value) pairs, and an object argument throws a TypeError."},
        "trace": MOCHA + [EXPRESS_UNIT], "max_depth": 1,
    },
    {
        "task_id": "l4_bwd_express_01", "corpus": "express", "family": "backward_adaptation",
        "qname": "lib.response.location",
        "tests": MOCHA + ["test/res.location.js", "test/res.redirect.js"],
    },
    {
        "task_id": "l4_bwd_express_02", "corpus": "express", "family": "backward_adaptation",
        "qname": "lib.response.format",
        "tests": MOCHA + ["test/res.format.js"],
    },
    # ------------------------------------------------------------- tRPC
    # Authored on Kaggle (kaggle/trpc_layer4_authoring_cell.py): the workspace
    # install needs registry.npmjs.com. Every focal is in packages/server/src;
    # `alternatives` are tried in order if a candidate yields no valid task;
    # forward gold widens one hop at a time up to max_depth_limit if no caller
    # sits within max_depth (test helpers and library frames add hops).
    {
        "task_id": "l4_fwd_trpc_01", "corpus": "trpc", "family": "forward_blast",
        "qname": "error.TRPCError.getTRPCErrorFromUnknown",
        "change": {"kind": "return_contract",
                   "description": "getTRPCErrorFromUnknown() returns undefined for causes that are not already a "
                                  "TRPCError, instead of wrapping them in an INTERNAL_SERVER_ERROR TRPCError."},
        "trace": VITEST + TRPC_ERROR_PATHS, "max_depth": 2, "max_depth_limit": 4,
        "alternatives": [
            {"trace": VITEST + TRPC_ERROR_PATHS_NO_WS},
            {"qname": "shared.transformTRPCResponse.transformTRPCResponse",
             "change": {"kind": "signature",
                        "description": "transformTRPCResponse(config, itemOrItems) becomes "
                                       "transformTRPCResponse(itemOrItems, transformer), taking the data transformer "
                                       "instead of the router config."}},
        ],
    },
    {
        "task_id": "l4_fwd_trpc_02", "corpus": "trpc", "family": "forward_blast",
        "qname": "shared.getErrorShape.getErrorShape",
        "change": {"kind": "signature",
                   "description": "getErrorShape(opts) no longer receives `config` inside opts: the error formatter "
                                  "must be passed as a second argument, getErrorShape(opts, errorFormatter)."},
        "trace": VITEST + TRPC_ERROR_PATHS, "max_depth": 2, "max_depth_limit": 4,
        "alternatives": [
            {"trace": VITEST + TRPC_ERROR_PATHS_NO_WS},
            {"qname": "core.router.callProcedure",
             "change": {"kind": "return_contract",
                        "description": "callProcedure() resolves to a { ok, data, error } result object instead of "
                                       "the procedure's output, and no longer throws on procedure errors."}},
        ],
    },
    {
        "task_id": "l4_bwd_trpc_01", "corpus": "trpc", "family": "backward_adaptation",
        "qname": "core.internals.mergeRouters.mergeRouters",
        "tests": VITEST + ["tests/server/index.test.ts", "tests/server/smoke.test.ts"],
        "alternatives": [
            {"qname": "shared.getCauseFromUnknown.getCauseFromUnknown",
             "tests": VITEST + ["tests/server/TRPCError.test.ts"]},
        ],
    },
    {
        "task_id": "l4_bwd_trpc_02", "corpus": "trpc", "family": "backward_adaptation",
        "qname": "observable.operators.share.share",
        "tests": VITEST + ["server/src/observable/operators/share.test.ts", "server/src/observable/observable.test.ts"],
        "alternatives": [
            {"qname": "observable.operators.tap.tap",
             "tests": VITEST + ["server/src/observable/observable.test.ts"]},
        ],
    },
]
