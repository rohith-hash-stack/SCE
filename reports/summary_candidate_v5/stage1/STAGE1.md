# Stage 1: candidate conversion gates (CANDIDATE_V3.md, CANDIDATE_V4.md)

## v5: **PASS**

3569 summaries over 10 repositories; first pass flagged 1215 (34%), rewritten 923, lost 0, unchecked 0.

| check | pooled rate | result |
|---|---|---|
| axis_label <= 0.05 | 0.0 | pass |
| filler <= 0.05 | 0.0 | pass |
| identifier <= 0.05 | 0.001 | pass |
| callee_list <= 0.05 | 0.0 | pass |
| long_short <= 0.05 | 0.033 | pass |
| restates_name <= 0.1 | 0.058 | pass |
| no summary lost |  | pass |
| every entry checked |  | pass |

| repo | n | first pass flagged | repaired | axis_label | filler | identifier | callee_list | restates_name | long_short |
|---|---|---|---|---|---|---|---|---|---|
| fastapi_template | 64 | 15 | 12 | 0.0 | 0.0 | 0.0 | 0.0 | 0.016 | 0.031 |
| styleguide | 191 | 67 | 46 | 0.0 | 0.0 | 0.0 | 0.0 | 0.099 | 0.026 |
| express | 154 | 38 | 32 | 0.0 | 0.0 | 0.0 | 0.0 | 0.026 | 0.019 |
| trpc | 610 | 230 | 174 | 0.0 | 0.0 | 0.003 | 0.0 | 0.044 | 0.059 |
| flask | 366 | 123 | 104 | 0.0 | 0.0 | 0.0 | 0.0 | 0.041 | 0.025 |
| httpie | 522 | 192 | 152 | 0.0 | 0.0 | 0.002 | 0.0 | 0.056 | 0.023 |
| fastify | 318 | 120 | 95 | 0.0 | 0.0 | 0.0 | 0.0 | 0.05 | 0.038 |
| zod | 287 | 96 | 52 | 0.0 | 0.0 | 0.0 | 0.0 | 0.139 | 0.028 |
| click | 502 | 161 | 121 | 0.0 | 0.0 | 0.0 | 0.0 | 0.06 | 0.026 |
| hono | 555 | 173 | 135 | 0.0 | 0.0 | 0.004 | 0.0 | 0.045 | 0.032 |

Before / after samples:

