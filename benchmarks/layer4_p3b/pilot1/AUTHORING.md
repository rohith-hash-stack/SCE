# P3b task authoring

**Outcome: NOT FROZEN: 9 tasks < 24: extend the specs from the same draw (p3b_draw --n) and author again before freezing
Extend the specs from the same draw (p3b_draw --n) and re-run this cell: decided specs are not traced again.**

Branch `feature/option3-contract-axis`, HEAD `cb9273b5c28ff4191ee1d3a372e56eacd30e80c7`, seed 20261024. Code: `{"src": "81d6b2a7af24dc773e1bb1f964620072f34f5555", "harness": "aff2e7b82e97895826ec8c46d224ee1acb87c522", "benchmarks/layer4/specs_p3b.py": "766cbdb6857a9a3570b04b105709224b26a34005", "benchmarks/layer4_p3b/draw.json": "cfac7beab9d6e06b4fc45e0c283b2e358890a364"}`.

| corpus | kept | decided | specs | finished |
|---|---|---|---|---|
| django | 4 | 24 | 24 | True |
| trpc | 1 | 24 | 24 | True |
| fastapi | 2 | 24 | 24 | True |
| express | 2 | 24 | 24 | True |

Total kept: 9 (target 32, minimum 24).

## django

| task | focal | status | gold / reason |
|---|---|---|---|
| p3b_fwd_django_0001 | `django.contrib.auth.views.PasswordResetView.form_valid` | skipped | focal_not_run |
| p3b_fwd_django_0002 | `django.db.models.manager.BaseManager.check` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0003 | `django.db.backends.postgresql.introspection.DatabaseIntrospection.get_table_description` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0005 | `django.contrib.gis.db.models.sql.conversion.DistanceField.get_internal_type` | skipped | focal_not_run |
| p3b_fwd_django_0006 | `django.db.migrations.operations.models.ModelOperation.name_lower` | skipped | focal_not_run |
| p3b_fwd_django_0007 | `django.template.defaultfilters.rjust` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0008 | `django.contrib.gis.db.backends.mysql.schema.MySQLGISSchemaEditor.remove_field` | skipped | focal_not_run |
| p3b_fwd_django_0011 | `django.utils.translation.get_language_bidi` | skipped | gold_below_min |
| p3b_fwd_django_0013 | `django.urls.resolvers.RegexPattern.check` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0014 | `django.contrib.admin.utils.get_deleted_objects` | kept | 5 |
| p3b_fwd_django_0016 | `django.forms.utils.RenderableErrorMixin.as_ul` | kept | 3 |
| p3b_fwd_django_0017 | `django.template.defaultfilters.make_list` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0019 | `django.contrib.gis.geos.geometry.GEOSGeometryBase.distance` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0020 | `django.db.models.query.QuerySet.as_manager` | skipped | gold_below_min |
| p3b_fwd_django_0021 | `django.db.models.sql.query.Query.__deepcopy__` | skipped | gold_below_min |
| p3b_fwd_django_0023 | `django.contrib.auth.views.PasswordResetConfirmView.get_form_kwargs` | skipped | focal_not_run |
| p3b_fwd_django_0024 | `django.contrib.sessions.backends.signed_cookies.SessionStore.delete` | skipped | focal_not_run |
| p3b_fwd_django_0027 | `django.utils.connection.BaseConnectionHandler.__delitem__` | skipped | focal_not_run |
| p3b_fwd_django_0028 | `django.template.defaulttags.widthratio` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0032 | `django.templatetags.i18n.TranslateNode.render` | skipped | trace_exit_nonzero |
| p3b_fwd_django_0033 | `django.db.migrations.writer.MigrationWriter.register_serializer` | skipped | gold_below_min |
| p3b_fwd_django_0034 | `django.utils.feedgenerator.rfc2822_date` | kept | 4 |
| p3b_fwd_django_0037 | `django.forms.widgets.CheckboxSelectMultiple.value_omitted_from_data` | skipped | gold_below_min |
| p3b_fwd_django_0038 | `django.db.migrations.operations.models.CreateModel.database_forwards` | kept | 4 |

