# Prism v1.1+ Empirical Benchmark Results

27 (engine, task, budget) cells evaluated.

## Task Success Rate (TSR)

Bootstrap 95% CI, `n_resamples=10000`.

+----------------------------+--------+-------+----------------+---+---------------+
| Engine                     | Budget | TSR   | 95% CI         | N | Seed Variance |
+----------------------------+--------+-------+----------------+---+---------------+
| baseline_bfs_bidirectional | 2000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| baseline_bfs_bidirectional | 4000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| baseline_bfs_bidirectional | 8000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| oracle                     | 2000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| oracle                     | 4000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| oracle                     | 8000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| prism_v11                  | 2000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| prism_v11                  | 4000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
| prism_v11                  | 8000   | 0.667 | [0.000, 1.000] | 3 | 0.0000        |
+----------------------------+--------+-------+----------------+---+---------------+

## Diagnostic Metrics (mean per engine)

+----------------------------+----------------+------------+---------+------------+-------+
| Engine                     | cpi_fractional | cpi_strict | fcc     | fpr_oracle | src   |
+----------------------------+----------------+------------+---------+------------+-------+
| baseline_bfs_bidirectional | 1.000          | 1.000      | —       | 0.056      | 0.056 |
| oracle                     | 1.000          | 1.000      | 171.748 | 0.000      | 0.333 |
| prism_v11                  | 1.000          | 1.000      | 167.501 | 0.389      | 0.333 |
+----------------------------+----------------+------------+---------+------------+-------+

### Notes on Diagnostic Metrics

- **FPR redefined (divergence from Oracle).** `fpr` used to mean `|S_M \ G*| / |S_M|` against the human-annotated ground truth `G*` alone - typically only 3-4 symbols per task, so *any* other real context an engine pulled in (imports, helpers, callers) counted as a false positive, regardless of whether it was actually relevant. Two metrics are now reported: `fpr_gt` (the original definition, kept in the raw JSON for transparency, excluded from this table) and **`fpr_oracle`** (shown above) - `|S_M \ S_Oracle| / |S_M|`, divergence from the Oracle engine's own package for the same (task, budget). `fpr_oracle` is `—` wherever no Oracle run was configured/available for that cell.
- **FCC (`—` = n/a, not zero).** FCC is an internal packing-density diagnostic defined over Prism's four-axis coordinate space. It is structurally inapplicable to topological and lexical baselines that do not operate over that space - their rows show `—`, never a fabricated `0.000`.

## Raw LLM Responses

Truncated to 200 characters for readability - the full, untruncated text for every call is in `eval_results_v11.json`'s own `raw_responses` field per record.

+-----------------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Task                                                | Engine                     | Budget | Seed Index | TSR Score | Response (truncated)                                                                                                                                                                                      |
+-----------------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| fastapi_t02_018_application_setup_docs_registration | oracle                     | 2000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods inherited from its base class. It checks if certain URLs are set and, if so, adds routes for … |
| fastapi_t02_018_application_setup_docs_registration | oracle                     | 4000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods inherited from its base class. It checks for the presence of `openapi_url`, `docs_url`, and `… |
| fastapi_t02_018_application_setup_docs_registration | oracle                     | 8000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods inherited from its base class. It checks if certain URLs are set and, if so, adds routes for … |
| fastapi_t02_018_application_setup_docs_registration | prism_v11                  | 2000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods inherited from its base class. It checks for the presence of URLs and conditionally adds rout… |
| fastapi_t02_018_application_setup_docs_registration | prism_v11                  | 4000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods from its base class. It checks for the presence of URLs and then delegates to methods like `a… |
| fastapi_t02_018_application_setup_docs_registration | prism_v11                  | 8000   | 0          | 0.0       | { "reasoning": "FastAPI.setup conditionally registers documentation routes by delegating to a single routing method inherited from its base class. It checks if the openapi_url is set and then conditio… |
| fastapi_t02_018_application_setup_docs_registration | baseline_bfs_bidirectional | 2000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods inherited from its base class. It checks if certain URLs are set and, if so, registers routes… |
| fastapi_t02_018_application_setup_docs_registration | baseline_bfs_bidirectional | 4000   | 0          | 0.0       | { "reasoning": "The `FastAPI.setup` method conditionally registers documentation routes by calling methods inherited from its base class. It checks if certain URLs are set and, if so, registers routes… |
| fastapi_t02_018_application_setup_docs_registration | baseline_bfs_bidirectional | 8000   | 0          | 0.0       | { "reasoning": "FastAPI.setup conditionally registers documentation routes by calling methods inherited from its base class. It checks if the corresponding URL is set and then calls the appropriate me… |
| fastapi_t02_019_orjson_response_render              | oracle                     | 2000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method checks if the `orjson` library is installed and then uses its `dumps` function to serialize the response content. This serialization is done directly… |
| fastapi_t02_019_orjson_response_render              | oracle                     | 4000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `orjson.dumps` function from the `orjson` third-party library. This m… |
| fastapi_t02_019_orjson_response_render              | oracle                     | 8000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method checks if the `orjson` library is installed and then uses its `dumps` function to serialize the response content. This serialization is done directly… |
| fastapi_t02_019_orjson_response_render              | prism_v11                  | 2000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method serializes the response content by calling the `orjson.dumps` function from the `orjson` library. This method is invoked directly without any further… |
| fastapi_t02_019_orjson_response_render              | prism_v11                  | 4000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method serializes the response content by calling the `orjson.dumps` function from the `orjson` library. This method is invoked directly without any further… |
| fastapi_t02_019_orjson_response_render              | prism_v11                  | 8000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method serializes the response content by calling the `orjson.dumps` function from the `orjson` library. This method is invoked directly without any further… |
| fastapi_t02_019_orjson_response_render              | baseline_bfs_bidirectional | 2000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `orjson.dumps` function from the `orjson` third-party library. This m… |
| fastapi_t02_019_orjson_response_render              | baseline_bfs_bidirectional | 4000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `orjson.dumps` function from the `orjson` third-party library. This m… |
| fastapi_t02_019_orjson_response_render              | baseline_bfs_bidirectional | 8000   | 0          | 1.0       | { "reasoning": "The `ORJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `orjson.dumps` function from the `orjson` third-party library. This m… |
| fastapi_t02_020_ujson_response_render               | oracle                     | 2000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This function convert… |
| fastapi_t02_020_ujson_response_render               | oracle                     | 4000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This function convert… |
| fastapi_t02_020_ujson_response_render               | oracle                     | 8000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This function convert… |
| fastapi_t02_020_ujson_response_render               | prism_v11                  | 2000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This method ensures that the content is encoded in UTF… |
| fastapi_t02_020_ujson_response_render               | prism_v11                  | 4000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This method ensures that the content is encoded in UTF… |
| fastapi_t02_020_ujson_response_render               | prism_v11                  | 8000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This method ensures that the content is encoded in UTF… |
| fastapi_t02_020_ujson_response_render               | baseline_bfs_bidirectional | 2000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This function convert… |
| fastapi_t02_020_ujson_response_render               | baseline_bfs_bidirectional | 4000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This function convert… |
| fastapi_t02_020_ujson_response_render               | baseline_bfs_bidirectional | 8000   | 0          | 1.0       | { "reasoning": "The `UJSONResponse.render` method in FastAPI's `responses.py` file serializes the response content by calling the `ujson.dumps` function from the `ujson` library. This function convert… |
+-----------------------------------------------------+----------------------------+--------+------------+-----------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

