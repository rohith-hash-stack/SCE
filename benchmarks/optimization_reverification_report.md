# Prism-Augmented LLM vs. Direct LLM: Comparative Benchmark Report

Model: `gpt-4o-mini` | Temperature: `0.0` | Tasks: 11 | Repositories: gin-gonic/gin, psf/black, pallets/click

> **Methodology note**: hallucination detection cross-checks every backtick-quoted, dotted identifier the model mentions against the repository's real symbol table. This is not a meaningful signal for Category 3 (codegen) tasks, whose prompts correctly ask the model to invent a new symbol name that doesn't exist yet - the headline Hallucination Rate above excludes Category 3 for this reason (its own numbers are still shown, unexcluded, in the Categorical Breakdown and Full Task Results tables below, so nothing is hidden). Pass@1 is a syntax-validity proxy (`ast.parse` for Python, brace/paren balance for Go) checked against the model's own generated code, not a full native-test-suite run (`go test`/`pytest`) against the actual repository, which requires splicing the generated snippet into the right file and resolving that task's own fixtures per-task - out of scope for this run.

## Summary Scorecard

| Metric | Baseline (Direct LLM) | Prism-Augmented | Delta |
|---|---|---|---|
| Total Context Tokens (sum, 11 tasks) | 44,604 | 13,817 | 69.0% reduction |
| Mean Context Tokens / Task | 4,055 | 1,256 | - |
| Blast Radius Context Recall (Cat. 1, 1 tasks) | N/A | N/A | N/A |
| Hallucination Rate (mean, excl. Cat. 3 - see note) | 3.7% | 0.0% | 3.7% reduction |
| Syntactic Conformance (mean, 1-5) | 4.1818 | 4.4545 | - |
| Pass@1 (syntax-valid proxy, Cat. 3, 2 tasks) | 100.0% | 100.0% | - |

## Categorical Breakdown

| Category | Tasks | Baseline Tokens (mean) | Prism Tokens (mean) | Token Reduction | Baseline Halluc. | Prism Halluc. |
|---|---|---|---|---|---|---|
| 1. Deep Transitive Refactoring & Blast Radius Analysis | 1 | 4,118 | 386 | 90.6% | 33.3% | 0.0% |
| 2. Cross-Layer Bug Localization & Silent Error Diagnosis | 7 | 4,051 | 1,364 | 66.3% | 0.0% | 0.0% |
| 3. Idiomatic Code Generation & Structural Slot-Filling | 2 | 3,976 | 1,700 | 57.2% | 25.0% | 0.0% |
| 4. Polysemic Disambiguation & Contextual Role Resolution | 1 | 4,178 | 484 | 88.4% | 0.0% | 0.0% |

## Per-Repository Breakdown

| Repository | Tasks | Baseline Tokens (mean) | Prism Tokens (mean) | Token Reduction |
|---|---|---|---|---|
| https://github.com/gin-gonic/gin | 5 | 3,963 | 827 | 79.1% |
| https://github.com/psf/black | 4 | 4,182 | 1,487 | 64.4% |
| https://github.com/pallets/click | 2 | 4,031 | 1,866 | 53.7% |

## Full Task Results

| Task | Category | Repo | Target Symbol | Base Tokens | Prism Tokens | Base Halluc. | Prism Halluc. | Blast Recall (B/P) |
|---|---|---|---|---|---|---|---|---|
| C1-03 | blast_radius | gin | `routergroup.Use` | 4,118 | 386 | 33.3% | 0.0% | - |
| C2-01 | bug_localization | gin | `debug.debugPrint` | 4,046 | 629 | 0.0% | 0.0% | - |
| C2-02 | bug_localization | gin | `binding.form_mapping.trySplit` | 3,692 | 1,099 | 0.0% | 0.0% | - |
| C2-03 | bug_localization | gin | `context.MustBindWith` | 4,007 | 555 | 0.0% | 0.0% | - |
| C2-04 | bug_localization | black | `src.black.format_file_in_place` | 4,274 | 1,737 | 0.0% | 0.0% | - |
| C2-05 | bug_localization | black | `src.black.concurrency.reformat_many` | 4,274 | 1,793 | 0.0% | 0.0% | - |
| C2-06 | bug_localization | click | `src.click.core.Command.main` | 4,021 | 1,944 | 0.0% | 0.0% | - |
| C2-07 | bug_localization | click | `src.click._termui_impl.Editor.edit_files` | 4,041 | 1,789 | 0.0% | 0.0% | - |
| C3-01 | codegen | gin | `recovery.CustomRecovery` | 3,950 | 1,466 | 50.0% | 0.0% | - |
| C3-05 | codegen | black | `src.black.trans.StringParenWrapper.do_splitter_match` | 4,003 | 1,935 | 0.0% | 0.0% | - |
| C4-03 | polysemy | black | `src.black.cache.Cache.hash_digest` | 4,178 | 484 | 0.0% | 0.0% | - |