## trpc

| task | focal | status | gold / reason |
|---|---|---|---|
| p3b_fwd_trpc_0007 | `core.middleware.experimental_standaloneMiddleware` | skipped | gold_below_min |
| p3b_fwd_trpc_0009 | `adapters.fetch.fetchRequestHandler.createContext` | skipped | gold_below_min |
| p3b_fwd_trpc_0010 | `observable.internals.observableToPromise.ObservableAbortError.constructor` | skipped | focal_not_run |
| p3b_fwd_trpc_0016 | `deprecated.router.Router.middleware` | skipped | gold_below_min |
| p3b_fwd_trpc_0019 | `adapters.fastify.fastifyTRPCPlugin.fastifyTRPCPlugin` | skipped | gold_below_min |
| p3b_fwd_trpc_0021 | `deprecated.router.Router.formatError` | skipped | gold_below_min |
| p3b_fwd_trpc_0025 | `observable.observable.observable` | kept | 3 |
| p3b_fwd_trpc_0026 | `observable.internals.observableToPromise.error` | skipped | focal_not_run |
| p3b_fwd_trpc_0027 | `adapters.aws-lambda.onError` | skipped | focal_not_run |
| p3b_fwd_trpc_0034 | `observable.operators.tap.next` | skipped | gold_below_min |
| p3b_fwd_trpc_0037 | `http.batchStreamFormatter.end` | skipped | focal_not_run |
| p3b_fwd_trpc_0038 | `deprecated.router.Router.subscription` | skipped | gold_below_min |
| p3b_fwd_trpc_0045 | `core.initTRPC.TRPCBuilder.create` | skipped | gold_below_min |
| p3b_fwd_trpc_0046 | `observable.operators.map.complete` | skipped | focal_not_run |
| p3b_fwd_trpc_0052 | `adapters.fastify.fastifyRequestHandler.createContext` | skipped | gold_below_min |
| p3b_fwd_trpc_0053 | `core.router.createRouterInner.createCaller` | skipped | gold_below_min |
| p3b_fwd_trpc_0054 | `adapters.next.createNextApiHandler` | skipped | gold_below_min |
| p3b_fwd_trpc_0059 | `adapters.node-http.content-type.form-data.fileUploadHandler.NodeOnDiskFile.stream` | skipped | focal_not_run |
| p3b_fwd_trpc_0061 | `observable.operators.map.subscribe` | skipped | focal_not_run |
| p3b_fwd_trpc_0064 | `shared.createProxy.noop` | skipped | focal_not_run |
| p3b_fwd_trpc_0068 | `observable.operators.tap.subscribe` | skipped | gold_below_min |
| p3b_fwd_trpc_0070 | `observable.operators.tap.tap` | skipped | gold_below_min |
| p3b_fwd_trpc_0073 | `deprecated.router.Router.constructor` | skipped | focal_not_run |
| p3b_fwd_trpc_0075 | `deprecated.router.Router.createCaller` | skipped | gold_below_min |

## fastapi

