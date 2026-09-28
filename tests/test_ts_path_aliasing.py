"""Architectural-audit Category 8: TypeScript/JavaScript path aliasing.

`ConcreteGraphBuilder._resolve_js_specifier` previously treated every
non-relative specifier as a bare package reference (`specifier.replace(
"/", ".")`, the same synthetic-module-name path a real npm package like
`express` gets) - a `tsconfig.json`/`jsconfig.json` `compilerOptions.
paths` alias (`"@/*": ["src/*"]`) resolved to a phantom module no real
symbol ever matches, exactly as if it were an unresolvable external
import. These tests exercise the fix (`_resolve_ts_path_alias`,
`_find_tsconfig_for_dir`, `_parse_ts_path_alias_config`,
`_best_ts_path_alias_match`, `_ts_path_alias_target_candidates`) against
real files on disk, matching the same `build_pipeline(tmp_path)`
methodology `tests/test_export_registry.py` already uses for barrel-file
resolution.
"""
from __future__ import annotations

import json

from prism.cli import build_pipeline
from prism.graph.concrete_builder import (
    ConcreteGraphBuilder,
    _best_ts_path_alias_match,
    _parse_ts_path_alias_config,
    _strip_jsonc_comments,
)


def _write_basic_ts_project(tmp_path, tsconfig: dict) -> None:
    (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
    src = tmp_path / "src"
    (src / "utils").mkdir(parents=True)
    (src / "components").mkdir(parents=True)
    (src / "utils" / "index.ts").write_text("export function helper() {\n    return 1;\n}\n")
    (src / "components" / "Button.tsx").write_text("export function Button() {\n    return null;\n}\n")


# --------------------------------------------------------------------- #
# Scope: wildcard alias patterns (`@/*` -> `src/*`)
# --------------------------------------------------------------------- #
def test_wildcard_alias_resolves_to_a_real_call_edge(tmp_path) -> None:
    _write_basic_ts_project(tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}})
    (tmp_path / "app.ts").write_text(
        "import { helper } from '@/utils';\n\nexport function run() {\n    return helper();\n}\n"
    )
    builder, _tags = build_pipeline(str(tmp_path), use_cache=False)
    assert builder.graph.has_edge("app.run", "src.utils.helper")


def test_wildcard_alias_resolves_a_nested_file_not_just_a_barrel(tmp_path) -> None:
    _write_basic_ts_project(tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}})
    (tmp_path / "app.ts").write_text(
        "import { Button } from '@/components/Button';\n\nexport function run() {\n    return Button();\n}\n"
    )
    builder, _tags = build_pipeline(str(tmp_path), use_cache=False)
    assert builder.graph.has_edge("app.run", "src.components.Button.Button")