## Side-by-Side: Representative Queries

### 1. Deep Transitive Refactoring & Blast Radius Analysis: `C1-03` (routergroup.Use)

- Baseline context: **4,118 tokens** (2 whole file(s): middleware_test.go, gin_test.go)
- Prism context: **386 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

To change `Engine.Use` so that middleware added after `Run()` returns an error instead of panicking, we need to identify all the internal call sites and registration paths that currently call `Use`. 

From the provided context, the following functions in `middleware_test.go` utilize `router.Use`:

1. **TestMiddlewareGeneralCase**
   - Calls `router.Use` twice to register middleware.

2. **TestMiddlewareNoRoute**
   - Calls `router.Use` twice to register middleware.

3. **TestMiddlewareNoMethodEnabled**
   - Calls `router.Use` twice to register middleware.

4. **TestMiddlewareNoMethodDisabled**
   - Calls `router.Use` twice to register middleware.

5. **TestMiddlewareAbort**
   - Calls `router.Use` twice to register middleware.

6. **TestMiddlewareAbortHandlersChainAndNext**
   - Calls `router.Use` once to register middleware.

7. **TestMiddlewareFailHandlersChain**
   - Calls `router.Use` twice to register middleware.

8. **TestMiddlewareWrite**
   - Calls `router.Use` three times to register middleware.

In summary, the following test functions would be affected by the change to `Engine.Use`:

- `TestMiddlewareGeneralCase`
- `TestMiddlewareNoRoute`
- `TestMiddlewareNoMethodEnabled`
- `TestMiddlewareNoMethodDisabled`
- `TestMiddlewareAbort`
- `TestMiddlewareAbortHandlersChainAndNext`
- `TestMiddlewareFailHandlersChain`
- `TestMiddlewareWrite`

These functions all register middleware using `router.Use`, and if `Use` is modified to return an error after `Run()` has been called, they will need to handle that error appropriately instead of allowing a panic to occur.

</details>

<details><summary>Prism-Augmented response</summary>

To identify the internal call sites and registration paths that currently call `routergroup.Use` and would be affected by the behavioral change of returning an error instead of panicking when middleware is added after `Run()` has been called, we need to consider the following:

1. **Direct Calls to `routergroup.Use`:** Any function that directly invokes `routergroup.Use` will be affected. This includes any middleware registration that occurs after the `Run()` method is executed.

2. **Middleware Registration Paths:** We need to look for any instances where middleware is registered in the application lifecycle, particularly after the `Run()` method is called on the `Engine` or any related structures.

3. **Error Handling:** The new behavior will require that these calls check for an error return value instead of assuming success, which may necessitate changes in the calling code to handle the error appropriately.

### Affected Call Sites
- **Middleware Registration:** Any function that registers middleware after the `Run()` method will be affected. This includes:
  - Any custom router groups that call `Use` after the main application has started.
  - Any tests or examples that might be invoking `Use` after `Run()`.

### Summary
To summarize, the following aspects will be affected:
- All instances of `routergroup.Use` that are called after the `Run()` method.
- Any custom logic that assumes middleware can be added at any point without error handling.

You will need to audit the codebase for these specific usages of `routergroup.Use` to ensure that they are updated to handle the new error return behavior.

</details>

### 2. Cross-Layer Bug Localization & Silent Error Diagnosis: `C2-03` (context.MustBindWith)

- Baseline context: **4,007 tokens** (1 whole file(s): context.go)
- Prism context: **555 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