| task | focal | status | gold / reason |
|---|---|---|---|
| p3b_fwd_fastapi_0002 | `fastapi._compat.is_bytes_sequence_annotation` | skipped | gold_below_min |
| p3b_fwd_fastapi_0004 | `fastapi.routing.APIRouter.get` | skipped | gold_below_min |
| p3b_fwd_fastapi_0006 | `fastapi.routing.APIRouter.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0007 | `fastapi.routing.APIRouter.put` | skipped | gold_below_min |
| p3b_fwd_fastapi_0008 | `fastapi.security.api_key.APIKeyCookie.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0009 | `fastapi.responses.UJSONResponse.render` | skipped | gold_below_min |
| p3b_fwd_fastapi_0012 | `fastapi.openapi.docs.get_swagger_ui_html` | skipped | gold_below_min |
| p3b_fwd_fastapi_0013 | `fastapi.security.oauth2.OAuth2AuthorizationCodeBearer.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0015 | `fastapi.param_functions.File` | skipped | gold_below_min |
| p3b_fwd_fastapi_0016 | `fastapi.applications.FastAPI.trace` | skipped | gold_below_min |
| p3b_fwd_fastapi_0019 | `fastapi.applications.FastAPI.put` | skipped | gold_below_min |
| p3b_fwd_fastapi_0021 | `fastapi.security.oauth2.SecurityScopes.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0023 | `fastapi._compat.ModelField.default` | kept | 5 |
| p3b_fwd_fastapi_0024 | `fastapi.cli.main` | skipped | focal_not_run |
| p3b_fwd_fastapi_0025 | `fastapi.security.api_key.APIKeyQuery.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0026 | `fastapi.param_functions.Cookie` | skipped | gold_below_min |
| p3b_fwd_fastapi_0027 | `fastapi.security.oauth2.OAuth2PasswordBearer.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0028 | `fastapi.applications.FastAPI.__init__` | skipped | gold_below_min |
| p3b_fwd_fastapi_0029 | `fastapi.responses.ORJSONResponse.render` | skipped | gold_below_min |
| p3b_fwd_fastapi_0032 | `fastapi.routing.APIRouter.delete` | skipped | gold_below_min |
| p3b_fwd_fastapi_0033 | `fastapi.params.Depends.__init__` | kept | 3 |
| p3b_fwd_fastapi_0034 | `fastapi.datastructures.UploadFile.write` | skipped | gold_below_min |
| p3b_fwd_fastapi_0035 | `fastapi.param_functions.Depends` | skipped | gold_below_min |
| p3b_fwd_fastapi_0037 | `fastapi.security.http.HTTPDigest.__init__` | skipped | gold_below_min |

## express

| task | focal | status | gold / reason |
|---|---|---|---|
| p3b_fwd_express_0003 | `lib.response.download` | skipped | gold_below_min |
| p3b_fwd_express_0005 | `lib.view.lookup` | skipped | gold_below_min |
| p3b_fwd_express_0007 | `lib.router.route` | skipped | trace_exit_nonzero |
| p3b_fwd_express_0011 | `lib.application.listen` | skipped | gold_below_min |
| p3b_fwd_express_0012 | `lib.request.is` | skipped | gold_below_min |
| p3b_fwd_express_0013 | `lib.application.enabled` | kept | 9 |
| p3b_fwd_express_0015 | `lib.application.disable` | skipped | gold_below_min |
| p3b_fwd_express_0016 | `lib.request.acceptsCharsets` | skipped | gold_below_min |
| p3b_fwd_express_0017 | `lib.express.app` | skipped | gold_below_min |
| p3b_fwd_express_0019 | `lib.application.render` | skipped | gold_below_min |
| p3b_fwd_express_0020 | `lib.view.resolve` | skipped | gold_below_min |
| p3b_fwd_express_0021 | `lib.request.acceptsEncodings` | skipped | gold_below_min |
| p3b_fwd_express_0023 | `lib.request.range` | skipped | gold_below_min |
| p3b_fwd_express_0026 | `lib.application.param` | skipped | gold_below_min |
| p3b_fwd_express_0027 | `lib.request.param` | skipped | gold_below_min |
| p3b_fwd_express_0028 | `lib.application.disabled` | skipped | gold_below_min |
| p3b_fwd_express_0029 | `lib.view.View` | skipped | gold_below_min |
| p3b_fwd_express_0031 | `lib.router.exports` | kept | 5 |
| p3b_fwd_express_0033 | `lib.view.render` | skipped | gold_below_min |
| p3b_fwd_express_0036 | `lib.application.engine` | skipped | gold_below_min |
| p3b_fwd_express_0037 | `lib.router.use` | skipped | trace_exit_nonzero |
| p3b_fwd_express_0038 | `lib.request.acceptsLanguages` | skipped | gold_below_min |
| p3b_fwd_express_0039 | `lib.router.param` | skipped | gold_below_min |
| p3b_fwd_express_0041 | `lib.application.route` | skipped | gold_below_min |

