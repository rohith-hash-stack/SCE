# Stage 1: candidate conversion gates (CANDIDATE_V3.md)

## v3-name: **FAIL**

3569 summaries over 10 repositories; first pass flagged 886 (25%), rewritten by repair 499, lost 0, unchecked 0.

| check | pooled rate | result |
|---|---|---|
| axis_label <= 0.05 | 0.0 | pass |
| filler <= 0.05 | 0.001 | pass |
| identifier <= 0.05 | 0.005 | pass |
| callee_list <= 0.05 | 0.0 | pass |
| restates_name <= 0.1 | 0.103 | **FAIL** |
| no summary lost |  | pass |
| every entry checked |  | pass |

| repo | n | first pass flagged | repaired | axis_label | filler | identifier | callee_list | restates_name |
|---|---|---|---|---|---|---|---|---|
| fastapi_template | 64 | 11 | 10 | 0.0 | 0.0 | 0.0 | 0.0 | 0.016 |
| styleguide | 191 | 57 | 30 | 0.0 | 0.0 | 0.0 | 0.0 | 0.141 |
| express | 154 | 25 | 12 | 0.0 | 0.0 | 0.0 | 0.006 | 0.078 |
| trpc | 610 | 147 | 87 | 0.0 | 0.0 | 0.01 | 0.0 | 0.089 |
| flask | 366 | 90 | 54 | 0.0 | 0.0 | 0.014 | 0.0 | 0.085 |
| httpie | 522 | 151 | 96 | 0.0 | 0.0 | 0.004 | 0.0 | 0.102 |
| fastify | 318 | 81 | 41 | 0.0 | 0.006 | 0.0 | 0.0 | 0.119 |
| zod | 287 | 82 | 36 | 0.0 | 0.0 | 0.0 | 0.0 | 0.16 |
| click | 502 | 126 | 68 | 0.0 | 0.0 | 0.002 | 0.0 | 0.114 |
| hono | 555 | 116 | 65 | 0.0 | 0.0 | 0.005 | 0.0 | 0.088 |

Before / after samples:

- `app.models.get_datetime_utc` (fastapi_template): 'Returns current UTC datetime.' -> 'Retrieves and returns the current date and time in UTC.'
- `app.utils.generate_test_email` (fastapi_template): 'Creates test email data for a specified recipient.' -> 'Generates test email data for a specified recipient using a template.'
- `app.api.deps.get_current_active_superuser` (fastapi_template): 'Checks if the current user is an active superuser and returns the user.' -> 'Verifies if the user is an active superuser and returns the user details.'
- `app.api.routes.login.test_token` (fastapi_template): 'Tests the validity of the provided access token.' -> 'Checks if the provided access token is valid.'
- `app.core.config.Settings.emails_enabled` (fastapi_template): 'Computes if email functionality is enabled.' -> 'Checks if email functionality is enabled and returns a boolean.'
- `app.core.config.Settings._set_default_emails_from` (fastapi_template): 'Sets default email sender address.' -> "Sets the default email sender address in the application's configuration."
- `styleguide_example.blog_examples.google_login_server_flow.sdk.services.GoogleAccessTokens.decode_id_token` (styleguide): 'Decodes Google ID token.' -> 'Decodes Google ID token using JWT library.'
- `styleguide_example.authentication.apis.UserSessionLoginApi.InputSerializer` (styleguide): 'Validates and serializes login input data.' -> 'Checks and converts login input data into a serialized format.'
- `styleguide_example.blog_examples.admin_2fa.views.AdminConfirmTwoFactorAuthView.Form` (styleguide): 'Form for confirming 2FA setup.' -> 'Form for users to confirm their two-factor authentication setup.'
- `styleguide_example.users.filters.BaseUserFilter.Meta` (styleguide): 'Metadata for user filtering.' -> 'Metadata for defining user filtering criteria.'
- `styleguide_example.files.apis.FileStandardUploadApi.post` (styleguide): 'Handles file upload, validates, and saves it.' -> 'Accepts file upload, checks validity, then saves it.'
- `styleguide_example.authentication.apis.UserMeApi.get` (styleguide): 'Retrieves user information and returns it.' -> 'Fetches user login data and returns it as a response.'
- `examples.view-locals.ferrets` (express): 'Filters and returns ferret data for a user.' -> 'Checks user data and returns filtered ferret information.'
- `lib.utils.setCharset` (express): 'Sets the charset in a Content-Type string, handling type and charset.' -> 'Updates the charset in a Content-Type string, handling both type and charset.'
- `lib.router.layer.handle_error` (express): 'Handles errors for a specific routing layer.' -> 'Processes errors for a specific routing layer.'
- `lib.router.mergeParams` (express): 'Combines current parameters with parent parameters.' -> 'Merges current parameters with parent parameters using a utility function.'
- `lib.router.wrap` (express): 'Wraps a function to modify its behavior.' -> "Modifies a function's behavior by wrapping it."
- `lib.application.render` (express): 'Renders a view with options and returns the template string.' -> 'Renders a view using options and a callback to return the template string.'
- `packages.server.src.deprecated.router.getDataTransformer` (trpc): 'Combines data transformer options into a single transformer object.' -> 'Takes data transformer options and merges them into a unified transformer object.'
- `packages.server.src.core.initTRPC.TRPCBuilder.context` (trpc): 'Sets up context for TRPC operations.' -> 'Configures the environment for TRPC operations.'
- `packages.server.src.observable.internals.observableToPromise.error` (trpc): 'Handles errors in the observable processing.' -> 'Processes and handles errors during observable conversion to promise.'
- `packages.client.src.links.loggerLink.constructPartsAndArgs` (trpc): 'Constructs parts and arguments for logging.' -> 'Assembles parts and arguments for logging based on options.'
- `examples.next-prisma-websockets-starter.src.pages.api.auth.[...nextauth].profile` (trpc): 'Handles user profile data for authentication.' -> 'Processes user profile data for authentication purposes.'
- `packages.server.src.observable.operators.tap.subscribe` (trpc): 'Handles subscription with an observer.' -> 'Subscribes an observer to receive updates.'
- `flask.json.provider.DefaultJSONProvider.response` (flask): 'Serializes data to JSON and returns a response object.' -> 'Converts data to JSON format and returns a response object with the JSON content.'
- `flask.templating.render_template` (flask): 'Renders a template by name with the given context.' -> 'Generates HTML by filling a template with provided data.'
- `flask.json.tag.TagDict.check` (flask): 'Checks if a value is a dictionary for tagging.' -> 'Determines if a given value is a dictionary for tagging purposes.'
- `flask.templating.DispatchingJinjaLoader.get_source` (flask): 'Retrieves template source from loaders.' -> 'Retrieves template source by checking multiple loaders.'
- `flask.cli.ScriptInfo.load_app` (flask): 'Loads and returns the Flask application.' -> 'Loads and returns the Flask application, checking if already loaded.'
- `flask.debughelpers.FormDataRoutingRedirect.__init__` (flask): 'Initializes redirect for form data routing.' -> 'Sets up redirection for form data handling in a request.'
- `extras.scripts.generate_man_pages.ManPageBuilder.title_line` (httpie): 'Adds a title line to the man page source.' -> 'Appends a title line to the man page source using provided program details.'
- `httpie.client.transform_headers` (httpie): 'Transforms headers to prepare the request.' -> 'Modifies headers to adjust the request preparation.'
- `httpie.output.formatters.xml.parse_xml` (httpie): 'Parses XML data string into a Document object.' -> 'Converts XML string into a Document object using defusedxml.'
- `httpie.output.formatters.colors.ColorFormatter.format_headers` (httpie): 'Formats headers with syntax highlighting.' -> 'Applies syntax highlighting to HTTP headers.'
- `httpie.cli.argparser.HTTPieArgumentParser.print_usage` (httpie): 'Prints usage information to the console.' -> 'Displays usage instructions in the console.'
- `httpie.cli.utils.LazyChoices.__contains__` (httpie): 'Checks if an item is in the loaded choices.' -> 'Determines if an item exists in the loaded choices.'
- `fastify.validateSchemaErrorFormatter` (fastify): 'Validates schema error formatter function.' -> 'Checks and sets a schema error formatter function for validation.'
- `lib.validation.compileSchemasForValidation` (fastify): 'Compiles schemas for validation, handling warnings.' -> 'Compiles schemas for validation and handles warnings during the process.'
- `lib.reqIdGenFactory.buildDefaultGenReqId` (fastify): 'Builds a default request ID generator function.' -> 'Creates a function to generate default request IDs.'
- `lib.reply.buildReply` (fastify): 'Builds reply properties from input.' -> 'Extracts and organizes properties from input to create a reply.'
- `lib.schema-controller.buildSchemaController` (fastify): 'Builds a schema controller for Fastify context.' -> 'Creates a schema controller for Fastify context using specified options.'
- `lib.hooks.handleResolve` (fastify): 'Handles the resolution of a hook.' -> 'Resolves a hook by calling the next handler in the sequence.'
- `types.constructor` (zod): 'Initializes a new object with specified methods and properties.' -> 'Sets up a new object with defined methods and properties.'
- `ZodError.ZodError.processError` (zod): 'Processes error recursively.' -> 'Recursively processes and formats error details.'
- `ZodError.ZodError.toString` (zod): 'Converts error to a string representation.' -> 'Transforms error details into a string format.'
- `helpers.parseUtil.ParseStatus.dirty` (zod): 'Marks parsing status as dirty.' -> 'Sets the parsing status to indicate changes.'
- `helpers.parseUtil.addIssueToContext` (zod): 'Adds issue to parsing context.' -> "Adds an issue to the parsing context's issues list."
- `types._parseSync` (zod): 'Synchronously parses input data and returns result.' -> 'Parses input data synchronously, checks for errors, and returns the result.'
- `click.utils.get_text_stream` (click): 'Returns a text stream for system input/output, handling encoding.' -> 'Returns a text stream for system input/output, handling encoding and errors.'
- `click.decorators.new_func#4` (click): 'Invokes the function with the current context.' -> 'Invokes a function using the current execution context.'
- `click.core.Parameter.add_to_parser` (click): 'Adds the parameter to a parser, not implemented.' -> 'Adds a parameter to a parser, raising NotImplementedError.'
- `click.core.MultiCommand.format_options` (click): 'Formats options and commands for the help output.' -> 'Formats options and commands for help display in a command-line interface.'
- `click.testing.EchoingStdin.readlines` (click): 'Reads lines from input and echoes them.' -> 'Reads lines from input and echoes them to standard output.'
- `click.exceptions.FileError.format_message` (click): 'Formats error message for file-related issues.' -> 'Generates a localized error message for file-related problems.'
- `router.reg-exp-router.router.RegExpRouter.constructor` (hono): 'Initializes a new instance of the RegExpRouter class.' -> 'Creates a new instance of the RegExpRouter class without any specific initialization.'
- `helper.ssg.utils.filterStaticGenerateRoutes` (hono): 'Filters static generate routes from hono routes.' -> 'Processes Hono routes to extract and filter static generate routes.'
- `jsx.dom.css.splitRule` (hono): 'Splits a CSS rule string into an array of strings.' -> 'Takes a CSS rule string and splits it into an array of strings.'
- `request.HonoRequest.json` (hono): 'Parses the request body as JSON and returns the parsed data.' -> 'Converts JSON request body into parsed data.'
- `helper.adapter.getRuntimeKey` (hono): 'Retrieves the current runtime key.' -> 'Checks user agent, retrieves and returns the current runtime key.'
- `middleware.cors.cors` (hono): 'Sets CORS headers based on provided options.' -> 'Configures Cross-Origin Resource Sharing headers using specified options.'

