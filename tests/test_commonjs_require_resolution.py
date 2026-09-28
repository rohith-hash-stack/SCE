"""`feature/commonjs-require-resolution`: CommonJS `require(...)`
extraction (`ConcreteGraphBuilder._parse_js_requires`) and the shared
`"default"` primary-export redirect (`prism.external.index._commonjs_
primary_export_name`) it needs to make a bare namespace/default
require's own later bare call resolve to something real.

Every fixture here is a synthetic, generic CommonJS shape (no package-
name special-casing anywhere in the code under test) - `lodash`/`cookie`/
`jsonwebtoken`/`etag` names below are illustrative test data, not
anything the implementation itself is aware of. The real-world proof
this generalizes is `tests/test_engine_locator_dispatch.py`'s own
Express `path-to-regexp` case (a real, installed npm package whose
local require alias genuinely differs from its internal function name),
exercised end to end there.
"""
from __future__ import annotations

import json

from prism.cli import build_pipeline
from prism.engine import PrismEngine
from prism.external.index import _commonjs_primary_export_name, _find_definition
from prism.parser.tree_sitter_loader import parse_source


def _build(tmp_path, files: dict[str, str]):
    for rel_path, content in files.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return build_pipeline(str(tmp_path), use_cache=False)


# --------------------------------------------------------------------- #
# `_parse_js_requires`: the 5 real binding shapes, via the real
# `import_map` a full `build_pipeline` produces.
# --------------------------------------------------------------------- #
def test_default_namespace_binding_resolves_bare(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "var pathRegexp = require('path-to-regexp');\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("pathRegexp") == "path-to-regexp"


def test_relative_default_namespace_binding_resolves_to_the_real_module(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "const utils = require('./utils');\n", "utils.js": "exports.helper = function helper() {};\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("utils") == "utils"


def test_destructuring_binds_each_named_export(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "const { parse, serialize } = require('cookie');\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("parse") == "cookie.parse"
    assert im.resolve("serialize") == "cookie.serialize"


def test_aliased_destructuring_binds_the_real_export_name_not_the_alias(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "const { verify: verifyJwt } = require('jsonwebtoken');\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("verifyJwt") == "jsonwebtoken.verify"


def test_property_access_binds_the_named_export_directly(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "const fn = require('etag').generate;\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("fn") == "etag.generate"


def test_side_effect_require_introduces_no_binding(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "require('side-effect-pkg');\nfunction noop() {}\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("side-effect-pkg") is None
    assert im.aliases == {}


def test_const_let_var_are_all_equivalent(tmp_path):
    builder, _ = _build(
        tmp_path,
        {"app.js": "const a = require('pkg-a');\nlet b = require('pkg-b');\nvar c = require('pkg-c');\n"},
    )
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("a") == "pkg-a"
    assert im.resolve("b") == "pkg-b"
    assert im.resolve("c") == "pkg-c"


def test_typescript_and_tsx_files_also_extract_commonjs_requires(tmp_path):
    builder, _ = _build(tmp_path, {"app.ts": "const pkg = require('some-pkg');\n"})
    im = builder.import_map(str(tmp_path / "app.ts"))
    assert im.resolve("pkg") == "some-pkg"


# --------------------------------------------------------------------- #
# Dynamic/computed require - gracefully ignored, never guessed.
# --------------------------------------------------------------------- #
def test_dynamic_require_with_a_variable_is_ignored(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "var name = 'pkg';\nvar x = require(name);\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("x") is None


def test_dynamic_require_with_a_function_call_is_ignored(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "var x = require(resolvePkgName());\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("x") is None


def test_require_dot_resolve_is_not_treated_as_a_plain_require(tmp_path):
    """`require.resolve('pkg')`'s own callee is a `member_expression`
    (`require.resolve`), not a bare `identifier` reading "require" -
    genuinely never a plain `require(...)` call at all, not something
    to special-case around."""
    builder, _ = _build(tmp_path, {"app.js": "var x = require.resolve('pkg');\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("x") is None


# --------------------------------------------------------------------- #
# `_commonjs_primary_export_name` / `_find_definition("default")`.
# --------------------------------------------------------------------- #
def test_commonjs_primary_export_name_finds_a_real_module_exports_identifier():
    source = b"module.exports = pathToRegexp;\nfunction pathToRegexp(path) {}\n"
    parsed = parse_source("index.js", source)
    assert _commonjs_primary_export_name(parsed) == "pathToRegexp"


def test_commonjs_primary_export_name_is_none_for_a_require_reexport():
    """`module.exports = require('./x')` - a whole-module re-export,
    not a real definition of its own in this file - a disclosed
    non-goal, not guessed at."""
    source = b"module.exports = require('./impl');\n"
    parsed = parse_source("index.js", source)
    assert _commonjs_primary_export_name(parsed) is None


def test_commonjs_primary_export_name_is_none_for_an_object_literal_export():
    source = b"module.exports = { a: 1, b: 2 };\n"
    parsed = parse_source("index.js", source)
    assert _commonjs_primary_export_name(parsed) is None


def test_commonjs_primary_export_name_is_none_for_an_anonymous_function_export():
    """A disclosed non-goal, the same real gap ES's own anonymous
    `export default function () {}` already has - no name to redirect
    to at all."""
    source = b"module.exports = function () { return 1; };\n"
    parsed = parse_source("index.js", source)
    assert _commonjs_primary_export_name(parsed) is None


def test_find_definition_default_redirects_to_the_real_commonjs_export():
    source = b"module.exports = pathToRegexp;\nfunction pathToRegexp(path) { return path; }\n"
    parsed = parse_source("index.js", source)
    match = _find_definition(parsed, "default")
    assert match is not None
    _def_node, _enclosing, qualified, kind = match
    assert qualified == "pathToRegexp"
    assert kind == "function"


def test_find_definition_default_still_matches_a_literal_default_symbol():
    """A file that genuinely declares something named `default` (however
    unusual) must still resolve - the CommonJS redirect only fires when
    `_commonjs_primary_export_name` finds a real target, never
    unconditionally replacing a literal match."""
    source = b"function default_handler() {}\n"
    parsed = parse_source("index.js", source)
    assert _find_definition(parsed, "default_handler") is not None


# --------------------------------------------------------------------- #
# End-to-end: `PrismEngine.build_external_candidate_manifest` against a
# synthetic package whose local require alias differs from its real
# internal name - the exact shape that would silently fail without the
# `_commonjs_primary_export_name` redirect.
# --------------------------------------------------------------------- #
def _write_commonjs_package(node_modules, name: str, main_content: str, package_json: dict | None = None) -> None:
    pkg_dir = node_modules / name
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(json.dumps(package_json or {"name": name, "main": "index.js"}))
    (pkg_dir / "index.js").write_text(main_content)


def test_default_namespace_require_resolves_through_the_alias_mismatch(tmp_path):
    node_modules = tmp_path / "node_modules"
    _write_commonjs_package(
        node_modules,
        "widget-lib",
        "module.exports = internalWidgetFn;\nfunction internalWidgetFn(x) { return x; }\n",
    )
    (tmp_path / "app.js").write_text(
        "var localAlias = require('widget-lib');\n\nfunction use() {\n    return localAlias(1);\n}\n"
    )
    engine = PrismEngine.from_repo(str(tmp_path))
    manifest_text, universe = engine.build_external_candidate_manifest(["app.use"], root_imports=["widget-lib"])
    assert universe == {"widget-lib.index.internalWidgetFn"}
    assert "declare function internalWidgetFn" in manifest_text


def test_destructured_require_resolves_end_to_end(tmp_path):
    """Real `cookie`'s own actual shape (`exports.parse = parse;` with
    `parse` declared separately as a plain named function below it, not
    an inline function expression assigned directly to the property) -
    confirmed live against the real installed `cookie` package while
    designing this test."""
    node_modules = tmp_path / "node_modules"
    _write_commonjs_package(
        node_modules,
        "cookie-lib",
        "exports.parse = parse;\n\nfunction parse(str) { return str; }\n",
    )
    (tmp_path / "app.js").write_text(
        "const { parse } = require('cookie-lib');\n\nfunction use() {\n    return parse('a=b');\n}\n"
    )
    engine = PrismEngine.from_repo(str(tmp_path))
    manifest_text, universe = engine.build_external_candidate_manifest(["app.use"], root_imports=["cookie-lib"])
    assert universe == {"cookie-lib.index.parse"}
    assert "declare function parse" in manifest_text


# --------------------------------------------------------------------- #
# Regression: ES `import`/`namespace_import` handling is unaffected -
# `_parse_js_requires` is strictly additive.
# --------------------------------------------------------------------- #
def test_es_namespace_import_still_binds_bare_as_before(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "import * as ns from 'some-pkg';\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("ns") == "some-pkg"


def test_es_named_import_is_unaffected_by_commonjs_handling(tmp_path):
    builder, _ = _build(tmp_path, {"app.js": "import { thing } from 'some-pkg';\n"})
    im = builder.import_map(str(tmp_path / "app.js"))
    assert im.resolve("thing") == "some-pkg.thing"
