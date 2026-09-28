"""Phase D (`TypeScriptSourceLocator` engine-wiring follow-up):
`PrismEngine.build_external_candidate_manifest` previously always
resolved through the default `PythonSourceLocator()` regardless of the
call site's real language - silently correct only because every real
caller through this method so far indexed a Python repo. These tests
exercise the real per-file language dispatch (`prism.engine.
_locator_for_language`) end to end against a synthetic TS/JS project
with a real `node_modules` tree, matching the same `build_pipeline`/
`PrismEngine.from_repo` methodology `tests/test_multi_repo_external_
indexing.py` already uses for Python.

Also covers a real, connected bug found live while wiring this in:
`_matching_root_import` (replacing a naive `origin.split(".")[0]`)
- `ConcreteGraphBuilder._resolve_js_specifier`'s own bare-npm-package
fallback already converts a scoped package's `/` to `.`
(`"@trpc/server"` -> `"@trpc.server"`) before an import's resolved
origin ever reaches `build_external_candidate_manifest`, so the old
`origin.split(".")[0]` check silently truncated it to `"@trpc"`, which
never matched a real `root_imports` entry of `"@trpc/server"` - every
scoped npm package's bare/aliased import was unreachable through Path 1
at all, regardless of this locator-dispatch work.
"""
from __future__ import annotations

import json

from prism.engine import PrismEngine, _locator_for_language, _matching_root_import
from prism.external.index import PythonSourceLocator
from prism.external.locator_ts import TypeScriptSourceLocator
from prism.parser.tree_sitter_loader import LanguageID


def _write_ts_project(root) -> None:
    node_modules = root / "node_modules"

    express_dir = node_modules / "express"
    express_dir.mkdir(parents=True)
    (express_dir / "package.json").write_text(json.dumps({"name": "express", "main": "index.js"}))
    (express_dir / "index.js").write_text("module.exports = function () {};\n")

    types_express_dir = node_modules / "@types" / "express"
    types_express_dir.mkdir(parents=True)
    (types_express_dir / "package.json").write_text(json.dumps({"name": "@types/express", "types": "index.d.ts"}))
    (types_express_dir / "index.d.ts").write_text(
        "declare namespace e {\n    export function Router(options?: RouterOptions): void;\n}\nexport = e;\n"
    )

    trpc_dir = node_modules / "@trpc" / "server"
    trpc_dir.mkdir(parents=True)
    (trpc_dir / "package.json").write_text(json.dumps({"name": "@trpc/server", "types": "index.d.ts"}))
    (trpc_dir / "index.d.ts").write_text("export declare function initTRPC(): void;\n")

    (root / "app.ts").write_text(
        "import express from 'express';\n\nexport function setup() {\n    const r = express.Router();\n    return r;\n}\n"
    )
    (root / "trpc_app.ts").write_text(
        "import { initTRPC } from '@trpc/server';\n\nexport function run() {\n    return initTRPC();\n}\n"
    )


def _accept_every_candidate(manifest_text: str) -> list[str]:
    lines = [ln for ln in manifest_text.split("\n")[1:-1] if ln]
    return [ln.split("|")[0] for ln in lines]


# --------------------------------------------------------------------- #
# Unit: _locator_for_language dispatch itself.
# --------------------------------------------------------------------- #
def test_python_dispatches_to_python_source_locator():
    locator = _locator_for_language(LanguageID.PYTHON, "/some/dir")
    assert isinstance(locator, PythonSourceLocator)


def test_typescript_javascript_and_tsx_dispatch_to_typescript_source_locator(tmp_path):
    for lang in (LanguageID.TYPESCRIPT, LanguageID.JAVASCRIPT, LanguageID.TSX):
        locator = _locator_for_language(lang, str(tmp_path))
        assert isinstance(locator, TypeScriptSourceLocator)


def test_unsupported_language_returns_none_not_a_guessed_default():
    assert _locator_for_language(LanguageID.GO, "/some/dir") is None
    assert _locator_for_language(LanguageID.JAVA, "/some/dir") is None
    assert _locator_for_language(LanguageID.CSHARP, "/some/dir") is None


