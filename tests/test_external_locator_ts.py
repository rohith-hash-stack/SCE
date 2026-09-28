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