## v3: **FAIL**

3569 summaries over 10 repositories; first pass flagged 1540 (43%), rewritten by repair 734, lost 0, unchecked 0.

| check | pooled rate | result |
|---|---|---|
| axis_label <= 0.05 | 0.0 | pass |
| filler <= 0.05 | 0.001 | pass |
| identifier <= 0.05 | 0.004 | pass |
| callee_list <= 0.05 | 0.0 | pass |
| restates_name <= 0.1 | 0.123 | **FAIL** |
| too_short <= 0.15 | 0.207 | **FAIL** |
| no summary lost |  | pass |
| every entry checked |  | pass |

| repo | n | first pass flagged | repaired | axis_label | filler | identifier | callee_list | restates_name | too_short |
|---|---|---|---|---|---|---|---|---|---|
| fastapi_template | 64 | 15 | 9 | 0.0 | 0.0 | 0.0 | 0.0 | 0.062 | 0.094 |
| styleguide | 191 | 86 | 45 | 0.0 | 0.0 | 0.0 | 0.0 | 0.141 | 0.23 |
| express | 154 | 52 | 25 | 0.0 | 0.0 | 0.0 | 0.006 | 0.084 | 0.156 |
| trpc | 610 | 291 | 129 | 0.0 | 0.0 | 0.008 | 0.0 | 0.098 | 0.256 |
| flask | 366 | 141 | 73 | 0.0 | 0.0 | 0.014 | 0.0 | 0.112 | 0.131 |
| httpie | 522 | 256 | 130 | 0.0 | 0.0 | 0.002 | 0.0 | 0.134 | 0.259 |
| fastify | 318 | 162 | 81 | 0.0 | 0.006 | 0.0 | 0.0 | 0.126 | 0.236 |
| zod | 287 | 127 | 48 | 0.0 | 0.0 | 0.0 | 0.0 | 0.178 | 0.251 |
| click | 502 | 205 | 100 | 0.0 | 0.0 | 0.002 | 0.0 | 0.143 | 0.175 |
| hono | 555 | 205 | 94 | 0.0 | 0.0 | 0.005 | 0.0 | 0.108 | 0.16 |