# --------------------------------------------------------------------- #
# Unit: _matching_root_import - the connected scoped-package fix.
# --------------------------------------------------------------------- #
def test_matching_root_import_handles_a_scoped_package_correctly():
    """The exact regression this session found: a naive `origin.split(
    ".")[0]` on `"@trpc.server.initTRPC"` returns only `"@trpc"`, never
    matching a real `root_imports` entry of `"@trpc/server"`."""
    assert _matching_root_import("@trpc.server.initTRPC", ["@trpc/server"]) == "@trpc/server"


def test_matching_root_import_is_unchanged_for_ordinary_unscoped_names():
    assert _matching_root_import("markdown_it.MarkdownIt", ["markdown_it"]) == "markdown_it"
    assert _matching_root_import("orjson.dumps", ["orjson"]) == "orjson"


def test_matching_root_import_prefers_the_longest_match():
    assert _matching_root_import("@scope.pkg.sub.Thing", ["@scope/pkg", "@scope/pkg/sub"]) == "@scope/pkg/sub"


def test_matching_root_import_returns_none_for_no_match():
    assert _matching_root_import("lodash.debounce", ["express"]) is None


# --------------------------------------------------------------------- #
# End-to-end: a real TS project with node_modules routes external
# lookups through TypeScriptSourceLocator.
# --------------------------------------------------------------------- #
def test_ts_project_resolves_an_unscoped_untyped_package_via_its_types_sibling(tmp_path):
    _write_ts_project(tmp_path)
    engine = PrismEngine.from_repo(str(tmp_path))
    manifest_text, universe = engine.build_external_candidate_manifest(["app.setup"], root_imports=["express"])
    assert universe == {"express.index.Router"}
    assert "declare function Router" in manifest_text


def test_ts_project_resolves_a_scoped_package_via_a_bare_named_import(tmp_path):
    """Path 1 (bare call after a named import) for a scoped package -
    the exact case `_matching_root_import` was needed for."""
    _write_ts_project(tmp_path)
    engine = PrismEngine.from_repo(str(tmp_path))
    manifest_text, universe = engine.build_external_candidate_manifest(["trpc_app.run"], root_imports=["@trpc/server"])
    assert universe == {"@trpc/server.index.initTRPC"}
    assert "declare function initTRPC" in manifest_text


def test_ts_project_end_to_end_retrieval_admits_a_real_declare_stub(tmp_path):
    _write_ts_project(tmp_path)
    engine = PrismEngine.from_repo(str(tmp_path))

    def request_symbols(manifest_text, task_prompt):
        return [], True

    def request_external_symbols(manifest_text, task_prompt):
        return _accept_every_candidate(manifest_text)

    pkg, diagnostics = engine.retrieve_two_or_three_pass(
        "app.setup", 4000, request_symbols, request_external_symbols, root_imports=["express"],
    )
    assert diagnostics["needs_external_deps"] is True
    assert diagnostics["external_skipped_hallucinated"] == []

    external_nodes = [n for n in pkg.nodes if n.role == "external"]
    assert len(external_nodes) == 1
    node = external_nodes[0]
    assert node.language == "typescript"
    assert node.compression == "L2_skeleton"
    assert node.contract is None
    assert node.body == "declare function Router(options: RouterOptions): void;"


def test_ts_and_scoped_candidates_resolve_together_in_one_manifest_call(tmp_path):
    """Both a plain and a scoped package, from two different files,
    resolved in the same Turn 2a call - confirms the per-file locator
    dispatch (and its cache) doesn't cross-contaminate between them."""
    _write_ts_project(tmp_path)
    engine = PrismEngine.from_repo(str(tmp_path))
    _manifest_text, universe = engine.build_external_candidate_manifest(
        ["app.setup", "trpc_app.run"], root_imports=["express", "@trpc/server"],
    )
    assert universe == {"express.index.Router", "@trpc/server.index.initTRPC"}