- `app.core.config.Settings.emails_enabled` (fastapi_template): 'Computes if email functionality is enabled.' -> 'Checks SMTP host and email; computes and returns boolean indicating if emails are enabled.'
- `app.utils.generate_test_email` (fastapi_template): 'Creates test email data for a specified recipient.' -> 'Generates test email with subject and HTML content, returns EmailData'
- `app.alembic.versions.e2412789c190_initialize_models.upgrade` (fastapi_template): 'Creates initial database tables and indexes during an upgrade.' -> 'Sets up database tables and constraints for users and items.'
- `app.api.routes.users.update_user_me` (fastapi_template): "Updates current user's details, raises 409 if email conflict." -> 'Updates user profile, checks for email conflicts, commits changes, and returns updated user.'
- `app.main.custom_generate_unique_id` (fastapi_template): 'Generates a unique ID for a given route.' -> 'Generates a unique ID by combining the first tag and the name of the route.'
- `app.api.routes.login.test_token` (fastapi_template): 'Tests the validity of the provided access token.' -> 'Returns the current user details after token test'
- `styleguide_example.blog_examples.google_login_server_flow.raw.apis.GoogleLoginRedirectApi.get` (styleguide): "Redirects to Google's authorization URL for login." -> 'Generates Google OAuth2 URL, stores state in session, redirects user'
- `styleguide_example.emails.models.Email.Status` (styleguide): 'Represents the status of an email.' -> 'Defines constants for email statuses: Ready, Sending, Sent, Failed'
- `styleguide_example.blog_examples.google_login_server_flow.raw.service.GoogleRawLoginFlowService.get_authorization_url` (styleguide): 'Constructs and returns the authorization URL for Google login.' -> 'Generates Google authorization URL with state token for redirect'
- `styleguide_example.files.services.FileDirectUploadService.__init__` (styleguide): 'Initializes FileDirectUploadService with user object.' -> 'Initializes the service with a user object, setting it as an instance attribute.'
- `styleguide_example.authentication.services.auth_jwt_response_payload_handler` (styleguide): 'Handles JWT response payload, adding custom data.' -> 'Returns a dictionary containing the token.'
- `styleguide_example.files.apis.FileDirectUploadFinishApi.InputSerializer` (styleguide): 'Serializes input data for direct upload finish.' -> 'Validates and extracts file ID from input data.'
- `lib.application.use` (express): 'Adds middleware to the app router, handling requests.' -> 'Adds middleware to app router, setting path if provided, and returns the app instance.'
- `lib.express.createApplication` (express): 'Creates an Express application by initializing and handling requests.' -> 'Creates and initializes an Express application, configures request and response prototypes, and returns the app instance.'
- `examples.view-locals.user.all` (express): 'Retrieves all users based on provided function.' -> 'Reads all users and asynchronously calls callback with users'
- `lib.router.route` (express): 'Creates a new route with specified path.' -> 'Creates a route with a path, configures it, adds it to the stack, and returns it.'
- `examples.view-locals.ferrets` (express): 'Filters and returns ferret data for a user.' -> 'Checks if the user is a ferret and returns true or false.'
- `lib.router.layer.handle_error` (express): 'Handles errors for a specific routing layer.' -> "Checks error handler's signature; handles error if valid, otherwise passes it to next handler."
- `packages.next.src.withTRPC.shouldDehydrateQuery` (trpc): 'Determines if a query should be dehydrated.' -> 'Determines if a query should be dehydrated by checking its state.'
- `packages.server.src.rpc.parseTRPCMessage.assertIsObject` (trpc): 'Asserts the input is an object.' -> 'Asserts input is an object, throwing error for non-objects'
- `www.og-image.utils.zodParams.truncatedWordSchema` (trpc): 'Creates a Zod schema for truncated strings.' -> 'Transforms a string by truncating it to a specified number of characters.'
- `packages.client.src.internals.dataLoader.groupItems` (trpc): 'Groups items based on validation function.' -> 'Groups items by validity, rejects invalid ones, returns grouped batches'
- `packages.react-query.src.ssg.ssg.createSSGHelpers` (trpc): 'Generates helper functions for server-side rendering and static generation.' -> 'Sets up server-side helpers for rendering, using options to resolve and send queries'
- `packages.client.src.TRPCClientError.isTRPCClientError` (trpc): 'Checks if an error is an instance of TRPCClientError.' -> 'Determines if an error is a TRPCClientError, considering legacy naming'
- `flask.templating.stream_template_string` (flask): 'Streams a template from source string with the given context.' -> 'Renders template from string with context, returns streaming content'
- `flask.app.Flask.update_template_context` (flask): 'Adds common variables to the template rendering context.' -> 'Updates template context with request, session, config, and custom variables, preserving original values.'
- `flask.sessions.SessionInterface.should_set_cookie` (flask): 'Decides if a Set-Cookie header should be set for the session.' -> 'Determines if cookie should be set by checking session modification or permanence'
- `flask.json.provider.JSONProvider.load` (flask): 'Deserializes JSON data from a file.' -> 'Reads JSON data from a file and deserializes it.'
- `flask.sansio.app.App.inject_url_defaults` (flask): 'Injects URL defaults for an endpoint into the values dictionary.' -> 'Injects URL defaults for an endpoint into the values dictionary used for URL building.'
- `flask.sansio.blueprints.Blueprint.teardown_app_request` (flask): "Registers a function to run after every request teardown, similar to Flask's teardown_request." -> 'Registers a function to run after every request, not just those handled by the blueprint.'
- `httpie.cli.argparser.HTTPieArgumentParser._process_output_options` (httpie): 'Applies default output options or validates user-provided ones.' -> 'Applies default output options or validates provided ones, adjusts for verbose and download flags.'
- `httpie.internal.update_warnings._read_data_error_free` (httpie): 'Reads data from a file, handling errors gracefully.' -> 'Reads and parses JSON file, returns empty dict on error'
- `httpie.output.streams.PrettyStream.iter_body` (httpie): 'Processes and yields the body lines, handling binary suppression.' -> 'Iterates over HTTP body, converts if needed, and yields processed lines'
- `httpie.downloads.DownloadStatus.has_finished` (httpie): 'Checks if a download has finished.' -> 'Returns whether the download has completed by checking if the finish time is set'
- `httpie.cli.nested_json.tokens.Path.reconstruct` (httpie): 'Reconstructs path as string.' -> 'Constructs and returns a string representing the path token based on its kind and accessor.'
- `httpie.sessions.is_anonymous_session` (httpie): 'Checks if a session is anonymous based on its name.' -> 'Determines if a session is anonymous by checking for a path separator in the session name.'
- `lib.reqIdGenFactory.buildDefaultGenReqId` (fastify): 'Builds a default request ID generator function.' -> 'Generates a unique request ID, wraps around at max 32-bit integer, and formats it as a base-36 string.'
- `lib.reply.sendTrailer` (fastify): 'Sends a trailer for the response and ends it.' -> 'Sends trailers if available, otherwise closes the response stream'
- `lib.route.buildRouterOptions` (fastify): 'Builds router options with default settings.' -> 'Builds router options by merging user options with defaults, warns on deprecated options'
- `lib.hooks.onListenHookRunner` (fastify): 'Runs hooks when the server starts listening.' -> 'Runs on-listen hooks sequentially, handling errors, and recursively on child servers.'
- `fastify.inject` (fastify): 'Simulates HTTP requests for testing, ensuring server readiness.' -> "Injects requests into a Fastify server, ensuring it's ready and handling errors gracefully."
- `lib.route.hasRoute` (fastify): 'Checks for a route without parameters or return value.' -> 'Checks if a route exists by normalizing the method to uppercase and querying the router.'
- `ZodError.ZodError.flatten` (zod): 'Flattens error issues using a mapper function.' -> 'Processes validation issues into field and form errors, mapping messages as needed'
- `helpers.parseUtil.OK` (zod): 'Marks value as valid.' -> "Wraps the input value in an object with a 'valid' status"
- `types.handleResult.error` (zod): 'Handles errors during result processing.' -> 'Checks for an error; if none, creates one from issues and caches it; returns the error.'
- `types.ZodObject.keyof` (zod): 'Returns a schema of the keys of an object schema.' -> "Generates an enum from the object's keys"
- `types.handleResult` (zod): 'Handles parsing results, returning success or error.' -> 'Checks validity, computes result, returns success or error if invalid'
- `types.deepPartialify` (zod): 'Recursively makes schema properties optional.' -> 'Converts a schema to a deep partial version by making all fields optional.'
- `click.parser.OptionParser._process_opts` (click): 'Processes options by matching and handling them.' -> 'Processes options, normalizes long options, and handles unmatched options'
- `click._compat._AtomicFile.name` (click): 'Returns name of the atomic file.' -> 'Returns the real filename of the atomic file.'
- `click.parser.OptionParser._match_long_opt` (click): 'Matches long options, processes them, and handles errors.' -> 'Matches long option, checks for value, adjusts state, processes option.'
- `click.core.Parameter.value_is_missing` (click): 'Determines if a parameter value is missing.' -> "Determines if a parameter's value is None or empty based on its nargs and multiple attributes"
- `click.types.FuncParamType.to_info_dict` (click): 'Returns dictionary of parameter information.' -> 'Converts parameter info to a dictionary, including the function'
- `click.types.UUIDParameterType.convert` (click): 'Converts a value to a UUID, handling string representations.' -> 'Converts input to UUID, ensuring valid format or raising error'
- `middleware.csrf.isSecFetchSite` (hono): 'Validates the Sec-Fetch-Site header value.' -> 'It checks if the input is a valid Sec-Fetch-Site value by seeing if it exists in a predefined list.'
- `jsx.dom.intrinsic-element.components.formActionableElement` (hono): 'Wraps form elements with action handling and ref composition.' -> 'Wraps form elements to trigger custom form actions on click'
- `middleware.cors.cors` (hono): 'Sets CORS headers based on provided options.' -> 'Configures and sends CORS headers for cross-origin requests'
- `client.client.ClientRequestImpl.constructor` (hono): 'Initializes a new client request with a URL and method.' -> 'Initializes a request with a URL and HTTP method'
- `jsx.base.JSXFunctionNode.toStringToBuffer` (hono): 'Converts function node to buffer, managing context and callbacks.' -> 'Converts JSX function result to buffer, handling promises and HTML escaping'
- `jsx.dom.client.createRoot.render` (hono): 'Renders a JSX node to the DOM.' -> 'Updates or initializes a JSX node in a mounted root, throwing if unmounted.'

