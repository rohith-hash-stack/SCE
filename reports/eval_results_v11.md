# Prism v1.1+ Empirical Benchmark Results

36 (engine, task, budget) cells evaluated.

## Task Success Rate (TSR)

Bootstrap 95% CI, `n_resamples=10000`.

+----------------------------+--------+-------+----------------+---+---------------+
| Engine                     | Budget | TSR   | 95% CI         | N | Seed Variance |
+----------------------------+--------+-------+----------------+---+---------------+
| baseline_bfs_bidirectional | 2000   | 0.250 | [0.000, 0.750] | 4 | 0.0000        |
| baseline_bfs_bidirectional | 4000   | 0.250 | [0.000, 0.750] | 4 | 0.0000        |
| baseline_bfs_bidirectional | 8000   | 0.250 | [0.000, 0.750] | 4 | 0.0000        |
| oracle                     | 2000   | 0.250 | [0.000, 0.750] | 4 | 0.0000        |
| oracle                     | 4000   | 0.250 | [0.000, 0.750] | 4 | 0.0000        |
| oracle                     | 8000   | 0.250 | [0.000, 0.750] | 4 | 0.0000        |
| prism_v11                  | 2000   | 0.500 | [0.000, 1.000] | 4 | 0.0000        |
| prism_v11                  | 4000   | 0.500 | [0.000, 1.000] | 4 | 0.0000        |
| prism_v11                  | 8000   | 0.500 | [0.000, 1.000] | 4 | 0.0000        |
+----------------------------+--------+-------+----------------+---+---------------+

## Diagnostic Metrics (mean per engine)

+----------------------------+----------------+------------+---------+------------+-------+
| Engine                     | cpi_fractional | cpi_strict | fcc     | fpr_oracle | src   |
+----------------------------+----------------+------------+---------+------------+-------+
| baseline_bfs_bidirectional | 1.000          | 1.000      | —       | 0.227      | 0.227 |
| oracle                     | 1.000          | 1.000      | 132.487 | 0.000      | 0.333 |
| prism_v11                  | 1.000          | 1.000      | 141.441 | 0.403      | 0.250 |
+----------------------------+----------------+------------+---------+------------+-------+

### Notes on Diagnostic Metrics

- **FPR redefined (divergence from Oracle).** `fpr` used to mean `|S_M \ G*| / |S_M|` against the human-annotated ground truth `G*` alone - typically only 3-4 symbols per task, so *any* other real context an engine pulled in (imports, helpers, callers) counted as a false positive, regardless of whether it was actually relevant. Two metrics are now reported: `fpr_gt` (the original definition, kept in the raw JSON for transparency, excluded from this table) and **`fpr_oracle`** (shown above) - `|S_M \ S_Oracle| / |S_M|`, divergence from the Oracle engine's own package for the same (task, budget). `fpr_oracle` is `—` wherever no Oracle run was configured/available for that cell.
- **FCC (`—` = n/a, not zero).** FCC is an internal packing-density diagnostic defined over Prism's four-axis coordinate space. It is structurally inapplicable to topological and lexical baselines that do not operate over that space - their rows show `—`, never a fabricated `0.000`.

## Raw LLM Responses

Truncated to 200 characters for readability - the full, untruncated text for every call is in `eval_results_v11.json`'s own `raw_responses` field per record.