To trace the issue with `MustBindWith` and the `BindXxx` wrapper methods, we need to look at how these methods handle errors when binding request data to structs.

1. **`MustBindWith`**: This function is typically used to bind the request body to a struct and will panic if the binding fails. If it does not panic, it means the binding was successful, even if the input was malformed. This could lead to a zero-valued struct being returned without an error being raised.

2. **`BindXxx` Methods**: These methods (like `BindJSON`, `BindQuery`, etc.) are responsible for binding the request data to the struct. They usually return an error if the binding fails. If the error is not handled properly (e.g., not checking the error before proceeding), it could lead to the handler continuing with a zero-valued struct.

To ensure that errors are not swallowed, you should check the error returned by these binding methods. If an error occurs, you should call `c.AbortWithStatus(400)` to return a 400 Bad Request response.

Here’s an example of how you might implement error handling in a handler using `MustBindWith`:

```go
func MyHandler(c *gin.Context) {
    var myStruct MyStruct
    if err := c.ShouldBindJSON(&myStruct); err != nil {
        c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": err.Error()})
        return
    }
    // Proceed with using myStruct
}
```

In this example, if the binding fails, the handler will return a 400 status with the error message, preventing the use of a zero-valued struct. Make sure to apply similar error handling in all relevant handlers that use `MustBindWith` or the `BindXxx` methods.

</details>

<details><summary>Prism-Augmented response</summary>

To trace the potential issue with the `MustBindWith` function and its related `BindXxx` wrapper methods, we can analyze the provided code snippet and the context around it.

The `MustBindWith` function attempts to bind a request body to a given object using the specified binding method. If an error occurs during this binding process, it checks the type of the error. Specifically, it handles two cases:

1. If the error is of type `*http.MaxBytesError`, it responds with a `413 Request Entity Too Large` status.
2. For all other errors, it responds with a `400 Bad Request` status.

The critical part of the function is the call to `c.ShouldBindWith(obj, b)`, which is where the binding occurs. If this function returns an error, it is handled in the `MustBindWith` function. 

The issue of a zero-valued struct being returned instead of a `400 Bad Request` could arise if:

- The `ShouldBindWith` method does not return an error even when the request body is malformed. This could happen if the binding logic in `ShouldBindWith` is not correctly validating the input.
- The error handling in `MustBindWith` is not being triggered due to the nature of the error returned by `ShouldBindWith`.

To further investigate, you should look into the implementation of `ShouldBindWith` and any `BindXxx` methods that it may call. Ensure that these methods are correctly validating the input and returning appropriate errors when the input is malformed.

In summary, the potential error could be getting swallowed in the `ShouldBindWith` method, which may not be returning an error for malformed requests, leading to a zero-valued struct being processed.

</details>

### 3. Idiomatic Code Generation & Structural Slot-Filling: `C3-01` (recovery.CustomRecovery)

- Baseline context: **3,950 tokens** (2 whole file(s): recovery.go, recovery_test.go)
- Prism context: **1,466 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

To implement the `Timeout(d time.Duration) HandlerFunc` middleware that aborts a request exceeding the specified duration, you can follow the existing middleware idiom as seen in `recovery.go`. Below is the implementation of the `Timeout` middleware:

```go
package gin

import (
	"net/http"
	"time"
)

// Timeout returns a middleware that aborts a request exceeding the specified duration.
func Timeout(d time.Duration) HandlerFunc {
	return func(c *Context) {
		// Create a channel to signal when the request is done
		done := make(chan struct{})

		// Start a goroutine to handle the request
		go func() {
			c.Next() // Call the next handler
			close(done) // Signal that the request is done
		}()

		// Wait for either the request to finish or the timeout
		select {
		case <-done:
			// Request completed within the timeout
		case <-time.After(d):
			// Timeout occurred
			c.AbortWithStatus(http.StatusGatewayTimeout) // Abort with 504 status
		}
	}
}
```

### Explanation:
- The `Timeout` function takes a `time.Duration` parameter `d` and returns a `HandlerFunc`.
- Inside the middleware, a goroutine is started to call `c.Next()`, which processes the next handler in the chain.
- A `select` statement is used to wait for either the completion of the request (signaled by closing the `done` channel) or the timeout (using `time.After(d)`).
- If the timeout occurs, the request is aborted with a `504 Gateway Timeout` status. If the request completes in time, it simply continues without any interruption.