Before / after samples:

- `app.utils.generate_new_account_email` (fastapi_template): 'Creates new account email data.' -> 'Creates an email for a new account with user details.'
- `app.core.config.Settings._use_psycopg_driver` (fastapi_template): 'Validates and sets the PostgreSQL driver value.' -> 'Validates and sets the PostgreSQL driver value using class method.'
- `app.api.routes.users.delete_user_me` (fastapi_template): 'Deletes current user, raises 403 if unauthorized.' -> 'Deletes the current user and raises a 403 error if unauthorized.'
- `app.api.routes.login.test_token` (fastapi_template): 'Tests the validity of the provided access token.' -> 'Checks if the provided access token is valid.'
- `app.utils.generate_password_reset_token` (fastapi_template): 'Generates a password reset token for the given email.' -> 'Creates a password reset token for a specified email using current time and JWT encoding.'
- `app.api.routes.items.create_item` (fastapi_template): 'Creates a new item in the database.' -> 'Adds a new item to the database after validating input.'
- `styleguide_example.tasks.celery.receiver_setup_logging` (styleguide): 'Sets up logging configuration for Celery tasks.' -> 'Configures logging for Celery tasks with specified parameters.'
- `styleguide_example.blog_examples.admin_2fa.views.AdminConfirmTwoFactorAuthView.get_form_class` (styleguide): 'Returns form class for 2FA confirmation.' -> 'Retrieves form class for 2FA admin confirmation.'
- `styleguide_example.files.apis.FileStandardUploadApi.post` (styleguide): 'Handles file upload, validates, and saves it.' -> 'Processes file upload, validates, and saves it.'
- `styleguide_example.tasks.celery.receiver_bind_extra_request_metadata` (styleguide): 'Binds extra request metadata to the structlog context.' -> 'Adds extra request metadata to the structlog context.'
- `styleguide_example.authentication.apis.UserSessionLoginApi.post` (styleguide): 'Handles user login, authenticates and logs user in.' -> 'Validates user credentials, logs them in, and returns login data.'
- `styleguide_example.blog_examples.google_login_server_flow.raw.service.GoogleAccessTokens.decode_id_token` (styleguide): "Decodes Google's ID token using JWT." -> 'Decodes Google ID token using JWT library within the GoogleAccessTokens class.'
- `examples.web-service.error` (express): 'Handles custom errors with status and message.' -> 'Manages custom errors by setting status and message.'
- `examples.view-locals.ferrets` (express): 'Filters and returns ferret data for a user.' -> 'Checks user data and returns filtered ferret information.'
- `lib.router.layer.handle_error` (express): 'Handles errors for a specific routing layer.' -> 'Processes errors for a routing layer.'
- `lib.router.matchLayer` (express): 'Matches a path to a routing layer.' -> 'Checks if a path matches a routing layer.'
- `examples.route-separation.post.list` (express): 'Handles GET requests for posts, renders list.' -> 'Processes GET requests for posts and displays the list.'
- `lib.router.exports` (express): 'Initializes a new Router with given options.' -> 'Creates a new Router object with specified options.'
- `examples.fastify-server.src.server.router.trpc.errorFormatter` (trpc): 'Formats errors for TRPC in Fastify server.' -> 'Handles error formatting for TRPC in a Fastify server.'
- `examples.soa.server-a.createContext` (trpc): 'Initializes context for server A.' -> 'Sets up initial context for server A.'
- `packages.server.src.observable.operators.tap.subscribe` (trpc): 'Handles subscription with an observer.' -> 'Subscribes an observer to handle events.'
- `examples.express-server.src.server.createMessage` (trpc): 'Emits a message event with given text.' -> 'Sends a message event with the specified text.'
- `www.src.pages.HomeHead` (trpc): 'Handles home page head content.' -> 'Generates content for the home page header.'
- `packages.server.src.core.middleware.experimental_standaloneMiddleware` (trpc): 'Enables standalone middleware usage in TRPC.' -> 'Allows middleware to be used independently in TRPC.'
- `flask.wrappers.Request.max_form_parts#2` (flask): 'Sets the maximum number of form parts.' -> 'Sets the maximum number of form parts in a request.'
- `flask.json.tag.JSONTag.__init__` (flask): 'Initializes a tagger for a JSON serializer.' -> 'Sets up a tagger for a JSON serializer.'
- `flask.json.tag.PassDict.check` (flask): 'Checks if the given value is a dictionary.' -> 'Determines if the provided value is a dictionary and returns a boolean result.'
- `flask.debughelpers.explain_template_loading_attempts` (flask): 'Explains failed template loading attempts.' -> 'Logs failed template loading attempts with detailed information.'
- `flask.sessions.SessionInterface.is_null_session` (flask): 'Checks if a given object is a null session.' -> 'Determines if an object is a null session by checking its properties.'
- `flask.config.Config.__repr__` (flask): 'Returns a string representation of the config object.' -> "Generates a string that represents the configuration object's contents."
- `httpie.legacy.v3_1_0_session_cookie_format.fix_layout` (httpie): 'Fixes the layout of session cookies.' -> 'Adjusts session cookie layout for given session and hostname.'
- `httpie.cli.argtypes.SessionNameValidator.__init__` (httpie): 'Initializes session name validator with error message.' -> 'Sets up session name validator with error message.'
- `httpie.output.formatters.colors.get_available_styles` (httpie): 'Retrieves available Pygments styles.' -> 'Lists all available Pygments styles.'
- `httpie.cli.requestitems.process_data_raw_json_embed_arg` (httpie): 'Processes data argument by embedding raw JSON.' -> 'Embeds raw JSON from data argument into request.'
- `httpie.internal.daemon_runner.run_daemon_task` (httpie): 'Runs a daemon task with redirected input/output.' -> 'Executes a background task with redirected input and output.'
- `httpie.plugins.manager.PluginManager.filter` (httpie): 'Filters plugins by type, returning a list of matching plugins.' -> 'Checks plugins by type and returns a list of matching ones.'
- `lib.pluginUtils.getDisplayName` (fastify): 'Fetches the display name of a function.' -> 'Retrieves the display name of a given function.'
- `lib.fourOhFour.setContext` (fastify): 'Sets the context for a 404 handler on a Fastify instance.' -> 'Configures a 404 handler with given context on a Fastify instance.'
- `lib.fourOhFour.setNotFoundHandler` (fastify): 'Sets a not found handler for a Fastify instance with options.' -> 'Configures a handler for missing routes in a Fastify server using specified options.'
- `lib.validation.getEssenceMediaType` (fastify): 'Extracts essence media type from header.' -> 'Extracts media type from header by splitting.'
- `fastify.addSchema` (fastify): 'Adds a JSON schema for request validation.' -> 'Registers a JSON schema for request validation with Fastify.'
- `lib.initialConfigValidation.deepFreezeObject` (fastify): 'Recursively freezes object to prevent modifications.' -> 'Recursively locks an object to prevent any modifications.'
- `ZodError.ZodError.format` (zod): 'Formats error using optional mapper function.' -> 'Processes error with optional mapping function.'
- `errors.getErrorMap` (zod): 'Retrieves error mapping for parsing issues.' -> 'Returns a mapping of errors for parsing issues.'
- `types.handleResult.error` (zod): 'Handles errors during result processing.' -> 'Processes and manages errors encountered during result handling.'
- `types.ZodString.isNANOID` (zod): 'Validates if string is a non-sequential identifier.' -> 'Checks if a string is a valid non-sequential identifier.'
- `types.default` (zod): "Provides a default value for a field if it's undefined." -> "Sets a default value for a field if it's not already defined."
- `types.ZodNumber.isFinite` (zod): 'Ensures the number is finite during parsing.' -> 'Checks if the number is finite during parsing.'
- `click.core.Context.get_usage` (click): 'Formats and returns usage string for current context and command.' -> 'Generates and returns a formatted usage string for the current context and command.'
- `click.exceptions.ClickException.format_message` (click): 'Formats the exception message for display.' -> 'Generates a formatted exception message for display.'
- `click.parser.Option.takes_value` (click): 'Indicates whether the option accepts a value.' -> 'Determines if the option can accept a value.'
- `click.types.IntParamType.__repr__` (click): 'Returns a string representation of the integer type.' -> 'Generates a string describing the integer parameter type.'
- `click.types.FuncParamType.convert` (click): 'Converts value using provided function, fails if error.' -> 'Applies a function to a value; raises an error if it fails.'
- `click.types.StringParamType.convert` (click): 'Converts value to string, handling filesystem encoding.' -> 'Converts input value to string using filesystem encoding.'
- `utils.body.handleParsingNestedValues` (hono): 'Handles parsing nested values using dot notation.' -> 'Parses nested values using dot notation keys in form data.'
- `utils.concurrent.run` (hono): 'Runs a function asynchronously, handles promise resolution.' -> 'Executes a function asynchronously and manages promise resolution.'
- `jsx.streaming.popNodeStack` (hono): 'Removes a node from the rendering stack.' -> 'Removes a node from the rendering stack by calling pop on the data stack.'
- `jsx.base.JSXNode.type` (hono): 'Returns the type of the JSX node.' -> 'Determines and returns the type of a JSX node.'
- `jsx.dom.context.createContextProviderFunction` (hono): 'Creates a function to provide context values.' -> 'Creates a function to manage context values using an array.'
- `adapter.cloudflare-workers.utils.getContentFromKVAsset` (hono): 'Fetches content from a KV asset, optionally with specified options.' -> 'Retrieves content from a KV asset with optional settings.'

