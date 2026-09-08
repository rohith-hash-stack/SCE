# Prism-Augmented LLM vs. Direct LLM: Comparative Benchmark Report

Model: `gpt-4o-mini` | Temperature: `0.0` | Tasks: 33 | Repositories: gin-gonic/gin, psf/black, pallets/click

> **Methodology note**: hallucination detection cross-checks every backtick-quoted, dotted identifier the model mentions against the repository's real symbol table. This is not a meaningful signal for Category 3 (codegen) tasks, whose prompts correctly ask the model to invent a new symbol name that doesn't exist yet - the headline Hallucination Rate above excludes Category 3 for this reason (its own numbers are still shown, unexcluded, in the Categorical Breakdown and Full Task Results tables below, so nothing is hidden). Pass@1 is a syntax-validity proxy (`ast.parse` for Python, brace/paren balance for Go) checked against the model's own generated code, not a full native-test-suite run (`go test`/`pytest`) against the actual repository, which requires splicing the generated snippet into the right file and resolving that task's own fixtures per-task - out of scope for this run.

## Summary Scorecard

| Metric | Baseline (Direct LLM) | Prism-Augmented | Delta |
|---|---|---|---|
| Total Context Tokens (sum, 33 tasks) | 134,031 | 76,144 | 43.2% reduction |
| Mean Context Tokens / Task | 4,062 | 2,307 | - |
| Blast Radius Context Recall (Cat. 1, 7 tasks) | 54.1% | 100.0% | 45.9% |
| Hallucination Rate (mean, excl. Cat. 3 - see note) | 9.9% | 7.0% | 2.9% reduction |
| Syntactic Conformance (mean, 1-5) | 3.8788 | 3.9394 | - |
| Pass@1 (syntax-valid proxy, Cat. 3, 7 tasks) | 85.7% | 100.0% | - |

## Categorical Breakdown

| Category | Tasks | Baseline Tokens (mean) | Prism Tokens (mean) | Token Reduction | Baseline Halluc. | Prism Halluc. |
|---|---|---|---|---|---|---|
| 1. Deep Transitive Refactoring & Blast Radius Analysis | 7 | 4,003 | 1,251 | 68.8% | 4.8% | 0.0% |
| 2. Cross-Layer Bug Localization & Silent Error Diagnosis | 7 | 4,051 | 2,890 | 28.6% | 25.0% | 21.4% |
| 3. Idiomatic Code Generation & Structural Slot-Filling | 7 | 4,070 | 3,058 | 24.9% | 57.1% | 14.3% |
| 4. Polysemic Disambiguation & Contextual Role Resolution | 6 | 4,165 | 2,100 | 49.6% | 0.0% | 0.0% |
| 5. Complex Concurrency, Transactional & State Invariant Audits | 6 | 4,029 | 2,191 | 45.6% | 8.3% | 5.5% |

## Per-Repository Breakdown

| Repository | Tasks | Baseline Tokens (mean) | Prism Tokens (mean) | Token Reduction |
|---|---|---|---|---|
| https://github.com/gin-gonic/gin | 11 | 4,013 | 1,595 | 60.2% |
| https://github.com/psf/black | 11 | 4,116 | 2,589 | 37.1% |
| https://github.com/pallets/click | 11 | 4,056 | 2,739 | 32.5% |

## Full Task Results

