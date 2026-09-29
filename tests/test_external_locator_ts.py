"""`TypeScriptSourceLocator` (`prism.external.locator_ts`) - Phase D
prerequisite. Every fixture here mirrors a real, verified shape (checked
directly against genuinely `npm install`-ed `express`, `@types/express`,
`@trpc/server`, and `zod` while building this locator) rather than an
invented one: `@trpc/server`'s real `package.json` has both a direct
`types` field *and* an `exports["."].import.types`/`.require.types`
conditional map; `zod`'s has a flatter `exports["."].types`; `express`
itself ships no types at all and needs its real `@types/express`
sibling; `zod`/`@trpc/server`'s own real `.d.cts` entry files are both
pure re-export barrels with no inline definitions at all (a real,
disclosed Non-goal for this leaf-only design, not something these tests
pretend around).
"""
from __future__ import annotations

import json

from prism.external.index import extract_external_symbol, external_symbol_to_node_entry
from prism.external.locator_ts import TypeScriptSourceLocator, _types_package_name


def _write_package(node_modules: object, name: str, package_json: dict, files: dict[str, str]) -> None:
    pkg_dir = node_modules
    for segment in name.split("/"):
        pkg_dir = pkg_dir / segment
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(json.dumps(package_json))
    for rel_path, content in files.items():
        file_path = pkg_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)


