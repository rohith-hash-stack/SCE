"""The 33-query benchmark matrix comparing Direct LLM (Baseline) against
Prism-Augmented LLM across 3 real, cloned production repositories -
`benchmarks/run_comparison_suite.py`'s fixed input.

Every `target_symbol` below is a REAL, verified qualified name discovered
by running Prism's own indexer against the actual cloned repository (see
that script's `--discover` mode) - not a placeholder. Repository
selection and the rationale for each is documented in
`benchmarks/README.md`'s "Comparison suite" section.

Distribution (per the spec): 33 queries total, 11 per repository, 7/7/7/6/6
across the five categories.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------- #
# Repositories under test
# --------------------------------------------------------------------- #
GIN = "gin"        # gin-gonic/gin - Go backend web framework (routing, middleware, binding)
BLACK = "black"    # psf/black - Python algorithmic/systems (AST-to-AST code formatter)
CLICK = "click"    # pallets/click - Python CLI orchestrator (commands, params, testing harness)

REPO_SOURCES = {
    GIN: "https://github.com/gin-gonic/gin",
    BLACK: "https://github.com/psf/black",
    CLICK: "https://github.com/pallets/click",
}

CATEGORY_BLAST_RADIUS = "blast_radius"
CATEGORY_BUG_LOCALIZATION = "bug_localization"
CATEGORY_CODEGEN = "codegen"
CATEGORY_POLYSEMY = "polysemy"
CATEGORY_INVARIANT_AUDIT = "invariant_audit"

CATEGORY_LABELS = {
    CATEGORY_BLAST_RADIUS: "1. Deep Transitive Refactoring & Blast Radius Analysis",
    CATEGORY_BUG_LOCALIZATION: "2. Cross-Layer Bug Localization & Silent Error Diagnosis",
    CATEGORY_CODEGEN: "3. Idiomatic Code Generation & Structural Slot-Filling",
    CATEGORY_POLYSEMY: "4. Polysemic Disambiguation & Contextual Role Resolution",
    CATEGORY_INVARIANT_AUDIT: "5. Complex Concurrency, Transactional & State Invariant Audits",
}


@dataclass(frozen=True)
class ComparisonTask:
    task_id: str
    category: str
    repo: str
    #: Fully qualified Prism symbol name this task centers on - the
    #: argument `prism query` is invoked with for the Treatment arm, and
    #: the node `calls_graph` in-edges are read from for the Blast Radius
    #: Recall ground truth.
    target_symbol: str
    prompt: str
    #: For blast_radius tasks: extra qualified names (beyond target_symbol
    #: itself) worth checking are surfaced too, e.g. a sibling entry point
    #: in the same refactor. Empty for most tasks.
    related_symbols: tuple[str, ...] = field(default_factory=tuple)


TASKS: list[ComparisonTask] = [
    # ------------------------------------------------------------- #
    # Category 1 - Deep Transitive Refactoring & Blast Radius (7)
    # ------------------------------------------------------------- #
    ComparisonTask(
        "C1-01", CATEGORY_BLAST_RADIUS, GIN, "binding.form_mapping.setWithProperType",
        "`setWithProperType` currently returns a bare `error`. Change its signature to return "
        "`(bool, error)` where the bool reports whether a value was actually set. Identify every "
        "caller that needs updating to handle the new return shape, including indirect callers "
        "reached through `tryToSetValue`.",
    ),
    ComparisonTask(
        "C1-02", CATEGORY_BLAST_RADIUS, GIN, "context.getTyped",
        "`getTyped` currently returns `any` and callers do their own type assertion. Refactor it "
        "into a generic `getTyped[T any]` that returns `(T, bool)` directly. Identify every one of "
        "the `GetXxx`-family Context methods that calls it and would need updating.",
    ),
    ComparisonTask(
        "C1-03", CATEGORY_BLAST_RADIUS, GIN, "routergroup.Use",
        "We need to change `Engine.Use` so middleware added after `Run()` has been called returns "
        "an error instead of panicking. Identify every internal call site and registration path "
        "that currently calls `Use` and would be affected by this behavioral change.",
    ),
    ComparisonTask(
        "C1-04", CATEGORY_BLAST_RADIUS, BLACK, "src.black.trans.is_valid_index_factory",
        "`is_valid_index_factory` currently returns a closure. Refactor it to return a small class "
        "instance with a `__call__` method instead (to make it picklable for a future multiprocessing "
        "change). Identify every one of its callers and confirm none rely on closure-specific behavior.",
    ),
    ComparisonTask(
        "C1-05", CATEGORY_BLAST_RADIUS, BLACK, "tests.util.get_case_path",
        "Add a new required `encoding: str` parameter to `get_case_path`. Identify every call site "
        "across the test suite that needs to be updated to pass it.",
    ),
    ComparisonTask(
        "C1-06", CATEGORY_BLAST_RADIUS, CLICK, "src.click.utils.echo",
        "We want to change `echo`'s default `file` argument from `None` (meaning stdout) to always "
        "require an explicit stream. Identify every internal caller across the `click` package whose "
        "output stream would silently change or break.",
    ),
    ComparisonTask(
        "C1-07", CATEGORY_BLAST_RADIUS, CLICK, "src.click._compat.isatty",
        "Change `isatty`'s return type from `bool` to a 3-state `TTYState` enum (`TTY`, `NOT_TTY`, "
        "`UNKNOWN`). Identify every caller that currently treats its result as a plain boolean and "
        "would need updating.",
    ),
    # ------------------------------------------------------------- #
    # Category 2 - Cross-Layer Bug Localization & Silent Error Diagnosis (7)
    # ------------------------------------------------------------- #
    ComparisonTask(
        "C2-01", CATEGORY_BUG_LOCALIZATION, GIN, "debug.debugPrint",
        "Users report that a malformed debug format string silently produces no output instead of "
        "an error, in production builds where debug printing is expected. Trace `debugPrint`'s own "
        "logic and its call sites to identify the true root cause - is it in this function, or in "
        "how a caller constructs its format string?",
    ),
    ComparisonTask(
        "C2-02", CATEGORY_BUG_LOCALIZATION, GIN, "binding.form_mapping.trySplit",
        "A user reports that submitting a form with an array field sometimes silently drops the "
        "last element instead of returning a binding error. Trace `trySplit` and its callers "
        "(`setSlice`/`setArray`) to find where this silent data loss could originate.",
    ),
    ComparisonTask(
        "C2-03", CATEGORY_BUG_LOCALIZATION, GIN, "context.MustBindWith",
        "A production handler is reported to occasionally proceed with a zero-valued struct instead "
        "of returning a 400 when the request body is malformed. Trace `MustBindWith` and the "
        "specific `BindXxx` wrapper methods to find where the error could be getting swallowed.",
    ),
    ComparisonTask(
        "C2-04", CATEGORY_BUG_LOCALIZATION, BLACK, "src.black.format_file_in_place",
        "A user reports that running Black on a file sometimes reports success but leaves the file "
        "unchanged on disk. Trace `format_file_in_place`'s full call chain to find where a "
        "formatted result could fail to be written back without raising.",
    ),
    ComparisonTask(
        "C2-05", CATEGORY_BUG_LOCALIZATION, BLACK, "src.black.concurrency.reformat_many",
        "When formatting a large directory in parallel, one worker's exception is reported to "
        "sometimes vanish instead of aborting the run or being reported to the user. Trace "
        "`reformat_many`'s worker-dispatch logic to find where an exception could be swallowed.",
    ),
    ComparisonTask(
        "C2-06", CATEGORY_BUG_LOCALIZATION, CLICK, "src.click.core.Command.main",
        "A custom exception raised deep inside a parameter's `callback` is reported to sometimes "
        "produce exit code 0 instead of a non-zero failure. Trace `Command.main`'s exception "
        "handling to find where that signal could be getting lost.",
    ),
    ComparisonTask(
        "C2-07", CATEGORY_BUG_LOCALIZATION, CLICK, "src.click._termui_impl.Editor.edit_files",
        "When the configured `$EDITOR` fails to launch, `click.edit()` is reported to sometimes "
        "return an empty string instead of raising `UsageError`. Trace `Editor.edit_files` to find "
        "where the subprocess failure could be getting silently absorbed.",
    ),
    # ------------------------------------------------------------- #
    # Category 3 - Idiomatic Code Generation & Structural Slot-Filling (7)
    # ------------------------------------------------------------- #
    ComparisonTask(
        "C3-01", CATEGORY_CODEGEN, GIN, "recovery.CustomRecovery",
        "Add a new middleware, `Timeout(d time.Duration) HandlerFunc`, that aborts a request "
        "exceeding `d`. Follow this repository's exact existing middleware idiom (see `recovery.go` "
        "and `logger.go`) for how a middleware constructor is shaped and how it calls `c.Next()`.",
    ),
    ComparisonTask(
        "C3-02", CATEGORY_CODEGEN, GIN, "context.GetInt64",
        "Add a new `Context.GetUint(key any) uint` helper method. Follow the exact existing idiom "
        "the `GetXxx` family in `context.go` already uses (including how it delegates to the "
        "underlying typed-get helper).",
    ),
    ComparisonTask(
        "C3-03", CATEGORY_CODEGEN, BLACK, "src.black.linegen.LineGenerator.visit_default",
        "Add a new `visit_STANDALONE_COMMENT` method to `LineGenerator`. Follow the exact visitor "
        "idiom the existing `visit_*` methods in `linegen.py` use (yielding `Line` objects, calling "
        "`self.line()`, following `visit_default`'s own fallback pattern).",
    ),
    ComparisonTask(
        "C3-04", CATEGORY_CODEGEN, BLACK, "tests.test_black.BlackTestCase.invokeBlack",
        "Add a new test method, `test_preview_feature_flag`, to `BlackTestCase`. Follow the exact "
        "idiom the existing test methods use for invoking Black and asserting on its output "
        "(`assert_collected_sources`, `invokeBlack`, the test-data-file convention in `tests/data/`).",
    ),
    ComparisonTask(
        "C3-05", CATEGORY_CODEGEN, BLACK, "src.black.trans.StringParenWrapper.do_splitter_match",
        "Add a new string transformer class following the exact idiom `StringParenWrapper`/"
        "`StringSplitter` use in `trans.py` (subclassing `BaseStringSplitter`, implementing "
        "`do_splitter_match`/`do_transform`, using `CustomSplitMapMixin`).",
    ),
    ComparisonTask(
        "C3-06", CATEGORY_CODEGEN, CLICK, "src.click.types._NumberParamTypeBase.convert",
        "Add a new `HexIntParamType` class that parses `0x`-prefixed hexadecimal integers. Follow "
        "the exact idiom `IntParamType`/`FloatParamType` use in `types.py` (subclassing `ParamType`, "
        "implementing `convert()`, calling `self.fail()` on invalid input).",
    ),
    ComparisonTask(
        "C3-07", CATEGORY_CODEGEN, CLICK, "src.click.testing.CliRunner.invoke",
        "Add a new test using `CliRunner.invoke` that verifies a command's `--help` output contains "
        "a specific option. Follow the exact idiom the existing tests in `tests/test_basic.py` use "
        "for invoking a command and asserting on `result.output`/`result.exit_code`.",
    ),
    # ------------------------------------------------------------- #
    # Category 4 - Polysemic Disambiguation & Contextual Role Resolution (6)
    # ------------------------------------------------------------- #
    ComparisonTask(
        "C4-01", CATEGORY_POLYSEMY, GIN, "binding.form_mapping.mapForm",
        "`mapForm`, `mapFormByTag`, and `MapFormWithTag` are three thin wrappers that all eventually "
        "call the same underlying `mapping`/`mappingByPtr` machinery with different tag arguments. "
        "For each of the three, explain its distinct semantic role and which caller module relies "
        "on each one specifically.",
    ),
    ComparisonTask(
        "C4-02", CATEGORY_POLYSEMY, BLACK, "src.black.linegen._first_right_hand_split",
        "`_first_right_hand_split` is called from at least three different sites, each binding its "
        "result to a differently-named variable (`rhs`, `rhs_oop`, `rhs_result`). For each call "
        "site, explain the distinct semantic intent behind that call - are they all doing the same "
        "thing, or does each one represent a genuinely different transformation step?",
    ),
    ComparisonTask(
        "C4-03", CATEGORY_POLYSEMY, BLACK, "src.black.cache.Cache.hash_digest",
        "`Cache.hash_digest` is called from more than one place, once bound to `hash` and once to "
        "`new_hash`. Explain what distinct role each call site plays in the cache-invalidation flow.",
    ),
    ComparisonTask(
        "C4-04", CATEGORY_POLYSEMY, CLICK, "src.click.types.convert_type",
        "`convert_type` is called across the codebase with its result bound to `guessed`, `type`, "
        "or `value_proc` depending on the caller. For each of these three call-site roles, explain "
        "the distinct semantic purpose - are all three treating the return value as a type, or do "
        "some treat it as something else entirely?",
    ),
    ComparisonTask(
        "C4-05", CATEGORY_POLYSEMY, CLICK, "src.click.core.Group.get_command",
        "`Group.get_command` is called from sites that bind its result to either `cmd` or "
        "`command`. Explain whether this naming difference reflects a real semantic distinction "
        "between the call sites (e.g. one path assumes the command must exist, the other treats "
        "`None` as a valid outcome) or is purely cosmetic.",
    ),
    ComparisonTask(
        "C4-06", CATEGORY_POLYSEMY, CLICK, "src.click.parser._normalize_opt",
        "`_normalize_opt` is called from sites binding its result to either `norm_long_opt` or "
        "`opt`. Explain the distinct role each call site plays in option-string parsing.",
    ),
    # ------------------------------------------------------------- #
    # Category 5 - Concurrency, Transactional & State Invariant Audits (6)
    # ------------------------------------------------------------- #
    ComparisonTask(
        "C5-01", CATEGORY_INVARIANT_AUDIT, GIN, "context.Set",
        "Audit `Context.Set`/`Get`/`MustGet` and the underlying `Keys map[string]any` they share. "
        "Under what circumstances (if any) could concurrent goroutines spawned from the same "
        "request handler race on this map, and does the current implementation guard against it?",
        related_symbols=("context.Get", "context.MustGet"),
    ),
    ComparisonTask(
        "C5-02", CATEGORY_INVARIANT_AUDIT, GIN, "binding.form_mapping.setByForm",
        "Audit `setByForm`/`setWithProperType`'s use of `reflect.Value` mutation. Could a `reflect.Value` "
        "obtained from one request's struct ever be reused or mutated in a way that leaks into a "
        "concurrently-handled request's own bound struct?",
    ),
    ComparisonTask(
        "C5-03", CATEGORY_INVARIANT_AUDIT, BLACK, "src.black.linegen.LineGenerator.__post_init__",
        "Audit `LineGenerator.__init__`/`__post_init__` for post-construction mutation. Which fields "
        "are mutated after the object is considered \"constructed\", and could that violate an "
        "invariant a caller might reasonably assume holds immediately after `LineGenerator(...)`?",
    ),
    ComparisonTask(
        "C5-04", CATEGORY_INVARIANT_AUDIT, BLACK, "src.black.lines.Line.append",
        "Audit `Line.append`'s state-mutation boundary. Is `Line` ever passed to a context that "
        "assumes it's immutable (e.g. stored in a set, compared, or cached) while still being "
        "mutated elsewhere via `append`?",
    ),
    ComparisonTask(
        "C5-05", CATEGORY_INVARIANT_AUDIT, CLICK, "src.click._termui_impl.ProgressBar.update",
        "Audit `ProgressBar`'s state machine across `update`/`make_step`/`render_progress`/`finish`. "
        "If an exception is raised mid-iteration inside the `with` block, is the terminal render "
        "state (cursor visibility, final newline) guaranteed to be restored correctly?",
        related_symbols=(
            "src.click._termui_impl.ProgressBar.make_step",
            "src.click._termui_impl.ProgressBar.render_progress",
            "src.click._termui_impl.ProgressBar.finish",
        ),
    ),
    ComparisonTask(
        "C5-06", CATEGORY_INVARIANT_AUDIT, CLICK, "examples.repo.repo.Repo.__init__",
        "Audit the `Repo`/`pass_context` pattern in `examples/repo/repo.py`. Could the mutable "
        "state constructed in `Repo.__init__` leak or persist across what should be independent "
        "`CliRunner.invoke()` calls in a test suite?",
    ),
]

assert len(TASKS) == 33, f"expected exactly 33 tasks, got {len(TASKS)}"
assert sum(1 for t in TASKS if t.category == CATEGORY_BLAST_RADIUS) == 7
assert sum(1 for t in TASKS if t.category == CATEGORY_BUG_LOCALIZATION) == 7
assert sum(1 for t in TASKS if t.category == CATEGORY_CODEGEN) == 7
assert sum(1 for t in TASKS if t.category == CATEGORY_POLYSEMY) == 6
assert sum(1 for t in TASKS if t.category == CATEGORY_INVARIANT_AUDIT) == 6
assert sum(1 for t in TASKS if t.repo == GIN) == 11
assert sum(1 for t in TASKS if t.repo == BLACK) == 11
assert sum(1 for t in TASKS if t.repo == CLICK) == 11