+-----------------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Task                                                | Engine                     | Budget | Seed Index | TSR Score | Response (truncated)                                                                                                                                                                                      |
+-----------------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| fastapi_t02_008_add_api_websocket_route             | oracle                     | 2000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then instantiates `APIWebSocketRoute… |
| fastapi_t02_008_add_api_websocket_route             | oracle                     | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then instantiates `APIWebSocketRoute… |
| fastapi_t02_008_add_api_websocket_route             | oracle                     | 8000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then instantiates `APIWebSocketRoute… |
| fastapi_t02_008_add_api_websocket_route             | prism_v11                  | 2000   | 0          | 1.0       | { "reasoning": "The pipeline starts at `fastapi.routing.APIRouter.add_api_websocket_route`, which merges the router's own dependencies with any route-specific ones. It then constructs the concrete rou… |
| fastapi_t02_008_add_api_websocket_route             | prism_v11                  | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts at `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then constructs the concrete route obj… |
| fastapi_t02_008_add_api_websocket_route             | prism_v11                  | 8000   | 0          | 1.0       | { "reasoning": "The pipeline starts at `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's own dependencies with any route-specific ones. It then constructs the concrete route… |
| fastapi_t02_008_add_api_websocket_route             | baseline_bfs_bidirectional | 2000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then instantiates `APIWebSocketRoute… |
| fastapi_t02_008_add_api_websocket_route             | baseline_bfs_bidirectional | 4000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then instantiates an `APIWebSocketRo… |
| fastapi_t02_008_add_api_websocket_route             | baseline_bfs_bidirectional | 8000   | 0          | 1.0       | { "reasoning": "The pipeline starts with `add_api_websocket_route` in `fastapi.routing.py`, which merges the router's dependencies with any route-specific ones. It then instantiates `APIWebSocketRoute… |
| fastapi_t02_018_application_setup_docs_registration | oracle                     | 2000   | 0          | 0.0       | { "reasoning": "FastAPI.setup configures its documentation routes by checking for the presence of specific URLs and then adding routes for /openapi.json, /docs, and /redoc using the inherited add_rout… |
| fastapi_t02_018_application_setup_docs_registration | oracle                     | 4000   | 0          | 0.0       | { "reasoning": "FastAPI.setup configures its documentation routes by checking for the presence of specific URLs and then adding routes for /openapi.json, /docs, and /redoc using the inherited add_rout… |
| fastapi_t02_018_application_setup_docs_registration | oracle                     | 8000   | 0          | 0.0       | { "reasoning": "FastAPI.setup configures its documentation routes by checking for the presence of specific URLs and then adding routes for /openapi.json, /docs, and /redoc using the inherited add_rout… |
| fastapi_t02_018_application_setup_docs_registration | prism_v11                  | 2000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method configures the application's documentation routes by checking for the presence of specific URLs and adding routes for `/openapi.json`, `/docs`, and `/redoc` … |
| fastapi_t02_018_application_setup_docs_registration | prism_v11                  | 4000   | 0          | 0.0       | { "reasoning": "FastAPI.setup configures its documentation routes by checking for the presence of specific URLs and then adding routes for /openapi.json, /docs, and /redoc using the inherited add_rout… |
| fastapi_t02_018_application_setup_docs_registration | prism_v11                  | 8000   | 0          | 0.0       | { "reasoning": "FastAPI.setup initializes the application's documentation routes by calling the inherited add_route method to add routes for /openapi.json, /docs, and /redoc.", "symbols": [ "fastapi.a… |
| fastapi_t02_018_application_setup_docs_registration | baseline_bfs_bidirectional | 2000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method configures the FastAPI application by adding routes for OpenAPI documentation, Swagger UI, and ReDoc. It first checks if the `openapi_url` is set and then ad… |
| fastapi_t02_018_application_setup_docs_registration | baseline_bfs_bidirectional | 4000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method configures the FastAPI application by adding routes for OpenAPI documentation, Swagger UI, and ReDoc. It first checks if the `openapi_url` is set and then ad… |
| fastapi_t02_018_application_setup_docs_registration | baseline_bfs_bidirectional | 8000   | 0          | 0.0       | { "reasoning": "FastAPI.setup checks if openapi_url is set and if so, adds routes for /openapi.json, /docs, and /redoc using the inherited add_route method.", "symbols": [ "fastapi.applications.FastAP… |
| fastapi_t02_019_orjson_response_render              | oracle                     | 2000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method checks if the `orjson` library is installed and then uses it to serialize the `content` parameter into a JSON-formatted byte string with specific opt… |
| fastapi_t02_019_orjson_response_render              | oracle                     | 4000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method checks if the `orjson` library is installed and then uses it to serialize the `content` parameter into a JSON-formatted byte string with specific opt… |
| fastapi_t02_019_orjson_response_render              | oracle                     | 8000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method checks if the `orjson` library is installed and then uses it to serialize the `content` parameter into a JSON-formatted byte string with specific opt… |
| fastapi_t02_019_orjson_response_render              | prism_v11                  | 2000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method is a FastAPI class method that serializes the given content to JSON using the `orjson.dumps` function from the `orjson` library. It checks if `orjson… |
| fastapi_t02_019_orjson_response_render              | prism_v11                  | 4000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method is a FastAPI class method that serializes the given content to JSON using the `orjson.dumps` function from the `orjson` library. It ensures that the … |
| fastapi_t02_019_orjson_response_render              | prism_v11                  | 8000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method is a FastAPI class method that serializes the given content to JSON using the `orjson.dumps` function from the `orjson` library. It ensures that the … |
| fastapi_t02_019_orjson_response_render              | baseline_bfs_bidirectional | 2000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `orjson` library. It first checks if `orjson` is installed, then… |
| fastapi_t02_019_orjson_response_render              | baseline_bfs_bidirectional | 4000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `orjson` library. It first checks if `orjson` is installed, then… |
| fastapi_t02_019_orjson_response_render              | baseline_bfs_bidirectional | 8000   | 0          | 0.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `orjson` library. It first checks if `orjson` is installed, then… |
| fastapi_t02_020_ujson_response_render               | oracle                     | 2000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `ujson` library. It first checks if `ujson` is installed, then us… |
| fastapi_t02_020_ujson_response_render               | oracle                     | 4000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `ujson` library. It first checks if `ujson` is installed, then us… |
| fastapi_t02_020_ujson_response_render               | oracle                     | 8000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `ujson` library. It first checks if `ujson` is installed, then us… |
| fastapi_t02_020_ujson_response_render               | prism_v11                  | 2000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for serializing the given content into a JSON-formatted byte string using the `ujson.dumps` function f… |
| fastapi_t02_020_ujson_response_render               | prism_v11                  | 4000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for serializing a Python object into a JSON-formatted byte string using the `ujson` library. It first … |
| fastapi_t02_020_ujson_response_render               | prism_v11                  | 8000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is a self-contained function that serializes the provided `content` using the `ujson.dumps` method from the `ujson` l… |
| fastapi_t02_020_ujson_response_render               | baseline_bfs_bidirectional | 2000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `ujson` library. It first checks if `ujson` is installed, then us… |
| fastapi_t02_020_ujson_response_render               | baseline_bfs_bidirectional | 4000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `ujson` library. It first checks if `ujson` is installed, then us… |
| fastapi_t02_020_ujson_response_render               | baseline_bfs_bidirectional | 8000   | 0          | 0.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file is responsible for rendering JSON content using the `ujson` library. It first checks if `ujson` is installed, then us… |
+-----------------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