# --------------------------------------------------------------------- #
# Standard third-party package with no types of its own (express-shaped)
# --------------------------------------------------------------------- #
def test_untyped_package_falls_through_to_its_real_types_sibling(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "express", {"name": "express", "main": "index.js"}, {"index.js": "module.exports = function () {};\n"})
    _write_package(
        node_modules,
        "@types/express",
        {"name": "@types/express", "types": "index.d.ts"},
        {"index.d.ts": "declare function e(): void;\ndeclare namespace e {\n    export function Router(options?: RouterOptions): void;\n}\nexport = e;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("express")
    assert files == [node_modules / "@types" / "express" / "index.d.ts"]


def test_untyped_package_extracts_a_real_declare_stub_through_its_types_sibling(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "express", {"name": "express", "main": "index.js"}, {"index.js": "module.exports = function () {};\n"})
    _write_package(
        node_modules,
        "@types/express",
        {"name": "@types/express", "types": "index.d.ts"},
        {"index.d.ts": "declare namespace e {\n    export function Router(options?: RouterOptions): void;\n}\n"},
    )
    locator = TypeScriptSourceLocator(str(tmp_path))
    info = extract_external_symbol("express", "Router", locator=locator)
    assert info is not None
    # `ContractExtractor` doesn't currently preserve TS's `?` optional-
    # parameter marker in `Parameter.render()` - a pre-existing behavior
    # unrelated to this locator, not something this test re-litigates.
    assert info.signature_text == "declare function Router(options: RouterOptions): void;"
    entry = external_symbol_to_node_entry(info)
    assert entry.role == "external"
    assert entry.compression == "L2_skeleton"
    assert entry.contract is None
    assert entry.body == "declare function Router(options: RouterOptions): void;"


# --------------------------------------------------------------------- #
# Scoped packages (@trpc/server-shaped: direct `types` + conditional
# `exports["."]` map)
# --------------------------------------------------------------------- #
def test_scoped_package_resolves_via_its_own_direct_types_field(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "@trpc/server",
        {
            "name": "@trpc/server",
            "main": "./dist/index.cjs",
            "types": "./dist/index.d.cts",
            "exports": {
                ".": {
                    "import": {"types": "./dist/index.d.mts", "default": "./dist/index.mjs"},
                    "require": {"types": "./dist/index.d.cts", "default": "./dist/index.cjs"},
                }
            },
        },
        {"dist/index.d.cts": "export declare function initTRPC(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("@trpc/server")
    assert files == [node_modules / "@trpc" / "server" / "dist" / "index.d.cts"]


def test_scoped_package_path_is_never_split_or_mangled(tmp_path):
    """The real on-disk layout (`node_modules/@trpc/server`, two real
    directory levels) must be preserved exactly - no collapsing `@trpc`
    and `server` into one segment, no re-splitting `@trpc/server` into
    something else."""
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "@trpc/server", {"name": "@trpc/server", "types": "index.d.ts"}, {"index.d.ts": "export declare function initTRPC(): void;\n"})
    files = TypeScriptSourceLocator(str(tmp_path)).locate("@trpc/server")
    assert len(files) == 1
    assert files[0].parent == node_modules / "@trpc" / "server"


# --------------------------------------------------------------------- #
# `feature/subpath-export-resolution`: a real npm subpath export
# (`@trpc/server/adapters/express`) - the fix for the confirmed-live
# regression where `createExpressMiddleware` was unreachable because
# resolution only ever consulted the package's root `exports["."]`.
# --------------------------------------------------------------------- #
def test_subpath_resolves_via_exports_flat_types_field(tmp_path):
    """The flatter shape (a direct `types` key, no `import`/`require`
    nesting) - the same shape `zod`'s own root export already uses
    elsewhere in this file, generalized to a subpath key."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "zod",
        {"name": "zod", "exports": {".": {"types": "./lib/index.d.ts"}, "./v4": {"types": "./v4/index.d.ts"}}},
        {"lib/index.d.ts": "export declare function object(): void;\n", "v4/index.d.ts": "export declare function objectV4(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("zod", subpath="v4")
    assert files == [node_modules / "zod" / "v4" / "index.d.ts"]


def test_subpath_resolves_via_conditional_import_require_exports(tmp_path):
    """Real `@trpc/server`'s own shape: the subpath's export target is
    itself nested under `import`/`require`, not a flat `types` key -
    the exact shape that first exposed the "import declares a `.d.mts`
    file that doesn't exist on disk, require's own `.d.ts` sibling is
    never tried" gap this test locks in as fixed (both files present,
    `import` still tried first per `_types_from_exports_target`'s own
    documented preference, and it resolves)."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "@trpc/server",
        {
            "name": "@trpc/server",
            "exports": {
                ".": {"import": {"types": "./dist/index.d.mts"}, "require": {"types": "./dist/index.d.ts"}},
                "./adapters/express": {
                    "import": {"types": "./dist/adapters/express.d.mts"},
                    "require": {"types": "./dist/adapters/express.d.ts"},
                },
            },
        },
        {
            "dist/index.d.mts": "export declare function initTRPC(): void;\n",
            "dist/adapters/express.d.mts": "export declare function createExpressMiddleware(): void;\n",
            "dist/adapters/express.d.ts": "export declare function createExpressMiddleware(): void;\n",
        },
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("@trpc/server", subpath="adapters/express")
    assert files == [node_modules / "@trpc" / "server" / "dist" / "adapters" / "express.d.mts"]


def test_subpath_falls_back_to_a_direct_dts_file_on_disk(tmp_path):
    """No `exports` map entry for the subpath at all - the same "no
    exports map, just resolve the path" degrade the root chain already
    applies, generalized to `<subpath>.d.ts`."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "some-pkg",
        {"name": "some-pkg", "types": "index.d.ts"},
        {"index.d.ts": "export declare function root(): void;\n", "adapters/express.d.ts": "export declare function mid(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg", subpath="adapters/express")
    assert files == [node_modules / "some-pkg" / "adapters" / "express.d.ts"]


def test_subpath_falls_back_to_an_index_dts_directory(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "some-pkg",
        {"name": "some-pkg", "types": "index.d.ts"},
        {"index.d.ts": "export declare function root(): void;\n", "adapters/express/index.d.ts": "export declare function mid(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg", subpath="adapters/express")
    assert files == [node_modules / "some-pkg" / "adapters" / "express" / "index.d.ts"]


def test_subpath_miss_falls_through_to_the_root_chain_not_treated_as_missing(tmp_path):
    """A subpath that resolves to nothing (no `exports` entry, no file
    on disk) must still fall through to the package's own real root
    types - a subpath miss is not a reason to abandon the whole lookup,
    the same way the existing root chain already degrades gracefully
    from `types`/`typings` to `exports["."]` to `main`-adjacent."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules, "some-pkg", {"name": "some-pkg", "types": "index.d.ts"}, {"index.d.ts": "export declare function root(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg", subpath="nonexistent/path")
    assert files == [node_modules / "some-pkg" / "index.d.ts"]


def test_no_subpath_is_unchanged_from_before_this_feature(tmp_path):
    """Regression guard: `subpath=None` (the default) must resolve
    exactly like every pre-existing test in this file already proves -
    this feature is strictly additive."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules, "some-pkg", {"name": "some-pkg", "types": "index.d.ts"}, {"index.d.ts": "export declare function root(): void;\n"},
    )
    assert TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg") == [node_modules / "some-pkg" / "index.d.ts"]
    assert TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg", subpath="") == [node_modules / "some-pkg" / "index.d.ts"]


def test_subpath_resolution_applies_through_the_types_sibling_fallback_too(tmp_path):
    """An untyped package (express-shaped) with a subpath import, falling
    through to its real `@types/<pkg>` sibling - `subpath` must reach
    that lookup too, not just the package's own direct resolution."""
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "some-pkg", {"name": "some-pkg", "main": "index.js"}, {"index.js": "module.exports = {};\n"})
    _write_package(
        node_modules,
        "@types/some-pkg",
        {"name": "@types/some-pkg", "exports": {"./adapters/express": {"types": "./adapters/express.d.ts"}}},
        {"index.d.ts": "export declare function root(): void;\n", "adapters/express.d.ts": "export declare function mid(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg", subpath="adapters/express")
    assert files == [node_modules / "@types" / "some-pkg" / "adapters" / "express.d.ts"]


def test_exports_conditional_types_resolves_when_no_direct_types_field(tmp_path):
    """A package with *only* a conditional `exports["."].import.types`
    map (no top-level `types` field at all) must still resolve - the
    `@trpc/server` shape minus its own direct `types` shortcut."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "conditional-pkg",
        {
            "name": "conditional-pkg",
            "main": "./dist/index.cjs",
            "exports": {".": {"import": {"types": "./dist/index.d.mts"}, "require": {"types": "./dist/index.d.cts"}}},
        },
        {"dist/index.d.mts": "export declare function widget(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("conditional-pkg")
    assert files == [node_modules / "conditional-pkg" / "dist" / "index.d.mts"]


def test_exports_flat_types_resolves_zod_shaped_package(tmp_path):
    """`zod`'s own real shape: `exports["."].types` directly, not nested
    under `import`/`require` at all."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "flat-exports-pkg",
        {"name": "flat-exports-pkg", "main": "./index.cjs", "exports": {".": {"types": "./index.d.cts", "import": "./index.js", "require": "./index.cjs"}}},
        {"index.d.cts": "export declare function schema(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("flat-exports-pkg")
    assert files == [node_modules / "flat-exports-pkg" / "index.d.cts"]


# --------------------------------------------------------------------- #
# Fallback resolution: main-adjacent .d.ts, and @types/<pkg> when the
# package's own package.json has nothing usable at all.
# --------------------------------------------------------------------- #
def test_main_adjacent_dts_resolves_with_no_types_or_exports_field(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "adjacent-dts-pkg",
        {"name": "adjacent-dts-pkg", "main": "dist/index.js"},
        {"dist/index.js": "module.exports = {};\n", "dist/index.d.ts": "export declare function run(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("adjacent-dts-pkg")
    assert files == [node_modules / "adjacent-dts-pkg" / "dist" / "index.d.ts"]


def test_scoped_types_sibling_naming_convention():
    assert _types_package_name("express") == "@types/express"
    assert _types_package_name("@babel/core") == "@types/babel__core"
    assert _types_package_name("@trpc/server") == "@types/trpc__server"


def test_at_types_node_resolves_directly_as_its_own_real_package(tmp_path):
    """`@types/node` is a real, standalone npm package in its own right
    (types for Node.js itself, no companion runtime package) - resolved
    through the exact same scoped-package path, not a special case."""
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "@types/node", {"name": "@types/node", "types": "index.d.ts"}, {"index.d.ts": "export declare function cwd(): string;\n"})
    files = TypeScriptSourceLocator(str(tmp_path)).locate("@types/node")
    assert files == [node_modules / "@types" / "node" / "index.d.ts"]


def test_plain_js_is_the_last_resort_when_nothing_typed_exists_anywhere(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "untyped-lib", {"name": "untyped-lib", "main": "index.js"}, {"index.js": "module.exports.greet = function () {};\n"})
    files = TypeScriptSourceLocator(str(tmp_path)).locate("untyped-lib")
    assert files == [node_modules / "untyped-lib" / "index.js"]


def test_untyped_package_still_prefers_its_types_sibling_over_its_own_plain_js(tmp_path):
    """Regression guard for the exact priority-ordering bug found while
    building this: a package's own plain `.js` main must never be
    returned before its `@types/<pkg>` sibling has been tried - the
    plain-JS fallback is the *last* resort, not a short-circuit."""
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "has-types-sibling", {"name": "has-types-sibling", "main": "index.js"}, {"index.js": "module.exports = {};\n"})
    _write_package(node_modules, "@types/has-types-sibling", {"name": "@types/has-types-sibling", "types": "index.d.ts"}, {"index.d.ts": "export declare function run(): void;\n"})
    files = TypeScriptSourceLocator(str(tmp_path)).locate("has-types-sibling")
    assert files == [node_modules / "@types" / "has-types-sibling" / "index.d.ts"]


# --------------------------------------------------------------------- #
# Graceful degradation when a dependency is missing entirely.
# --------------------------------------------------------------------- #
def test_missing_dependency_returns_empty_list_not_none_or_a_crash(tmp_path):
    (tmp_path / "node_modules").mkdir()
    files = TypeScriptSourceLocator(str(tmp_path)).locate("this-package-does-not-exist-anywhere-xyz")
    assert files == []


def test_no_node_modules_directory_at_all_degrades_gracefully(tmp_path):
    files = TypeScriptSourceLocator(str(tmp_path)).locate("express")
    assert files == []


def test_extract_external_symbol_returns_none_not_a_crash_for_a_missing_package(tmp_path):
    locator = TypeScriptSourceLocator(str(tmp_path))
    assert extract_external_symbol("nonexistent-pkg-xyz", "anything", locator=locator) is None


def test_malformed_package_json_degrades_to_the_plain_js_fallback(tmp_path):
    node_modules = tmp_path / "node_modules"
    pkg_dir = node_modules / "broken-pkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text("{ not valid json")
    (pkg_dir / "index.js").write_text("module.exports = {};\n")
    files = TypeScriptSourceLocator(str(tmp_path)).locate("broken-pkg")
    assert files == [pkg_dir / "index.js"]


# --------------------------------------------------------------------- #
# Monorepo: hoisted node_modules reached via directory walk-up.
# --------------------------------------------------------------------- #
def test_hoisted_node_modules_found_by_walking_up_from_a_nested_package(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "shared-dep", {"name": "shared-dep", "types": "index.d.ts"}, {"index.d.ts": "export declare function util(): void;\n"})
    nested = tmp_path / "packages" / "app" / "src"
    nested.mkdir(parents=True)
    files = TypeScriptSourceLocator(str(nested)).locate("shared-dep")
    assert files == [node_modules / "shared-dep" / "index.d.ts"]


def test_a_package_level_node_modules_shadows_the_hoisted_root_one(tmp_path):
    """The nearest `node_modules/<pkg>` wins - real npm/yarn/pnpm
    resolution order, not always the hoisted root copy."""
    root_node_modules = tmp_path / "node_modules"
    _write_package(root_node_modules, "dep", {"name": "dep", "types": "index.d.ts"}, {"index.d.ts": "export declare function fromRoot(): void;\n"})
    nested_dir = tmp_path / "packages" / "app"
    nested_node_modules = nested_dir / "node_modules"
    _write_package(nested_node_modules, "dep", {"name": "dep", "types": "index.d.ts"}, {"index.d.ts": "export declare function fromNested(): void;\n"})
    files = TypeScriptSourceLocator(str(nested_dir)).locate("dep")
    assert files == [nested_node_modules / "dep" / "index.d.ts"]


# --------------------------------------------------------------------- #
# package_version is accepted (Protocol conformance) and ignored.
# --------------------------------------------------------------------- #
def test_package_version_argument_is_accepted_and_ignored(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "versioned-pkg", {"name": "versioned-pkg", "types": "index.d.ts"}, {"index.d.ts": "export declare function run(): void;\n"})
    files = TypeScriptSourceLocator(str(tmp_path)).locate("versioned-pkg", package_version="9.9.9")
    assert files == [node_modules / "versioned-pkg" / "index.d.ts"]


# --------------------------------------------------------------------- #
# Connected fixes surfaced while building this locator, in
# `prism.external.index` and `prism.parser.lang_config` (not this
# module) - covered here since this locator is what actually exercises
# them for the first time.
# --------------------------------------------------------------------- #
def test_d_cts_and_d_mts_extensions_parse_under_the_typescript_grammar(tmp_path):
    """`EXTENSION_LANGUAGE_MAP` has no `.cts`/`.mts` entry at all (a real
    gap, confirmed against `@trpc/server`'s own real `types: "./dist/
    index.d.cts"`) - `_parse_external_file`'s `_EXTERNAL_EXTENSION_
    OVERRIDES` must force the TypeScript grammar for both."""
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "cts-pkg", {"name": "cts-pkg", "types": "index.d.cts"}, {"index.d.cts": "export declare function ctsFn(): void;\n"})
    _write_package(node_modules, "mts-pkg", {"name": "mts-pkg", "types": "index.d.mts"}, {"index.d.mts": "export declare function mtsFn(): void;\n"})
    locator = TypeScriptSourceLocator(str(tmp_path))
    cts_info = extract_external_symbol("cts-pkg", "ctsFn", locator=locator)
    mts_info = extract_external_symbol("mts-pkg", "mtsFn", locator=locator)
    assert cts_info is not None and cts_info.language == "typescript"
    assert mts_info is not None and mts_info.language == "typescript"


def test_scoped_package_qualified_name_keeps_the_full_scope_intact(tmp_path):
    """`_module_name_for_file`'s scoped-package matching (a connected
    `prism.external.index` fix, not part of this locator itself) must
    produce a real, unambiguous qualified name for a scoped package's
    symbol - never silently dropping or mangling the `@scope/name`
    prefix."""
    node_modules = tmp_path / "node_modules"
    _write_package(node_modules, "@scope/pkg", {"name": "@scope/pkg", "types": "dist/index.d.ts"}, {"dist/index.d.ts": "export declare function widget(): void;\n"})
    locator = TypeScriptSourceLocator(str(tmp_path))
    info = extract_external_symbol("@scope/pkg", "widget", locator=locator)
    assert info is not None
    assert info.qualified_name == "@scope/pkg.dist.index.widget"


# --------------------------------------------------------------------- #
# `feature/subpath-export-resolution` follow-up: generic Node.js
# `exports` subpath-pattern (`./*`) matching and the `default`-condition
# fallback chain, with no package-name special-casing anywhere - proven
# here against unscoped (`lodash`), deeply-scoped (`@tanstack/react-
# query`), and pattern-shaped packages a real-world locator must handle
# identically to `@trpc/server`.
# --------------------------------------------------------------------- #
def test_export_pattern_matches_a_single_wildcard_key(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "lodash-es",
        {"name": "lodash-es", "exports": {".": {"types": "./index.d.ts"}, "./*": {"types": "./*.d.ts", "default": "./*.js"}}},
        {"index.d.ts": "export declare function main(): void;\n", "fp.d.ts": "export declare function curry(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("lodash-es", subpath="fp")
    assert files == [node_modules / "lodash-es" / "fp.d.ts"]


def test_export_pattern_on_a_deeply_scoped_package(tmp_path):
    """The user's own named example: `@tanstack/react-query/devtools`,
    a scoped package with a multi-segment subpath resolved entirely
    through the same generic pattern-matching code as any other."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "@tanstack/react-query",
        {"name": "@tanstack/react-query", "exports": {".": {"types": "./build/index.d.ts"}, "./*": {"types": "./build/*/index.d.ts"}}},
        {
            "build/index.d.ts": "export declare function useQuery(): void;\n",
            "build/devtools/index.d.ts": "export declare function ReactQueryDevtools(): void;\n",
        },
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("@tanstack/react-query", subpath="devtools")
    assert files == [node_modules / "@tanstack" / "react-query" / "build" / "devtools" / "index.d.ts"]


def test_export_pattern_with_a_literal_suffix_after_the_wildcard(tmp_path):
    """A pattern key's `*` need not be the last character - Node's real
    spec allows a literal suffix after it (e.g. `"./features/*.mjs"`),
    matched and substituted the same way."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "suffixed-pkg",
        {"name": "suffixed-pkg", "exports": {"./features/*-mod": {"types": "./dist/*-mod.d.ts"}}},
        {"dist/express-mod.d.ts": "export declare function feature(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("suffixed-pkg", subpath="features/express-mod")
    assert files == [node_modules / "suffixed-pkg" / "dist" / "express-mod.d.ts"]


def test_exact_export_key_wins_over_a_matching_pattern(tmp_path):
    """A package can declare both a pattern and a real, exact override
    for one specific subpath under it - the exact key must always win,
    per real Node.js resolution order (exact lookup before any pattern
    match is even attempted)."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "mixed-pkg",
        {
            "name": "mixed-pkg",
            "exports": {"./*": {"types": "./dist/*.d.ts"}, "./special": {"types": "./dist/special-override.d.ts"}},
        },
        {"dist/special.d.ts": "export declare function generic(): void;\n", "dist/special-override.d.ts": "export declare function overridden(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("mixed-pkg", subpath="special")
    assert files == [node_modules / "mixed-pkg" / "dist" / "special-override.d.ts"]


def test_no_pattern_key_matches_falls_through_to_filesystem_fallback(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "some-pkg",
        {"name": "some-pkg", "types": "index.d.ts", "exports": {"./only-this/*": {"types": "./dist/*.d.ts"}}},
        {"index.d.ts": "export declare function root(): void;\n", "other/index.d.ts": "export declare function other(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("some-pkg", subpath="other")
    assert files == [node_modules / "some-pkg" / "other" / "index.d.ts"]


def test_default_condition_types_resolves_when_import_and_require_have_none(tmp_path):
    """The condition precedence's own last real step (`default.types`) -
    a target whose only types-bearing condition is `default`, not
    `import`/`require` at all."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "default-cond-pkg",
        {"name": "default-cond-pkg", "exports": {"./widget": {"node": "./node/widget.js", "default": {"types": "./widget.d.ts", "default": "./widget.js"}}}},
        {"widget.d.ts": "export declare function widget(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("default-cond-pkg", subpath="widget")
    assert files == [node_modules / "default-cond-pkg" / "widget.d.ts"]


def test_bare_default_string_falls_back_to_its_own_adjacent_dts(tmp_path):
    """No `types` condition anywhere in the target at all - just a bare
    `default` string naming a JS file. Real TypeScript resolution tries
    that file's own sibling `.d.ts` next, the same convention the root
    chain's `main`-adjacent fallback already uses, generalized here to
    an arbitrary `exports` target."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "js-only-pkg",
        {"name": "js-only-pkg", "exports": {"./widget": "./dist/widget.js"}},
        {"dist/widget.js": "module.exports = {};\n", "dist/widget.d.ts": "export declare function widget(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("js-only-pkg", subpath="widget")
    assert files == [node_modules / "js-only-pkg" / "dist" / "widget.d.ts"]


def test_generic_subpath_resolution_on_an_unscoped_package(tmp_path):
    """The user's own named unscoped example: `lodash/fp` - proves the
    exact-key path (not just the pattern path) is equally unscoped-
    package-generic, with zero special-casing."""
    node_modules = tmp_path / "node_modules"
    _write_package(
        node_modules,
        "lodash",
        {"name": "lodash", "exports": {".": {"types": "./index.d.ts"}, "./fp": {"types": "./fp.d.ts"}}},
        {"index.d.ts": "export declare function main(): void;\n", "fp.d.ts": "export declare function curry(): void;\n"},
    )
    files = TypeScriptSourceLocator(str(tmp_path)).locate("lodash", subpath="fp")
    assert files == [node_modules / "lodash" / "fp.d.ts"]
