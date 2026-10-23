# Stage 1: candidate conversion gates (CANDIDATE_V3.md, CANDIDATE_V4.md)

## v4: **PASS**

3569 summaries over 10 repositories; first pass flagged 886 (25%), rewritten 676, lost 0, unchecked 0.

| check | pooled rate | result |
|---|---|---|
| axis_label <= 0.05 | 0.0 | pass |
| filler <= 0.05 | 0.0 | pass |
| identifier <= 0.05 | 0.001 | pass |
| callee_list <= 0.05 | 0.0 | pass |
| restates_name <= 0.1 | 0.058 | pass |
| no summary lost |  | pass |
| every entry checked |  | pass |

| repo | n | first pass flagged | repaired | axis_label | filler | identifier | callee_list | restates_name |
|---|---|---|---|---|---|---|---|---|
| fastapi_template | 64 | 11 | 10 | 0.0 | 0.0 | 0.0 | 0.0 | 0.016 |
| styleguide | 191 | 57 | 38 | 0.0 | 0.0 | 0.0 | 0.0 | 0.099 |
| express | 154 | 25 | 21 | 0.0 | 0.0 | 0.0 | 0.0 | 0.026 |
| trpc | 610 | 147 | 118 | 0.0 | 0.0 | 0.003 | 0.0 | 0.044 |
| flask | 366 | 90 | 75 | 0.0 | 0.0 | 0.0 | 0.0 | 0.041 |
| httpie | 522 | 151 | 121 | 0.0 | 0.0 | 0.002 | 0.0 | 0.056 |
| fastify | 318 | 81 | 65 | 0.0 | 0.0 | 0.0 | 0.0 | 0.05 |
| zod | 287 | 82 | 42 | 0.0 | 0.0 | 0.0 | 0.0 | 0.139 |
| click | 502 | 126 | 96 | 0.0 | 0.0 | 0.0 | 0.0 | 0.06 |
| hono | 555 | 116 | 90 | 0.0 | 0.0 | 0.004 | 0.0 | 0.045 |

Before / after samples:

- `app.utils.generate_new_account_email` (fastapi_template): 'Creates new account email data.' -> 'Creates new account email with subject and HTML content, returns EmailData'
- `app.utils.generate_test_email` (fastapi_template): 'Creates test email data for a specified recipient.' -> 'Generates test email with subject and HTML content, returns EmailData'
- `app.api.deps.get_current_active_superuser` (fastapi_template): 'Checks if the current user is an active superuser and returns the user.' -> 'Ensures user is superuser; raises 403 if not, else returns user'
- `app.api.routes.login.test_token` (fastapi_template): 'Tests the validity of the provided access token.' -> 'Returns the current user details after token test'
- `app.core.config.Settings.emails_enabled` (fastapi_template): 'Computes if email functionality is enabled.' -> 'Checks SMTP host and email; computes and returns boolean indicating if emails are enabled.'
- `app.core.config.Settings._set_default_emails_from` (fastapi_template): 'Sets default email sender address.' -> 'Sets email sender name to project name if not already set, then returns the settings object.'
- `styleguide_example.files.apis.FileDirectUploadFinishApi.InputSerializer` (styleguide): 'Serializes input data for direct upload finish.' -> 'Validates and extracts file ID from input data.'
- `styleguide_example.blog_examples.google_login_server_flow.sdk.services.GoogleSdkLoginFlowService._generate_client_config` (styleguide): 'Generates client configuration for Google login.' -> 'Creates a client config dictionary with credentials and URLs'
- `styleguide_example.files.services.FileDirectUploadService.__init__` (styleguide): 'Initializes FileDirectUploadService with user object.' -> 'Initializes the service with a user object, setting it as an instance attribute.'
- `styleguide_example.common.models.BaseModel.Meta` (styleguide): 'Metadata for a base model class.' -> 'Marks the model as abstract, preventing direct instantiation.'
- `styleguide_example.blog_examples.google_login_server_flow.raw.service.GoogleAccessTokens.decode_id_token` (styleguide): "Decodes Google's ID token using JWT." -> 'Decodes JWT token and returns its claims without verifying the signature.'
- `styleguide_example.tasks.celery.receiver_bind_extra_request_metadata` (styleguide): 'Binds extra request metadata to the structlog context.' -> 'Adds task name to context variables when task is available'
- `examples.view-locals.user.count` (express): 'Counts users based on provided function.' -> 'Asynchronously returns the number of users to a callback function'
- `lib.router.layer.handle_error` (express): 'Handles errors for a specific routing layer.' -> "Checks error handler's signature; handles error if valid, otherwise passes it to next handler."
- `lib.utils.createETagGenerator` (express): 'Creates an ETag generator function with specified options.' -> 'Generates an ETag for data, converting it to a buffer if needed.'
- `examples.view-locals.user.all` (express): 'Retrieves all users based on provided function.' -> 'Reads all users and asynchronously calls callback with users'
- `lib.router.exports` (express): 'Initializes a new Router with given options.' -> 'Creates a new router with specified options to handle routing.'
- `lib.utils.acceptParams` (express): 'Parses accept parameters from a string into an object with value, quality, and params.' -> 'Parses input string to extract value, quality, and parameters.'
- `packages.client.src.links.internals.httpUtils.getBody` (trpc): 'Prepares the request body from input options.' -> 'Checks if type is query; if not, gets input, converts to JSON, and returns body.'
- `examples.next-prisma-websockets-starter.src.pages.api.auth.[...nextauth].profile` (trpc): 'Handles user profile data for authentication.' -> 'Transforms user profile data for API response'
- `packages.server.src.observable.operators.share.resetIfNeeded` (trpc): 'Resets the shared observable if needed.' -> 'Resets subscription if ref count is zero and subscription exists.'
- `examples.next-prisma-todomvc.src.components.footer.TRPCLogo` (trpc): 'Displays TRPC logo in footer.' -> 'Renders a blue rounded rectangle with white paths as an SVG logo.'
- `www.src.pages.media.PressContent` (trpc): 'Manages press content display.' -> 'Renders a media assets page with logo and info cards.'
- `www.og-image.utils.zodParams.truncatedWordSchema` (trpc): 'Creates a Zod schema for truncated strings.' -> 'Transforms a string by truncating it to a specified number of characters.'
- `flask.sansio.blueprints.Blueprint.after_app_request` (flask): "Registers a function to run after every request, similar to Flask's after_request." -> 'Registers a function to run after each request, not just those handled by the blueprint.'
- `flask.sessions.SessionInterface.get_expiration_time` (flask): 'Calculates and returns the expiration time for the session.' -> 'Returns session expiration time if permanent, else None'
- `flask.views.MethodView.dispatch_request` (flask): 'Dispatches request based on method, returns a response.' -> 'Finds and calls the appropriate handler for the HTTP request method, retrying for HEAD if needed.'
- `flask.ctx.RequestContext.__repr__` (flask): 'Returns string representation of request context.' -> 'Generates a string showing request URL, method, and app name'
- `flask.sansio.blueprints.Blueprint.register_template` (flask): 'Registers a template with the blueprint.' -> "Registers a template filter in the app's Jinja environment."
- `flask.sessions.SecureCookieSession.__getitem__` (flask): 'Retrieves a value from the session by key.' -> 'Marks session as accessed and returns the value for the given key'
- `httpie.cli.options.Group.add_argument` (httpie): 'Adds an argument to group with given options.' -> 'Creates an argument, initializes it, adds to list, and returns it.'
- `httpie.output.formatters.colors.get_available_styles` (httpie): 'Retrieves available Pygments styles.' -> 'Returns a sorted list of available styles for coloring output'
- `httpie.output.streams.EncodedStream.encoding` (httpie): 'Gets or sets the encoding for the stream.' -> 'Returns encoding if available; otherwise, guesses and sets if consistent'
- `httpie.uploads.wrapped` (httpie): 'Caches properties by wrapping a function.' -> 'Wraps a function to compute a chunk, send a callback, and return the chunk.'
- `httpie.cli.definition.format_style_help` (httpie): 'Formats style help text using text wrapping.' -> 'Formats and returns help text for output coloring styles, optionally adding system-specific instructions'
- `httpie.manager.compat._run_pip_subprocess` (httpie): 'Runs pip subprocess with given executable and arguments, returns output.' -> 'Executes pip command, captures output, raises error if fails'
- `lib.validation.answer` (fastify): 'Validates an answer, returns result.' -> 'Checks return value; if false, returns errors; if error, returns it; if value, updates request and returns false.'
- `lib.schemas.Schemas` (fastify): 'Initializes the schemas with a store.' -> 'Sets up a store with provided initial data or an empty object if none is given.'
- `lib.schema-controller.buildSchemaController` (fastify): 'Builds a schema controller for Fastify context.' -> 'Builds schema controller with options, ensuring validators and serializers are set'
- `lib.reply.sendWebStream` (fastify): 'Sends a web stream as a response, handling locked streams.' -> 'Checks if stream is locked; if not, converts and sends the stream.'
- `lib.fourOhFour.setNotFoundHandler` (fastify): 'Sets a not found handler for a Fastify instance with options.' -> "Sets not found handler for Fastify instance, ensuring it's not already set and binding it correctly."
- `fastify.defaultBuildPrettyMeta` (fastify): 'Builds pretty metadata for a given route.' -> "It creates and returns a shallow copy of the route's context, filtering only allowed properties."
- `types.customMap` (zod): 'Maps input to output using custom logic.' -> 'Computes and returns an error message based on error code and context.'
- `ZodError.ZodError.flatten` (zod): 'Flattens error issues using a mapper function.' -> 'Processes validation issues into field and form errors, mapping messages as needed'
- `helpers.parseUtil.OK` (zod): 'Marks value as valid.' -> "Wraps the input value in an object with a 'valid' status"
- `types.ZodString._addCheck` (zod): 'Adds a validation check to the string schema.' -> 'Adds a validation check to the string schema by creating a new schema instance with the updated checks list.'
- `types.isOptional` (zod): 'Checks if a value is optional, returns boolean.' -> 'Determines if a value is optional by parsing undefined and checking success'
- `types.ZodString.uuid` (zod): 'Validates if string is a valid UUID.' -> 'Adds UUID format check to schema with optional custom error message'
- `click.core.MultiCommand.function` (click): 'Handles function calls for multi-command operations.' -> 'Processes input by calling an old function and then another'
- `click.core.Context.find_object` (click): 'Finds the closest object of a given type.' -> 'Traverses context hierarchy to find and return the closest object of a specified type, or None if not found'
- `click.utils.LazyFile.open` (click): "Opens the file if it's not already open and returns the file object." -> "Opens a file if not already open, handling errors with Click's FileError."
- `click.core.MultiCommand.format_options` (click): 'Formats options and commands for the help output.' -> 'Formats command options and appends command list to help output'
- `click.utils.KeepOpenFile.__enter__` (click): 'Returns KeepOpenFile for context management.' -> 'Returns the instance itself to support context management.'
- `click._termui_impl.ProgressBar.format_pos` (click): 'Formats current position in progress as string.' -> 'Formats position string, appending length if available, and returns it'
- `helper.streaming.text.streamText` (hono): 'Streams text data with context and error handling.' -> 'Sets headers for text streaming, initiates text stream, manages errors'
- `utils.body.parseBody` (hono): 'Parses request body based on options.' -> 'Parses request body based on content type and options, returns parsed data or empty object'
- `hono.Hono.constructor` (hono): 'Creates a new Hono application instance.' -> 'Sets up a new Hono instance with a default router if none provided.'
- `http-exception.HTTPException.getResponse` (hono): 'Returns the response object for the HTTP exception.' -> 'Returns a response object with error message and status code if no response object exists.'
- `helper.css.newCssClassNameObject` (hono): 'Generates a new CSS class name object from a common class name.' -> 'Adds CSS styles to context, updates map, and returns script promise'
- `utils.accept.sortByQualityAndIndex` (hono): 'Sorts items by quality and index.' -> 'Sorts two items by quality descending, then by index ascending'