| Task | Category | Repo | Target Symbol | Base Tokens | Prism Tokens | Base Halluc. | Prism Halluc. | Blast Recall (B/P) |
|---|---|---|---|---|---|---|---|---|
| C1-01 | blast_radius | gin | `binding.form_mapping.setWithProperType` | 3,692 | 2,989 | 0.0% | 0.0% | 100.0% / 100.0% |
| C1-02 | blast_radius | gin | `context.getTyped` | 4,007 | 501 | 0.0% | 0.0% | 100.0% / 100.0% |
| C1-03 | blast_radius | gin | `routergroup.Use` | 4,118 | 351 | 33.3% | 0.0% | - |
| C1-04 | blast_radius | black | `src.black.trans.is_valid_index_factory` | 4,003 | 632 | 0.0% | 0.0% | 42.9% / 100.0% |
| C1-05 | blast_radius | black | `tests.util.get_case_path` | 4,037 | 655 | 0.0% | 0.0% | 42.9% / 100.0% |
| C1-06 | blast_radius | click | `src.click.utils.echo` | 4,154 | 3,196 | 0.0% | 0.0% | 10.5% / 100.0% |
| C1-07 | blast_radius | click | `src.click._compat.isatty` | 4,012 | 434 | 0.0% | 0.0% | 28.6% / 100.0% |
| C2-01 | bug_localization | gin | `debug.debugPrint` | 4,046 | 597 | 100.0% | 0.0% | - |
| C2-02 | bug_localization | gin | `binding.form_mapping.trySplit` | 3,692 | 2,753 | 0.0% | 100.0% | - |
| C2-03 | bug_localization | gin | `context.MustBindWith` | 4,007 | 520 | 0.0% | 0.0% | - |
| C2-04 | bug_localization | black | `src.black.format_file_in_place` | 4,274 | 4,158 | 0.0% | 0.0% | - |
| C2-05 | bug_localization | black | `src.black.concurrency.reformat_many` | 4,274 | 4,067 | 0.0% | 0.0% | - |
| C2-06 | bug_localization | click | `src.click.core.Command.main` | 4,021 | 3,917 | 0.0% | 0.0% | - |
| C2-07 | bug_localization | click | `src.click._termui_impl.Editor.edit_files` | 4,041 | 4,220 | 75.0% | 50.0% | - |
| C3-01 | codegen | gin | `recovery.CustomRecovery` | 3,950 | 3,022 | 100.0% | 100.0% | - |
| C3-02 | codegen | gin | `context.GetInt64` | 4,007 | 337 | 100.0% | 0.0% | - |
| C3-03 | codegen | black | `src.black.linegen.LineGenerator.visit_default` | 4,067 | 4,054 | 100.0% | 0.0% | - |
| C3-04 | codegen | black | `tests.test_black.BlackTestCase.invokeBlack` | 4,257 | 598 | 100.0% | 0.0% | - |
| C3-05 | codegen | black | `src.black.trans.StringParenWrapper.do_splitter_match` | 4,003 | 4,875 | 0.0% | 0.0% | - |
| C3-06 | codegen | click | `src.click.types._NumberParamTypeBase.convert` | 4,049 | 4,334 | 0.0% | 0.0% | - |
| C3-07 | codegen | click | `src.click.testing.CliRunner.invoke` | 4,154 | 4,187 | 0.0% | 0.0% | - |
| C4-01 | polysemy | gin | `binding.form_mapping.mapForm` | 4,571 | 3,151 | 0.0% | 0.0% | - |
| C4-02 | polysemy | black | `src.black.linegen._first_right_hand_split` | 4,067 | 3,724 | 0.0% | 0.0% | - |
| C4-03 | polysemy | black | `src.black.cache.Cache.hash_digest` | 4,178 | 447 | 0.0% | 0.0% | - |
| C4-04 | polysemy | click | `src.click.types.convert_type` | 4,001 | 4,247 | 0.0% | 0.0% | - |
| C4-05 | polysemy | click | `src.click.core.Group.get_command` | 4,021 | 432 | 0.0% | 0.0% | - |
| C4-06 | polysemy | click | `src.click.parser._normalize_opt` | 4,154 | 598 | 0.0% | 0.0% | - |
| C5-01 | invariant_audit | gin | `context.Set` | 4,030 | 373 | 50.0% | 33.3% | - |
| C5-02 | invariant_audit | gin | `binding.form_mapping.setByForm` | 4,018 | 2,951 | 0.0% | 0.0% | - |
| C5-03 | invariant_audit | black | `src.black.linegen.LineGenerator.__post_init__` | 4,048 | 884 | 0.0% | 0.0% | - |
| C5-04 | invariant_audit | black | `src.black.lines.Line.append` | 4,067 | 4,380 | 0.0% | 0.0% | - |
| C5-05 | invariant_audit | click | `src.click._termui_impl.ProgressBar.update` | 4,011 | 4,205 | 0.0% | 0.0% | - |
| C5-06 | invariant_audit | click | `examples.repo.repo.Repo.__init__` | 4,000 | 355 | 0.0% | 0.0% | - |

## Side-by-Side: Representative Queries

### 1. Deep Transitive Refactoring & Blast Radius Analysis: `C1-03` (routergroup.Use)

- Baseline context: **4,118 tokens** (2 whole file(s): middleware_test.go, gin_test.go)
- Prism context: **351 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

To change `Engine.Use` so that middleware added after `Run()` returns an error instead of panicking, we need to identify all the internal call sites and registration paths that currently call `Use`. 

From the provided context, the following functions in the `middleware_test.go` file utilize `Use`:

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

In summary, the following test functions will be affected by the change to `Engine.Use`:

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