</details>

<details><summary>Prism-Augmented response</summary>

To add a new middleware `Timeout(d time.Duration) HandlerFunc` that aborts a request exceeding the specified duration, you can follow the idiomatic structure used in the existing middleware like `CustomRecovery`. Below is the implementation:

```go
package recovery

import (
	"net/http"
	"time"
)

// Timeout middleware that aborts a request exceeding the specified duration.
func Timeout(d time.Duration) HandlerFunc {
	return func(c *Context) {
		// Create a channel to signal when the request is done
		done := make(chan struct{})

		// Start a goroutine to handle the request
		go func() {
			c.Next() // Call the next handler
			close(done) // Signal that the request is done
		}()

		// Wait for either the request to finish or the timeout
		select {
		case <-done:
			// Request completed within the timeout
		case <-time.After(d):
			// Timeout occurred, abort the request
			c.AbortWithStatus(http.StatusGatewayTimeout)
		}
	}
}
```

This middleware uses a goroutine to handle the request and a `select` statement to wait for either the completion of the request or the timeout. If the timeout occurs, it aborts the request with a `http.StatusGatewayTimeout` status.

</details>

### 4. Polysemic Disambiguation & Contextual Role Resolution: `C4-03` (src.black.cache.Cache.hash_digest)

- Baseline context: **4,178 tokens** (2 whole file(s): src/black/cache.py, tests/test_black.py)
- Prism context: **484 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

In the `Cache` class, the `hash_digest` method is called in two distinct contexts, each serving a different role in the cache-invalidation flow.

1. **Bound to `hash` in `get_file_data` method**:
   - In the `get_file_data` method, the `hash` variable is assigned the result of `Cache.hash_digest(path)`. This call computes the hash of the file's contents at the specified `path`. The purpose of this call is to generate a unique identifier (the hash) for the file based on its current content. This hash is then stored in a `FileData` instance, which encapsulates the file's metadata (modification time, size, and hash). This information is crucial for caching because it allows the system to determine if the file has changed since it was last cached.

   ```python
   hash = Cache.hash_digest(path)
   ```

2. **Bound to `new_hash` in `is_changed` method**:
   - In the `is_changed` method, the `new_hash` variable is assigned the result of `Cache.hash_digest(res_src)`. This call is made after checking the file's size and modification time against the cached version. The purpose of this call is to compute the hash of the file again to compare it with the previously stored hash (from the cache). If the new hash differs from the cached hash, it indicates that the file's content has changed, which triggers a cache invalidation. This is a critical step in ensuring that the cache remains accurate and up-to-date.

   ```python
   new_hash = Cache.hash_digest(res_src)
   ```

In summary, the first call (to `hash`) is about generating and storing the hash when initially caching the file, while the second call (to `new_hash`) is about validating whether the cached version is still valid by comparing the newly computed hash against the cached hash.

</details>

<details><summary>Prism-Augmented response</summary>

In the cache-invalidation flow, `Cache.hash_digest` is called from two distinct places: `src.black.cache.Cache.get_file_data` and `src.black.cache.Cache.is_changed`. Each call site serves a different purpose in the context of cache management.

1. **`src.black.cache.Cache.get_file_data`**:
   - This call site likely uses `hash_digest` to compute the hash of the file's contents when retrieving data. The purpose here is to generate a unique identifier (the hash) for the file based on its current state. This hash can then be used to determine if the cached data is still valid or if it needs to be updated. Essentially, it helps in fetching the correct data from the cache or deciding to read the file anew if the hash indicates a change.

2. **`src.black.cache.Cache.is_changed`**:
   - In contrast, this call site likely uses `hash_digest` to check if the file has changed since it was last cached. By comparing the newly computed hash with the previously stored hash, this function can determine whether the cached data is still valid or if it should be invalidated. This is crucial for maintaining the integrity of the cache, ensuring that stale data is not served to the user.

In summary, `hash_digest` is used in `get_file_data` to generate a hash for data retrieval, while in `is_changed`, it is used to verify if the cached data is still valid by comparing hashes.

</details>