def test_more_specific_wildcard_pattern_wins_over_a_catch_all(tmp_path) -> None:
    """`tsc`'s own tie-break: `@/components/*` (longer literal prefix)
    must win over a broader `@/*` for the same specifier."""
    _write_basic_ts_project(
        tmp_path,
        {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"], "@/components/*": ["src/components/*"]}}},
    )
    (tmp_path / "app.ts").write_text(
        "import { Button } from '@/components/Button';\n\nexport function run() {\n    return Button();\n}\n"
    )
    builder, _tags = build_pipeline(str(tmp_path), use_cache=False)
    assert builder.graph.has_edge("app.run", "src.components.Button.Button")


# --------------------------------------------------------------------- #
# Scope: exact/non-wildcard path aliases (`utils` -> `src/utils/index.ts`)
# --------------------------------------------------------------------- #
def test_exact_non_wildcard_alias_resolves(tmp_path) -> None:
    _write_basic_ts_project(
        tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"utils": ["src/utils/index.ts"]}}}
    )
    (tmp_path / "app.ts").write_text("import { helper } from 'utils';\n\nexport function run() {\n    return helper();\n}\n")
    builder, _tags = build_pipeline(str(tmp_path), use_cache=False)
    assert builder.graph.has_edge("app.run", "src.utils.helper")


# --------------------------------------------------------------------- #
# Scope: multi-target path fallbacks
# --------------------------------------------------------------------- #
def test_multi_target_alias_falls_through_to_the_second_real_target(tmp_path) -> None:
    """`"@/*": ["nonexistent/*", "src/*"]` - the first target doesn't
    exist on disk anywhere; resolution must fall through to the second,
    matching `tsc`'s own "first target that resolves wins" rule."""
    _write_basic_ts_project(
        tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["nonexistent/*", "src/*"]}}}
    )
    (tmp_path / "app.ts").write_text("import { helper } from '@/utils';\n\nexport function run() {\n    return helper();\n}\n")
    builder, _tags = build_pipeline(str(tmp_path), use_cache=False)
    assert builder.graph.has_edge("app.run", "src.utils.helper")


def test_multi_target_alias_with_no_real_target_degrades_to_a_synthetic_module(tmp_path) -> None:
    """When an alias pattern matches but none of its targets exist on
    disk, resolution must degrade the same way an unresolved relative
    import already does - a synthetic, never-matching module name - not
    fall through to being treated as an npm package."""
    _write_basic_ts_project(tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["ghost/*"]}}})
    builder = ConcreteGraphBuilder(str(tmp_path))
    module_ref = builder._resolve_js_specifier("@/missing", str(tmp_path / "app.ts"), "app")
    assert module_ref == "ghost.missing"
    assert not module_ref.startswith("@")


# --------------------------------------------------------------------- #
# Scope: unaliased external imports fall through untouched
# --------------------------------------------------------------------- #
def test_unaliased_bare_packages_are_unaffected_by_a_present_tsconfig(tmp_path) -> None:
    _write_basic_ts_project(tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}})
    builder = ConcreteGraphBuilder(str(tmp_path))
    file_path = str(tmp_path / "app.ts")
    assert builder._resolve_js_specifier("express", file_path, "app") == "express"
    assert builder._resolve_js_specifier("lodash", file_path, "app") == "lodash"
    assert builder._resolve_js_specifier("@trpc/server", file_path, "app") == "@trpc.server"


# --------------------------------------------------------------------- #
# Scope: absence of tsconfig.json degrades gracefully
# --------------------------------------------------------------------- #
def test_no_tsconfig_at_all_degrades_to_standard_bare_specifier_handling(tmp_path) -> None:
    (tmp_path / "app.ts").write_text("import x from '@/utils';\n")
    builder = ConcreteGraphBuilder(str(tmp_path))
    assert builder._resolve_js_specifier("@/utils", str(tmp_path / "app.ts"), "app") == "@.utils"


def test_tsconfig_with_no_paths_key_degrades_to_standard_handling(tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"target": "es2020"}}))
    builder = ConcreteGraphBuilder(str(tmp_path))
    file_path = str(tmp_path / "app.ts")
    assert builder._resolve_js_specifier("@/utils", file_path, "app") == "@.utils"


def test_malformed_tsconfig_json_degrades_gracefully_without_crashing(tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text("{ this is not valid json")
    builder = ConcreteGraphBuilder(str(tmp_path))
    file_path = str(tmp_path / "app.ts")
    assert builder._resolve_js_specifier("@/utils", file_path, "app") == "@.utils"


# --------------------------------------------------------------------- #
# JSONC tolerance (real tsconfig.json files routinely have comments and
# trailing commas - plain json.loads rejects both outright).
# --------------------------------------------------------------------- #
def test_tsconfig_with_comments_and_trailing_commas_still_parses(tmp_path) -> None:
    tsconfig_text = (
        "{\n"
        "  // a line comment\n"
        "  \"compilerOptions\": {\n"
        "    /* a block comment */\n"
        "    \"baseUrl\": \".\",\n"
        "    \"paths\": {\n"
        "      \"@/*\": [\"src/*\"],\n"
        "    },\n"
        "  },\n"
        "}\n"
    )
    (tmp_path / "tsconfig.json").write_text(tsconfig_text)
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.ts").write_text("export function helper() { return 1; }\n")
    builder = ConcreteGraphBuilder(str(tmp_path))
    module_ref = builder._resolve_js_specifier("@/utils", str(tmp_path / "app.ts"), "app")
    assert module_ref == "src.utils"


def test_strip_jsonc_comments_leaves_a_slash_inside_a_string_untouched() -> None:
    """A real path value legitimately containing `//` (unusual, but not
    invalid JSON) must never be mistaken for the start of a comment."""
    text = '{"paths": {"a/*": ["not//a/comment/*"]}}'
    assert _strip_jsonc_comments(text) == text


def test_strip_jsonc_comments_removes_line_and_block_comments_and_trailing_commas() -> None:
    text = '{\n  // comment\n  "a": 1, /* block */\n  "b": [1, 2,],\n}\n'
    stripped = _strip_jsonc_comments(text)
    assert json.loads(stripped) == {"a": 1, "b": [1, 2]}


# --------------------------------------------------------------------- #
# Monorepo: nearest tsconfig wins over one at the repo root.
# --------------------------------------------------------------------- #
def test_nested_tsconfig_shadows_the_repo_root_one_for_files_under_it(tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["root_src/*"]}}}))
    pkg = tmp_path / "packages" / "app"
    pkg.mkdir(parents=True)
    (pkg / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["pkg_src/*"]}}}))
    (pkg / "pkg_src").mkdir()
    (pkg / "pkg_src" / "utils.ts").write_text("export function helper() { return 1; }\n")

    builder = ConcreteGraphBuilder(str(tmp_path))
    module_ref = builder._resolve_js_specifier("@/utils", str(pkg / "app.ts"), "packages.app.app")
    assert module_ref == "packages.app.pkg_src.utils"