To identify the internal call sites and registration paths that currently call `routergroup.Use` and would be affected by the behavioral change to return an error instead of panicking when middleware is added after `Run()` has been called, we need to look for all instances where `routergroup.Use` is invoked in the codebase.

1. **Direct Calls to `routergroup.Use`:** 
   - Any direct invocation of `Use` on a `RouterGroup` instance would be affected. This includes any middleware registration that occurs after the `Run()` method is called on the `Engine`.

2. **Indirect Calls via Other Functions:**
   - If there are any wrapper functions or methods that internally call `routergroup.Use`, those would also need to be identified. This could include functions that are designed to set up middleware for specific routes or groups.

3. **Middleware Registration Patterns:**
   - Look for patterns in the codebase where middleware is typically registered. This could include initialization functions or setup functions that are called during application startup.

4. **Testing and Validation:**
   - Any tests that currently expect the middleware registration to succeed after `Run()` would need to be updated to handle the new error return behavior.

To summarize, you should search the codebase for:
- All instances of `group.Use(...)` where `group` is a `RouterGroup` instance.
- Any functions that wrap or abstract the use of `routergroup.Use`.
- Middleware registration logic that occurs after the `Run()` method is invoked.

This will help you identify all affected call sites and registration paths that need to be modified to handle the new error return behavior instead of panicking.

</details>

### 2. Cross-Layer Bug Localization & Silent Error Diagnosis: `C2-03` (context.MustBindWith)

- Baseline context: **4,007 tokens** (1 whole file(s): context.go)
- Prism context: **520 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

To trace the issue with `MustBindWith` and the `BindXxx` wrapper methods, we need to look at how these methods handle errors when binding request data to structs. The problem you're facing, where a zero-valued struct is returned instead of a 400 error for a malformed request body, suggests that the error handling in these methods might not be functioning as expected.

1. **`MustBindWith`**: This function is typically used to bind the request body to a struct and will panic if the binding fails. If it does not panic, it means that the binding was either successful or the error was not handled properly.

2. **`BindXxx` Methods**: These methods (like `BindJSON`, `BindQuery`, etc.) are responsible for binding the request data to the struct. They should return an error if the binding fails. If the error is not checked or handled properly, it could lead to the situation where a zero-valued struct is returned.

To ensure that errors are not swallowed, you should check the implementation of these methods to confirm that they properly handle and propagate errors. Specifically, look for:

- **Error Checking**: Ensure that after calling a binding method, the error is checked. If an error occurs, it should lead to an appropriate response (like a 400 status code).
  
- **Panic Handling**: If `MustBindWith` is used, ensure that it is wrapped in a way that catches panics and converts them into proper HTTP responses.

Here’s a general approach to ensure that errors are handled correctly:

```go
func (c *Context) BindJSON(obj any) error {
	if err := c.ShouldBindJSON(obj); err != nil {
		c.AbortWithError(http.StatusBadRequest, err)
		return err
	}
	return nil
}

func (c *Context) MustBindJSON(obj any) {
	if err := c.BindJSON(obj); err != nil {
		// Handle the error appropriately, e.g., log it or return a specific response
	}
}
```

In this example, `BindJSON` checks for errors and calls `AbortWithError` to return a 400 status code if the binding fails. The `MustBindJSON` method can t

</details>

<details><summary>Prism-Augmented response</summary>

To trace the potential issue with the `MustBindWith` function and its handling of malformed request bodies, we can analyze the provided code.

The `MustBindWith` function calls `c.ShouldBindWith(obj, b)`, which is responsible for binding the request data to the provided object. If `ShouldBindWith` returns an error, `MustBindWith` checks the type of the error. If the error is of type `*http.MaxBytesError`, it responds with a `413 Request Entity Too Large` status. For all other errors, it responds with a `400 Bad Request` status.

However, if `ShouldBindWith` does not return an error, the function simply returns `nil`, indicating that the binding was successful. This means that if `ShouldBindWith` does not properly handle malformed input and does not return an error, `MustBindWith` will proceed without any indication of a problem, potentially leading to a zero-valued struct being used.

To investigate further, you should look into the implementation of `ShouldBindWith` to ensure that it correctly identifies and returns errors for malformed request bodies. If it fails to do so, that would be the source of the issue where a zero-valued struct is being processed instead of returning a `400 Bad Request`.

In summary, the error could be getting swallowed in the `ShouldBindWith` method, which is called by `MustBindWith`. You should check the logic in `ShouldBindWith` to ensure it properly handles and returns errors for malformed requests.

</details>

### 3. Idiomatic Code Generation & Structural Slot-Filling: `C3-02` (context.GetInt64)