# --------------------------------------------------------------------- #
# Scope: workspace caching - parsed once, reused across files/imports.
# --------------------------------------------------------------------- #
def test_tsconfig_lookup_and_parse_are_each_cached_after_first_resolution(tmp_path) -> None:
    _write_basic_ts_project(tmp_path, {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}})
    builder = ConcreteGraphBuilder(str(tmp_path))
    file_a = str(tmp_path / "a.ts")
    file_b = str(tmp_path / "b.ts")

    builder._resolve_js_specifier("@/utils", file_a, "a")
    assert len(builder._tsconfig_lookup_cache) == 1
    assert len(builder._tsconfig_parse_cache) == 1
    tsconfig_path = next(iter(builder._tsconfig_parse_cache))

    builder._resolve_js_specifier("@/components/Button", file_b, "b")
    # A second file in the same directory reuses the same lookup-cache
    # entry keyed by directory, and the same parsed-config cache entry
    # keyed by the resolved tsconfig path - not a second disk read/parse.
    assert len(builder._tsconfig_parse_cache) == 1
    assert tsconfig_path in builder._tsconfig_parse_cache


def test_parse_ts_path_alias_config_returns_none_for_a_missing_file(tmp_path) -> None:
    assert _parse_ts_path_alias_config(str(tmp_path / "does_not_exist.json")) is None


# --------------------------------------------------------------------- #
# Unit: pattern matching in isolation.
# --------------------------------------------------------------------- #
def test_best_ts_path_alias_match_prefers_exact_over_wildcard() -> None:
    paths = {"utils": ["src/utils/index.ts"], "*": ["src/*"]}
    assert _best_ts_path_alias_match("utils", paths) == ("utils", "")


def test_best_ts_path_alias_match_prefers_longest_wildcard_prefix() -> None:
    paths = {"@/*": ["src/*"], "@/components/*": ["src/components/*"]}
    assert _best_ts_path_alias_match("@/components/Button", paths) == ("@/components/*", "Button")


def test_best_ts_path_alias_match_returns_none_when_nothing_matches() -> None:
    paths = {"@/*": ["src/*"]}
    assert _best_ts_path_alias_match("express", paths) is None