- Baseline context: **4,007 tokens** (1 whole file(s): context.go)
- Prism context: **337 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

To add the `Context.GetUint(key any) uint` helper method, you can follow the existing pattern used in the `GetXxx` family of methods in `context.go`. This method will delegate to the `getTyped` function, similar to how other `Get` methods are implemented.

Here is the code to add the `GetUint` method:

```go
// GetUint returns the value associated with the key as an unsigned integer.
func (c *Context) GetUint(key any) uint {
	return getTyped[uint](c, key)
}
```

This method retrieves the value associated with the provided key and attempts to cast it to a `uint`, returning the result. Make sure to place this method within the `Context` struct in `context.go`, following the existing `GetXxx` methods for consistency.

</details>

<details><summary>Prism-Augmented response</summary>

To add the `Context.GetUint(key any) uint` helper method, you can follow the existing pattern used in the `GetInt64` method. This involves creating a new method that delegates to a typed-get helper function, similar to how `GetInt64` does.

Here’s how you can implement the `GetUint` method:

```go
func (c *Context) GetUint(key any) uint {
	return getTyped[uint](c, key)
}
```

</details>

### 4. Polysemic Disambiguation & Contextual Role Resolution: `C4-03` (src.black.cache.Cache.hash_digest)

- Baseline context: **4,178 tokens** (2 whole file(s): src/black/cache.py, tests/test_black.py)
- Prism context: **447 tokens** (knapsack-budgeted AST slice)

<details><summary>Baseline (Direct LLM) response</summary>

In the `Cache` class, the `hash_digest` method is called in two distinct contexts, each serving a different role in the cache-invalidation flow.

1. **Bound to `hash` in `get_file_data` method**:
   - In the `get_file_data` method, `hash` is assigned the value returned by `Cache.hash_digest(path)`. This call computes the hash of the file's contents at the specified `path`. The resulting hash is then stored in a `FileData` instance, which encapsulates the file's metadata (modification time, size, and hash). This is crucial for establishing a baseline representation of the file when it is first added to the cache. The hash serves as a unique identifier for the file's content, allowing the cache to determine if the content has changed in future operations.

   ```python
   hash = Cache.hash_digest(path)
   ```

2. **Bound to `new_hash` in `is_changed` method**:
   - In the `is_changed` method, `new_hash` is assigned the value returned by `Cache.hash_digest(res_src)`. This call is made after checking the file's size and modification time against the cached version. If either of these attributes indicates a change, the method computes a new hash for the current state of the file. The purpose of this call is to verify if the content of the file has changed since it was last cached. If the newly computed hash (`new_hash`) differs from the cached hash (`old.hash`), it indicates that the file's content has changed, prompting the cache to invalidate the entry for that file.

   ```python
   new_hash = Cache.hash_digest(res_src)
   ```

In summary, the first call (to `hash`) establishes the initial state of the file when it is added to the cache, while the second call (to `new_hash`) checks for changes in the file's content during subsequent validations, thus playing a critical role in the cache-invalidation logic.

</details>

<details><summary>Prism-Augmented response</summary>

In the context of the `Cache.hash_digest` method, there are two distinct call sites: one from `src.black.cache.Cache.get_file_data` and another from `src.black.cache.Cache.is_changed`. Each of these call sites plays a unique role in the cache-invalidation flow.

1. **`src.black.cache.Cache.get_file_data`**:
   - This call site likely uses `hash_digest` to compute the hash of a file's contents when retrieving data from the cache. The purpose here is to generate a unique identifier (the hash) for the file based on its current state. If the file's contents change, the hash will also change, indicating that the cached data is no longer valid. This is crucial for ensuring that the cache reflects the most up-to-date information.

2. **`src.black.cache.Cache.is_changed`**:
   - This call site likely uses `hash_digest` to determine if the contents of a file have changed since the last time it was cached. By comparing the newly computed hash with a previously stored hash, the method can ascertain whether the file has been modified. If the hashes differ, it indicates that the cache needs to be invalidated and refreshed with the new data.

In summary, `hash_digest` is used in `get_file_data` to generate a hash for caching purposes, while in `is_changed`, it is used to check for changes in the file's contents to decide if the cache should be invalidated.

</details>

### 5. Complex Concurrency, Transactional & State Invariant Audits: `C5-01` (context.Set)

- Baseline context: **4,030 tokens** (2 whole file(s): context_test.go, render/render_test.go)
- Prism context: **373 tokens** (knapsack-budgeted AST slice)
